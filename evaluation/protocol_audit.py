"""Aggregate-only audit of the organizer's v4 baseline.

Run locally: python3 -B -m evaluation.protocol_audit data/dataset_v4.xlsx
No customer IDs, items, or rows are emitted. Requires openpyxl for the CLI.
The official basket key is (order_id, week), even across clients. Popularity
counts distinct customers; ties use popularity then item ID. Short lists count
missing slots as misses; fill means at least one recommendation.
"""
from collections import Counter, defaultdict
from datetime import date, datetime
import json
import sys


def _week(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


def fit_official(train):
    """Build the official graph; caller supplies only training rows."""
    history, baskets = defaultdict(set), defaultdict(set)
    for order, user, week, item in train:
        week = _week(week)
        if not isinstance(week, date) or week > date(2026, 8, 31) or not all((order, user, item)):
            raise ValueError('Invalid or non-training row')
        history[user].add(item)
        baskets[(order, week)].add(item)
    counts = Counter()
    for items in history.values():
        counts.update(items)
    pairs = defaultdict(Counter)
    for items in baskets.values():
        for item in items:
            for other in items - {item}:
                pairs[item][other] += 1
    return {'history': history, 'counts': counts, 'pairs': pairs}


def recommend_official(model, history, method):
    if method == 'popularity':
        scores = model['counts']
    elif method == 'copurchase':
        scores = Counter()
        for item in history:
            scores.update(model['pairs'].get(item, {}))
    else:
        raise ValueError('Unknown method')
    return sorted((i for i in scores if i not in history),
                  key=lambda i: (-scores[i], -model['counts'][i], i))[:3]


def analyze(sales, groups):
    train, future = [], defaultdict(set)
    row_counts = Counter(train=0, gap=0, test=0)
    for order, user, week, item in sales:
        week = _week(week)
        if not isinstance(week, date) or not user or not order or not item:
            raise ValueError('Invalid sale: missing identifier or invalid week')
        if week <= date(2026, 8, 31):
            row_counts['train'] += 1
            train.append((order, user, week, item))
        else:
            row_counts['test'] += 1
            future[user].add(item)
    model = fit_official(train)
    history, counts, pairs = model['history'], model['counts'], model['pairs']
    a = {u for u in history if future[u] - history[u]}
    b = {u for u in a if len(history[u]) == 1}
    if any(groups.get(u) not in ('B2B', 'B2C') for u in a):
        raise ValueError('Missing or unknown client_group in cohort')
    cohorts = {'A': a, 'B': b}
    results = {'rows': dict(row_counts), 'cohorts': {}, 'metrics': {}}
    for name, users in cohorts.items():
        results['cohorts'][name] = {'size': len(users), **{
            group: sum(groups[u] == group for u in users) for group in ('B2B', 'B2C')}}
    for method in ('popularity', 'copurchase'):
        metrics = results['metrics'][method] = {}
        for name, cohort in cohorts.items():
            for group in ('all', 'B2B', 'B2C'):
                users = {u for u in cohort if group == 'all' or groups[u] == group}
                hits = fill = short = ties = 0
                for user in users:
                    seen = history[user]
                    if method == 'popularity':
                        scores = counts
                    else:
                        scores = Counter()
                        for item in seen:
                            scores.update(pairs.get(item, {}))
                    ranked = sorted((i for i in scores if i not in seen),
                                    key=lambda i: (-scores[i], -counts[i], i))
                    chosen = recommend_official(model, seen, method)
                    hits += len(set(chosen) & (future[user] - seen))
                    fill += bool(chosen)
                    short += len(chosen) < 3
                    ties += len(ranked) > 3 and scores[ranked[2]] == scores[ranked[3]]
                key = name if group == 'all' else name + '_' + group
                n = len(users)
                metrics[key] = {'n': n, 'precision_at_3': hits / (3 * n) if n else None,
                                'fill_rate': fill / n if n else None,
                                'short_lists': short, 'boundary_ties': ties}
    return results


def main():
    import openpyxl
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 -m evaluation.protocol_audit PATH_TO_XLSX')
    workbook = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        groups = {r[0]: r[1] for r in workbook['Клиенты'].iter_rows(min_row=2, values_only=True)}
        rows = ((r[0], r[1], r[2], r[3]) for r in
                workbook['Продажи'].iter_rows(min_row=2, values_only=True))
        print(json.dumps(analyze(rows, groups), ensure_ascii=False, indent=2))
    finally:
        workbook.close()


if __name__ == '__main__':
    main()