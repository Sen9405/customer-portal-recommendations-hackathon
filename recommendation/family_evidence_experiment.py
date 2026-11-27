"""Offline exploratory family-relation retrieval, NOT a historical backtest.

Run: python -B -m recommendation.family_evidence_experiment XLSX MAPPING INDEX
Prints aggregate counts only. Current catalog, mappings and registry may contain
future information. Family-name mentions are hypotheses, not identities, SKU
verification, recommendations, compatibility, entitlements or availability.
The sales-derived target universe contains future items: no deployable ranking.
Strict historical evaluation is deliberately unavailable until independently
verifiable archived snapshots and temporal mappings have been implemented.
"""
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

from catalog.v4_index_mapping import digest
from recommendation.profile_v4_evaluation import split_rows
from sources.registry import validate_registry

REGISTRY = Path(__file__).resolve().parent.parent / 'sources/manufacturer_relations.json'


def historical_evaluation(*_args, **_kwargs):
    """Fail closed: dates or caller assertions alone cannot prove provenance."""
    raise ValueError('historical evaluation unavailable: no verified as-of catalog, mappings and source snapshots')


def family_mentioned(name, family):
    # Literal complete family phrase with token boundaries; not SKU identity.
    if not isinstance(name, str) or not isinstance(family, str) or not family.strip():
        return False
    return bool(re.search(r'(?<!\w)' + re.escape(family.casefold()) + r'(?!\w)', name.casefold()))


def make_candidates(items, registry):
    """Items are unverified catalog names keyed by private item identifier.

    Returns directed subject->object hypotheses only, each carrying exact public
    source evidence; both directions are NOT presumed appropriate. No fallback.
    """
    if validate_registry(registry):
        raise ValueError('invalid manufacturer registry')
    by_relation = {}
    for r in registry['relations']:
        sides = {side: {key for key, name in items.items() if family_mentioned(name, r[side])}
                 for side in ('subject', 'object')}
        by_relation[r['id']] = (r, sides)
    result = defaultdict(dict)
    for r, sides in by_relation.values():
        for source in sides['subject']:
            for target in sides['object'] - {source}:
                result[source].setdefault(target, []).append({
                    'relation_id': r['id'], 'url': r['url'],
                    'permitted_claim': r['permitted_claim'], 'caveats': r['caveats'],
                    'source_version': r['source_version'], 'observed_at': r['observed_at'],
                    'status': 'conditional_family_hypothesis_not_identity',
                    'backtest_eligible': False})
    return result


def mapped_names(mapping, conn):
    """Reject ambiguous mappings, shared offers, repeated feed IDs and missing IDs."""
    unique = {k: v['candidates'][0] for k, v in mapping['items'].items()
              if len(v.get('candidates', [])) == 1}
    offer_counts = Counter(c.get('offer_id') for c in unique.values() if c.get('offer_id'))
    items = {}
    for item, candidate in unique.items():
        offer = candidate.get('offer_id')
        if not offer or offer_counts[offer] != 1 or candidate.get('duplicate_feed_offer_id'):
            continue
        rows = conn.execute('SELECT offer_id, name FROM offers WHERE row_id = ?',
                            (candidate['row_id'],)).fetchall()
        if len(rows) != 1 or rows[0][0] != offer:
            raise ValueError('mapping/index row mismatch')
        if conn.execute('SELECT count(*) FROM offers WHERE offer_id = ?', (offer,)).fetchone()[0] != 1:
            continue
        items[item] = rows[0][1]
    return items


def full_catalog_aggregate(rows, names, registry, conn):
    """Count conditional full-catalog hypotheses, independent of sales targets.

    These are feed occurrences, not distinct products or recommendations; no
    identity, current availability or entitlement is assumed. No raw rows leave.
    """
    if validate_registry(registry):
        raise ValueError('invalid manufacturer registry')
    _, histories, future = split_rows(rows)
    users = {u for u in histories if future[u] - histories[u]}
    relations = registry['relations']
    offers = {r['id']: set() for r in relations}
    for row_id, name in conn.execute('SELECT row_id, name FROM offers'):
        for r in relations:
            if family_mentioned(name, r['object']):
                offers[r['id']].add(row_id)
    applicable = {str(item): {r['id'] for r in relations if family_mentioned(name, r['subject'])}
                  for item, name in names.items()}
    counts = Counter(clients_with_subject_mention=0, clients_with_catalog_hypotheses=0,
                     summed_distinct_offer_occurrences_per_client=0)
    for user in users:
        rels = set().union(*(applicable.get(str(item), set()) for item in histories[user]))
        candidates = set().union(*(offers[rel] for rel in rels))
        counts['clients_with_subject_mention'] += bool(rels)
        counts['clients_with_catalog_hypotheses'] += bool(candidates)
        counts['summed_distinct_offer_occurrences_per_client'] += len(candidates)
    return {'catalog_object_mention_occurrences_by_public_relation':
            {r['id']: len(offers[r['id']]) for r in relations},
            'counts': dict(counts), 'backtest_eligible': False,
            'limitations': 'Current full-feed row occurrences only, not unique products, recommendations, cold-target matches or validated identity. May include previously purchased items, duplicates, unavailable offers, wrong versions or licenses. No SKU or compatibility claim.'}


def aggregate(rows, names, registry):
    train, histories, future = split_rows(rows)
    train_items = {str(r[3]) for r in train}
    edges = make_candidates(names, registry)
    users = {u for u in histories if future[u] - histories[u]}
    counts = Counter(clients=len(users), cold_target_pairs=0, cold_reachable=0,
                     cold_top3=0, nonempty_candidate_clients=0, candidate_slots=0)
    for user in users:
        seen = {str(x) for x in histories[user]}
        cold = {str(x) for x in future[user] - histories[user]} - train_items
        scores = Counter()
        for source in seen:
            for target, evidence in edges.get(source, {}).items():
                if target not in seen:
                    scores[target] += len(evidence)
        ranked = sorted(scores, key=lambda x: (-scores[x], x))
        counts['cold_target_pairs'] += len(cold)
        counts['cold_reachable'] += len(cold & scores.keys())
        counts['cold_top3'] += len(cold & set(ranked[:3]))
        counts['nonempty_candidate_clients'] += bool(ranked)
        counts['candidate_slots'] += min(3, len(ranked))
    return {'cutoff': '2026-08-31 (week label inclusive)',
            'mapped_unique_nonshared_items': len(names),
            'distinct_directed_candidate_pairs': sum(len(x) for x in edges.values()),
            'counts': dict(counts), 'historical_evaluation': 'blocked',
            'backtest_eligible': False,
            'limitations': 'EXPLORATORY ONLY: current catalog and manufacturer documents, sales-selected universe and mappings include future information. Outcome-conditioned clients with train history and a novel future purchase; cold means absent from all train sales. Reachability before top-3 is optimistic and top-3 ordering uses private item IDs only to break ties. Mapping and family mentions are unverified hypotheses, no identity, compatibility, SKU, entitlement or availability established. Current registry has no verified historical snapshots. Official window previously observed; no independent test or quality claim.'}


def main():
    if len(sys.argv) != 4:
        raise SystemExit('usage: python -m recommendation.family_evidence_experiment XLSX MAPPING INDEX')
    xlsx, mapping_path, index = sys.argv[1:]
    with open(mapping_path, encoding='utf8') as f:
        mapping = json.load(f)
    with REGISTRY.open(encoding='utf8') as f:
        registry = json.load(f)
    provenance = mapping['provenance']
    if provenance['workbook_sha256'] != digest(xlsx) or provenance['index_sha256'] != digest(index):
        raise ValueError('mapping provenance hash mismatch')
    uri = Path(index).resolve().as_uri() + '?mode=ro&immutable=1'
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        rows = [r[:4] for r in wb['Продажи'].iter_rows(min_row=2, values_only=True)]
    finally:
        wb.close()
    with sqlite3.connect(uri, uri=True) as conn:
        names = mapped_names(mapping, conn)
        result = aggregate(rows, names, registry)
        result['full_catalog_exploration'] = full_catalog_aggregate(rows, names, registry, conn)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
