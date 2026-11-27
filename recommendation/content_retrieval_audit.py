"""Local, aggregate-only retrospective content experiment, not a production model.

python -B -m recommendation.content_retrieval_audit DATA.xlsx FEATURES.json INDEX.sqlite
Current catalog and full-sales-derived candidate universe are future information;
identity matches are unverified. No network, availability, or compatibility claims.
"""
from collections import Counter, defaultdict
from functools import lru_cache
import json
import sqlite3
import sys

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from recommendation.catalog_similarity import build_index
from recommendation.segmented_history import day, split_rows
from recommendation.sequential_diagnostics import WINDOWS, lexical_variant
from recommendation.temporal_selection import fit


def load_items(snapshot, database):
    index = build_index(snapshot)
    con = sqlite3.connect('file:' + database + '?mode=ro', uri=True)
    try:
        items = {}
        for item, (category, _) in index['items'].items():
            cand = snapshot['items'][item]['candidate']
            matches = con.execute('SELECT name,vendor,params_json FROM offers WHERE offer_id=? LIMIT 2', (str(cand['offer_id']),)).fetchall()
            if len(matches) != 1:
                continue
            name, vendor, params = matches[0]
            # The local index stores names, vendor and structured params, but not
            # offer descriptions. Never pretend unavailable descriptions exist.
            pieces = [name or '', vendor or '']
            for p in json.loads(params or '[]'):
                pieces.extend(str(p.get(k) or '') for k in ('name', 'value', 'unit'))
            items[item] = {'category': category, 'name': name or '', 'text': ' '.join(pieces)}
        return items
    finally:
        con.close()


def evaluate(rows, items, cutoff, start, end):
    if not day(cutoff) < day(start) or (end is not None and day(start) > day(end)):
        raise ValueError('invalid chronological window')
    train, test = split_rows(rows, cutoff, start, end)
    model = fit(train)
    train_items = set(model['pop'])
    ids = sorted(items, key=str)
    position = {x: i for i, x in enumerate(ids)}
    # Fit vocabulary and IDF on train-item documents only; transforming all
    # mapped documents still uses a nonhistorical feed and future item universe.
    docs = [items[x]['text'] for x in ids]
    train_docs = [items[x]['text'] for x in ids if x in train_items]
    vectorizer = TfidfVectorizer(token_pattern=r'(?u)\b\w+\b', sublinear_tf=True, dtype=np.float32)
    matrix = None
    if train_docs:
        try:
            vectorizer.fit(train_docs)
            matrix = vectorizer.transform(docs).tocsr()
        except ValueError as exc:
            if 'empty vocabulary' not in str(exc):
                raise
    categories = defaultdict(set)
    for x in ids:
        if items[x]['category']:
            categories[items[x]['category']].add(x)
    future = defaultdict(set)
    for _, user, _, item in test:
        future[user].add(item)
    users = {u for u, seen in model['history'].items() if future[u] - seen}

    @lru_cache(maxsize=50000)
    def variant(a, b):
        return lexical_variant(items[a]['name'], items[b]['name'])

    def candidates(seen, kind, ablate):
        scores = Counter()
        for src in sorted(seen & items.keys(), key=str):
            category = items[src]['category']
            if not category:
                continue
            possible = categories[category] - seen
            if ablate:
                possible = {dst for dst in possible if not variant(src, dst)}
            if kind == 'category':
                scores.update(possible)
            elif matrix is not None and possible:
                # One sparse row times the candidate subset; no all-pairs matrix.
                targets = sorted(possible, key=str)
                similarity = (matrix[position[src]] @ matrix[[position[x] for x in targets]].T).tocoo()
                for col, value in zip(similarity.col, similarity.data):
                    if value > 0:
                        scores[targets[col]] += float(value)
        return sorted(scores, key=lambda x: (-scores[x], str(x)))

    output = {}
    for cohort, members in [('A', users), ('B', {u for u in users if len(model['history'][u]) == 1})]:
        result = {}
        for kind in ('category', 'tfidf'):
            for ablate in (False, True):
                counts = Counter(n=len(members), hits=0, targets=0, cold_targets=0,
                                 cold_reachable=0, cold_hits=0, slots=0, filled_users=0,
                                 full_users=0, cold_slots=0)
                for user in members:
                    seen = model['history'][user]
                    targets = future[user] - seen
                    cold = targets - train_items
                    ranked = candidates(seen, kind, ablate)
                    chosen = set(ranked[:3])
                    counts['hits'] += len(chosen & targets)
                    counts['targets'] += len(targets)
                    counts['cold_targets'] += len(cold)
                    counts['cold_reachable'] += len(cold & set(ranked))
                    counts['cold_hits'] += len(cold & chosen)
                    counts['slots'] += len(chosen)
                    counts['filled_users'] += bool(chosen)
                    counts['full_users'] += len(chosen) == 3
                    counts['cold_slots'] += len(chosen - train_items)
                n = counts['n']
                result[kind + ('_lexical_ablation' if ablate else '')] = {
                    **counts, 'precision_at_3': counts['hits'] / (3*n) if n else None,
                    'client_fill_rate': counts['filled_users'] / n if n else None,
                    'top3_full_client_rate': counts['full_users'] / n if n else None,
                    'slot_fill_rate': counts['slots'] / (3*n) if n else None}
        output[cohort] = result
    return {'window': [cutoff, start, end], 'train_rows': len(train), 'test_rows': len(test),
            'train_items': len(train_items), 'mapped_items': len(items),
            'mapped_train_items': len(train_items & items.keys()), 'cohorts': output}


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    import openpyxl
    with open(sys.argv[2], encoding='utf8') as stream:
        items = load_items(json.load(stream), sys.argv[3])
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        rows = [tuple(r[:4]) for r in wb['Продажи'].iter_rows(min_row=2, values_only=True)]
    finally:
        wb.close()
    windows = list(WINDOWS) + [('2026-08-31', '2026-09-07', None)]
    result = {'future_snapshot': True, 'official_previously_observed': True,
              'limitations': 'Exploratory overlapping outcome-conditioned A/B windows; B has one train item, not zero history. Feed, candidate mappings and entire sales-derived item universe are current/future, not point-in-time; vocabulary and IDF only use train-item documents, but those documents are future snapshots. Identity is unverified, candidates limited to sales items uniquely mapped to distinct offers; not the full catalog. Descriptions absent from local index, so only offer name, vendor and params used. No availability checks or compatibility claims. Lexical ablation removes >=0.85 name-similar feed pairs, including future item names, proxy only and itself future-contaminated. Ties by item ID. Category baseline and TF-IDF have same category constraints; TF-IDF requires positive overlap. Cold reach is before top-3 and optimistic; rows count user-target pairs. Official test previously observed, not independent. No tuning or selection on official window.',
              'windows': [evaluate(rows, items, *window) for window in windows]}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
