"""Offline cold-rank diagnostics; never a production recommender.

python -B -m recommendation.cold_rank_audit XLSX AUDIT FEATURES INDEX PRIVATE_JSON
PRIVATE_JSON must be a new path directly in data/. Stdout contains aggregates only.
Variants fixed in advance: sum, max, and mean of positive source cosine scores.
Sensitivity: remove similar-name source edges; remove train-exposed candidate
competitors (oracle cold-only pool, not a deployable ranking or causal estimate).
First two overlapping early windows are exploratory; final early window was
already observed and is descriptive validation only. Official window is not run.
Current feed, mappings and candidate universe leak future; identity unverified.
Similarity is not relevance, identity, availability, compatibility or approval.
"""
from collections import Counter, defaultdict
from functools import lru_cache
import json
import os
from pathlib import Path
import sys

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from recommendation.cross_category_experiment import eligible_items
from recommendation.segmented_history import split_rows
from recommendation.sequential_diagnostics import WINDOWS, lexical_variant
from recommendation.temporal_selection import fit

VARIANTS = ('sum', 'max', 'mean')


def rank_scores(edges, seen, mode='sum', ablate=False, exposed=None):
    """Aggregate positive edges; mean divides by contributing sources only."""
    if mode not in VARIANTS:
        raise ValueError('unknown variant')
    scores = defaultdict(list)
    for src, dst, value, similar in edges:
        if src in seen and dst not in seen and value > 0 and not (ablate and similar):
            if exposed is None or dst not in exposed:
                scores[dst].append(float(value))
    totals = {dst: (sum(v) if mode == 'sum' else max(v) if mode == 'max' else sum(v)/len(v))
              for dst, v in scores.items()}
    return sorted(totals, key=lambda x: (-totals[x], str(x))), totals


def audit(rows, items, window):
    train, test = split_rows(rows, *window)
    model = fit(train)
    exposed = set(model['pop'])
    ids = sorted(items, key=str)
    pos = {x: i for i, x in enumerate(ids)}
    docs = [items[x]['text'] for x in ids]
    train_docs = [items[x]['text'] for x in ids if x in exposed]
    matrix = None
    if train_docs:
        vec = TfidfVectorizer(token_pattern=r'(?u)\b\w+\b', sublinear_tf=True, dtype=np.float32)
        try:
            vec.fit(train_docs)
            matrix = vec.transform(docs).tocsr()
        except ValueError as exc:
            if 'empty vocabulary' not in str(exc):
                raise
    def evidence(src, dst):
        # Weighted shared vocabulary contributions explain cosine, not relevance.
        overlap = matrix[pos[src]].multiply(matrix[pos[dst]]).tocoo()
        vocabulary = vec.get_feature_names_out()
        return [{'token': str(vocabulary[col]), 'contribution': float(value)}
                for col, value in sorted(zip(overlap.col, overlap.data),
                                         key=lambda p: (-p[1], str(vocabulary[p[0]])))[:10]]
    @lru_cache(maxsize=None)
    def source_edges(src):
        if matrix is None:
            return ()
        row = (matrix[pos[src]] @ matrix.T).tocoo()
        return tuple((src, ids[int(col)], float(value),
                      lexical_variant(items[src]['name'], items[ids[int(col)]]['name']))
                     for col, value in zip(row.col, row.data) if value > 0)
    future = defaultdict(set)
    for _, u, _, item in test:
        future[u].add(item)
    users = sorted((u for u, seen in model['history'].items() if future[u] - seen), key=str)
    counts = {name: Counter(n=len(users), cold_targets=0, reachable=0, cold_hits=0,
                            hits=0, slots=0, filled=0) for name in
              list(VARIANTS) + ['sum_no_similar', 'sum_no_exposed_competitors']}
    ranks = Counter()
    causes = Counter()
    records = []
    for u in users:
        seen = model['history'][u]
        targets = future[u] - seen
        cold = targets - exposed
        edges = [edge for src in sorted(seen & items.keys(), key=str) for edge in source_edges(src)]
        outcomes = {}
        for name in counts:
            mode = name if name in VARIANTS else 'sum'
            ranked, scores = rank_scores(edges, seen, mode, name == 'sum_no_similar',
                                         exposed if name == 'sum_no_exposed_competitors' else None)
            selected = set(ranked[:3]); c = counts[name]
            c['cold_targets'] += len(cold); c['reachable'] += len(cold & scores.keys())
            c['cold_hits'] += len(cold & selected); c['hits'] += len(targets & selected)
            c['slots'] += len(selected); c['filled'] += bool(selected)
            outcomes[name] = (ranked, scores)
        ranked, scores = outcomes['sum']
        for target in sorted(cold & scores.keys(), key=str):
            idx = ranked.index(target); rank = idx + 1
            ranks['1-3' if rank <= 3 else '4-10' if rank <= 10 else '11-50' if rank <= 50 else '51+'] += 1
            competitors = ranked[:idx]
            sources = [e for e in edges if e[1] == target and e[0] not in {target}]
            source_cats = {items[s]['category'] for s in seen & items.keys() if items[s]['category']}
            same = bool(items[target]['category'] and items[target]['category'] in source_cats)
            causes['same_category_as_any_source' if same else 'no_same_nonempty_category'] += 1
            causes['target_has_similar_edge'] += any(e[3] for e in sources)
            causes['lost_top3'] += rank > 3
            causes['lost_top3_with_3_exposed_above'] += rank > 3 and sum(x in exposed for x in competitors) >= 3
            causes['lost_top3_with_3_cold_above'] += rank > 3 and sum(x not in exposed for x in competitors) >= 3
            records.append({'user': str(u), 'target': str(target), 'target_category': items[target]['category'],
                            'rank': rank, 'score': scores[target], 'same_category_as_any_source': same,
                            'exposed_above': sum(x in exposed for x in competitors),
                            'cold_above': sum(x not in exposed for x in competitors),
                            'score_at_top3_boundary': scores[ranked[2]] if len(ranked) >= 3 else None,
                            'target_edges': [{'source': str(s), 'source_category': items[s]['category'],
                                              'cosine': v, 'similar_name_proxy': sim,
                                              'top_shared_tfidf_terms': evidence(s, target)}
                                             for s, _, v, sim in sources],
                            'top3': [{'item': str(x), 'score': scores[x], 'exposed': x in exposed,
                                      'category': items[x]['category']} for x in ranked[:3]],
                            'variant_ranks': {name: (r.index(target)+1 if target in sc else None)
                                              for name, (r, sc) in outcomes.items()}})
    result = {'window': window, 'users': len(users), 'variants': {k: dict(v) for k, v in counts.items()},
              'base_reachable_rank_buckets': dict(ranks), 'base_diagnostics': dict(causes)}
    assert sum(ranks.values()) == counts['sum']['reachable'] == len(records)
    return result, records


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 5:
        raise SystemExit(__doc__)
    xlsx, audit_path, features_path, db, output = argv
    parent = Path(output).resolve().parent
    if parent != (Path.cwd() / 'data').resolve():
        raise ValueError('private output must be directly within data/')
    with open(audit_path, encoding='utf8') as f:
        audit_data = json.load(f)
    with open(features_path, encoding='utf8') as f:
        features = json.load(f)
    items = eligible_items(features, audit_data, db)
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        rows = [tuple(row[:4]) for row in wb['Продажи'].iter_rows(min_row=2, values_only=True)]
    finally:
        wb.close()
    summary, private = [], []
    for window in WINDOWS:
        aggregate, details = audit(rows, items, window)
        summary.append(aggregate)
        private.append({'window': window, 'pairs': details})
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(output, flags, 0o600)
    with os.fdopen(fd, 'w', encoding='utf8') as f:
        json.dump({'limitations': __doc__, 'windows': private}, f, ensure_ascii=False, indent=2)
    print(json.dumps({'limitations': __doc__, 'windows': summary}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
