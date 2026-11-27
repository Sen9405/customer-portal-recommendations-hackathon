"""Offline first-observed-purchase replay and fixed-demo fallback coverage."""
import argparse
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import product
import json

import openpyxl

from closed_review_server import load_data
from recommendation.contest_recommender import GOAL_OPTIONS, PLATFORM_OPTIONS
from recommendation.profile_pipeline import _day
from recommendation.profile_v4_evaluation import CUTOFF, official_models, official_rank, split_rows


def cold_first_week(rows, hist):
    first = {}
    for _, user, week, item, *_ in rows:
        week = _day(week)
        if week <= CUTOFF or user in hist:
            continue
        current = first.get(user)
        if current is None or week < current[0]:
            first[user] = (week, {item})
        elif week == current[0]:
            current[1].add(item)
    return first


def evaluate(data, rows):
    train, hist, _ = split_rows(rows)
    first = cold_first_week(rows, hist)
    pop, co = official_models(train, hist)
    empty = object()
    baseline = official_rank(empty, defaultdict(set), pop)
    assert not official_rank(empty, defaultdict(set), pop, co)
    rec = data.contest
    assert rec is not None

    @lru_cache(maxsize=None)
    def visible(group, industry, goal=None, platform=None):
        return tuple(i for i, _, _ in rec._cold_popular_rows(data.customers, group, industry, goal, platform))

    @lru_cache(maxsize=None)
    def matched_baseline(group):
        return tuple(i for i, _, _ in rec._cold_popular_rows(data.customers, group, None, None, None, global_scores=True))

    @lru_cache(maxsize=None)
    def sales_only(group, industry):
        broad, narrow = Counter(), Counter()
        for client, items in hist.items():
            attrs = data.customers.get(client, {})
            if group is not None and attrs.get('group') != group:
                continue
            broad.update(items)
            if group == 'B2B' and industry and attrs.get('industry') == industry:
                narrow.update(items)
        return sorted(broad, key=lambda i: (narrow[i] < 3, -narrow[i] if narrow[i] >= 3 else -broad[i], str(i)))[:3]

    stats = defaultdict(Counter)
    for user, (_, bought) in first.items():
        attrs = data.customers.get(user, {})
        group = attrs.get('group') if attrs.get('group') in ('B2B','B2C') else None
        industry = attrs.get('industry') if group == 'B2B' else None
        methods = {'global_popularity':baseline, 'co_purchase_no_history':(),
                   'sales_by_client_type':sales_only(group,None), 'sales_by_type_and_industry':sales_only(group,industry),
                   'demo_global_same_pool':matched_baseline(group),
                   'demo_type_only':visible(group,None), 'demo_type_and_industry':visible(group,industry)}
        keys = ['all', group or 'unknown'] + (['B2B_known_industry'] if industry else [])
        for key in keys:
            stats[key].update(clients=1)
            for method, picks in methods.items():
                hits = len(set(picks) & bought)
                stats[key].update({f'{method}_hits':hits, f'{method}_covered':bool(picks), f'{method}_full':len(picks)==3})

    counts, per_platform, per_goal = Counter(), defaultdict(Counter), defaultdict(Counter)
    examples = {}
    final_counts, tiers = Counter(), Counter()
    audience = [(None,None),('B2C',None),('B2B',None)] + [('B2B',v) for v in data.industries]
    for (group, industry), goal, platform in product(audience,(None,*GOAL_OPTIONS),(None,*PLATFORM_OPTIONS)):
        picks = visible(group,industry,goal,platform)
        n = len(picks)
        counts[n] += 1
        final = rec.cold_fallback(data.customers,group,industry,goal,platform)
        final_counts[len(final['candidates'])] += 1
        tiers[(final['kind'], final['category_relaxed'], final['platform_relaxed'])] += 1
        per_platform[platform or 'unspecified'][n] += 1
        per_goal[goal or 'unspecified'][n] += 1
        if industry is None and n == 0 and (platform or 'unspecified') not in examples:
            examples[platform or 'unspecified'] = {'group':group, 'goal':goal}
    assert sum(counts.values()) == len(audience)*(1+len(GOAL_OPTIONS))*(1+len(PLATFORM_OPTIONS))
    return {'cutoff':CUTOFF.isoformat(), 'fixed_snapshot':rec.metadata.get('feed_date_as_supplied'),
            'first_week':{g:{**s,'denominator':3*s['clients']} for g,s in stats.items()},
            'fallback_matrix':{'recommendation_count':counts,'by_platform':per_platform,'by_goal':per_goal,
                               'examples_of_empty':examples, 'fallback_after_expansion':final_counts,
                               'tiers':[{'kind':k,'other_category':g,'without_architecture':p,'scenarios':n} for (k,g,p),n in sorted(tiers.items())]}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',default='data')
    a = p.parse_args()
    import os
    from pathlib import Path
    os.environ.pop('CATALOG_LIVE', None)
    data = load_data(a.data_dir)
    book = openpyxl.load_workbook(Path(a.data_dir)/'dataset_v5.xlsx',read_only=True,data_only=True)
    try:
        rows = list(book['Продажи'].iter_rows(min_row=2,values_only=True))
    finally:
        book.close()
    print(json.dumps(evaluate(data,rows),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
