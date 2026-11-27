"""Paired client bootstrap for the already-observed v4 test; aggregate output only.

No fitting or model selection uses test outcomes. Percentile intervals describe
resampling uncertainty conditional on the fitted models and observed clients,
not causality, an independent holdout, or uncertainty in the training process.
A includes B, and segment labels are undated. No catalogue is used.
"""
import argparse
import json
import random

from recommendation.profile_pipeline import ProfilePipeline
from recommendation.profile_v4_evaluation import (
    CUTOFF, official_models, official_rank, profile_rank, split_rows,
)


def percentile(values, q):
    """Linear interpolation of the empirical quantile."""
    values = sorted(values)
    position = (len(values) - 1) * q
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def summarize(hits, resamples=10000, seed=20261126):
    """Summarize aligned per-client hit counts, resampling whole paired rows."""
    if resamples < 1:
        raise ValueError('resamples must be positive')
    names = ('profile', 'popularity', 'copurchase')
    n = len(hits)
    if not n:
        return {'clients': 0, 'methods': None, 'differences': None}
    if any(len(row) != 3 or any(not isinstance(x, int) or x < 0 or x > 3 for x in row) for row in hits):
        raise ValueError('expected three hit counts from 0 to 3 per client')
    totals = [sum(row[j] for row in hits) for j in range(3)]
    rng = random.Random(seed)
    sampled = [[] for _ in names]
    deltas = [[], []]
    for _ in range(resamples):
        sums = [0, 0, 0]
        for _ in range(n):
            row = hits[rng.randrange(n)]
            for j in range(3):
                sums[j] += row[j]
        for j in range(3):
            sampled[j].append(sums[j] / (3 * n))
        for j in (1, 2):
            deltas[j - 1].append((sums[0] - sums[j]) / (3 * n))
    interval = lambda v: [percentile(v, .025), percentile(v, .975)]
    return {
        'clients': n, 'denominator': 3 * n,
        'methods': {name: {'hits': totals[j], 'precision_at_3': totals[j] / (3 * n),
                           'bootstrap_95_percentile': interval(sampled[j])}
                    for j, name in enumerate(names)},
        'differences': {names[j]: {'profile_minus_baseline': (totals[0] - totals[j]) / (3 * n),
                                    'bootstrap_95_percentile': interval(deltas[j - 1])}
                        for j in (1, 2)},
    }


def run(rows, groups, resamples=10000, seed=20261126):
    train, hist, future = split_rows(rows)
    targets = {u: items - hist[u] for u, items in future.items() if u in hist and items - hist[u]}
    cohorts = {'A': sorted(targets, key=str),
               'B': sorted((u for u in targets if len(hist[u]) == 1), key=str)}
    pop, co = official_models(train, hist)
    pipeline = ProfilePipeline(train, CUTOFF)
    # Compute once: no retraining, no fitting on future and no selection on results.
    hits = {}
    for u in cohorts['A']:
        ranks = (profile_rank(pipeline, u, 'full'), official_rank(u, hist, pop),
                 official_rank(u, hist, pop, co))
        hits[u] = tuple(len(set(rank[:3]) & targets[u]) for rank in ranks)
    result = {}
    for cohort, users in cohorts.items():
        result[cohort] = {}
        for segment in ('all', 'B2B', 'B2C', 'other_or_unknown'):
            selected = [u for u in users if segment == 'all' or
                        (groups.get(u) == segment if segment != 'other_or_unknown'
                         else groups.get(u) not in ('B2B', 'B2C'))]
            result[cohort][segment] = summarize([hits[u] for u in selected], resamples, seed)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xlsx')
    parser.add_argument('--resamples', type=int, default=10000)
    parser.add_argument('--seed', type=int, default=20261126)
    args = parser.parse_args()
    import openpyxl
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    try:
        rows = [r[:4] for r in wb['Продажи'].iter_rows(min_row=2, values_only=True)]
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
    finally:
        wb.close()
    print(json.dumps({'resamples': args.resamples, 'seed': args.seed,
                      'method': 'paired client bootstrap, percentile 95% intervals, fixed denominator 3*n',
                      'limitations': 'Observed test; profile tuned on v3; not independent or causal. Fixed fitted models, undated groups, unknown historical availability; A includes B. Small groups have unstable intervals. Official and profile basket keys differ.',
                      'results': run(rows, groups, args.resamples, args.seed)}, indent=2))


if __name__ == '__main__':
    main()
