"""Experimental train-only transaction graph; run: python -B -m recommendation.experimental_graph data/dataset.xlsx.

Predeclared variants: raw co-order count (protocol approximation), count with
popularity fallback, cosine-normalized co-order count with popularity fallback,
and train row-count popularity. Graph edges count distinct order_id associations;
order_id is assumed globally identifying an order; unlike the official protocol,
this experimental graph deliberately does not distinguish weeks. Only train rows are grouped: test/gap order IDs are
never joined. Rows do not establish causality, compatibility or availability.
No future catalog or client attributes other than cohort group are used.
Offline client coverage is not visit fill rate. Never export individual output.
"""
from collections import Counter, defaultdict
from datetime import date, datetime
import json
import math
import sys

VARIANTS = ('popularity', 'count', 'count_fallback', 'cosine_fallback')


def fit(train):
    """Fit solely on train rows. Reject rows outside the training window."""
    orders, counts, history = defaultdict(set), Counter(), defaultdict(set)
    for order, user, week, item in train:
        if isinstance(week, datetime):
            week = week.date()
        elif isinstance(week, str):
            week = date.fromisoformat(week)
        if not isinstance(week, date) or week > date(2026, 8, 31) or not all((order, user, item)):
            raise ValueError('Invalid or non-training row')
        orders[order].add(item)
        counts[item] += 1
        history[user].add(item)
    pairs, degree = defaultdict(Counter), Counter()
    for items in orders.values():
        for item in items:
            degree[item] += 1
            for other in items - {item}:
                pairs[item][other] += 1
    return {'history': history, 'pairs': pairs, 'degree': degree, 'counts': counts,
            'ranking': sorted(counts, key=lambda i: (-counts[i], str(i)))}


def recommend(model, history, variant):
    if variant not in VARIANTS:
        raise ValueError('Unknown variant')
    history = set(history)
    if variant == 'popularity':
        return [i for i in model['ranking'] if i not in history][:3]
    scores = Counter()
    # Sort history to ensure deterministic floating-point accumulation.
    for item in sorted(history, key=str):
        for other, count in model['pairs'].get(item, {}).items():
            if other not in history:
                score = count
                if variant == 'cosine_fallback':
                    score /= math.sqrt(model['degree'][item] * model['degree'][other])
                scores[other] += score
    ranked = sorted(scores, key=lambda i: (-scores[i], str(i)))[:3]
    if variant.endswith('_fallback'):
        selected = set(ranked) | history
        for item in model['ranking']:
            if len(ranked) == 3:
                break
            if item not in selected:
                ranked.append(item)
                selected.add(item)
    return ranked


def backtest(sales, groups):
    train, future = [], defaultdict(set)
    rows = Counter(train=0, gap=0, test=0)
    for order, user, week, item in sales:
        if isinstance(week, datetime):
            week = week.date()
        elif isinstance(week, str):
            week = date.fromisoformat(week)
        if not isinstance(week, date) or not all((order, user, item)):
            raise ValueError('Invalid sale')
        if week <= date(2026, 8, 31):
            rows['train'] += 1
            train.append((order, user, week, item))
        elif week >= date(2026, 9, 7):
            rows['test'] += 1
            future[user].add(item)
        else:
            rows['gap'] += 1
    model = fit(train)
    history = model['history']
    a = {u for u in history if future[u] - history[u]}
    b = {u for u in a if len(history[u]) == 1}
    if any(groups.get(u) not in ('B2B', 'B2C') for u in a):
        raise ValueError('Missing or unknown client_group in cohort')
    cohorts = {'A': a, 'B': b}
    results = {'rows': dict(rows), 'cohorts': {}, 'metrics': {}}
    for name, users in cohorts.items():
        results['cohorts'][name] = {'size': len(users), **{g: sum(groups[u] == g for u in users) for g in ('B2B', 'B2C')}}
    for variant in VARIANTS:
        metrics = results['metrics'][variant] = {}
        for name, cohort in cohorts.items():
            for group in ('all', 'B2B', 'B2C'):
                users = [u for u in cohort if group == 'all' or groups[u] == group]
                hits = full = partial = empty = slots = 0
                for user in users:
                    chosen = recommend(model, history[user], variant)
                    hits += len(set(chosen) & (future[user] - history[user]))
                    full += len(chosen) == 3
                    partial += 0 < len(chosen) < 3
                    empty += not chosen
                    slots += len(chosen)
                n = len(users)
                metrics[name if group == 'all' else name + '_' + group] = {
                    'n': n, 'hits': hits, 'precision_at_3': hits / (3*n) if n else None,
                    'fill_rate': (full + partial) / n if n else None,
                    'full_coverage': full / n if n else None,
                    'partial_coverage': partial / n if n else None,
                    'empty_coverage': empty / n if n else None,
                    'slot_fill_rate': slots / (3*n) if n else None}
    return results


def main():
    import openpyxl
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python -m recommendation.experimental_graph PATH_TO_XLSX')
    workbook = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        groups = {r[0]: r[1] for r in workbook['Клиенты'].iter_rows(min_row=2, values_only=True)}
        sales = ((r[0], r[1], r[2], r[3]) for r in workbook['Продажи'].iter_rows(min_row=2, values_only=True))
        print(json.dumps(backtest(sales, groups), ensure_ascii=False, indent=2))
    finally:
        workbook.close()


if __name__ == '__main__':
    main()
