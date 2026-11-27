"""Strict sales-only temporal selection; aggregate output, no network or files.

Run: python3 -B -m recommendation.temporal_selection data/dataset.xlsx
Predeclared validation choice: highest A Precision@3, ties in VARIANTS order.
Validation: train <= May 25, labels June 1 through August 31, 2026.
Official: refit <= Aug 31, labels >= Sep 7; only chosen method and popularity.
A requires train history and >=1 novel test item; B is the A subset with exactly
one distinct historical item. A/B are retrospective outcome-conditioned cohorts.
Group labels are only used to report slices, never as model inputs; they are
undated and therefore the slice classifications are uncertain.

Order graph: same client, week and order only. Sequential graph: transitions
between consecutive distinct observed weeks for the same client, fully linked
across the two week baskets, without within-week direction. At inference use
last observed week. Both fall back to train row-count global popularity.
All items, histories, edges and fallback counts come exclusively from train.
Ties sorted by string item ID; fixed denominator 3 even for short lists.
This is an exploratory evaluation: prior work has observed official test results,
so even a fixed procedure here does not restore a pristine locked test.
"""
from collections import Counter, defaultdict
import json
import sys

from recommendation.segmented_history import split_rows, day

VARIANTS = ('popularity', 'coorder', 'sequential')


def ranking(counts):
    return sorted(counts, key=lambda item: (-counts[item], str(item)))


def fit(train):
    history, orders, weeks = defaultdict(set), defaultdict(set), defaultdict(set)
    counts = Counter()
    for order, user, week, item in train:
        week = day(week)
        history[user].add(item)
        orders[(user, week, order)].add(item)
        weeks[(user, week)].add(item)
        counts[item] += 1
    co, seq = defaultdict(Counter), defaultdict(Counter)
    for basket in orders.values():
        for item in basket:
            co[item].update(basket - {item})
    by_user = defaultdict(list)
    for (user, week), items in weeks.items():
        by_user[user].append((week, items))
    latest = {}
    for user, baskets in by_user.items():
        baskets.sort(key=lambda pair: pair[0])
        latest[user] = baskets[-1][1]
        for (_, previous), (_, current) in zip(baskets, baskets[1:]):
            for item in previous:
                seq[item].update(current - {item})
    return {'history': history, 'pop': ranking(counts), 'co': co, 'seq': seq, 'latest': latest}


def recommend(model, user, variant):
    if variant not in VARIANTS:
        raise ValueError('Unknown variant')
    seen = model['history'].get(user, set())
    scores = Counter()
    if variant == 'coorder':
        for item in seen:
            scores.update(model['co'].get(item, {}))
    elif variant == 'sequential':
        for item in model['latest'].get(user, set()):
            scores.update(model['seq'].get(item, {}))
    selected = set(seen)
    result = []
    for item in ranking(scores) + model['pop']:
        if item not in selected:
            result.append(item)
            selected.add(item)
            if len(result) == 3:
                break
    return result


def evaluate(model, test, variant, groups=None):
    future = defaultdict(set)
    for _, user, _, item in test:
        future[user].add(item)
    cohort = {user for user, history in model['history'].items() if future[user] - history}
    result = {}
    subsets = [('A', cohort), ('B', {u for u in cohort if len(model['history'][u]) == 1})]
    if groups is not None:
        subsets += [(name + '_' + group, {u for u in users if groups.get(u) == group})
                    for name, users in list(subsets) for group in ('B2B', 'B2C')]
    for name, users in subsets:
        hits = slots = filled = 0
        for user in users:
            chosen = recommend(model, user, variant)
            hits += len(set(chosen) & (future[user] - model['history'][user]))
            slots += len(chosen)
            filled += bool(chosen)
        n = len(users)
        result[name] = {'n': n, 'hits': hits, 'precision_at_3': hits / (3*n) if n else None,
                        'fill_rate': filled / n if n else None,
                        'slot_fill_rate': slots / (3*n) if n else None}
    return result


def select(validation):
    if not validation or any(v['A']['n'] == 0 for v in validation.values()):
        raise ValueError('Cannot select without validation cohort')
    return max((v for v in VARIANTS if v in validation),
               key=lambda v: validation[v]['A']['hits'] / validation[v]['A']['n'])


def run(rows, groups):
    vt, vl = split_rows(rows, '2026-05-25', '2026-06-01', '2026-08-31')
    model = fit(vt)
    validation = {v: evaluate(model, vl, v, groups) for v in VARIANTS}
    chosen = select(validation)
    ot, ol = split_rows(rows, '2026-08-31', '2026-09-07')
    model = fit(ot)
    official = {v: evaluate(model, ol, v, groups) for v in dict.fromkeys(('popularity', chosen))}
    return {'limitations': 'Previously observed official outputs: not a pristine locked test. No feed, client attributes or groups in fitting. Group labels undated. Offline fill is client-level, not visit-level. Novel test items absent from train cannot be recommended; historical availability unknown. Co-order and sequential associations do not establish compatibility.',
            'selection': 'Max validation A precision@3; tie popularity, coorder, sequential',
            'chosen': chosen, 'validation_rows': {'train': len(vt), 'test': len(vl)},
            'validation': validation, 'official_rows': {'train': len(ot), 'test': len(ol)}, 'official': official}


def main():
    import openpyxl
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python -m recommendation.temporal_selection PATH_TO_XLSX')
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        rows = [tuple(r[:4]) for r in wb['Продажи'].iter_rows(min_row=2, values_only=True)]
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
        print(json.dumps(run(rows, groups), ensure_ascii=False, indent=2))
    finally:
        wb.close()


if __name__ == '__main__':
    main()
