import json
import pytest
from recommendation.hermes_candidate_review import parse_selection, ReviewError

CANDIDATES = [{'id': 'candidate-1', 'evidence': [{'id': 'candidate-1-basis-1'}]},
              {'id': 'candidate-2', 'evidence': [{'id': 'candidate-2-basis-1'}]}]
GOOD = [{'candidate_id': 'candidate-1', 'category': 'statistical_signal', 'basis_ids': ['candidate-1-basis-1']},
        {'candidate_id': 'candidate-2', 'category': 'uncertain', 'basis_ids': []}]


def test_valid_bounded_diagnostics():
    assert parse_selection(json.dumps({'diagnostics': GOOD, 'source_ids': []}), CANDIDATES, [])['diagnostics'] == GOOD


@pytest.mark.parametrize('diagnostics,sources', [
    (GOOD[:1], []),
    ([GOOD[0], GOOD[0]], []),
    ([dict(GOOD[0], basis_ids=['candidate-2-basis-1']), GOOD[1]], []),
    ([dict(GOOD[0], basis_ids=[]), GOOD[1]], []),
    ([dict(GOOD[0], category='approved'), GOOD[1]], []),
    ([dict(GOOD[0], explanation='compatible'), GOOD[1]], []),
    (GOOD, ['https://evil.example']),
    (GOOD, ['x', 'x']),
])
def test_reject_injected_or_invalid_response(diagnostics, sources):
    with pytest.raises((ReviewError, ValueError)):
        parse_selection(json.dumps({'diagnostics': diagnostics, 'source_ids': sources}), CANDIDATES, ['x'])


def test_duplicate_json_key_rejected():
    with pytest.raises(ValueError):
        parse_selection('{"diagnostics":[],"diagnostics":[],"source_ids":[]}', [], [])
