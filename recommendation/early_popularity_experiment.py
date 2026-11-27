"""Exploratory early validation only; no official test or catalog.

Run: python3 -B -m recommendation.early_popularity_experiment data/dataset_v5.xlsx
Fixed hybrid: first two profile items then first unseen distinct-client popularity
item, refilling from profile and popularity if needed. Groups only for reporting.
"""
import json
import random
import sys
from collections import Counter, defaultdict
from functools import lru_cache

import openpyxl
from recommendation.profile_pipeline import ProfilePipeline, _day
from recommendation.profile_v4_evaluation import official_models, official_rank
from recommendation.profile_v4_diagnostics import rank, relation

CUTOFF = _day('2026-05-25')
START = _day('2026-06-01')
END = _day('2026-08-31')


def combine(profile, pop):
    # Reserve position 3 for the highest-ranked popularity item not already chosen.
    return list(dict.fromkeys(profile[:2] + pop + profile[2:]))[:3]


def run(rows, groups):
    train, names, future, own_rows = [], {}, defaultdict(set), defaultdict(list)
    for order, user, week, item, name in rows:
        week = _day(week)
        if week <= CUTOFF:
            row = (order, user, week, item)
            train.append(row)
            own_rows[user].append(row)
            if name and item not in names:
                names[item] = name
        elif START <= week <= END:
            future[user].add(item)
    p = ProfilePipeline(train, CUTOFF)
    pop, _ = official_models(train, p.history)
    targets = {u: items - p.history[u] for u, items in future.items()
               if u in p.history and items - p.history[u]}

    @lru_cache(maxsize=None)
    def edge_ok(a, b):
        return relation(names.get(a), names.get(b)) != 'similar'

    choices = {}
    for user in targets:
        profile = [c['item_id'] for c in p.profile(user)['candidates']]
        assert profile == rank(p, user)
        popularity = official_rank(user, p.history, pop)
        own = ProfilePipeline(own_rows[user], CUTOFF)
        choices[user] = {
            'profile': profile,
            'popularity': popularity,
            'two_profile_one_popularity': combine(profile, popularity),
            'profile_no_similar_edges': rank(p, user, edge_ok=edge_ok),
            'profile_loo_graph': rank(p, user, own=own),
        }
    output = {}
    for cohort in ('A', 'B'):
        output[cohort] = {}
        for segment in ('all', 'B2B', 'B2C', 'other_or_missing'):
            users = sorted((u for u in targets if (cohort == 'A' or len(p.history[u]) == 1)
                            and (segment == 'all' or (groups.get(u) == segment if segment != 'other_or_missing'
                                 else groups.get(u) not in ('B2B', 'B2C')))), key=str)
            stats = {}
            for method in ('profile', 'popularity', 'two_profile_one_popularity',
                           'profile_no_similar_edges', 'profile_loo_graph'):
                hits = sum(len(set(choices[u][method]) & targets[u]) for u in users)
                n = len(users)
                stats[method] = {'hits': hits, 'denominator': 3*n,
                                 'precision_at_3': hits/(3*n) if n else None,
                                 'nonempty': sum(bool(choices[u][method]) for u in users),
                                 'full_triples': sum(len(choices[u][method]) == 3 for u in users)}
            # Paired fixed-model client bootstrap; informative only, no model selection adjustment.
            differences = [len(set(choices[u]['profile']) & targets[u]) -
                           len(set(choices[u]['popularity']) & targets[u]) for u in users]
            rng = random.Random(20261126)
            if users:
                replicates = sorted(sum(rng.choices(differences, k=len(users))) / (3*len(users))
                                    for _ in range(10000))
                interval = [replicates[249], replicates[9749]]
            else:
                interval = None
            output[cohort][segment] = {'clients': len(users),
                'novel_targets': sum(len(targets[u]) for u in users),
                'cold_targets': sum(len(targets[u] - set(pop)) for u in users),
                'profile_minus_popularity_bootstrap_95': interval, 'methods': stats}
    return {'window': {'train_week_max': str(CUTOFF), 'validation_week_min': str(START),
                       'validation_week_max': str(END)},
            'train_rows': len(train), 'train_clients': len(p.history), 'train_items': len(pop),
            'limitations': 'Exploratory known window; outcome-conditioned cohort, B subset A. Undated group used for reporting only. No catalog or availability. Profile fallback counts client-week-order baskets, official popularity distinct clients. Bootstrap fixed models, not model selection or training uncertainty. Similarity threshold 0.85 is a proxy, unknown retained. LOO removes own graph contributions but not history or fallback. Profile tuned on earlier work; official test previously observed and not used here.',
            'results': output}


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 -B -m recommendation.early_popularity_experiment DATA.xlsx')
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        rows = ((r[0], r[1], r[2], r[3], r[5]) for r in wb['Продажи'].iter_rows(min_row=2, values_only=True))
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
        print(json.dumps(run(rows, groups), ensure_ascii=False, indent=2))
    finally:
        wb.close()


if __name__ == '__main__':
    main()
