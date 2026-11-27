import json
import random
from datetime import date
from recommendation.sales_model_evaluation import sample, build_input, reorder
from recommendation.profile_v4_evaluation import split_rows, official_models, evaluate, CUTOFF
from recommendation.profile_pipeline import ProfilePipeline


def test_prefix_is_original_fixed_sample():
    hist = {i: {0} for i in range(40)}
    future = {i: {1} for i in range(40)}
    users, _ = sample(hist, future)
    assert users == random.Random(20261126).sample(sorted(hist, key=lambda x: (type(x).__name__, str(x))), 30)[:20]


def test_train_only_and_real_adapter_with_synthetic_response():
    rows = [('a', 'u', date(2026, 8, 1), 'seen'), ('b', 'v', date(2026, 8, 1), 'candidate'),
            ('c', 'u', date(2026, 9, 7), 'future')]
    train, hist, future = split_rows(rows)
    pop, _ = official_models(train, hist)
    p, ids = build_input('u', ProfilePipeline(train, CUTOFF), {'seen': 's', 'candidate': 'c'}, pop, hist)
    assert ids == ['candidate'] and 'future' not in str(p)
    seen = []
    def provider(payload):
        seen.append(payload)
        return json.dumps({'order': ['candidate-1'], 'source_ids': [], 'assessments': [{'candidate_id': 'candidate-1', 'category': 'uncertain', 'history_ids': [], 'basis': ['insufficient_name_detail'], 'uncertainties': ['insufficient_context'], 'source_ids': []}]})
    out, status = reorder(p, ids, provider)
    assert out == ids and status == 'model_ordered'
    assert seen[0]['sources'] == [] and 'future' not in json.dumps(seen)
    assert reorder(p, ids, lambda _: '{}') == (ids, 'model_invalid_fallback')
    assert evaluate(['u'], {'u': {'candidate'}}, lambda _: out)['precision_at_3'] == 1/3


def test_transient_propagates():
    from recommendation.hermes_candidate_review import ReviewError
    from pytest import raises
    p = {'as_of': CUTOFF.isoformat(), 'item_history': [], 'contest': {'candidates': [{'offer_id': 'x', 'name': '', 'source': 'popularity', 'snapshot_available': True}]}}
    def fail(_): raise ReviewError('rate limited')
    with raises(ReviewError): reorder(p, ['x'], fail)
