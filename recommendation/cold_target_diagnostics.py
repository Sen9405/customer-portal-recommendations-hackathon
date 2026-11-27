"""Local aggregate-only early-validation cold-target reconciliation.

python3 -B -m recommendation.cold_target_diagnostics DATA.xlsx AUDIT.json FEATURES.json
The separately downloaded current snapshots are not historic, nor proof of identity.
"""
from collections import Counter, defaultdict
import json
import sys

from recommendation.catalog_similarity import build_index
from recommendation.hybrid_evaluation import catalog_edges, evaluate_window
from recommendation.segmented_history import split_rows
from recommendation.temporal_selection import fit, ranking

BUCKETS = ('no_candidate', 'ambiguous_candidate', 'unique_missing_offer_id',
           'unique_duplicate_offer_id', 'target_missing_category',
           'source_no_indexed_category', 'source_category_mismatch',
           'viable_ranking_lost', 'viable_top3_hit')


def diagnose(rows, names, audit, snapshot):
    if audit['feed'] != snapshot['feed']:
        raise ValueError('Audit and feature feed metadata differ')
    details, features = audit['items'], snapshot['items']
    unique = {key: value['matches']['all_union'][0] for key, value in details.items()
              if len(value['matches']['all_union']) == 1}
    if set(unique) != set(features):
        raise ValueError('Audit and feature unique candidate sets differ')
    if any(unique[k].get('offer_id') != features[k]['candidate'].get('offer_id')
           for k in unique):
        raise ValueError('Audit and feature candidate offer IDs differ')
    offers = Counter(str(v['candidate']['offer_id']) for v in features.values()
                     if v['candidate'].get('offer_id'))
    index = build_index(snapshot)
    edges = catalog_edges(snapshot)
    train, test = split_rows(rows, '2026-05-25', '2026-06-01', '2026-08-31')
    model = fit(train)
    future = defaultdict(set)
    for _, user, _, item in test:
        future[user].add(item)
    users = {u for u, seen in model['history'].items() if future[u] - seen}
    counts = Counter({bucket: 0 for bucket in BUCKETS})
    for user in users:
        seen = model['history'][user]
        scores = Counter()
        for source in sorted(seen, key=str):
            scores.update(edges.get(source, ()))
        history_scores = Counter()
        for source in model['latest'][user]:
            history_scores.update(model['seq'].get(source, {}))
        selected, chosen = set(seen), []
        for item in ranking(scores) + ranking(history_scores) + model['pop']:
            if item not in selected and len(chosen) < 3:
                chosen.append(item)
                selected.add(item)
        for target in (future[user] - seen) - set(model['pop']):
            key = str(target)
            if key not in details:
                raise ValueError('Target absent from audit universe')
            matches = details[key]['matches']['all_union']
            if not matches:
                bucket = 'no_candidate'
            elif len(matches) > 1:
                bucket = 'ambiguous_candidate'
            elif not features[key]['candidate'].get('offer_id'):
                bucket = 'unique_missing_offer_id'
            elif offers[str(features[key]['candidate']['offer_id'])] != 1:
                bucket = 'unique_duplicate_offer_id'
            elif not index['items'][key][0]:
                bucket = 'target_missing_category'
            else:
                source_categories = {index['items'].get(str(src), ('', ''))[0] for src in seen} - {''}
                if not source_categories:
                    bucket = 'source_no_indexed_category'
                elif index['items'][key][0] not in source_categories:
                    bucket = 'source_category_mismatch'
                elif target not in scores:
                    raise ValueError('Index/edge item types or reachability inconsistent')
                elif target in chosen:
                    bucket = 'viable_top3_hit'
                else:
                    bucket = 'viable_ranking_lost'
            counts[bucket] += 1
    reference = evaluate_window(rows, names, '2026-05-25', '2026-06-01', '2026-08-31', edges, content_label='future_catalog')['A']['hybrid']
    total = sum(counts.values())
    reachable = counts['viable_ranking_lost'] + counts['viable_top3_hit']
    if (total, reachable, counts['viable_top3_hit'], len(users)) != (reference['cold_targets'], reference['cold_reachable'], reference['cold_hits'], reference['n']):
        raise ValueError('Funnel disagrees with existing hybrid evaluation')
    return {'limitations': 'Client-item pairs, distinct per client. Outcome-conditioned A: train history plus novel future purchase; not all clients. Current feed and sales-selected item universe include future information; snapshots downloaded separately and matching metadata/IDs do not prove identical contents. Candidate identity unverified, similarity not compatibility, availability unknown; not production or point-in-time evaluation. No raw identifiers output.',
            'window': 'train <= 2026-05-25; labels 2026-06-01..2026-08-31',
            'cohort_clients': len(users), 'cold_target_client_items': total,
            'disjoint_first_failure_or_hit': dict(counts),
            'reachable_before_top3': reachable, 'hybrid_top3_cold_hits': counts['viable_top3_hit'],
            'reference_hybrid_reconciled': True}


def main():
    if len(sys.argv) != 4:
        raise SystemExit('Usage: python -m recommendation.cold_target_diagnostics DATA.xlsx AUDIT.json FEATURES.json')
    import openpyxl
    with open(sys.argv[2], encoding='utf-8') as f:
        audit = json.load(f)
    with open(sys.argv[3], encoding='utf-8') as f:
        snapshot = json.load(f)
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        records = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
        rows, names = [tuple(r[:4]) for r in records], [r[5] for r in records]
        print(json.dumps(diagnose(rows, names, audit, snapshot), ensure_ascii=False, indent=2))
    finally:
        wb.close()


if __name__ == '__main__':
    main()
