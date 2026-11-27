"""Exploratory aggregate-only v4 diagnostics; run python -B -m recommendation.profile_v4_diagnostics DATA.xlsx.

SequenceMatcher normalized-name threshold 0.85 was preset as a crude proxy, not
validated identity. Unknown names are not treated as dissimilar. All names,
models and candidates come exclusively from sales rows observed by each cutoff.
Windows overlap, official test was seen, and profile was tuned on v3. No catalog,
client attributes or availability information are used. No statistical claim.
"""
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from functools import lru_cache
import json
import sys

from recommendation.profile_pipeline import ProfilePipeline, _day
from recommendation.profile_v4_evaluation import official_models, official_rank

WINDOWS = (('2026-03-30', '2026-04-06', '2026-05-25'),
           ('2026-04-27', '2026-05-04', '2026-06-29'),
           ('2026-05-25', '2026-06-01', '2026-08-31'),
           ('2026-08-31', '2026-09-07', None))


def relation(a, b):
    a, b = (' '.join(str(x or '').casefold().split()) for x in (a, b))
    if min(len(a), len(b)) < 8:
        return 'unknown'
    return 'similar' if SequenceMatcher(None, a, b).ratio() >= 0.85 else 'other'


def rank(p, user, *, component='both', own=None, edge_ok=None, blocked=frozenset(), candidate_ok=None, remove_own_pop=False):
    """Preserve history and ranking rules; optional LOO removes client graph counts."""
    seen = p.history[user]
    baskets = p.user_weeks[user]
    dates = sorted(baskets)
    latest = baskets[dates[-1]] if dates else set()
    co, seq = Counter(), Counter()
    if component in ('both', 'coorder'):
        for src in sorted(seen, key=str):
            n = p.item_orders[user][src]
            last = max(d for d in dates if src in baskets[d])
            weight = n / (n + 1) / (1 + (p.as_of - last).days / 91)
            for dst, count in p.co.get(src, {}).items():
                count -= own.co.get(src, {}).get(dst, 0) if own else 0
                if count > 0 and (edge_ok is None or edge_ok(src, dst)):
                    co[dst] += weight * count / (count + 1) / len(seen)
    if component in ('both', 'sequence'):
        for src in sorted(latest, key=str):
            for dst, count in p.seq.get(src, {}).items():
                count -= own.seq.get(src, {}).get(dst, 0) if own else 0
                if count > 0 and (edge_ok is None or edge_ok(src, dst)):
                    seq[dst] += 0.25 * count / (count + 1) / len(latest)
    targets = (set(co) | set(seq)) - seen - blocked
    ranked = sorted(targets, key=lambda x: (-(co[x] + seq[x]), -seq[x], str(x)))
    if remove_own_pop and own:
        pop = p.popularity - own.popularity
        pop_rank = sorted(pop, key=lambda x: (-pop[x], str(x)))
    else:
        pop_rank = p.pop_rank
    ranked.extend(x for x in pop_rank if x not in seen and x not in targets and x not in blocked)
    if candidate_ok is None:
        return ranked[:3]
    chosen = []
    for item in ranked:
        if candidate_ok(item):
            chosen.append(item)
            if len(chosen) == 3:
                break
    return chosen


def audit(rows, cutoff, start, end):
    cutoff, start = _day(cutoff), _day(start)
    end = _day(end) if end else None
    if start <= cutoff or (end is not None and end < start):
        raise ValueError('Invalid chronological window')
    train, names, by_user = [], {}, defaultdict(list)
    future = defaultdict(set)
    for order, user, week, item, name in rows:
        week = _day(week)
        row = (order, user, week, item)
        if week <= cutoff:
            train.append(row)
            by_user[user].append(row)
            if name and item not in names:
                names[item] = name
        elif week >= start and (end is None or week <= end):
            future[user].add(item)
    p = ProfilePipeline(train, cutoff)
    pop, _ = official_models(train, p.history)
    universe = set(p.popularity)
    targets = {u: values - p.history[u] for u, values in future.items()
               if u in p.history and values - p.history[u]}

    @lru_cache(maxsize=None)
    def rel(a, b):
        return relation(names.get(a), names.get(b))

    def classify(item, seen):
        results = {rel(item, other) for other in seen}
        if 'similar' in results:
            return 'similar'
        return 'unknown' if 'unknown' in results or not results else 'other'

    def edge_ok(a, b):
        return rel(a, b) != 'similar'

    results = {}
    for cohort in ('A', 'B'):
        users = [u for u in targets if cohort == 'A' or len(p.history[u]) == 1]
        out = {'clients': len(users), 'denominator': 3 * len(users),
               'target_count': sum(len(targets[u]) for u in users),
               'unseen_train_target_count': sum(len(targets[u] - universe) for u in users)}
        stats = defaultdict(Counter)
        for user in users:
            seen = p.history[user]
            own = ProfilePipeline(by_user[user], cutoff)
            base = rank(p, user)
            # Guard faithful reimplementation rather than assume it.
            assert base == [c['item_id'] for c in p.profile(user)['candidates']]
            choices = {'profile': base,
                       'popularity_official': official_rank(user, p.history, pop),
                       'coorder_only': rank(p, user, component='coorder'),
                       'sequence_only': rank(p, user, component='sequence'),
                       'no_similar_edges': rank(p, user, edge_ok=edge_ok),
                       'no_similar_candidates': rank(p, user, candidate_ok=lambda x: classify(x, seen) != 'similar'),
                       'loo_graph': rank(p, user, own=own),
                       'loo_graph_and_fallback': rank(p, user, own=own, remove_own_pop=True)}
            base_hits = set(base) & targets[user]
            for method, chosen in choices.items():
                hits = set(chosen) & targets[user]
                s = stats[method]
                s['hits'] += len(hits)
                s['slots'] += len(chosen)
                s['full_triples'] += len(chosen) == 3
                s['changed_lists'] += chosen != base
                s['lost_hits'] += len(base_hits - hits)
                s['gained_hits'] += len(hits - base_hits)
                for hit in hits:
                    s['hits_' + classify(hit, seen)] += 1
        for method in ('profile', 'popularity_official', 'coorder_only', 'sequence_only',
                       'no_similar_edges', 'no_similar_candidates', 'loo_graph', 'loo_graph_and_fallback'):
            s = stats[method]
            keys = ('hits', 'slots', 'full_triples', 'changed_lists', 'lost_hits', 'gained_hits',
                    'hits_similar', 'hits_other', 'hits_unknown')
            out[method] = {k: s[k] for k in keys}
            out[method]['precision_at_3'] = s['hits'] / out['denominator'] if out['denominator'] else None
        results[cohort] = out
    return results


def main():
    import openpyxl
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python -m recommendation.profile_v4_diagnostics DATA.xlsx')
    wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
    try:
        rows = [(r[0], r[1], r[2], r[3], r[5]) for r in wb['Продажи'].iter_rows(min_row=2, values_only=True)]
    finally:
        wb.close()
    output = {'limitations': __doc__, 'loo': 'Graph-only removes evaluated client coorder and sequential contributions while retaining their history and global fallback; second LOO also removes their fallback basket counts. Popularity baseline remains official unique-client ranking.',
              'edge_ablation': 'Remove similar source-target edges, keep unknown and all fallback; candidate filter separately excludes similar-to-history items including fallback; refill to 3 when possible.',
              'windows': [{'window': list(w), 'results': audit(rows, *w)} for w in WINDOWS]}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
