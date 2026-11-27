"""Predeclared exploratory backtest, run locally: python -B -m recommendation.segmented_history data/dataset.xlsx.

Methods (fixed before results): global popularity; B2B/B2C popularity; B2B
segment or industry popularity, min 20 distinct train clients per value; and
within-group co-order count graph with group popularity fallback. B2C never uses
profile fields. Unavailable/undersupported profile -> group -> global ranking.
Group graph uses (client, week, order) to avoid joining reused IDs; no causal or
compatibility claim. All items and edges are from train rows only; ties by ID.

Validation: train through 2026-05-25, test 2026-06-01..2026-08-31.
Official: train through 2026-08-31, test from 2026-09-07. No tuning on test;
all variants reported, not selected. Profiles and group labels are undated:
profile results are retrospective and potentially leaky, NOT point-in-time proof.
Group labels also lack snapshots. Output is aggregate only; never export rows.
"""
from collections import Counter, defaultdict
from datetime import date, datetime
import json
import sys

VARIANTS = ('global_pop', 'group_pop', 'segment_pop', 'industry_pop', 'group_graph')
MIN_SUPPORT = 20


def day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def split_rows(rows, train_end, test_start, test_end=None):
    train_end, test_start = day(train_end), day(test_start)
    test_end = day(test_end) if test_end else None
    if train_end >= test_start or (test_end and test_end < test_start):
        raise ValueError('Invalid split')
    train, test = [], []
    for row in rows:
        d = day(row[2])
        if not all((row[0], row[1], row[3])):
            raise ValueError('Missing sale identifier')
        if d <= train_end:
            train.append(row)
        elif d >= test_start and (test_end is None or d <= test_end):
            test.append(row)
    return train, test


def valid(value):
    if value is None:
        return None
    value = str(value).strip()
    return value if value and value.casefold() not in {'nan', 'none', 'null', 'unknown', 'неизвестно', 'не указан', 'не указано', '-'} else None


def ranking(counts):
    return sorted(counts, key=lambda item: (-counts[item], str(item)))


def fit(train, profiles, min_support=MIN_SUPPORT):
    if min_support < 1:
        raise ValueError('min_support must be positive')
    history, group_counts = defaultdict(set), defaultdict(Counter)
    profile_counts, supporters = defaultdict(Counter), defaultdict(set)
    all_counts, orders = Counter(), defaultdict(set)
    for order, user, week, item in train:
        group, segment, industry = profiles.get(user, (None, None, None))
        history[user].add(item)
        all_counts[item] += 1
        if group not in ('B2B', 'B2C'):
            continue
        group_counts[group][item] += 1
        orders[(group, user, day(week), order)].add(item)
        if group == 'B2B':
            for field, value in (('segment', segment), ('industry', industry)):
                value = valid(value)
                if value is not None:
                    key = (field, value)
                    profile_counts[key][item] += 1
                    supporters[key].add(user)
    graphs = defaultdict(lambda: defaultdict(Counter))
    for (group, _, _, _), items in orders.items():
        for item in items:
            for other in items - {item}:
                graphs[group][item][other] += 1
    return {'history': history, 'global': ranking(all_counts),
            'groups': {k: ranking(v) for k, v in group_counts.items()},
            'profiles': {k: ranking(v) for k, v in profile_counts.items() if len(supporters[k]) >= min_support},
            'graphs': graphs}


def route(model, profile, variant):
    group, segment, industry = profile
    if variant in ('segment_pop', 'industry_pop') and group == 'B2B':
        field = variant.removesuffix('_pop')
        key = (field, valid(segment if field == 'segment' else industry))
        if key[1] is not None and key in model['profiles']:
            return field
    return 'group' if group in model['groups'] else 'global'


def recommend(model, user, profile, variant):
    if variant not in VARIANTS:
        raise ValueError('Unknown variant')
    group, segment, industry = profile
    history = model['history'].get(user, set())
    sources = []
    if variant == 'group_graph':
        scores = Counter()
        for item in history:
            scores.update(model['graphs'].get(group, {}).get(item, {}))
        sources.append(ranking(scores))
    if variant in ('segment_pop', 'industry_pop') and route(model, profile, variant) in ('segment', 'industry'):
        field = variant.removesuffix('_pop')
        sources.append(model['profiles'][(field, valid(segment if field == 'segment' else industry))])
    if variant != 'global_pop':
        sources.append(model['groups'].get(group, []))
    sources.append(model['global'])
    result, selected = [], set(history)
    for source in sources:
        for item in source:
            if item not in selected:
                selected.add(item)
                result.append(item)
                if len(result) == 3:
                    return result
    return result


def backtest(rows, profiles, train_end, test_start, test_end=None):
    train, test = split_rows(rows, train_end, test_start, test_end)
    model = fit(train, profiles)
    future = defaultdict(set)
    for _, user, _, item in test:
        future[user].add(item)
    cohort_a = {u for u, history in model['history'].items() if future[u] - history}
    cohort_b = {u for u in cohort_a if len(model['history'][u]) == 1}
    if any(profiles.get(u, (None,))[0] not in ('B2B', 'B2C') for u in cohort_a):
        raise ValueError('Unknown cohort group')
    output = {'rows': {'train': len(train), 'test': len(test)}, 'metrics': {}}
    for variant in VARIANTS:
        metrics = output['metrics'][variant] = {}
        for name, cohort in (('A', cohort_a), ('B', cohort_b)):
            for group in ('all', 'B2B', 'B2C'):
                users = [u for u in cohort if group == 'all' or profiles[u][0] == group]
                hits = slots = filled = full = 0
                routes = Counter()
                for user in users:
                    chosen = recommend(model, user, profiles[user], variant)
                    hits += len(set(chosen) & (future[user] - model['history'][user]))
                    slots += len(chosen)
                    filled += bool(chosen)
                    full += len(chosen) == 3
                    if variant in ('segment_pop', 'industry_pop'):
                        routes[route(model, profiles[user], variant)] += 1
                n = len(users)
                metrics[name if group == 'all' else name + '_' + group] = {
                    'n': n, 'hits': hits, 'precision_at_3': hits / (3*n) if n else None,
                    'fill_rate': filled / n if n else None, 'full_coverage': full / n if n else None,
                    'slot_fill_rate': slots / (3*n) if n else None, 'profile_routes': dict(routes)}
    return output


def main():
    import openpyxl
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python -m recommendation.segmented_history PATH_TO_XLSX')
    workbook = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        profiles = {r[0]: (r[1], r[4], r[3]) for r in workbook['Клиенты'].iter_rows(min_row=2, values_only=True)}
        rows = [tuple(r[:4]) for r in workbook['Продажи'].iter_rows(min_row=2, values_only=True)]
        output = {'limitations': 'Undated client attributes and groups: retrospective, potentially leaky; no catalog; no test tuning. Offline client fill is not visit fill.',
                  'validation': backtest(rows, profiles, '2026-05-25', '2026-06-01', '2026-08-31'),
                  'official': backtest(rows, profiles, '2026-08-31', '2026-09-07')}
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        workbook.close()


if __name__ == '__main__':
    main()
