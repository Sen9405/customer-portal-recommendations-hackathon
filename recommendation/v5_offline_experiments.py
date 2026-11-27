"""Aggregate-only sales backtest: fixed normalization and diversity ablations.

Run: python3 -B -m recommendation.v5_offline_experiments data/dataset_v5.xlsx
No catalog, customer attributes, network, or model. The final window is known,
not an independent holdout. No parameters or production rank are selected here.
"""
import argparse
import json
from collections import Counter, defaultdict
from functools import lru_cache

from recommendation.profile_pipeline import ProfilePipeline, _day
from recommendation.profile_v4_diagnostics import relation
from recommendation.profile_v4_evaluation import CUTOFF, official_models, official_rank, profile_rank
from recommendation.segmented_history import split_rows
from recommendation.sequential_diagnostics import WINDOWS, transitions
from recommendation.temporal_selection import fit, ranking

FINAL = (CUTOFF.isoformat(), '2026-09-07', None)
SHRINK = 5
DIVERSITY_HEAD = 10


def sequential_rank(graph, latest, seen, fallback, *, normalized=False, own=None,
                    edge_ok=None, limit=3):
    """Add raw counts or c/(total outgoing + 5), then append the same fallback.

    Outgoing mass includes edges to seen items (not eligible for recommendation).
    For ablations remove rejected edges from numerator AND denominator.
    """
    scores = defaultdict(float)
    for src in sorted(latest, key=str):
        counts = graph.get(src, {})
        if own is not None:
            counts = Counter(counts) - Counter(own.get(src, {}))
        if edge_ok is not None:
            counts = {dst: n for dst, n in counts.items() if edge_ok(src, dst)}
        scale = sum(counts.values()) + SHRINK if normalized else 1
        for dst, count in counts.items():
            if dst not in seen and count > 0:
                scores[dst] += count / scale if normalized else count
    ordered = ranking(scores) + [x for x in fallback if x not in seen and x not in scores]
    return ordered[:limit]


def diversify(ranked, similar, *, head=DIVERSITY_HEAD):
    """Stable lexical diversification in the first `head` slots; backfill if needed."""
    selected, deferred = [], []
    for item in ranked[:head]:
        if any(similar(item, prior) for prior in selected):
            deferred.append(item)
        else:
            selected.append(item)
            if len(selected) == 3:
                return selected
    return (selected + deferred + ranked[head:])[:3]


def evaluate_window(rows, window):
    cutoff, start, end = window
    if _day(cutoff) >= _day(start) or (end is not None and _day(start) > _day(end)):
        raise ValueError('Invalid chronological window')
    train, test = split_rows([r[:4] for r in rows], cutoff, start, end)
    model = fit(train)
    hist = model['history']
    profile = ProfilePipeline(train, cutoff)
    pop, co = official_models(train, hist)
    fallback = ranking(pop)
    future = defaultdict(set)
    for _, user, _, item in test:
        future[user].add(item)
    targets = {u: items - hist[u] for u, items in future.items() if u in hist and items - hist[u]}
    names = {}
    for _, _, week, item, name in rows:
        if name and _day(week) <= _day(cutoff):
            names.setdefault(item, name)

    @lru_cache(maxsize=None)
    def rel(a, b):
        # Canonical direction for a stable, symmetric proxy (SequenceMatcher can be asymmetric).
        return relation(names.get(a), names.get(b))

    def lexical(a, b):
        return rel(*sorted((a, b), key=str))

    def similar(a, b):
        return lexical(a, b) == 'similar'

    def edge_ok(a, b):
        return not similar(a, b)

    labels = ('popularity', 'copurchase', 'profile', 'profile_2plus1_sales_only',
              'sequential_raw', 'sequential_normalized', 'raw_diverse', 'normalized_diverse',
              'raw_no_similar_edges', 'normalized_no_similar_edges', 'normalized_loo_graph')
    buckets = {c: {m: Counter() for m in labels} for c in ('A', 'B')}
    for user, actual in targets.items():
        latest, seen = model['latest'][user], hist[user]
        rank_args = (model['seq'], latest, seen, fallback)
        raw = sequential_rank(*rank_args, limit=DIVERSITY_HEAD)
        norm = sequential_rank(*rank_args, normalized=True, limit=DIVERSITY_HEAD)
        own = transitions(sorted(profile.user_weeks[user].items()))
        lists = dict(zip(labels, (
            official_rank(user, hist, pop), official_rank(user, hist, pop, co),
            profile_rank(profile, user, 'full'),
            [c['item_id'] for c in profile.blended_candidates(user)],
            raw[:3], norm[:3], diversify(raw, similar), diversify(norm, similar),
            sequential_rank(*rank_args, edge_ok=edge_ok),
            sequential_rank(*rank_args, normalized=True, edge_ok=edge_ok),
            sequential_rank(*rank_args, normalized=True, own=own),
        ), strict=True))
        for cohort in ('A', 'B') if len(seen) == 1 else ('A',):
            for method, chosen in lists.items():
                hit = set(chosen) & actual
                comparator = lists['sequential_normalized'] if method.startswith('normalized_') else raw[:3]
                ref_hit = set(comparator) & actual
                pairs = [lexical(a, b) for i, a in enumerate(chosen) for b in chosen[i+1:]]
                stat = buckets[cohort][method]
                stat.update({'hits': len(hit), 'nonempty': int(bool(chosen)), 'full_triples': int(len(chosen) == 3),
                             'similar_list_pairs': pairs.count('similar'), 'comparable_list_pairs': len(pairs) - pairs.count('unknown'),
                             'changed_lists_vs_reference': int(chosen != comparator),
                             'lost_hits_vs_reference': len(ref_hit - hit), 'gained_hits_vs_reference': len(hit - ref_hit),
                             'hits_similar_to_history': sum(any(similar(i, j) for j in seen) for i in hit)})
    result = {}
    for cohort, methods in buckets.items():
        users = [u for u in targets if cohort == 'A' or len(hist[u]) == 1]
        n = len(users)
        result[cohort] = {'clients': n, 'denominator': 3 * n,
                          'targets': sum(len(targets[u]) for u in users),
                          'cold_targets': sum(len(targets[u] - pop.keys()) for u in users),
                          'methods': {m: {**s, 'precision_at_3': s['hits'] / (3*n) if n else None,
                                           'fill_rate': s['nonempty'] / n if n else None}
                                      for m, s in methods.items()}}
    return {'window': window, 'train_rows': len(train), 'label_rows': len(test),
            'last_label_week': max((_day(r[2]).isoformat() for r in test), default=None),
            'cohorts': result}


def main():
    import openpyxl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xlsx')
    args = parser.parse_args()
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    try:
        rows = [(*r[:4], r[5]) for r in wb['Продажи'].iter_rows(min_row=2, values_only=True)]
    finally:
        wb.close()
    print(json.dumps({
        'limitations': 'Exploratory overlapping windows; final window already observed. A and B depend on future novel labels; B is nested. Train names are lexical proxies, not verified product families. Historical availability unknown.',
        'fallback': 'Distinct-client train popularity, identical for sequential variants; official co-purchase is not backfilled.',
        'settings': {'normalization_shrink': SHRINK, 'diversity_head': DIVERSITY_HEAD,
                     'lexical_similarity_threshold': 0.85,
                     'reference': 'normalized for normalized_* ablations, raw otherwise'},
        'windows': [evaluate_window(rows, w) for w in (*WINDOWS, FINAL)],
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Offline evaluation failed; check the local workbook and Python dependencies.') from None
