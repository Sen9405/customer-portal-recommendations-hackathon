"""One fixed exploratory score on the previously observed early window.

Run: python -B -m recommendation.weighted_popularity_experiment DATA.xlsx
No current catalog or official test. Results are aggregate only.
"""
import json
import math
import sys
from collections import Counter, defaultdict
import openpyxl
from recommendation.profile_pipeline import ProfilePipeline, _day
from recommendation.profile_v4_evaluation import official_models, official_rank
from recommendation.early_popularity_experiment import CUTOFF, START, END, combine


def weighted_rank(p, user, pop, weight=0.5):
    """Equal weights for relative personal score and log distinct-client popularity.

    Relative personal score is normalized by this user's best candidate; zero if
    no association. Popularity is log1p(count)/log1p(max train count). Every train
    item is eligible except previously bought; tie breaks by personal score then id.
    """
    if not 0 <= weight <= 1:
        raise ValueError('weight outside [0,1]')
    seen = p.history.get(user, set())
    dates = sorted(p.user_weeks.get(user, {}))
    latest = p.user_weeks[user][dates[-1]] if dates else set()
    co, seq = Counter(), Counter()
    for src in sorted(seen, key=str):
        n = p.item_orders[user][src]
        last = max(d for d in dates if src in p.user_weeks[user][d])
        factor = n / (n + 1) / (1 + (p.as_of - last).days / 91) / len(seen)
        for dst, count in p.co.get(src, {}).items():
            co[dst] += factor * count / (count + 1)
    for src in sorted(latest, key=str):
        for dst, count in p.seq.get(src, {}).items():
            seq[dst] += .25 * count / (count + 1) / len(latest)
    candidates = set(pop) - seen
    personal = {x: co[x] + seq[x] for x in candidates}
    mx = max(personal.values(), default=0)
    pm = max(pop.values(), default=0)
    scale = math.log1p(pm) if pm else 1
    return sorted(candidates, key=lambda x: (-(weight * (personal[x] / mx if mx else 0) +
                         (1-weight) * math.log1p(pop[x]) / scale), -personal[x], str(x)))[:3]


def run(rows, groups):
    train, future = [], defaultdict(set)
    for order, user, week, item in rows:
        week = _day(week)
        if week <= CUTOFF:
            train.append((order, user, week, item))
        elif START <= week <= END:
            future[user].add(item)
    p = ProfilePipeline(train, CUTOFF)
    pop, _ = official_models(train, p.history)
    targets = {u: items - p.history[u] for u, items in future.items()
               if u in p.history and items - p.history[u]}
    choices = {}
    for u in targets:
        profile = [c['item_id'] for c in p.profile(u)['candidates']]
        popularity = official_rank(u, p.history, pop)
        choices[u] = {'profile': profile, 'popularity': popularity,
                      'two_profile_one_popularity': combine(profile, popularity),
                      'weighted_equal': weighted_rank(p, u, pop)}
    result = {}
    for cohort in ('A', 'B'):
        result[cohort] = {}
        for segment in ('all', 'B2B', 'B2C'):
            users = [u for u in targets if (cohort == 'A' or len(p.history[u]) == 1)
                     and (segment == 'all' or groups.get(u) == segment)]
            methods = {}
            for method in ('profile','popularity','two_profile_one_popularity','weighted_equal'):
                hits = sum(len(set(choices[u][method]) & targets[u]) for u in users)
                methods[method] = {'hits': hits, 'slots': len(users)*3,
                    'precision': hits/(len(users)*3) if users else None,
                    'nonempty': sum(bool(choices[u][method]) for u in users),
                    'full': sum(len(choices[u][method]) == 3 for u in users)}
            result[cohort][segment] = {'clients':len(users), 'methods':methods}
    return {'window': {'train_end':str(CUTOFF), 'validation_start':str(START), 'validation_end':str(END)},
            'formula':'0.5 * personal/max_personal + 0.5 * log1p(distinct_clients)/log1p(max_distinct_clients)',
            'limitations':'Exploratory known window, no official test, no catalog or availability; undated segments reported only; B subset A; equal weights are illustrative, not selected or validated independently.', 'results':result}


def main():
    wb=openpyxl.load_workbook(sys.argv[1],read_only=True,data_only=True)
    try:
        rows=[r[:4] for r in wb['Продажи'].iter_rows(min_row=2,values_only=True)]
        groups={r[0]:r[1] for r in wb['Клиенты'].iter_rows(min_row=2,values_only=True)}
        print(json.dumps(run(rows,groups),ensure_ascii=False,indent=2))
    finally:
        wb.close()

if __name__ == '__main__': main()
