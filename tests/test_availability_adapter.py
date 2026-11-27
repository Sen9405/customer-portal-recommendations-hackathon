import asyncio
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from recommendation.availability_adapter import AvailabilityAdapter, AvailabilityEvidence
import test_hybrid


def evidence(offer='offer-b', available=True, observed=None):
    return AvailabilityEvidence(offer, available, observed or datetime.now(timezone.utc))


class AvailabilityAdapterTest(unittest.TestCase):
    def adapter(self, provider, verifier=lambda _: True, **kw):
        return AvailabilityAdapter(provider, verifier, timeout_seconds=kw.get('timeout', 0.1),
                                   max_age_seconds=kw.get('age', 10))

    def test_fresh_true_false_and_malformed(self):
        for value, expected in [(True, True), (False, False), (1, False), ('true', False)]:
            self.assertIs(self.adapter(lambda _: evidence(available=value))('offer-b'), expected)
        self.assertFalse(self.adapter(lambda _: {'available': True})('offer-b'))
        self.assertFalse(self.adapter(lambda _: evidence())(''))

    def test_errors_stale_future_and_mismatched_id(self):
        def broken(_):
            raise RuntimeError('private provider error')
        self.assertFalse(self.adapter(broken)('offer-b'))
        for result in (evidence(observed=datetime.now(timezone.utc) - timedelta(seconds=11)),
                       evidence(observed=datetime.now(timezone.utc) + timedelta(seconds=1)),
                       evidence(observed=datetime.now()), evidence(offer='other')):
            self.assertFalse(self.adapter(lambda _: result)('offer-b'))
        self.assertFalse(self.adapter(lambda _: evidence(), verifier=lambda _: False)('offer-b'))
        self.assertFalse(self.adapter(lambda _: evidence(), verifier=broken)('offer-b'))

    def test_tampering_rejected_by_explicit_verifier(self):
        authentic = evidence()
        verifier = lambda actual: actual == authentic
        self.assertTrue(self.adapter(lambda _: authentic, verifier)('offer-b'))
        for altered in (replace(authentic, offer_id='other'), replace(authentic, available=False),
                        replace(authentic, observed_at=authentic.observed_at + timedelta(seconds=1))):
            self.assertFalse(self.adapter(lambda _: altered, verifier)('offer-b'))

    def test_sync_and_async_provider_timeout_and_async_api(self):
        def slow(_):
            time.sleep(0.2)
            return evidence()
        async def async_provider(_):
            await asyncio.sleep(0.01)
            return evidence()
        async def async_slow(_):
            await asyncio.sleep(0.2)
            return evidence()
        start = time.monotonic()
        self.assertFalse(self.adapter(slow, timeout=0.03)('offer-b'))
        self.assertLess(time.monotonic() - start, 0.15)
        self.assertTrue(self.adapter(async_provider)('offer-b'))
        self.assertFalse(self.adapter(async_slow, timeout=0.03)('offer-b'))
        self.assertTrue(asyncio.run(self.adapter(async_provider).check_async('offer-b')))

    def test_timeout_in_verifier_and_worker_cap(self):
        import threading
        entered = threading.Event()
        release = threading.Event()
        def blocked(_):
            entered.set()
            release.wait(1)
            return True
        checker = AvailabilityAdapter(lambda _: evidence(), blocked, timeout_seconds=0.03,
                                      max_age_seconds=10, max_in_flight=1)
        try:
            self.assertFalse(checker('offer-b'))
            self.assertTrue(entered.is_set())
            start = time.monotonic()
            self.assertFalse(checker('offer-b'))
            self.assertLess(time.monotonic() - start, 0.02)
        finally:
            release.set()

    def test_configuration_validation(self):
        for args in [(None, lambda _: True, 1, 1), (lambda _: None, None, 1, 1),
                     (lambda _: None, lambda _: True, 0, 1),
                     (lambda _: None, lambda _: True, 1, float('nan'))]:
            with self.assertRaises(ValueError):
                AvailabilityAdapter(args[0], args[1], timeout_seconds=args[2], max_age_seconds=args[3])

    def test_hybrid_uses_adapter_without_changes(self):
        fixture = test_hybrid.HybridTest(methodName='test_sources_are_offers_and_cross_namespace_deduplicated')
        fixture.setUp()
        try:
            checker = self.adapter(lambda oid: evidence(offer=oid))
            result = fixture.engine(availability_check=checker).recommend(['item-a'], history_complete=True)
            self.assertTrue(result['recommendations'])
            denied = self.adapter(lambda oid: evidence(offer=oid), verifier=lambda _: False)
            result = fixture.engine(availability_check=denied).recommend(['item-a'], history_complete=True)
            self.assertEqual(result['recommendations'], [])
        finally:
            fixture.tearDown()
