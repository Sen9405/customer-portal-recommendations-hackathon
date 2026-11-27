import json

import pytest

from recommendation import hermes_candidate_review as agent
from test_closed_review_server import fixture


def subject(category='possible_variant'):
    return {'category': category, 'history_ids': ['history-1'],
            'basis': ['name_similarity'], 'uncertainties': ['relation_unverified']}


def test_synthetic_end_to_end_subject_hypotheses():
    data = fixture()
    handle = next(h for h, u in data.handles.items()
                  if data.pipeline.history.get(u) and data.review(h)['candidates'])
    p = data.review(handle)
    def provider(payload):
        assert 'product_group' not in json.dumps(payload)
        return json.dumps({'diagnostics': [
            {'candidate_id': c['id'], 'category': 'statistical_signal',
             'basis_ids': [c['evidence'][0]['id']],
             'subject': subject(('possible_variant', 'possible_new_product', 'questionable_relation')[i % 3])}
            for i, c in enumerate(payload['candidates'])], 'source_ids': []})
    result = agent.run(p, provider)
    assert result['status'] == 'blocked' and result['recommendations'] == []
    assert len(result['subject_analysis']) == len(p['candidates'])
    for entry in result['subject_analysis']:
        assert entry['history_item_ids'] == [p['item_history'][0]['item_id']]
        assert entry['assessment'] == 'name_based_hypothesis'
        assert 'live_availability' in entry['mandatory_unknowns']
        assert 'compatibility_and_entitlement' in entry['mandatory_unknowns']


@pytest.mark.parametrize('change', [
    {'history_ids': ['history-999']}, {'history_ids': []},
    {'history_ids': ['history-1', 'history-1']}, {'basis': []},
    {'basis': ['compatible']}, {'basis': ['<script>']},
    {'uncertainties': []}, {'uncertainties': ['available']},
    {'category': 'confirmed_variant'}, {'category': []},
    {'explanation': 'compatible'},
])
def test_reject_invalid_or_untrusted_subject(change):
    s = subject(); s.update(change)
    diagnostic = {'candidate_id': 'candidate-1', 'category': 'uncertain', 'basis_ids': [], 'subject': s}
    candidates = [{'id': 'candidate-1', 'evidence': []}]
    with pytest.raises(agent.ReviewError):
        agent.parse_selection(json.dumps({'diagnostics': [diagnostic], 'source_ids': []}), candidates, [], {'history-1'})


def test_legacy_response_still_accepted_without_subject():
    diagnostic = {'candidate_id': 'candidate-1', 'category': 'uncertain', 'basis_ids': []}
    assert agent.parse_selection(json.dumps({'diagnostics': [diagnostic], 'source_ids': []}),
                                 [{'id': 'candidate-1', 'evidence': []}], [])['diagnostics'] == [diagnostic]
