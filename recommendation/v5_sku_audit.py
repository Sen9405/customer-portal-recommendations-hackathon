"""Aggregate current-snapshot SKU audit (no changes to mappings or ranking)."""
import argparse
import json
from collections import Counter, defaultdict

from recommendation.contest_recommender import ContestRecommender, _offer_audience, _offer_policy
from recommendation.profile_v4_evaluation import CUTOFF, split_rows


def stage(model, item, known, owned, segment):
    key = str(item)
    if item in known:
        return 'reachable'
    if key not in model.train_items:
        return 'not_in_train'
    candidates = model.items.get(key, {}).get('candidates', ())
    if not candidates:
        return 'no_mapping'
    if len(candidates) > 1:
        return 'multiple_feed_rows'
    entry = candidates[0]
    oid = entry.get('offer_id')
    if not oid:
        return 'missing_offer_id'
    oid = str(oid)
    if entry.get('duplicate_feed_offer_id') or (oid in model.by_id and model.by_id[oid] is None):
        return 'duplicate_offer_id'
    if len(model.claims.get(oid, ())) != 1:
        return 'shared_offer_id'
    offer = model.by_id.get(oid)
    if not offer:
        return 'missing_offer'
    if offer['availability'] != 'true':
        return 'unavailable_or_unknown'
    if oid in owned:
        return 'owned_offer'
    reason, _ = _offer_policy(offer, segment, None)
    if reason:
        return reason
    raise ValueError('Unexpected gap: mapped and eligible target absent from pool')


def title_key(value):
    # No token deletion, transliteration, stemming, or fuzzy match: edition,
    # counts, dates and punctuation can be part of the SKU identity.
    return ' '.join(str(value).casefold().split()) if value else ''


def audit(model, rows, groups):
    _, hist, future = split_rows(rows)
    sales_names, title_owners, feed_titles = defaultdict(set), defaultdict(set), defaultdict(set)
    for row in rows:
        if len(row) > 5 and (title := title_key(row[5])):
            sales_names[str(row[3])].add(title)
            title_owners[title].add(str(row[3]))
    for oid, offer in model.by_id.items():
        if offer and (title := title_key(offer['name'])):
            feed_titles[title].add(oid)
    dispositions = {'A': Counter(), 'B': Counter()}
    review_pairs = {'A': Counter(), 'B': Counter()}
    review_unique = {'A': set(), 'B': set()}
    for u, future_items in future.items():
        targets = future_items - hist.get(u, set())
        if u not in hist or not targets:
            continue
        segment = groups.get(u)
        segment = segment if segment in ('B2B', 'B2C') else None
        _, pool = model.recommend_with_catalog_pool(u, segment)
        known = set(pool.values()) - {None}
        owned = {offer['offer_id'] for i in hist[u] if (offer := model._unique(i))}
        for item in targets:
            cohorts = ('A', 'B') if len(hist[u]) == 1 else ('A',)
            disposition = stage(model, item, known, owned, segment)
            for c in cohorts:
                dispositions[c][disposition] += 1
            if disposition not in ('no_mapping', 'not_in_train'):
                continue
            key = str(item)
            names = sales_names[key]
            if len(names) != 1:
                continue
            title, = names
            offers = feed_titles[title]
            if title_owners[title] != {key} or len(offers) != 1:
                continue
            oid, = offers
            # Exclude existing claims, sold purchases and anything unavailable or disallowed.
            if model.claims.get(oid) or oid in owned or _offer_audience(model.by_id[oid], segment, None) is None:
                continue
            for c in cohorts:
                review_pairs[c][disposition] += 1
                review_unique[c].add(key)
    return {c: {'target_pairs': sum(dispositions[c].values()), 'by_first_gate': dict(dispositions[c]),
                'strict_title_review_pairs_not_confirmed': dict(review_pairs[c]),
                'strict_title_review_unique_items_not_confirmed': len(review_unique[c])} for c in dispositions}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('xlsx', 'mapping', 'index'):
        p.add_argument(name)
    a = p.parse_args()
    import openpyxl
    wb = openpyxl.load_workbook(a.xlsx, read_only=True, data_only=True)
    try:
        rows = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
    finally:
        wb.close()
    train, _, _ = split_rows(rows)
    model = ContestRecommender(train, CUTOFF, a.mapping, a.index, workbook_path=a.xlsx)
    print(json.dumps(audit(model, rows, groups), indent=2))


if __name__ == '__main__':
    main()
