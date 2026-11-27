"""Offline aggregate-only microbenchmark. Run: python -B -m recommendation.local_benchmark.

Uses synthetic sales history/model and artificial identity/availability assertions,
with the existing local catalog opened read-only. Never emits catalog records.
Single process, sequential calls, no HTTP and no index build.
"""
import argparse
import datetime
import json
import math
import os
import platform
import resource
import socket
import sqlite3
import statistics
import time
from pathlib import Path

from recommendation.experimental_graph import fit
from recommendation.hybrid import Hybrid
from recommendation.provenance_links import demo_inputs


def percentile(values, p):
    """Nearest-rank percentile, requiring a non-empty sample."""
    if not values or not 0 < p <= 1:
        raise ValueError('invalid sample or percentile')
    return sorted(values)[math.ceil(p * len(values)) - 1]


def summarize(values):
    return {'count': len(values), 'p50_ms': percentile(values, .5),
            'p95_ms': percentile(values, .95), 'min_ms': min(values), 'max_ms': max(values)}


def benchmark(path, warmup, iterations):
    if warmup < 0 or iterations < 1:
        raise ValueError('warmup must be nonnegative and iterations positive')
    # Defense in depth: no network sockets are required by the measured path.
    def no_socket(*args, **kwargs):
        raise RuntimeError('network disabled in benchmark')
    start = time.perf_counter_ns()
    path = Path(path).resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    original_socket = socket.socket
    socket.socket = no_socket
    try:
        count = conn.execute('SELECT count(*) FROM offers').fetchone()[0]
        # One deterministic unique available offer in a populated category. No records leave the process.
        offer = conn.execute("""SELECT o.offer_id FROM offers o
            WHERE o.available='true' AND o.category_id IS NOT NULL AND o.category_id != ''
            AND o.offer_id IS NOT NULL AND o.offer_id != ''
            AND (SELECT count(*) FROM offers x WHERE x.offer_id=o.offer_id)=1
            ORDER BY o.row_id LIMIT 1""").fetchone()
        if not offer:
            raise RuntimeError('no suitable local offer')
        model = fit([('synthetic-order', 'synthetic-user', '2026-01-01', 'synthetic-item')])
        _, registry, _ = demo_inputs()
        engine = Hybrid(model, conn, {'schema_version': 1, 'items': {}}, registry, [],
                        verified_identities={'synthetic-item': offer[0]}, availability_check=lambda _: True)
        setup_ms = (time.perf_counter_ns() - start) / 1e6
        scenarios = {'catalog_path': (['synthetic-item'], True),
                     'blocked_incomplete': (['synthetic-item'], False)}
        results = {}
        for name, (history, complete) in scenarios.items():
            times, slots, blocks = [], 0, 0
            for i in range(warmup + iterations):
                before = time.perf_counter_ns()
                result = engine.recommend(history, history_complete=complete)
                elapsed = (time.perf_counter_ns() - before) / 1e6
                if i >= warmup:
                    times.append(elapsed)
                    slots += len(result['recommendations'])
                    blocks += result['blocked_reason'] is not None
            results[name] = {**summarize(times), 'recommendation_slots': slots, 'blocked_requests': blocks}
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return {'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'host': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine(),
                         'python': platform.python_version(), 'logical_cpus': os.cpu_count()},
                'configuration': {'warmup_per_scenario': warmup, 'iterations_per_scenario': iterations,
                                  'concurrency': 1, 'history_length': 1, 'limit': 3,
                                  'catalog_offer_rows': count, 'catalog_file_bytes': path.stat().st_size,
                                  'index_mode': 'existing immutable read-only; no rebuild',
                                  'scope': 'direct Hybrid.recommend only; no HTTP, model training, live availability or LLM',
                                  'availability': 'synthetic always-true callback, not live',
                                  'identity': 'synthetic mapping to one local catalog row, not an attestation',
                                  'percentiles': 'nearest rank', 'cache': 'warm after setup and warmup; OS cache uncontrolled'},
                'setup_wall_ms': setup_ms, 'scenarios': results,
                'process': {'peak_rss_kib_linux': usage.ru_maxrss, 'user_cpu_seconds': usage.ru_utime,
                            'system_cpu_seconds': usage.ru_stime,
                            'scope': 'whole benchmark process including imports, setup, both scenarios; not per request'},
                'limitations': ['Not a production SLA or capacity test.', 'Synthetic tiny model; real model, HTTP and integrations excluded.',
                                'Catalog path uses simulated assertions; does not validate identities or availability.',
                                'One sequential run; no price, concurrency, network or provider costs measured.']}
    finally:
        socket.socket = original_socket
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', default='data/catalog-20261126.sqlite')
    parser.add_argument('--warmup', type=int, default=20)
    parser.add_argument('--iterations', type=int, default=200)
    parser.add_argument('--output', help='Optional new private JSON under data/; never overwritten')
    args = parser.parse_args()
    if args.output:
        out = Path(args.output).resolve()
        if not out.is_relative_to(Path('data').resolve()):
            parser.error('output must be under data/')
        fd = os.open(out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(benchmark(args.catalog, args.warmup, args.iterations), f, ensure_ascii=False, indent=2)
                f.write('\n')
        except BaseException:
            out.unlink(missing_ok=True)
            raise
    else:
        print(json.dumps(benchmark(args.catalog, args.warmup, args.iterations), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
