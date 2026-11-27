"""Offline, leakage-contaminated current-catalog similarity experiment.

Only the uniquely matched sales-item subset is indexed, not the full feed.
Similarity is not compatibility; matches are unverified identity candidates.
CLI prints aggregate counts only; do not export per-item recommendations.
"""
from collections import Counter, defaultdict
from datetime import date, datetime
import json
import sys


def build_index(snapshot):
    entries = snapshot['items']
    offer_counts = Counter(str(v['candidate']['offer_id']) for v in entries.values() if v['candidate'].get('offer_id'))
    items, categories = {}, defaultdict(set)
    for item_id, entry in entries.items():
        c = entry['candidate']
        offer = c.get('offer_id')
        if not offer or offer_counts[str(offer)] != 1:
            continue
        category = str(c['category_id']).strip() if c.get('category_id') else ''
        vendor = str(c['vendor']).strip().casefold() if c.get('vendor') else ''
        items[str(item_id)] = (category, vendor)
        if category:
            categories[category].add(str(item_id))
    return {'items': items, 'categories': categories}


def recommend(index, history, limit=3):
    history = {str(i) for i in history}
    scores = {}
    for source in sorted(history):
        category, vendor = index['items'].get(source, ('', ''))
        if not category:
            continue
        for target in index['categories'][category] - history:
            _, other_vendor = index['items'][target]
            same_vendor = bool(vendor and vendor == other_vendor)
            score = 1 + int(same_vendor)
            if score > scores.get(target, (0, []))[0]:
                scores[target] = (score, ['same_category'] + (['same_vendor'] if same_vendor else []))
    return [{'item_id': i, 'score': score, 'evidence': evidence} for i, (score, evidence) in sorted(scores.items(), key=lambda p: (-p[1][0], p[0]))[:limit]]


def backtest(sales, groups, snapshot):
    history, future = defaultdict(set), defaultdict(set)
    train_items = set()
    rows = Counter(train=0, gap=0, test=0)
    for order, user, week, item in sales:
        if isinstance(week, datetime):
            week = week.date()
        elif isinstance(week, str):
            week = date.fromisoformat(week)
        if not isinstance(week, date) or not all((order, user, item)):
            raise ValueError('Invalid sale')
        item = str(item)
        if week <= date(2026, 8, 31):
            rows['train'] += 1
            history[user].add(item)
            train_items.add(item)
        elif week >= date(2026, 9, 7):
            rows['test'] += 1
            future[user].add(item)
        else:
            rows['gap'] += 1
    index = build_index(snapshot)
    indexed_items = set(index['items'])
    users = {u for u in history if future[u] - history[u]}
    if any(groups.get(u) not in ('B2B', 'B2C') for u in users):
        raise ValueError('Missing or unknown client_group in cohort')
    result = {'rows': dict(rows), 'index': {'matched_sales_items': len(index['items']), 'cold_matched_items': len(set(index['items']) - train_items)}, 'cohorts': {}, 'limitations': ['Current feed leaks future information into historical evaluation.', 'Historical offer availability unknown; current availability is not used.', 'Unique feed match is unverified; similarity does not establish compatibility.', 'Only matched sales items, not all catalog offers, are candidates.']}
    for cohort, members in [('A', users), ('B', {u for u in users if len(history[u]) == 1})]:
        for group in ('all', 'B2B', 'B2C'):
            selected = [u for u in members if group == 'all' or groups[u] == group]
            metrics = Counter(n=len(selected), hits=0, cold_hits=0, relevant=0, cold_relevant=0, eligible_relevant=0, reachable_relevant=0, cold_recommendations=0, covered_users=0, full_users=0, slots=0)
            for user in selected:
                relevant = future[user] - history[user]
                ranked = recommend(index, history[user])
                chosen = {r['item_id'] for r in ranked}
                hits = chosen & relevant
                metrics['hits'] += len(hits)
                metrics['cold_hits'] += len(hits - train_items)
                metrics['relevant'] += len(relevant)
                metrics['cold_relevant'] += len(relevant - train_items)
                metrics['eligible_relevant'] += len(relevant & indexed_items)
                reachable = {r['item_id'] for r in recommend(index, history[user], limit=len(indexed_items))}
                metrics['reachable_relevant'] += len(relevant & reachable)
                metrics['cold_recommendations'] += len(chosen - train_items)
                metrics['covered_users'] += bool(ranked)
                metrics['full_users'] += len(ranked) == 3
                metrics['slots'] += len(ranked)
            metrics = dict(metrics)
            n = metrics['n']
            metrics['precision_at_3'] = metrics['hits'] / (3*n) if n else None
            metrics['client_coverage'] = metrics['covered_users'] / n if n else None
            metrics['relevant_item_coverage'] = metrics['eligible_relevant'] / metrics['relevant'] if metrics['relevant'] else None
            result['cohorts'][cohort if group == 'all' else cohort + '_' + group] = metrics
    return result


def main():
    import openpyxl
    if len(sys.argv) != 3:
        raise SystemExit('Usage: python -m recommendation.catalog_similarity workbook snapshot')
    with open(sys.argv[2], encoding='utf-8') as f:
        snapshot = json.load(f)
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
        sales = ((r[0], r[1], r[2], r[3]) for r in wb['Продажи'].iter_rows(min_row=2, values_only=True))
        print(json.dumps(backtest(sales, groups, snapshot), ensure_ascii=False, indent=2))
    finally:
        wb.close()


if __name__ == '__main__':
    main()
