"""Aggregate-only, offline early-window cross-category retrieval experiment.

Usage: python -B -m recommendation.cross_category_experiment XLSX AUDIT FEATURES INDEX
Methods fixed before execution: category count, category-gated TF-IDF, and
ungated TF-IDF, all on exactly the same uniquely matched, nonduplicate sales
items. TF-IDF uses offer name, vendor, structured parameters; positive cosine
only; IDF/vocabulary fitted on train-item documents. Sum over history sources,
exclude purchased items, ties by item ID. Lexical ablation removes source-target
pairs with offer-name SequenceMatcher >= .85 (length >= 8), before ranking.
This is not a compatibility, identity, availability, or historical claim.
"""
from collections import Counter, defaultdict
from functools import lru_cache
import json
import sys

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from recommendation.content_retrieval_audit import load_items
from recommendation.segmented_history import split_rows
from recommendation.sequential_diagnostics import WINDOWS, lexical_variant
from recommendation.temporal_selection import fit


def eligible_items(snapshot, audit, database):
    # Explicitly exclude no-match AND ambiguous, even if a malformed snapshot
    # happens to give them an offer. Existing loader rejects duplicate claims
    # and duplicate index occurrences before this filter, conservatively.
    loaded = load_items(snapshot, database)
    return {item: value for item, value in loaded.items()
            if len(audit['items'].get(str(item), {}).get('matches', {}).get('all_union', [])) == 1}


def evaluate(rows, items, cutoff, start, end):
    train, test = split_rows(rows, cutoff, start, end)
    model = fit(train)
    train_items = set(model['pop'])
    ids = sorted(items, key=str)
    position = {item: i for i, item in enumerate(ids)}
    docs = [items[i]['text'] for i in ids]
    train_docs = [items[i]['text'] for i in ids if i in train_items]
    matrix = None
    if train_docs:
        vectorizer = TfidfVectorizer(token_pattern=r'(?u)\b\w+\b', sublinear_tf=True, dtype=np.float32)
        try:
            vectorizer.fit(train_docs)
            matrix = vectorizer.transform(docs).tocsr()
        except ValueError as exc:
            if 'empty vocabulary' not in str(exc):
                raise
    categories = defaultdict(set)
    for item in ids:
        if items[item]['category']:
            categories[items[item]['category']].add(item)

    @lru_cache(maxsize=None)
    def positive(src):
        if matrix is None:
            return ()
        result = (matrix[position[src]] @ matrix.T).tocoo()
        return tuple((ids[int(col)], float(value)) for col, value in zip(result.col, result.data) if value > 0)

    @lru_cache(maxsize=None)
    def variant(src, dst):
        return lexical_variant(items[src]['name'], items[dst]['name'])

    def rank(seen, kind, ablate):
        scores = Counter()
        for src in sorted(seen & items.keys(), key=str):
            category = items[src]['category']
            if kind == 'category':
                pairs = ((dst, 1.0) for dst in categories.get(category, ()) if category)
            else:
                pairs = positive(src)
            for dst, score in pairs:
                if dst in seen:
                    continue
                if kind == 'gated_tfidf' and (not category or category != items[dst]['category']):
                    continue
                if ablate and variant(src, dst):
                    continue
                scores[dst] += score
        return sorted(scores, key=lambda item: (-scores[item], str(item)))

    future = defaultdict(set)
    for _, user, _, item in test:
        future[user].add(item)
    users = {u for u, seen in model['history'].items() if future[u] - seen}
    cohorts = {}
    for label, members in (('A', users), ('B', {u for u in users if len(model['history'][u]) == 1})):
        results = {}
        for kind in ('category', 'gated_tfidf', 'global_tfidf'):
            for ablate in (False, True):
                counts = Counter(n=len(members), hits=0, targets=0, cold_targets=0,
                                 cold_eligible=0, cold_reachable=0, cold_hits=0,
                                 slots=0, filled_users=0, full_users=0, cold_slots=0,
                                 cold_reachable_cross_category_only=0)
                for user in members:
                    seen = model['history'][user]
                    targets = future[user] - seen
                    cold = targets - train_items
                    ranked = rank(seen, kind, ablate)
                    reachable = set(ranked)
                    chosen = set(ranked[:3])
                    source_categories = {items[s]['category'] for s in seen & items.keys() if items[s]['category']}
                    counts['targets'] += len(targets)
                    counts['hits'] += len(targets & chosen)
                    counts['cold_targets'] += len(cold)
                    counts['cold_eligible'] += len(cold & items.keys())
                    counts['cold_reachable'] += len(cold & reachable)
                    counts['cold_hits'] += len(cold & chosen)
                    counts['cold_reachable_cross_category_only'] += sum(items[t]['category'] not in source_categories for t in cold & reachable)
                    counts['slots'] += len(chosen)
                    counts['cold_slots'] += len(chosen - train_items)
                    counts['filled_users'] += bool(chosen)
                    counts['full_users'] += len(chosen) == 3
                n = counts['n']
                results[kind + ('_lexical_ablation' if ablate else '')] = {
                    **counts, 'precision_at_3': counts['hits'] / (3*n) if n else None,
                    'client_fill_rate': counts['filled_users'] / n if n else None,
                    'slot_fill_rate': counts['slots'] / (3*n) if n else None}
        cohorts[label] = results
    return {'window': [cutoff, start, end], 'train_rows': len(train), 'test_rows': len(test),
            'train_items': len(train_items), 'eligible_items': len(ids),
            'eligible_train_items': len(train_items & items.keys()), 'cohorts': cohorts}


def main():
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    import openpyxl
    with open(sys.argv[2], encoding='utf8') as f:
        audit = json.load(f)
    with open(sys.argv[3], encoding='utf8') as f:
        snapshot = json.load(f)
    items = eligible_items(snapshot, audit, sys.argv[4])
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        rows = [tuple(row[:4]) for row in wb['Продажи'].iter_rows(min_row=2, values_only=True)]
    finally:
        wb.close()
    output = {'limitations': 'Overlapping early validation only; outcome-conditioned A/B, B one train item, not zero-history. Current catalog and sales-derived candidate universe and mappings include future information, including cold items; not point-in-time. IDF train-item documents only but themselves current. Exact same universe for all methods: unmatched, ambiguous, duplicate claims and duplicate catalog offer rows excluded, unique matches unverified. No source-registry SKU-level attested edges, none used. No full-feed retrieval, availability or compatibility claims. Lexical ablation uses current feed names including cold targets and is only a similarity proxy. Cold reach before top 3 is optimistic; counts are client-target pairs. Official test not run; previously observed and not independent.',
              'windows': [evaluate(rows, items, *window) for window in WINDOWS]}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
