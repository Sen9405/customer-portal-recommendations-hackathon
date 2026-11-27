"""Exploratory aggregate candidate experiment, NOT the production fail-closed Hybrid.

Run: python -B -m recommendation.hybrid_evaluation DATA.xlsx FEATURES.json
The current catalog and its undated, unverified identity candidates leak future
information. It is evaluated separately from the strict sales-only baseline.
The synthetic fixture uses invented, trusted-by-construction edges only. No real
SKU evidence is reviewed; no compatibility or availability is established.
No individual identifiers or recommendations are printed or written.
"""
from collections import Counter, defaultdict
from functools import lru_cache
import json
import sys

from recommendation.catalog_similarity import build_index
from recommendation.segmented_history import day, split_rows
from recommendation.sequential_diagnostics import lexical_variant
from recommendation.temporal_selection import fit, ranking


def catalog_edges(snapshot):
    index = build_index(snapshot)
    edges = {}
    for source, (category, _vendor) in index['items'].items():
        if not category:
            continue
        # Same category is similarity, not compatibility. Ranking counts matching
        # history categories and breaks ties by ID, not by vendor or popularity.
        edges[source] = sorted(index['categories'][category] - {source}, key=str)
    return edges


def evaluate_window(rows, names, cutoff, start, end, content, *, content_label):
    if content_label not in {'none', 'synthetic', 'future_catalog'}:
        raise ValueError('content provenance must be explicit')
    if content_label == 'none' and content:
        raise ValueError('strict sales-only run cannot contain content edges')
    train, test = split_rows(rows, cutoff, start, end)
    model = fit(train)
    train_items = set(model['pop'])
    train_names = {}
    for row, name in zip(rows, names):
        if day(row[2]) <= day(cutoff) and name:
            train_names.setdefault(row[3], name)

    @lru_cache(maxsize=None)
    def similar(a, b):
        return lexical_variant(train_names.get(a), train_names.get(b))

    future = defaultdict(set)
    for _, user, _, item in test:
        future[user].add(item)
    cohort = {u for u, seen in model['history'].items() if future[u] - seen}
    output = {}
    variants = ('popularity', 'history', 'history_lexical', 'content_only',
                'hybrid', 'hybrid_lexical')
    for cohort_name, users in [('A', cohort), ('B', {u for u in cohort if len(model['history'][u]) == 1})]:
        sums = {v: Counter(n=0, hits=0, cold_hits=0, targets=0, cold_targets=0,
                           cold_reachable=0, cold_recommendations=0, users_with_cold=0,
                           filled=0, users_lt3=0, slots=0, slots_from_history=0,
                           slots_from_content=0, slots_from_popularity=0) for v in variants}
        for user in users:
            seen = model['history'][user]
            targets = future[user] - seen
            content_scores = Counter()
            # Preserve content ranking: synthetic sets have deterministic ID ties.
            for src in sorted(seen, key=str):
                for dst in content.get(src, ()):
                    content_scores[dst] += 1
            content_rank = ranking(content_scores)
            for variant in variants:
                scores = Counter()
                if variant in ('history', 'history_lexical', 'hybrid', 'hybrid_lexical'):
                    for src in model['latest'][user]:
                        for dst, weight in model['seq'].get(src, {}).items():
                            if not (variant.endswith('_lexical') and similar(src, dst)):
                                scores[dst] += weight
                channels = []
                if variant in ('content_only', 'hybrid', 'hybrid_lexical'):
                    channels.append(('content', content_rank))
                if variant in ('history', 'history_lexical', 'hybrid', 'hybrid_lexical'):
                    channels.append(('history', ranking(scores)))
                if variant != 'content_only':
                    channels.append(('popularity', model['pop']))
                reachable = set().union(*(set(items) for _, items in channels)) - seen
                chosen, source_counts, selected = [], Counter(), set(seen)
                for channel, items in channels:
                    for item in items:
                        if item not in selected and len(chosen) < 3:
                            selected.add(item)
                            chosen.append(item)
                            source_counts[channel] += 1
                hits = set(chosen) & targets
                cold = targets - train_items
                c = sums[variant]
                c['n'] += 1
                c['hits'] += len(hits)
                c['cold_hits'] += len(hits & cold)
                c['targets'] += len(targets)
                c['cold_targets'] += len(cold)
                c['cold_reachable'] += len(cold & reachable)
                c['cold_recommendations'] += len(set(chosen) - train_items)
                c['users_with_cold'] += bool(set(chosen) - train_items)
                c['filled'] += bool(chosen)
                c['users_lt3'] += len(chosen) < 3
                c['slots'] += len(chosen)
                for source in ('history', 'content', 'popularity'):
                    c['slots_from_' + source] += source_counts[source]
        output[cohort_name] = {}
        for variant, c in sums.items():
            n = c['n']
            metrics = dict(c)
            metrics['precision_at_3'] = c['hits'] / (3*n) if n else None
            metrics['client_fill_rate'] = c['filled'] / n if n else None
            metrics['slot_fill_rate'] = c['slots'] / (3*n) if n else None
            metrics['cold_target_reach_rate'] = c['cold_reachable'] / c['cold_targets'] if c['cold_targets'] else None
            output[cohort_name][variant] = metrics
    return output


def synthetic_demo():
    rows = [('1','u','2026-01-01','a'), ('2','v','2026-01-01','a'),
            ('3','v','2026-01-08','b'), ('4','u','2026-02-01','cold')]
    return evaluate_window(rows, ['Product version 1', 'Product version 1',
                            'Product version 2', 'future'], '2026-01-08',
                           '2026-02-01', None, {'a': {'cold'}}, content_label='synthetic')


def main():
    if len(sys.argv) != 3:
        raise SystemExit('Usage: python -m recommendation.hybrid_evaluation DATA.xlsx FEATURES.json')
    import openpyxl
    with open(sys.argv[2], encoding='utf-8') as f:
        edges = catalog_edges(json.load(f))
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        records = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
        rows, names = [tuple(r[:4]) for r in records], [r[5] for r in records]
        windows = [('early_validation_exploratory', '2026-05-25', '2026-06-01', '2026-08-31'),
                   ('official_previously_observed_exploratory', '2026-08-31', '2026-09-07', None)]
        output = {'limitations': 'A/B outcome-conditioned; B is one-item history, not zero-history. Fixed content-first then sequential then popularity; lexical ablation removes similar historical edges only. Train-only names, missing names treated unknown. Content uses current feed and undated unverified candidate mappings including post-cutoff items; not a point-in-time backtest or compatibility. No reviewed real source edges, no historical availability, no production safety checks. Official and early validation already observed; neither is independent. Offline fill is client-level. Content cold reach before top-3 may be optimistic.',
                  'synthetic_trusted_edges_only': synthetic_demo()}
        for label, cutoff, start, end in windows:
            output[label] = {'strict_sales_only_no_catalog': evaluate_window(rows, names, cutoff, start, end, {}, content_label='none'),
                             'retrospective_current_catalog_future_information': evaluate_window(rows, names, cutoff, start, end, edges, content_label='future_catalog')}
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        wb.close()


if __name__ == '__main__':
    main()
