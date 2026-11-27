"""Aggregate-only diagnostic for lost first-three 2+1 hits behind v5 mapping.

Run: python3 -B -m recommendation.v5_mapping_loss WORKBOOK MAPPING INDEX
Uses the unchanged default carousel as a read-only comparison; the mapping and
catalog are current, so this is NOT a leakage-free benchmark or identity review.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from functools import partial
from pathlib import Path

from catalog.softline_audit import normalize_sku, url_sku
from catalog.v4_index_mapping import digest
from recommendation import contest_recommender
from recommendation.profile_pipeline import _day
from recommendation.profile_v4_evaluation import CUTOFF, split_rows

REASONS = ('no_mapping_candidate', 'ambiguous_or_unusable', 'history_offer_exclusion',
           'not_available_in_snapshot', 'snapshot_policy_exclusion', 'eligible_not_top3')


def loss_reason(item, entries, unique, excluded, snapshot_policy=None):
    """First blocking gate for a lost sales-only hit, in carousel order."""
    if not entries.get(str(item), {}).get('candidates'):
        return 'no_mapping_candidate'
    offer = unique(item)
    if not offer:
        return 'ambiguous_or_unusable'
    if offer['offer_id'] in excluded:
        return 'history_offer_exclusion'
    if offer.get('availability') != 'true':
        return 'not_available_in_snapshot'
    if snapshot_policy is not None and snapshot_policy(offer) is None:
        return 'snapshot_policy_exclusion'
    return 'eligible_not_top3'


def exact_probes(sales, offers):
    """Probe all three exact channels for every item, counting offer occurrences.

    Compared with the original, also try exact names for non-N items, SKU for N
    items, and trim catalog names. Evidence doesn't approve or disambiguate SKU.
    """
    by_sku, by_name = defaultdict(set), defaultdict(set)
    for item, meta in sales.items():
        for code in meta['skus']:
            if normalized := normalize_sku(code):
                by_sku[normalized].add(item)
        for name in meta['names']:
            if name.strip():
                by_name[name.strip()].add(item)
    counts = {item: Counter() for item in sales}
    for code, url, name in offers:
        vendor = by_sku.get(normalize_sku(code), set())
        url_candidates = by_sku.get(url_sku(url), set())
        name_candidates = by_name.get((name or '').strip(), set())
        for channel, items in (('vendor_code', vendor), ('url_sku', url_candidates),
                               ('exact_trimmed_name', name_candidates),
                               ('union_occurrences', vendor | url_candidates | name_candidates)):
            for item in items:
                counts[item][channel] += 1
    return counts


def concentration(counter):
    values = sorted(counter.values(), reverse=True)
    return {'distinct_items': len(values), 'top1_hits': sum(values[:1]), 'top3_hits': sum(values[:3])}


def lexical_focus(picks, feed):
    """Rank private hypotheses locally; return only aggregate evidence by rank."""
    from recommendation.cold_mapping_investigation import marker_comparison, retrieve
    queries = [(rank, name) for rank, (_, meta, _) in enumerate(picks) for name in sorted(meta['names'])]
    names = [n or '' for _, _, n in feed]
    by_rank = defaultdict(dict)
    for (rank, name), nearest in zip(queries, retrieve([n for _, n in queries], names, top=10)):
        for index, score in nearest:
            current = by_rank[rank].get(index)
            if current is None or current[0] < score:
                by_rank[rank][index] = (score, marker_comparison(name, names[index]))
    summaries = []
    for rank, (hits, meta, exact) in enumerate(picks):
        best = sorted(by_rank[rank].values(), key=lambda x: -x[0])[:10]
        summaries.append({'rank': rank + 1, 'lost_hits': hits, 'train_names': len(meta['names']),
                          'train_has_sku': bool(meta['skus']), 'exact_occurrences': exact['union_occurrences'],
                          'best_lexical_score': best[0][0] if best else None,
                          'strong_name_candidates': sum(score >= .65 for score, _ in best),
                          'numeric_disagreements_top10': sum(bool(m['sales_only_numbers'] or m['offer_only_numbers']) for _, m in best),
                          'license_term_disagreements_top10': sum(m['sales']['terms'] != m['offer']['terms'] for _, m in best)})
    return summaries


def diagnose(rows, groups, rec, offers, *, offer_policy=None, policy_reason=None, private_review=None):
    _, hist, future = split_rows(rows)
    sales = defaultdict(lambda: {'skus': set(), 'names': set()})
    for r in rows:
        if _day(r[2]) <= CUTOFF:
            if r[4] is not None and str(r[4]).strip():
                sales[r[3]]['skus'].add(str(r[4]))
            if r[5] is not None and str(r[5]).strip():
                sales[r[3]]['names'].add(str(r[5]))
    counts = {c: Counter() for c in ('A', 'B')}
    lost_items = {c: Counter() for c in counts}
    unmapped_items = {c: Counter() for c in counts}
    lost_clients = {c: set() for c in counts}
    unmatched_clients = {c: set() for c in counts}
    detailed_policy = {c: Counter() for c in counts}
    for user, after in future.items():
        if user not in hist or not (targets := after - hist[user]):
            continue
        raw = {c['item_id'] for c in rec.pipeline.blended_candidates(user)}
        group = groups.get(user)
        segment = group if group in ('B2B', 'B2C') else None
        demo_list = rec.recommend(user, segment)['candidates'][:3]
        demo = {c['item_id'] for c in demo_list if c['item_id'] is not None}
        hit_raw, hit_demo = raw & targets, demo & targets
        lost = hit_raw - hit_demo
        if offer_policy is None:
            excluded = {str(c['offer_id']) for item in hist[user]
                        for c in rec.items.get(str(item), {}).get('candidates', [])
                        if c.get('offer_id') is not None}
            policy = None
        else:
            excluded = {o['offer_id'] for item in hist[user] if (o := rec._unique(item)) is not None}
            policy = partial(offer_policy, client_type=segment, platform=None)
        reasons = {i: loss_reason(i, rec.items, rec._unique, excluded, policy) for i in lost}
        classified = Counter(policy_reason(rec._unique(i), segment, None)[0]
                             for i, reason in reasons.items() if reason == 'snapshot_policy_exclusion') if policy_reason else Counter()
        if policy_reason and (None in classified or sum(classified.values()) != sum(r == 'snapshot_policy_exclusion' for r in reasons.values())):
            raise ValueError('Policy reasons did not reconcile')
        for c in ('A', 'B') if len(hist[user]) == 1 else ('A',):
            detailed_policy[c].update(classified)
            s = counts[c]
            s.update(clients=1, targets=len(targets), sales_only_2plus1_hits=len(hit_raw),
                     carousel_known_hits=len(hit_demo), shared=len(hit_raw & hit_demo),
                     gained=len(hit_demo - hit_raw), lost=len(lost), returned_slots=len(demo_list),
                     unknown_item_id_slots=sum(x['item_id'] is None for x in demo_list))
            s.update(reasons.values())
            lost_items[c].update(lost)
            unmapped_items[c].update(i for i, reason in reasons.items() if reason == 'no_mapping_candidate')
            if lost:
                lost_clients[c].add(user)
            if 'no_mapping_candidate' in reasons.values():
                unmatched_clients[c].add(user)
    probes = exact_probes({i: sales[i] for i in unmapped_items['A']}, offers)
    if private_review is not None:
        private_review([(hits, sales[i], probes[i]) for i, hits in
                        sorted(unmapped_items['A'].items(), key=lambda x: (-x[1], str(x[0])))[:3]])
    result = {}
    for c, s in counts.items():
        if (s['lost'] != sum(s[r] for r in REASONS)
                or s['sales_only_2plus1_hits'] != s['shared'] + s['lost']
                or s['carousel_known_hits'] != s['shared'] + s['gained']
                or s['no_mapping_candidate'] != sum(unmapped_items[c].values())):
            raise ValueError('Hit attribution did not reconcile')
        exact = Counter()
        for item, hits in unmapped_items[c].items():
            suffix = 'no_occurrence' if probes[item]['union_occurrences'] == 0 else (
                'one_occurrence' if probes[item]['union_occurrences'] == 1 else 'multiple_occurrences')
            categories = [suffix]
            categories.extend(k for k in ('vendor_code', 'url_sku', 'exact_trimmed_name') if probes[item][k])
            categories.append('sku_present' if sales[item]['skus'] else 'sku_missing')
            categories.append('n_prefix' if str(item).startswith('N-') else 'non_n_prefix')
            for label in categories:
                exact[label + '_items'] += 1
                exact[label + '_hits'] += hits
        result[c] = {k: v for k, v in s.items() if k not in REASONS}
        result[c].update(denominator=3*s['clients'], lost_clients=len(lost_clients[c]),
                         no_mapping_clients=len(unmatched_clients[c]),
                         lost_first_blocker={r: s[r] for r in REASONS},
                         snapshot_policy_breakdown=dict(detailed_policy[c]),
                         lost_concentration=concentration(lost_items[c]),
                         no_mapping_concentration=concentration(unmapped_items[c]),
                         exact_probe={f'{label}_{unit}': exact[f'{label}_{unit}'] for label in (
                             'no_occurrence', 'one_occurrence', 'multiple_occurrences', 'vendor_code', 'url_sku',
                             'exact_trimmed_name', 'sku_present', 'sku_missing', 'n_prefix', 'non_n_prefix')
                             for unit in ('items', 'hits')})
    return result


def main():
    import openpyxl
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('xlsx', 'mapping', 'index'):
        parser.add_argument(name)
    parser.add_argument('--lexical-focus', action='store_true', help='top-three unresolved items, aggregate-only (requires scikit-learn)')
    args = parser.parse_args()
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    try:
        rows = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
    finally:
        wb.close()
    train, _, _ = split_rows(rows)
    rec = contest_recommender.ContestRecommender(train, CUTOFF, args.mapping, args.index, workbook_path=args.xlsx)
    with sqlite3.connect(Path(args.index).resolve().as_uri() + '?mode=ro', uri=True) as con:
        feed = list(con.execute('SELECT vendor_code,url,name FROM offers'))
        focus = []
        cohorts = diagnose(rows, groups, rec, feed,
                           offer_policy=contest_recommender._offer_audience,
                           policy_reason=contest_recommender._offer_policy,
                           private_review=(lambda picks: focus.extend(lexical_focus(picks, feed))) if args.lexical_focus else None)
    print(json.dumps({'cohorts': cohorts,
                      'lexical_focus': focus,
                      'carousel_code_sha256': digest(contest_recommender.__file__), 'limitations': (
        'Observed final v5 window. Only sales-only 2+1 vs unchanged current-feed 2+1; not a causal mapping effect. '
        'Whole-workbook mapping and current snapshot may leak future info. '
        'Unresolved carousel slots are not measured misses. Exact probes use train SKU/name against current indexed rows, '
        'not offers verified to be available, unique, or correct. Channels can overlap. '
        'No result is not proof of absence; no identities are approved.')}, indent=2, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Mapping loss audit failed; check the local workbook, mapping, index and dependencies.') from None
