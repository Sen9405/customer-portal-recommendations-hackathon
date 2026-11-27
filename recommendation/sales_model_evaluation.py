"""Partial sales-only model reranking, NOT the feed carousel or a blind test.

Uses the existing Hermes carousel adapter solely as a permutation validator; its
snapshot_available flag is a schema sentinel, not an availability observation.
No source documents, current feed, customer attributes, or test labels in payload.
"""
import hashlib
import json
import os
import random
from pathlib import Path
from unittest.mock import patch

import openpyxl
from recommendation.profile_v4_evaluation import CUTOFF, split_rows, official_models, official_rank, profile_rank, evaluate
from recommendation.profile_pipeline import ProfilePipeline, _day
from recommendation.hermes_carousel import run
from recommendation.hermes_candidate_review import ReviewError

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
STATE = DATA / 'sales-model-evaluation-20261126-private.json'
FILES = ('recommendation/sales_model_evaluation.py', 'recommendation/hermes_carousel.py',
         'recommendation/hermes_candidate_review.py', 'recommendation/public_evidence_agent.py',
         'recommendation/closed_agent_workflow.py', 'recommendation/profile_pipeline.py',
         'recommendation/profile_v4_evaluation.py', 'recommendation/carousel_model_evaluation.py',
         'data/dataset_v5.xlsx', 'data/organizer_baseline_v5.py')
TRANSIENT = {'rate limited', 'provider failure', 'provider unavailable', 'timeout', 'invalid stream', 'busy'}


def signature():
    return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in FILES}


def sample(hist, future):
    targets = {u: items - hist[u] for u, items in future.items() if u in hist and items - hist[u]}
    population = sorted(targets, key=lambda x: (type(x).__name__, str(x)))
    return random.Random(20261126).sample(population, 30)[:20], targets


def build_input(user, pipeline, names, pop, hist):
    profile = pipeline.profile(user)
    first = [c['item_id'] for c in profile['candidates']]
    rest = sorted((i for i in pop if i not in hist[user] and i not in first),
                  key=lambda i: (-pop[i], str(i)))
    ids = (first + rest)[:20]
    assert len(ids) == len(set(ids)) and set(ids) <= set(pop) and not set(ids) & hist[user]
    original = {c['item_id']: c for c in profile['candidates']}
    cs = []
    for index, item in enumerate(ids):
        c = original.get(item, {})
        ev = c.get('evidence', {})
        related = [x['history_item'] for x in ev.get('coorder', [])] + [x['last_week_item'] for x in ev.get('sequential', [])]
        cs.append({'offer_id': f'sales-candidate-{index+1}', 'name': names.get(item, ''),
                   'source': 'profile' if item in original else 'popularity',
                   'evidence': {'history_item_ids': list(dict.fromkeys(related)), 'method': c.get('method')},
                   'snapshot_available': True})  # adapter schema only, never sent to model
    history = [{**h, 'label': names.get(h['item_id'], '')} for h in profile['item_history']]
    return {'as_of': CUTOFF.isoformat(), 'item_history': history, 'contest': {'candidates': cs}}, ids


def reorder(profile, ids, provider=None):
    if not ids:
        return [], 'empty'
    try:
        # Do not introduce non-dated external evidence by name or offer ID.
        with patch('recommendation.public_evidence_agent.evidence_for_offers', return_value={}):
            result = run(profile, provider=provider)
        positions = {c['offer_id']: i for c, i in zip(profile['contest']['candidates'], ids)}
        return [positions[c['offer_id']] for c in result['candidates']], 'model_ordered'
    except ReviewError as e:
        if str(e) in TRANSIENT:
            raise
        return ids, 'model_invalid_fallback'


def save_exclusive(value):
    # First write uses O_EXCL; further updates operate only on our open fd.
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(fd, 'w', encoding='utf8')


def checkpoint(f, state):
    f.seek(0); json.dump(state, f, ensure_ascii=False); f.truncate(); f.flush(); os.fsync(f.fileno())


def main():
    sig = signature()
    wb = openpyxl.load_workbook(DATA / 'dataset_v5.xlsx', read_only=True, data_only=True)
    try:
        rows = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
    finally:
        wb.close()
    train, hist, future = split_rows(rows)
    users, targets = sample(hist, future)
    names = {r[3]: str(r[5] or '') for r in rows if _day(r[2]) <= CUTOFF}
    pop, co = official_models(train, hist)
    pipeline = ProfilePipeline(train, CUTOFF)
    records = []
    state = {'signature': sig, 'seed': 20261126, 'records': records, 'attempted': 0, 'stop_reason': None}
    with save_exclusive(state) as f:
        checkpoint(f, state)
        for u in users:
            if signature() != sig:
                state['stop_reason'] = 'code_or_inputs_changed'; break
            profile, ids = build_input(u, pipeline, names, pop, hist)
            if ids:
                state['attempted'] += 1
                checkpoint(f, state)
            try:
                order, status = reorder(profile, ids)
            except ReviewError as e:
                state['stop_reason'] = str(e); break
            if signature() != sig:
                state['stop_reason'] = 'code_or_inputs_changed_during_call'; break
            records.append({'user': u, 'status': status, 'rank': order[:3]})
            checkpoint(f, state)
            print('completed=%d/20' % len(records), flush=True)
        checkpoint(f, state)
    completed = [r['user'] for r in records]
    by_user = {r['user']: r for r in records}
    summary = {'seed': 20261126, 'sample': len(users), 'completed': len(records),
               'attempted': state['attempted'], 'stop_reason': state['stop_reason'], 'statuses': {s: sum(r['status'] == s for r in records) for s in ('model_ordered', 'model_invalid_fallback', 'empty')},
               'signature_sha256': hashlib.sha256(json.dumps(sig, sort_keys=True).encode()).hexdigest(), 'cohorts': {}}
    for group, members in [('A', completed), ('B', [u for u in completed if len(hist[u]) == 1])]:
        methods = {'model_with_fallback': lambda u: by_user[u]['rank'],
                   'popularity': lambda u: official_rank(u, hist, pop),
                   'copurchase': lambda u: official_rank(u, hist, pop, co),
                   'profile': lambda u: profile_rank(pipeline, u, 'full')}
        summary['cohorts'][group] = {m: evaluate(members, targets, fn) for m, fn in methods.items()}
        valid = [u for u in members if by_user[u]['status'] == 'model_ordered']
        summary['cohorts'][group]['model_valid_only'] = evaluate(valid, targets, methods['model_with_fallback'])
    summary['full_sample_baselines'] = {name: evaluate(users, targets, fn) for name, fn in {
        'popularity': lambda u: official_rank(u, hist, pop),
        'copurchase': lambda u: official_rank(u, hist, pop, co),
        'profile': lambda u: profile_rank(pipeline, u, 'full')}.items()}
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
