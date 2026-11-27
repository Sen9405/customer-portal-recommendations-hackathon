"""Coverage-first, aggregate-only pilot of new-offer discovery against v5.

The current snapshot, mapping and vendor documents cannot support a point-in-time
historical claim. Optional model calls are allowed only after a coverage prepass.
"""
import argparse
from collections import Counter
import contextlib
import importlib.util
import io
import json
import runpy
import sys

from recommendation.contest_recommender import ContestRecommender
from recommendation.new_offer_agent import find_offers, paths_for, propose, refresh
from recommendation.profile_v4_evaluation import CUTOFF, profile_rank, split_rows
from recommendation.profile_v5_comparison import compare
from recommendation.public_research import PublicResearch


def coverage(contest, user, base_ids, seen, audience, train_map, evidence, *, expanded=False):
    base_offers = [o for item in base_ids if (o := contest._unique(item))]
    owned, excluded, paths = paths_for(contest, user, base_offers, audience, expanded=expanded)
    eligible, by_rule = {}, {}
    found, supported, mapped = set(), set(), set()
    for tool in paths:
        state, pool = find_offers(contest, tool, owned, excluded, audience)
        ids = {o['offer_id'] for o in pool}
        found.update(ids)
        has_evidence = any(c['status'] == 'confirmed' and c['evidence']
                           for c in evidence.get(tool, {}).get('candidates', ()))
        if has_evidence:
            supported.update(ids)
            mapped.update(ids & train_map.keys())
            # Match the actual agent's per-tool shortlist order and limit.
            allowed = [o['offer_id'] for o in pool if o['offer_id'] in train_map
                       and train_map[o['offer_id']] not in seen and train_map[o['offer_id']] not in base_ids]
            if len(base_ids) == 3 and allowed:
                eligible[tool] = set(allowed[:8])
        by_rule[tool] = Counter(profiles_with_direction=1, profiles_blocked_owned=state == 'already_owned_family',
                                profiles_with_current_offer=bool(ids), profiles_eligible_for_model=tool in eligible)
    counts = Counter(profiles_with_direction=bool(paths), profiles_with_current_offer=bool(found),
                     profiles_with_source_and_offer=bool(supported), profiles_with_train_mapping=bool(mapped),
                     profiles_eligible_for_model=bool(eligible), current_offers=len(found),
                     source_supported_offers=len(supported), eligible_offers=len(set().union(*eligible.values())))
    return base_offers, eligible, counts, by_rule


def main():
    import openpyxl
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('xlsx','baseline','mapping','index'):
        parser.add_argument(arg)
    parser.add_argument('--adapter', help='trusted local model adapter; never loaded when coverage is empty')
    parser.add_argument('--expanded', action='store_true', help='include additional fixed vendor-backed relations')
    args = parser.parse_args()
    evidence = refresh(PublicResearch(), expanded=args.expanded)
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    try:
        rows = list(wb['Продажи'].iter_rows(min_row=2,values_only=True))
        groups = {r[0]:r[1] for r in wb['Клиенты'].iter_rows(min_row=2,values_only=True)}
    finally: wb.close()
    train, hist, future = split_rows(rows)
    rec = ContestRecommender(train,CUTOFF,args.mapping,args.index,workbook_path=args.xlsx)
    train_map = {o['offer_id']:i for i in {r[3] for r in train} if (o:=rec._unique(i))}
    argv = sys.argv
    try:
        sys.argv = [args.baseline,args.xlsx]
        with contextlib.redirect_stdout(io.StringIO()):
            original = runpy.run_path(args.baseline)
    finally: sys.argv = argv
    official = compare(rows,groups,original)
    assert not any(official['original_list_mismatches_A_plus_B'].values())
    assert not any(official['original_metric_mismatches_segments'].values())
    aggregate = {c:Counter() for c in ('A','B')}
    control = {c:Counter(changed=0, added=0, lost=0, reachable_targets=0, possible_added_hits=0)
               for c in aggregate}
    rules = {tool: {c:Counter(profiles_with_direction=0, profiles_blocked_owned=0,
                             profiles_with_current_offer=0, profiles_eligible_for_model=0)
                    for c in aggregate} for tool in evidence}
    distinct_offers = {tool:set() for tool in evidence}
    cases = []
    for u, seen in hist.items():
        targets = future.get(u,set()) - seen
        if not targets: continue
        cohorts = ('A','B') if len(seen)==1 else ('A',)
        audience = groups.get(u)
        base = profile_rank(rec.pipeline,u,'full')
        offers, allowed, stats, by_rule = coverage(rec,u,base,seen,audience,train_map,evidence,expanded=args.expanded)
        for c in cohorts:
            aggregate[c].update(stats)
            aggregate[c].update(clients=1, profile_hits=len(set(base)&targets))
            for tool, counts in by_rule.items():
                rules[tool][c].update(counts)
        for tool, ids in allowed.items():
            distinct_offers[tool].update(ids)
        if allowed:
            cases.append((u,cohorts,audience,base,offers,allowed,targets))
            # Same visible pool as the agent. Choose without reading targets;
            # use targets afterwards only to score and report the ceiling.
            pool = {train_map[o] for ids in allowed.values() for o in ids}
            pick = min(pool, key=lambda i: (-rec.pipeline.distinct_clients[i], i))
            for c in cohorts:
                control[c].update(changed=1, added=pick in targets and base[2] not in targets,
                                  lost=pick not in targets and base[2] in targets,
                                  reachable_targets=bool(pool & targets),
                                  possible_added_hits=bool(pool & targets) and base[2] not in targets)
    calls = 0
    completed = 0
    failure = None
    if cases and args.adapter:
        spec = importlib.util.spec_from_file_location('local_pilot_adapter',args.adapter)
        if spec is None or spec.loader is None: parser.error('invalid adapter source')
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        try:
            with adapter.client() as client:
                def invoke(instruction,payload):
                    nonlocal calls
                    calls += 1
                    return adapter.invoke(client,instruction,payload)[0]
                for u,cohorts,audience,base,offers,allowed,targets in cases[:8]:
                    result = propose(rec,u,offers,audience,evidence,invoke,allowed_by_tool=allowed,expanded=args.expanded)
                    candidate = result['candidate']
                    new = [*base[:2],train_map[candidate['offer_id']]] if candidate else base
                    before,after = set(base)&targets,set(new)&targets
                    for c in cohorts:
                        aggregate[c].update(model_profiles_tested=1, changed=base!=new, added=len(after-before), lost=len(before-after))
                    completed += 1
        except Exception as exc:
            status = getattr(exc, 'status_code', None)
            failure = f'http_{status}' if type(status) is int else type(exc).__name__
    for c, stats in aggregate.items():
        baseline = official['results'][c]['all']
        assert stats['profile_hits']==baseline['profile']['hits']
        stats.update(popularity_hits=baseline['popularity']['hits'],copurchase_hits=baseline['copurchase']['hits'], denominator=3*stats['clients'],
                     changed=0,added=0,lost=0)
    totals = {c:{**s,'experimental_hits':s['profile_hits']+s['added']-s['lost']
                 if completed and completed == len(cases) else None} for c,s in aggregate.items()}
    print(json.dumps({'cohorts':totals,'eligible_cases':len(cases),'completed_model_cases':completed,'model_calls':calls,'error':failure,
                      'same_pool_popularity_control': {c:{**s,'hits':aggregate[c]['profile_hits']+s['added']-s['lost']}
                                                       for c,s in control.items()},
                      'relations':{tool:{'source_status': next((c['status'] for c in evidence[tool]['candidates']), evidence[tool]['outcome']),
                                         'distinct_eligible_train_items':len(distinct_offers[tool]), 'cohorts':groups}
                                   for tool,groups in rules.items()},
                      'public_loads':sum(t['outcome']=='fetched' for r in evidence.values() for t in r['trace']),
                      'confirmed_sources':sum(any(c['status']=='confirmed' and c['evidence'] for c in r['candidates']) for r in evidence.values()),
                      'note':'Effect is conditional on eligible replacements. This uses current evidence and is not the official historical protocol.'},indent=2))
    if failure: raise SystemExit(1)


if __name__=='__main__': main()
