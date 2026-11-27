"""Offline cold mapping investigation; private output, aggregate-only stdout.

Run: python -B -m recommendation.cold_mapping_investigation data/cold-mapping-investigation.json
Requires existing dataset.xlsx, softline-audit.json, softline-features.json and
catalog-20261126.sqlite in data/. Never downloads or queries a network service.
Scores are retrieval heuristics, not identity, compatibility, or absence proof.
"""
from collections import Counter, defaultdict
import json
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import openpyxl
from sklearn.feature_extraction.text import TfidfVectorizer
from catalog.softline_audit import normalize_sku, url_sku
from recommendation.cold_target_diagnostics import diagnose
from recommendation.private_candidate_diagnostics import write_report
from recommendation.segmented_history import split_rows
from recommendation.temporal_selection import fit


def license_markers(text):
    """Surface literal licensing/edition terms and quantities, not semantic equivalence."""
    text = (text or '').casefold()
    terms = re.findall(r'(?<!\w)(?:subscription|renewal|perpetual|license|licence|annual|standard|enterprise|professional|pro|user|device|server|подписка|продление|лицензия|бессрочная|пользователь\w*|устройств\w*|сервер\w*|год\w*|месяц\w*)(?!\w)', text)
    numbers = re.findall(r'(?<!\w)\d+(?:[.,]\d+)?(?!\w)', text)
    return {'terms': sorted(set(terms)), 'numbers': sorted(set(numbers))}


def marker_comparison(a, b):
    x, y = license_markers(a), license_markers(b)
    return {'sales': x, 'offer': y,
            'shared_numbers': sorted(set(x['numbers']) & set(y['numbers'])),
            'sales_only_numbers': sorted(set(x['numbers']) - set(y['numbers'])),
            'offer_only_numbers': sorted(set(y['numbers']) - set(x['numbers'])),
            'shared_terms': sorted(set(x['terms']) & set(y['terms']))}


def retrieve(names, offer_names, top=5):
    """Character TF-IDF over every offer name; return only positive scores."""
    if not offer_names or not any(offer_names):
        return [[] for _ in names]
    vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(2, 4), lowercase=True, dtype=np.float32, sublinear_tf=True)
    matrix = vectorizer.fit_transform(offer_names)
    queries = vectorizer.transform(names)
    result = []
    for query in queries:
        scores = (query @ matrix.T).tocoo()
        ranked = sorted(zip(scores.col.tolist(), scores.data.tolist()), key=lambda p: (-p[1], p[0]))[:top]
        result.append([(i, round(float(score), 6)) for i, score in ranked if score > 0])
    return result


def classify(score, sales_name, offer_name):
    """Uncalibrated triage bands; strong != identity. Numeric gaps flag review."""
    comparison = marker_comparison(sales_name, offer_name)
    if score >= .65:
        label = 'strong_lexical_candidate'
    elif score >= .35:
        label = 'possible_lexical_candidate'
    else:
        label = 'weak_lexical_candidate'
    return label, comparison


def targets(rows, audit, snapshot):
    train, test = split_rows(rows, '2026-05-25', '2026-06-01', '2026-08-31')
    model = fit(train)
    future = defaultdict(set)
    for _, user, _, item in test:
        future[user].add(item)
    users = {u for u, seen in model['history'].items() if future[u] - seen}
    offer_counts = Counter(str(v['candidate']['offer_id']) for v in snapshot['items'].values() if v['candidate'].get('offer_id'))
    result = {'no_candidate': [], 'unique_duplicate_offer_id': []}
    for user in users:
        for item in (future[user] - model['history'][user]) - set(model['pop']):
            key = str(item)
            matches = audit['items'][key]['matches']['all_union']
            if not matches:
                result['no_candidate'].append((user, key))
            elif len(matches) == 1:
                offer = snapshot['items'][key]['candidate'].get('offer_id')
                if offer and offer_counts[str(offer)] != 1:
                    result['unique_duplicate_offer_id'].append((user, key))
    return result


def run(records, audit, snapshot, con):
    rows = [tuple(r[:4]) for r in records]
    names = [r[5] for r in records]
    reference = diagnose(rows, names, audit, snapshot)
    pairs = targets(rows, audit, snapshot)
    for bucket, values in pairs.items():
        if len(values) != reference['disjoint_first_failure_or_hit'][bucket]:
            raise ValueError('Funnel reconciliation failed')
    sales = defaultdict(lambda: {'skus': set(), 'names': set(), 'rows': 0})
    for r in records:
        item = str(r[3]); sales[item]['rows'] += 1
        if r[4] is not None: sales[item]['skus'].add(str(r[4]))
        if r[5] is not None: sales[item]['names'].add(str(r[5]))
    claims = defaultdict(list)
    for item, value in snapshot['items'].items():
        offer_id = value['candidate'].get('offer_id')
        if offer_id: claims[str(offer_id)].append(item)
    con.row_factory = sqlite3.Row
    offers = [dict(r) for r in con.execute('SELECT row_id,offer_id,name,vendor,vendor_code,category_id,url,available,params_json FROM offers ORDER BY row_id')]
    by_offer = defaultdict(list)
    by_vendor_sku, by_url_sku, by_name = defaultdict(list), defaultdict(list), defaultdict(list)
    for offer in offers:
        by_offer[str(offer['offer_id'])].append(offer)
        vendor_key, url_key = normalize_sku(offer['vendor_code']), url_sku(offer['url'])
        if vendor_key: by_vendor_sku[vendor_key].append(offer['row_id'])
        if url_key: by_url_sku[url_key].append(offer['row_id'])
        if offer['name']: by_name[offer['name'].strip()].append(offer['row_id'])
    dup_items = {i for _, i in pairs['unique_duplicate_offer_id']}
    no_items = {i for _, i in pairs['no_candidate']}
    duplicate_groups = []
    for oid in sorted({str(snapshot['items'][i]['candidate']['offer_id']) for i in dup_items}):
        claim_details = []
        for item in sorted(claims[oid]):
            s = sales[item]
            comparisons = []
            for o in by_offer[oid]:
                params = json.loads(o['params_json'] or '[]')
                text = ' '.join([o['name'] or ''] + [str(p.get('value') or '') for p in params])
                comparisons.append({'row_id': o['row_id'],
                    'literal_vendor_code_equal': any(sku == (o['vendor_code'] or '') for sku in s['skus']),
                    'normalized_vendor_code_equal': any(normalize_sku(sku) and normalize_sku(sku) == normalize_sku(o['vendor_code']) for sku in s['skus']),
                    'normalized_url_sku_equal': any(normalize_sku(sku) and normalize_sku(sku) == url_sku(o['url']) for sku in s['skus']),
                    'name_and_offer_markers': [marker_comparison(n, text) for n in sorted(s['names'])]})
            claim_details.append({'item_id': item, 'is_cold_target': item in dup_items, 'sales_skus': sorted(s['skus']), 'sales_names': sorted(s['names']), 'sales_rows': s['rows'], 'comparisons': comparisons})
        duplicate_groups.append({'offer_id': oid, 'catalog_occurrences': by_offer[oid], 'claimants': claim_details})
    # Evaluate every distinct observed sales name for each target, not just one arbitrary row.
    queries = [(item, name) for item in sorted(no_items) for name in sorted(sales[item]['names'])]
    ranked = retrieve([name for _, name in queries], [o['name'] or '' for o in offers])
    unmatched = []
    for item in sorted(no_items):
        candidates = {}
        for (query_item, name), matches in zip(queries, ranked):
            if query_item != item: continue
            for index, score in matches:
                if index not in candidates or score > candidates[index][0]: candidates[index] = (score, name)
        tops = []
        for idx, (score, query) in sorted(candidates.items(), key=lambda p: (-p[1][0], p[0]))[:5]:
            offer = offers[idx]; label, comparison = classify(score, query, offer['name'] or '')
            comparison['offer_including_params'] = license_markers((offer['name'] or '') + ' ' + ' '.join(str(p.get('value') or '') for p in json.loads(offer['params_json'] or '[]')))
            tops.append({'score': score, 'class': label, 'query_name': query, 'offer': offer, 'markers': comparison})
        sku_keys = {normalize_sku(s) for s in sales[item]['skus']} - {''}
        exact = {'normalized_vendor_code_rows': sorted({r for k in sku_keys for r in by_vendor_sku[k]}),
                 'normalized_url_sku_rows': sorted({r for k in sku_keys for r in by_url_sku[k]}),
                 'trimmed_exact_name_rows': sorted({r for n in sales[item]['names'] for r in by_name[n.strip()]})}
        unmatched.append({'item_id': item, 'sales_skus': sorted(sales[item]['skus']), 'sales_names': sorted(sales[item]['names']), 'sales_rows': sales[item]['rows'], 'exact_current_index_row_ids': exact, 'candidates': tops})
    pair_counts = {bucket: dict(Counter(item for _, item in p)) for bucket, p in pairs.items()}
    classes = Counter((u['candidates'][0]['class'] if u['candidates'] else 'no_positive_name_overlap') for u in unmatched)
    weighted = Counter()
    for u in unmatched:
        weighted[u['candidates'][0]['class'] if u['candidates'] else 'no_positive_name_overlap'] += pair_counts['no_candidate'][u['item_id']]
    all_claims = [c for g in duplicate_groups for c in g['claimants']]
    stats = {'reference_cold_pairs': reference['cold_target_client_items'], 'catalog_offer_occurrences': len(offers),
        'duplicate_pairs': len(pairs['unique_duplicate_offer_id']), 'duplicate_distinct_target_items': len(dup_items),
        'duplicate_offer_groups': len(duplicate_groups), 'duplicate_all_unique_match_sales_claimants': len(all_claims),
        'duplicate_offer_occurrence_counts': dict(Counter(str(len(g['catalog_occurrences'])) for g in duplicate_groups)),
        'duplicate_claimants_exact_vendor_code_any_occurrence': sum(any(c['literal_vendor_code_equal'] for c in x['comparisons']) for x in all_claims),
        'duplicate_claimants_normalized_vendor_code_any_occurrence': sum(any(c['normalized_vendor_code_equal'] for c in x['comparisons']) for x in all_claims),
        'duplicate_claimants_url_sku_any_occurrence': sum(any(c['normalized_url_sku_equal'] for c in x['comparisons']) for x in all_claims),
        'duplicate_groups_two_claimants': sum(len(g['claimants']) == 2 for g in duplicate_groups),
        'duplicate_groups_distinct_sku_sets': sum(len({tuple(c['sales_skus']) for c in g['claimants']}) > 1 for g in duplicate_groups),
        'duplicate_claimants_literal_sku_only': sum(any(c['literal_vendor_code_equal'] and not c['normalized_url_sku_equal'] for c in x['comparisons']) for x in all_claims),
        'duplicate_claimants_url_sku_only': sum(any(c['normalized_url_sku_equal'] and not c['literal_vendor_code_equal'] for c in x['comparisons']) for x in all_claims),
        'duplicate_claimants_with_unshared_name_numbers': sum(any(m['sales_only_numbers'] or m['offer_only_numbers'] for c in x['comparisons'] for m in c['name_and_offer_markers']) for x in all_claims),
        'unmatched_pairs': len(pairs['no_candidate']), 'unmatched_distinct_items': len(no_items),
        'unmatched_N_prefix_items': sum(u['item_id'].startswith('N-') for u in unmatched),
        'unmatched_no_sku_items': sum(not u['sales_skus'] for u in unmatched),
        'unmatched_current_index_exact_vendor_items': sum(bool(u['exact_current_index_row_ids']['normalized_vendor_code_rows']) for u in unmatched),
        'unmatched_current_index_exact_url_items': sum(bool(u['exact_current_index_row_ids']['normalized_url_sku_rows']) for u in unmatched),
        'unmatched_current_index_exact_name_items': sum(bool(u['exact_current_index_row_ids']['trimmed_exact_name_rows']) for u in unmatched),
        'unmatched_top1_unshared_numbers_items': sum(bool(u['candidates'] and (u['candidates'][0]['markers']['sales_only_numbers'] or u['candidates'][0]['markers']['offer_only_numbers'])) for u in unmatched),
        'unmatched_top1_class_items': dict(classes), 'unmatched_top1_class_pairs': dict(weighted)}
    return {'limitations': 'Offline retrospective only. Current separately collected catalog is not a historical snapshot; metadata equality does not prove same snapshot. Unique claims are candidates, not identity; only sales items with a single audit match are claimants. TF-IDF character 2-4 name retrieval top 5 over full indexed offer occurrences; threshold bands are uncalibrated triage, not probabilities or absence proof. License tokens/numbers are literal proxies, not conflict or entitlement decisions. Catalog parameters and sales names may omit details; availability not verified.',
        'aggregates': stats, 'pair_counts_by_item': pair_counts, 'duplicate_groups': duplicate_groups, 'unmatched': unmatched}


def main():
    if len(sys.argv) != 2: raise ValueError('Provide a new output inside data/')
    root = Path('data').resolve(strict=True)
    with (root / 'softline-audit.json').open(encoding='utf8') as f: audit = json.load(f)
    with (root / 'softline-features.json').open(encoding='utf8') as f: snapshot = json.load(f)
    wb = openpyxl.load_workbook(root / 'dataset.xlsx', read_only=True, data_only=True)
    try: records = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
    finally: wb.close()
    with sqlite3.connect((root / 'catalog-20261126.sqlite').as_uri() + '?mode=ro', uri=True) as con:
        report = run(records, audit, snapshot, con)
    write_report(sys.argv[1], json.dumps(report, ensure_ascii=False, indent=2), root)
    print(json.dumps(report['aggregates'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try: main()
    except Exception: raise SystemExit('Offline diagnostic failed; check input, dependencies and new output path locally.') from None
