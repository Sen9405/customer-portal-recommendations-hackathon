"""Local aggregate-only exploratory audit. Run: python -m recommendation.sequential_diagnostics DATA.xlsx.

Windows are fixed before execution and end before the official test. Same-week
rows stay together. SKU-name similarity is a lexical proxy, not a verified
product family; names must be provided only from training rows. No output files.
"""
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import json
import re
import sys

from recommendation.segmented_history import day, split_rows
from recommendation.temporal_selection import fit, ranking, recommend

WINDOWS = (
    ('2026-03-30', '2026-04-06', '2026-05-25'),
    ('2026-04-27', '2026-05-04', '2026-06-29'),
    ('2026-05-25', '2026-06-01', '2026-08-31'),
)


def structures(rows):
    weeks, orders = defaultdict(set), defaultdict(set)
    for order, user, week, item in rows:
        week = day(week)
        weeks[user, week].add(item)
        orders[order].add((user, week))
    by_user = defaultdict(list)
    for (user, week), basket in weeks.items():
        by_user[user].append((week, basket))
    for baskets in by_user.values():
        baskets.sort(key=lambda x: x[0])
    return by_user, orders


def transitions(baskets):
    result = defaultdict(Counter)
    for (_, prev), (_, curr) in zip(baskets, baskets[1:]):
        for item in prev:
            result[item].update(curr - {item})
    return result


def lexical_variant(a, b):
    def norm(s):
        return re.sub(r'\s+', ' ', str(s or '').casefold()).strip()
    a, b = norm(a), norm(b)
    return bool(len(a) >= 8 and len(b) >= 8 and SequenceMatcher(None, a, b).ratio() >= .85)


def score(model, user, seq, future):
    seen = model['history'][user]
    counts = Counter()
    for item in model['latest'][user]:
        counts.update(seq.get(item, {}))
    chosen, sources, selected = [], [], set(seen)
    for source, candidates in (('graph', ranking(counts)), ('fallback', model['pop'])):
        for item in candidates:
            if item not in selected and len(chosen) < 3:
                chosen.append(item)
                sources.append(source)
                selected.add(item)
    assert chosen == recommend({**model, 'seq': seq}, user, 'sequential')
    return chosen, sources, set(future) - seen


def audit_window(rows, cutoff, start, end, names=None):
    train, test = split_rows(rows, cutoff, start, end)
    if any(day(r[2]) > day(cutoff) for r in train) or any(not day(start) <= day(r[2]) <= day(end) for r in test) or day(cutoff) >= day(start):
        raise ValueError('Non-chronological window')
    model = fit(train)
    train_weeks, train_orders = structures(train)
    test_weeks, test_orders = structures(test)
    future = defaultdict(set)
    for _, user, _, item in test:
        future[user].add(item)
    cohort = {u for u in model['history'] if future[u] - model['history'][u]}
    all_weeks, all_orders = structures(train + test)
    repeated_baskets = sum(len(bs) - len({frozenset(b) for _, b in bs}) for bs in all_weeks.values())
    week_orders = defaultdict(set)
    for order, user, week, _ in train + test:
        week_orders[user, day(week)].add(order)
    stats = Counter()
    nonvariant_seq = None
    if names is not None:
        nonvariant_seq = {src: Counter({dst: weight for dst, weight in dests.items()
                          if not lexical_variant(names.get(src), names.get(dst))})
                          for src, dests in model['seq'].items()}
    for user in cohort:
        chosen, sources, targets = score(model, user, model['seq'], future[user])
        own = transitions(train_weeks[user])
        # Subtract this customer's contribution to the transition graph only.
        # Preserve their history, last basket and global popularity by design.
        reduced = {}
        for item in model['latest'][user]:
            reduced[item] = model['seq'].get(item, Counter()) - own.get(item, Counter())
        loo, _, _ = score(model, user, reduced, future[user])
        stats['hits'] += len(set(chosen) & targets)
        stats['loo_hits'] += len(set(loo) & targets)
        stats['loo_changed_lists'] += chosen != loo
        stats['pop_hits'] += len(set(recommend(model, user, 'popularity')) & targets)
        stats['graph_slots'] += sources.count('graph')
        stats['fallback_slots'] += sources.count('fallback')
        stats['graph_hits'] += sum(item in targets for item, source in zip(chosen, sources) if source == 'graph')
        stats['fallback_hits'] += sum(item in targets for item, source in zip(chosen, sources) if source == 'fallback')
        stats['users_with_fallback'] += 'fallback' in sources
        stats['users_with_own_supported_candidate'] += any(own.get(src, {}).get(dest, 0) for src in model['latest'][user] for dest in chosen)
        stats['hits_with_own_support'] += sum(any(own.get(src, {}).get(dest, 0) for src in model['latest'][user]) for dest in set(chosen) & targets)
        stats['future_pairs_with_prior_own_transition'] += sum(any(own.get(src, {}).get(dest, 0) for src in model['latest'][user]) for dest in targets)
        if names is not None:
            stats['hits_lexical_variant_of_last'] += sum(any(lexical_variant(names.get(src), names.get(dest)) for src in model['latest'][user]) for dest in set(chosen) & targets)
            ablated, _, _ = score(model, user, nonvariant_seq, future[user])
            stats['lexical_ablation_hits'] += len(set(ablated) & targets)
            stats['lexical_ablation_changed_lists'] += ablated != chosen
    edges = [(a, b, n) for a, targets in model['co'].items() for b, n in targets.items() if str(a) < str(b)]
    if names is not None:
        stats['coorder_undirected_edges'] = len(edges)
        stats['coorder_lexical_variant_edges'] = sum(lexical_variant(names.get(a), names.get(b)) for a, b, _ in edges)
        stats['coorder_edge_weight'] = sum(n for _, _, n in edges)
        stats['coorder_lexical_variant_edge_weight'] = sum(n for a, b, n in edges if lexical_variant(names.get(a), names.get(b)))
    n = len(cohort)
    return {'window': [cutoff, start, end], 'rows': {'train': len(train), 'test': len(test)},
            'cohort_A': n, 'cohort_B': sum(len(model['history'][u]) == 1 for u in cohort),
            'order_ids_multiple_weeks': sum(len({week for _, week in values}) > 1 for values in all_orders.values()),
            'order_ids_multiple_clients': sum(len({user for user, _ in values}) > 1 for values in all_orders.values()),
            'order_ids_across_split': len(train_orders.keys() & test_orders.keys()),
            'repeated_identical_client_week_baskets_after_first': repeated_baskets,
            'client_weeks_multiple_orders': sum(len(orders) > 1 for orders in week_orders.values()),
            'client_weeks': sum(map(len, all_weeks.values())),
            'precision_at_3': stats['hits'] / (3*n) if n else None,
            'loo_graph_precision_at_3': stats['loo_hits'] / (3*n) if n else None,
            'popularity_precision_at_3': stats['pop_hits'] / (3*n) if n else None,
            'lexical_ablation_precision_at_3': stats['lexical_ablation_hits'] / (3*n) if n and names is not None else None,
            'counts': dict(stats)}


def run(rows, metadata):
    results = []
    for cutoff, start, end in WINDOWS:
        # Derive descriptions strictly from training rows, never from later rows.
        names = {}
        for row, name in zip(rows, metadata):
            if day(row[2]) <= day(cutoff) and row[3] not in names and name:
                names[row[3]] = name
        results.append(audit_window(rows, cutoff, start, end, names))
    return {'limitations': 'Exploratory overlapping rolling windows, not independent, not a locked test; A/B require novel future purchases. LOO removes only evaluated client transition edges, not popularity or history. Order reuse is not proof of leakage. Name similarity >=0.85 is a crude train-only lexical proxy, not verified SKU variants or compatibility; unavailable names are nonmatches. No availability data.', 'windows': results}


def main():
    import openpyxl
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python -m recommendation.sequential_diagnostics DATA.xlsx')
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        records = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
        print(json.dumps(run([tuple(r[:4]) for r in records], [r[5] for r in records]), ensure_ascii=False, indent=2))
    finally:
        wb.close()


if __name__ == '__main__':
    main()
