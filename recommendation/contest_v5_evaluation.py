"""Aggregate v5 sales-only benchmark and separately the current-feed demo experiment.

Run: python3 -B -m recommendation.contest_v5_evaluation WORKBOOK BASELINE MAPPING INDEX
BASELINE must be trusted executable Python. No individual output is printed.
The feed experiment compares the actual carousel first three with a train
popularity control on the same untruncated per-client feasible offer pool.
Feed-only positions have unknown item identity: reported precision is a lower
bound, NOT a measured miss; a deliberately loose upper bound is also reported.
Mapping is unverified and derived from the whole workbook. This is not a fair
historical backtest. The sales-only ranking has no availability/feed filter;
product groups and segment labels are undated retrospective reporting data.
"""
import argparse
import contextlib
import io
import json
import runpy
import sys
from collections import Counter

from recommendation.contest_recommender import ContestRecommender
from recommendation.profile_v4_evaluation import CUTOFF, split_rows
from recommendation.profile_v5_comparison import compare


def demo_metrics(users, targets, hist, product_groups, recommendations):
    """Score known item IDs only; unresolved positions are neither hits nor misses."""
    n = len(users)
    hits = variants = nonempty = full = slots = evaluable = unresolved = 0
    upper_hits = 0
    for user in users:
        rec = recommendations[user][:3]
        nonempty += bool(rec)
        full += len(rec) == 3
        slots += len(rec)
        known = [c['item_id'] for c in rec if c.get('item_id') is not None]
        unknown = len(rec) - len(known)
        evaluable += len(known)
        unresolved += unknown
        hit = set(known) & targets[user]
        hits += len(hit)
        own = {product_groups[i] for i in hist[user]}
        variants += sum(product_groups[i] in own for i in hit)
        # Unknown slots could match at most the remaining distinct targets.
        upper_hits += len(hit) + min(unknown, len(targets[user] - hit))
    denominator = 3 * n
    return {'clients': n, 'denominator': denominator,
            'known_hits': hits, 'precision_at_3_lower_bound': hits / denominator if denominator else None,
            'precision_at_3_upper_bound_loose': upper_hits / denominator if denominator else None,
            'known_hit_variants': variants, 'known_hit_new': hits - variants,
            'known_hit_variant_share': variants / hits if hits else None,
            'nonempty': nonempty, 'fill_rate': nonempty / n if n else None,
            'full_triples': full, 'full_triples_rate': full / n if n else None,
            'returned_slots': slots, 'item_id_evaluable_slots': evaluable,
            'feed_only_unresolved_slots': unresolved,
            'item_id_coverage_returned_slots': evaluable / slots if slots else None,
            'item_id_coverage_all_protocol_slots': evaluable / denominator if denominator else None}


def popularity_control(pool, distinct_clients):
    """Rank feasible mapped offers by distinct train buyers; feed-only score zero."""
    def key(entry):
        offer, item = entry
        return (-distinct_clients.get(item, 0), str(item) if item is not None else '', offer)

    return [{'offer_id': offer, 'item_id': item} for offer, item in sorted(pool.items(), key=key)[:3]]


def _known_hits(recommendations, targets):
    return {c['item_id'] for c in recommendations if c['item_id'] is not None} & targets


def evaluate(rows, groups, original, recommender):
    historical = compare(rows, groups, original)
    _, hist, future = split_rows(rows)
    targets = {u: items - hist[u] for u, items in future.items() if u in hist and items - hist[u]}
    cohorts = {'A': list(targets), 'B': [u for u in targets if len(hist[u]) == 1]}
    pg = {r[3]: (r[6] if len(r) > 6 and r[6] else r[3]) for r in rows}
    recs, popularity, pool_counts, paired_counts = {}, {}, {}, {}
    # Use the actual default carousel and its feasible pool, once per A client.
    for u in cohorts['A']:
        segment = groups.get(u)
        result, pool = recommender.recommend_with_catalog_pool(u, segment if segment in ('B2B', 'B2C') else None)
        recs[u] = result['candidates'][:3]
        if (len(recs[u]) != min(3, len(pool)) or len({c['offer_id'] for c in recs[u]}) != len(recs[u])
                or any(c['offer_id'] not in pool or pool[c['offer_id']] != c['item_id'] for c in recs[u])):
            raise ValueError('Carousel first three inconsistent with feasible catalog pool')
        popularity[u] = popularity_control(pool, recommender.pipeline.distinct_clients)
        known = {item for item in pool.values() if item is not None}
        reachable = known & targets[u]
        pool_counts[u] = Counter(feasible_offer_pairs=len(pool), mapped_train_offer_pairs=len(known),
                                 feed_only_offer_pairs=len(pool) - len(known),
                                 clients_with_known_target=bool(reachable), known_target_pairs=len(reachable),
                                 target_pairs=len(targets[u]))
        actual_hits = _known_hits(recs[u], targets[u])
        pop_hits = _known_hits(popularity[u], targets[u])
        paired_counts[u] = Counter(shared=len(actual_hits & pop_hits),
                                   catalog_ranker_only=len(actual_hits - pop_hits),
                                   popularity_only=len(pop_hits - actual_hits))

    def totals(selected, counts, keys):
        agg = dict.fromkeys(keys, 0)
        for u in selected:
            for key, value in counts[u].items():
                agg[key] += value
        return agg

    demo = {}
    control = {}
    for cohort, users in cohorts.items():
        demo[cohort] = {}
        control[cohort] = {}
        for segment in ('all', 'B2B', 'B2C'):
            selected = [u for u in users if segment == 'all' or groups.get(u) == segment]
            demo[cohort][segment] = demo_metrics(selected, targets, hist, pg, recs)
            control[cohort][segment] = {
                'catalog_ranker': demo[cohort][segment],
                'popularity': demo_metrics(selected, targets, hist, pg, popularity),
                'pool': totals(selected, pool_counts, ('feasible_offer_pairs', 'mapped_train_offer_pairs',
                    'feed_only_offer_pairs', 'clients_with_known_target', 'known_target_pairs', 'target_pairs')),
                'paired_known_hits': totals(selected, paired_counts, ('shared', 'catalog_ranker_only', 'popularity_only')),
            }
    return {'sales_only_historical': historical,
            'current_feed_retrospective_experiment_NOT_historical_backtest': demo,
            'same_catalog_pool_popularity_control': control,
            'limitations': ['Current feed and mapping built from the entire workbook may leak future information; no fair comparison with historical sales-only metrics.',
                'Feed-only recommendations lack item_id; they are unresolved, not measured errors. Precision bounds are conditional on unverified mappings and the upper bound is loose.',
                'Mapping candidates are not verified SKU identity; current snapshot availability is not historical or live availability.',
                'Product groups use whole-workbook last-row-wins organizer rule and client segments are undated; variants/new classify known hits only.',
                'Sales-only profile and official baseline use different basket keys; test window already observed and profile tuned on v3.',
                'Fill is the share of clients with at least one recommendation, full triples reported separately.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('xlsx', 'baseline', 'mapping', 'index'):
        p.add_argument(name)
    args = p.parse_args()
    import openpyxl
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    try:
        rows = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
    finally:
        wb.close()
    old_argv = sys.argv
    try:
        sys.argv = [args.baseline, args.xlsx]
        with contextlib.redirect_stdout(io.StringIO()):
            original = runpy.run_path(args.baseline)
    finally:
        sys.argv = old_argv
    train, _, _ = split_rows(rows)
    recommender = ContestRecommender(train, CUTOFF, args.mapping, args.index, workbook_path=args.xlsx)
    result = evaluate(rows, groups, original, recommender)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    check = result['sales_only_historical']
    if any(check['original_list_mismatches_A_plus_B'].values()) or any(check['original_metric_mismatches_segments'].values()):
        raise SystemExit('Original baseline mismatch')


if __name__ == '__main__':
    main()
