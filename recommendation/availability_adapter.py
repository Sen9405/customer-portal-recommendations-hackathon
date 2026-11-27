"""Opt-in availability adapter; no provider, endpoint or trust is configured here.

The caller must supply a provider and a verifier that authenticates the *entire*
response (including offer ID, availability and timestamp), using independently
verified credentials/provenance. A verifier that merely returns True is not
verification. A feed flag or locally generated timestamp is not live evidence.
The callback is responsible for privacy, transport security and timely updates.
No availability result guarantees purchase: recheck at transaction time.

Sync/async providers and verifiers are supported. Calls are deadline-bounded, but
Python cannot forcibly stop a blocking callback; timed-out daemon workers may
continue. A per-adapter cap prevents unbounded worker creation. The async method
is for event-loop callers; __call__ implements Hybrid's strict bool contract.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
import math
import queue
import threading
import time


@dataclass(frozen=True)
class AvailabilityEvidence:
    offer_id: str
    available: bool
    observed_at: datetime
    proof: bytes = b''


class AvailabilityAdapter:
    def __init__(self, provider, verifier, *, timeout_seconds, max_age_seconds, max_in_flight=8):
        if not callable(provider) or not callable(verifier):
            raise ValueError('explicit provider and verifier callbacks required')
        for value in (timeout_seconds, max_age_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('timeout and maximum age must be finite positive seconds')
        if type(max_in_flight) is not int or max_in_flight < 1:
            raise ValueError('max_in_flight must be a positive integer')
        self._provider = provider
        self._verifier = verifier
        self._timeout = timeout_seconds
        self._max_age = max_age_seconds
        self._slots = threading.BoundedSemaphore(max_in_flight)

    def __call__(self, offer_id):
        if type(offer_id) is not str or not offer_id or not self._slots.acquire(blocking=False):
            return False
        deadline = time.monotonic() + self._timeout
        results = queue.Queue(maxsize=1)

        def run():
            async def resolve(value):
                return await value if inspect.isawaitable(value) else value

            async def check():
                response = await resolve(self._provider(offer_id))
                if type(response) is not AvailabilityEvidence:
                    return False
                verified = await resolve(self._verifier(response))
                if verified is not True or response.offer_id != offer_id or response.available is not True:
                    return False
                timestamp = response.observed_at
                if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    return False
                age = (datetime.now(timezone.utc) - timestamp).total_seconds()
                return 0 <= age <= self._max_age and time.monotonic() <= deadline

            try:
                results.put_nowait(asyncio.run(check()) is True)
            except Exception:
                results.put_nowait(False)
            finally:
                self._slots.release()

        try:
            threading.Thread(target=run, daemon=True).start()
        except Exception:
            self._slots.release()
            return False
        try:
            result = results.get(timeout=max(0, deadline - time.monotonic()))
            return result is True and time.monotonic() <= deadline
        except queue.Empty:
            return False

    async def check_async(self, offer_id):
        """Non-blocking wrapper; cancellation cannot terminate provider workers."""
        return await asyncio.to_thread(self, offer_id)
