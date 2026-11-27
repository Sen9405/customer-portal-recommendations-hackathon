"""Aggregate lexical sensitivity, not verified SKU identity; no network or output files.

Run: python -B -m recommendation.variant_sensitivity data/dataset.xlsx
Both official and earlier validation are exploratory; official was previously seen.
Names are taken only from train rows; missing names are unknown, not dissimilar.
B is one-item history, not zero-history client or cold-item evaluation.
"""
from collections import Counter, defaultdict
from functools import lru_cache
import json
import sys

from recommendation.segmented_history import day, split_rows
from recommendation.sequential_diagnostics import lexical_variant
from recommendation.temporal_selection import fit, ranking, recommend


def audit(rows, names_by_row, cutoff, start, end=None):
    train, test = split_rows(rows, cutoff, start, end)
    names = {}
    for row, name in zip(rows, names_by_row):
        if day(row[2]) <= day(cutoff) and name and row[3] not in names:
            names[row[3]] = name
    model = fit(train)
    future = defaultdict(set)
    for _, user, _, item in test:
        future[user].add(item)
    cohort = {u for u, seen in model['history'].items() if future[u] - seen}

    @lru_cache(maxsize=None)
    def relation(a, b):
        if any(len(' '.join(str(names.get(x) or '').casefold().split())) < 8 for x in (a, b)):
            return 'unknown'
        return 'similar' if lexical_variant(names[a], names[b]) else 'other'

    def classify(item, seen):
        relations = [relation(item, other) for other in seen]
        if 'similar' in relations:
            return 'similar'
        return 'unknown' if 'unknown' in relations else 'other'

    ablated = {src: Counter({dst: n for dst, n in targets.items()
                              if relation(src, dst) != 'similar'})
               for src, targets in model['seq'].items()}
    output = {}
    for label, users in [('A', cohort), ('B', {u for u in cohort if len(model['history'][u]) == 1})]:
        counts = Counter()
        for user in users:
            seen = model['history'][user]
            targets = future[user] - seen
            base = recommend(model, user, 'sequential')
            graph_model = {**model, 'seq': ablated}
            graph = recommend(graph_model, user, 'sequential')
            # Stronger hypothetical policy applies proxy to all candidates, including fallback.
            scores = Counter()
            for src in model['latest'][user]:
                scores.update(model['seq'].get(src, {}))
            selected = set(seen)
            filtered = []
            for item in ranking(scores) + model['pop']:
                if item not in selected and classify(item, seen) != 'similar':
                    selected.add(item)
                    filtered.append(item)
                    if len(filtered) == 3:
                        break
            counts['users'] += 1
            counts['targets'] += len(targets)
            counts['target_users_similar'] += any(classify(t, seen) == 'similar' for t in targets)
            for t in targets:
                counts['targets_' + classify(t, seen)] += 1
            for label2, chosen in [('base', base), ('graph_ablation', graph), ('all_candidate_filter', filtered)]:
                hits = set(chosen) & targets
                counts[label2 + '_hits'] += len(hits)
                counts[label2 + '_slots'] += len(chosen)
                counts[label2 + '_filled_users'] += bool(chosen)
                counts[label2 + '_hit_users'] += bool(hits)
                for item in hits:
                    counts[label2 + '_hits_' + classify(item, seen)] += 1
                if label2 != 'base':
                    counts[label2 + '_changed_lists'] += chosen != base
                    counts[label2 + '_lost_hits'] += len((set(base) & targets) - hits)
                    counts[label2 + '_gained_hits'] += len(hits - (set(base) & targets))
        output[label] = dict(counts)
    return output


def main():
    import openpyxl
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python -m recommendation.variant_sensitivity DATA.xlsx')
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        records = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
        rows, names = [tuple(r[:4]) for r in records], [r[5] for r in records]
        print(json.dumps({'limitations': __doc__,
            'validation': audit(rows, names, '2026-05-25', '2026-06-01', '2026-08-31'),
            'official_exploratory': audit(rows, names, '2026-08-31', '2026-09-07')}, indent=2))
    finally:
        wb.close()


if __name__ == '__main__':
    main()
