"""Private resumable retrospective comparison; not a historical backtest.

Run: python3 -B -m recommendation.carousel_model_evaluation
Individual results stay in data/, never print client data. Current catalog and
mapping leak future information; unresolved IDs make precision only bounds.
"""
import json
import os
import random
from pathlib import Path
import tempfile

import openpyxl
from closed_review_server import load_data
from recommendation.contest_v5_evaluation import demo_metrics
from recommendation.hermes_candidate_review import ReviewError
from recommendation.hermes_carousel import run
from recommendation.profile_v4_evaluation import split_rows, official_models, official_rank, profile_rank, CUTOFF
from recommendation.profile_pipeline import ProfilePipeline
from catalog.v4_index_mapping import digest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
STATE = DATA / 'carousel-model-evaluation-private.json'
REPORT = DATA / 'carousel-model-evaluation-aggregate.json'
INPUTS = ['dataset_v5.xlsx', 'v5-catalog-mapping-20261126.json', 'catalog-20261126.sqlite']


def save(path, value):
    fd, name = tempfile.mkstemp(dir=DATA, prefix='.carousel-eval-')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf8') as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    signature = {name: digest(DATA / name) for name in INPUTS}
    # Bind resume to code as well as inputs, including the model prompt.
    for name in ['recommendation/hermes_carousel.py', 'recommendation/hermes_candidate_review.py',
                 'recommendation/contest_recommender.py', 'recommendation/public_evidence_agent.py',
                 'closed_review_server.py', 'recommendation/carousel_model_evaluation.py']:
        signature[name] = digest(ROOT / name)
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if state['signature'] != signature:
            raise SystemExit('Inputs/code changed: refusing to mix runs')
    else:
        state = {'signature': signature, 'records': {}}
    wb = openpyxl.load_workbook(DATA / INPUTS[0], read_only=True, data_only=True)
    try:
        rows = list(wb['Продажи'].iter_rows(min_row=2, values_only=True))
        groups = {r[0]: r[1] for r in wb['Клиенты'].iter_rows(min_row=2, values_only=True)}
    finally:
        wb.close()
    train, hist, future = split_rows(rows)
    targets = {u: items - hist[u] for u, items in future.items() if u in hist and items - hist[u]}
    pg = {r[3]: (r[6] if len(r) > 6 and r[6] else r[3]) for r in rows}
    data = load_data()
    handles = {u: h for h, u in data.handles.items()}
    # IDs live only in the private checkpoint. Use canonical serialization to
    # distinguish types, rather than printing or exporting IDs.
    population = sorted(targets, key=lambda x: (type(x).__name__, str(x)))
    # Fixed, reproducible simple random sample, without excluding empty carousels.
    users = random.Random(20261126).sample(population, 30)
    pop, co = official_models(train, hist)
    pipeline = ProfilePipeline(train, CUTOFF)
    def key(u):
        return json.dumps([type(u).__name__, u], ensure_ascii=False, default=str)
    error = None
    for index, u in enumerate(users, 1):
        k = key(u)
        if k in state['records']:
            continue
        profile = data.review(handles[u])
        before = profile['contest']['candidates']
        if not before:
            state['records'][k] = {'status': 'empty', 'before': [], 'after': []}
        else:
            try:
                result = run(profile)
            except ReviewError as exc:
                error = str(exc)
                if error in ('rate limited', 'provider failure', 'provider unavailable', 'timeout', 'invalid stream', 'busy'):
                    print(f'stopped: {error}; completed={len(state["records"])}/{len(users)}', flush=True)
                    break
                compact = [{'item_id': c.get('item_id')} for c in before[:3]]
                state['records'][k] = {'status': 'model_invalid_fallback', 'before': compact,
                                       'after': compact, 'error': error}
                error = None
            else:
                lookup = {c['offer_id']: c for c in before}
                after = [lookup[c['offer_id']] for c in result['candidates']]
                compact = lambda cs: [{'item_id': c.get('item_id')} for c in cs[:3]]
                state['records'][k] = {'status': 'model_ordered', 'before': compact(before),
                                       'after': compact(after), 'seconds': result['model_seconds'],
                                       'tokens': result['token_usage']}
        save(STATE, state)
        if index % 10 == 0:
            print(f'completed={len(state["records"])}/{len(users)}', flush=True)
    completed = [u for u in users if key(u) in state['records']]
    modelled = [u for u in completed if state['records'][key(u)]['status'] == 'model_ordered']
    cohorts = {'A': users, 'B': [u for u in users if len(hist[u]) == 1]}
    result = {'scope': 'current-feed retrospective, not a historical backtest',
              'completed_total': len(completed), 'model_ordered': len(modelled),
              'empty': sum(state['records'][key(u)]['status'] == 'empty' for u in completed),
              'model_invalid_fallback': sum(state['records'][key(u)]['status'] == 'model_invalid_fallback' for u in completed),
              'population_A': len(population), 'sample_A': len(users), 'sample_seed': 20261126, 'stop_reason': error,
              'metrics': {}}
    for cohort, members in cohorts.items():
        result['metrics'][cohort] = {}
        for segment in ('all', 'B2B', 'B2C'):
            full = [u for u in members if segment == 'all' or groups.get(u) == segment]
            selected = [u for u in full if u in completed]
            outcome = {'population': len(full), 'completed': len(selected), 'methods': {}}
            for method in ('before', 'after'):
                recs = {u: state['records'][key(u)][method] for u in selected}
                outcome['methods'][method] = demo_metrics(selected, targets, hist, pg, recs)
            for method in ('popularity', 'copurchase', 'profile'):
                if method == 'profile':
                    ranks = {u: profile_rank(pipeline, u, 'full') for u in selected}
                else:
                    ranks = {u: official_rank(u, hist, pop, co if method == 'copurchase' else None) for u in selected}
                recs = {u: [{'item_id': i} for i in ranks[u]] for u in selected}
                outcome['methods'][method] = demo_metrics(selected, targets, hist, pg, recs)
            result['metrics'][cohort][segment] = outcome
    save(REPORT, result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
