"""Synthetic checks for the private paired experiment harness."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    'paired_quality_harness', Path(__file__).resolve().parents[1] / 'data' / 'paired_quality_20261127.py'
)
pytestmark = pytest.mark.skipif(not spec.origin or not Path(spec.origin).exists(), reason='private harness not present')
if spec.origin and Path(spec.origin).exists():
    paired = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(paired)


def test_same_pool_with_opposite_input_order():
    case = {'profile': {'item_history': []}, 'pool': [
        {'offer_id': 'synthetic-a'}, {'offer_id': 'synthetic-b'}, {'offer_id': 'synthetic-c'}
    ]}
    forward = paired.ordered_profile(case, 'forward')['contest']['candidates']
    reverse = paired.ordered_profile(case, 'reverse')['contest']['candidates']
    assert forward == list(reversed(reverse))
    assert case['pool'] == forward
    case['pool'].append({'offer_id': 'synthetic-a'})
    with pytest.raises(ValueError):
        paired.ordered_profile(case, 'forward')


def test_comparison_preserves_candidate_identity():
    def item(oid, category):
        return {'offer_id': oid, 'assessment': {'category': category, 'history_ids': [], 'basis': ['name_similarity'], 'sources': []}}
    a = {'candidates': [item('synthetic-a', 'uncertain'), item('synthetic-b', 'possible_variant')]}
    b = {'candidates': list(reversed(a['candidates']))}
    result = paired.compare(a, b)
    assert result['top_overlap'] == 2
    assert result['same_category'] == 2
    assert result['forward']['name_similarity_basis'] == 2
    with pytest.raises(ValueError):
        paired.compare(a, {'candidates': [item('synthetic-a', 'uncertain')]})


def test_history_signature_ignores_order_and_duplicates():
    first = [{'item_id': 1}, {'item_id': 2}, {'item_id': 2}]
    second = [{'item_id': 2}, {'item_id': 1}]
    assert paired.signature(first) == paired.signature(second)
    assert paired.signature([{'item_id': 1}, {}]) is None


def test_empty_status_does_not_disappear():
    manifest = {'cases': [{'status': 'no_paths', 'pool': []}, {'status': 'no_candidates', 'pool': []}, {'status': 'explored', 'pool': [{'offer_id': 'synthetic'}]}]}
    result = paired.run(manifest, max_cases=0)
    assert [x['status'] for x in result['results']] == ['no_paths', 'no_candidates', 'not_run']
    assert result['model_calls'] == 0
