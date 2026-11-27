"""A/B sales-only baseline plus a separate assumed-architecture snapshot sensitivity audit."""
import argparse
from collections import Counter
import contextlib
import io
import json
import runpy
import sys

from recommendation.agent_offer_gate import checks, refill
from recommendation.contest_recommender import ContestRecommender, PLATFORM_OPTIONS
from recommendation.contest_v5_evaluation import demo_metrics
from recommendation.profile_v4_evaluation import CUTOFF, split_rows
from recommendation.profile_v5_comparison import compare


def evaluate(rows, audiences, original, rec):
    _, hist, future = split_rows(rows)
    targets = {u: items - hist[u] for u, items in future.items() if u in hist and items - hist[u]}
    cohorts = {'A': list(targets), 'B': [u for u in targets if len(hist[u]) == 1]}
    prod = {r[3]: (r[6] if len(r) > 6 and r[6] else r[3]) for r in rows}
    scenarios = {p: {} for p in (None, *PLATFORM_OPTIONS)}
    for u in targets:
        audience = audiences.get(u) if audiences.get(u) in ('B2B', 'B2C') else None
        candidates = rec.recommend(u, audience)['candidates']
        scenarios[None][u] = candidates
        for platform in PLATFORM_OPTIONS:
            evidence = checks(rec, candidates, audience, platform)
            removals = [alias for alias, fact in evidence.items() if fact['status'] == 'incompatible']
            scenarios[platform][u] = refill(rec, candidates, evidence, removals, audience, platform)
    historical = compare(rows, audiences, original)
    for name in ('original_list_mismatches_A_plus_B', 'original_metric_mismatches_segments'):
        if any(historical[name].values()):
            raise ValueError('organizer baseline mismatch')
    snapshot = {}
    for name, users in cohorts.items():
        snapshot[name] = {}
        for platform, ranks in scenarios.items():
            metrics = demo_metrics(users, targets, hist, prod, ranks)
            changes = Counter()
            for user in users:
                before, after = (scenarios[None][user][:3], ranks[user][:3])
                bh = {c['item_id'] for c in before} & targets[user]
                ah = {c['item_id'] for c in after} & targets[user]
                changes.update(changed=before != after, added=len(ah - bh), lost=len(bh - ah))
            assert metrics['known_hits'] == snapshot[name].get('unknown', metrics)['known_hits'] + changes['added'] - changes['lost']
            snapshot[name][platform or 'unknown'] = {**metrics, **changes}
    return {'historical': historical, 'snapshot_sensitivity': snapshot, 'llm_calls': 0,
            'limits': 'Unknown is the only observed platform state in v5. Each other row assigns one identical hypothetical architecture to every client; it is not a measured outcome. The offline run applies only verified tool decisions, not model reliability. Snapshot/mapping are not point-in-time.'}


def main():
    import openpyxl
    p = argparse.ArgumentParser(description=__doc__)
    for field in ('xlsx', 'baseline', 'mapping', 'index'):
        p.add_argument(field)
    a = p.parse_args()
    w = openpyxl.load_workbook(a.xlsx, read_only=True, data_only=True)
    try:
        rows = list(w['Продажи'].iter_rows(min_row=2, values_only=True))
        audiences = {r[0]: r[1] for r in w['Клиенты'].iter_rows(min_row=2, values_only=True)}
    finally:
        w.close()
    argv = sys.argv
    try:
        sys.argv = [a.baseline, a.xlsx]
        with contextlib.redirect_stdout(io.StringIO()):
            original = runpy.run_path(a.baseline)
    finally:
        sys.argv = argv
    rec = ContestRecommender(split_rows(rows)[0], CUTOFF, a.mapping, a.index, workbook_path=a.xlsx)
    print(json.dumps(evaluate(rows, audiences, original, rec), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
