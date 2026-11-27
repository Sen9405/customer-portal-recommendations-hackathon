"""Reproduce v4 official baselines and evaluate the unchanged profile pipeline.

Run: python3 -B -m recommendation.profile_v4_evaluation data/dataset_v4.xlsx
Only aggregate statistics are printed; input data and per-client output stay local.
The Monday cutoff includes the complete labelled week through Sunday Sept 6.
Customer group is undated and used only for retrospective reporting. Availability
is unknown. The official test was observed, and the profile model was tuned on
v3: these comparisons and predeclared ablations are not a blind held-out test.
The official co-purchase basket key is (order_id, week), unlike ProfilePipeline's
(client, week, order) key. Scores are not evidence of compatibility or causality.
"""
import argparse
import itertools
import json
from collections import Counter, defaultdict
from datetime import date

from recommendation.profile_pipeline import ProfilePipeline, _day

CUTOFF = date(2026, 8, 31)
MODES = ('full', 'no_recency', 'no_frequency', 'neither')


def split_rows(rows):
    train, hist, future = [], defaultdict(set), defaultdict(set)
    for order, user, week, item, *_ in rows:
        week = _day(week)
        if any(x is None or x == '' for x in (order, user, item)):
            raise ValueError('Missing identifier')
        if week <= CUTOFF:
            train.append((order, user, week, item))
            hist[user].add(item)
        else:
            future[user].add(item)
    return train, hist, future


def official_models(train, hist):
    pop = Counter()
    for items in hist.values():
        pop.update(items)
    baskets = defaultdict(set)
    for order, _user, week, item in train:
        baskets[(order, _day(week))].add(item)
    co = defaultdict(Counter)
    for basket in baskets.values():
        for a, b in itertools.permutations(basket, 2):
            co[a][b] += 1
    return pop, co


def official_rank(user, hist, pop, co=None):
    scores = Counter()
    if co is None:
        scores.update(pop)
    else:
        for item in hist[user]:
            scores.update(co.get(item, {}))
    return sorted((x for x in scores if x not in hist[user]),
                  key=lambda x: (-scores[x], -pop[x], str(x)))[:3]


def profile_rank(pipeline, user, mode):
    """Full delegates to production; other modes independently alter weights only.

    no_recency sets the recency factor to 1; no_frequency sets its factor to 1;
    neither does both. Sequential scoring and ordering/fallback remain identical.
    """
    if mode not in MODES:
        raise ValueError('Unknown score mode')
    if mode == 'full':
        return [c['item_id'] for c in pipeline.profile(user)['candidates']]
    seen = pipeline.history.get(user, set())
    baskets = pipeline.user_weeks.get(user, {})
    dates = sorted(baskets)
    latest = baskets[dates[-1]] if dates else set()
    co_scores, seq_scores = Counter(), Counter()
    for item in sorted(seen, key=str):
        n = pipeline.item_orders[user][item]
        last = max(d for d in dates if item in baskets[d])
        frequency = 1 if mode in ('no_frequency', 'neither') else n / (n + 1)
        recency = 1 if mode in ('no_recency', 'neither') else 1 / (1 + (pipeline.as_of - last).days / 91)
        for target, count in pipeline.co.get(item, {}).items():
            co_scores[target] += frequency * recency * count / (count + 1) / len(seen)
    for item in sorted(latest, key=str):
        for target, count in pipeline.seq.get(item, {}).items():
            seq_scores[target] += 0.25 * count / (count + 1) / len(latest)
    targets = (set(co_scores) | set(seq_scores)) - seen
    ranked = sorted(targets, key=lambda x: (-(co_scores[x] + seq_scores[x]), -seq_scores[x], str(x)))
    ranked.extend(x for x in pipeline.pop_rank if x not in seen and x not in targets)
    return ranked[:3]


def evaluate(clients, targets, recommend):
    matches = nonempty = full = 0
    for user in clients:
        # Duplicates never earn extra credit or fill multiple positions.
        unique = list(dict.fromkeys(recommend(user)[:3]))
        matches += len(set(unique) & targets[user])
        nonempty += bool(unique)
        full += len(unique) == 3
    denominator = 3 * len(clients)
    return {'clients': len(clients), 'matches': matches, 'denominator': denominator,
            'precision_at_3': matches / denominator if denominator else None,
            'nonempty': nonempty, 'full_triples': full}


def run(rows, groups):
    train, hist, future = split_rows(rows)
    targets = {u: items - hist[u] for u, items in future.items() if u in hist and items - hist[u]}
    cohorts = {'A': sorted(targets, key=str),
               'B': sorted((u for u in targets if len(hist[u]) == 1), key=str)}
    pop, co = official_models(train, hist)
    pipeline = ProfilePipeline(train, CUTOFF)
    methods = {'popularity': lambda u: official_rank(u, hist, pop),
               'copurchase': lambda u: official_rank(u, hist, pop, co)}
    for mode in MODES:
        methods['profile_' + mode] = lambda u, mode=mode: profile_rank(pipeline, u, mode)
    result = {}
    for cohort, users in cohorts.items():
        result[cohort] = {}
        for segment in ('all', 'B2B', 'B2C'):
            selected = [u for u in users if segment == 'all' or groups.get(u) == segment]
            result[cohort][segment] = {name: evaluate(selected, targets, method)
                                       for name, method in methods.items()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xlsx')
    args = parser.parse_args()
    import openpyxl
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    try:
        rows = [r[:4] for r in wb['Продажи'].iter_rows(min_row=2, values_only=True)]
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
    finally:
        wb.close()
    print(json.dumps({'cutoff_inclusive': CUTOFF.isoformat(),
                      'limitations': 'Official test already observed; model tuned on v3, not blind held-out. Groups undated; historical availability unknown. No current catalog.',
                      'results': run(rows, groups)}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
