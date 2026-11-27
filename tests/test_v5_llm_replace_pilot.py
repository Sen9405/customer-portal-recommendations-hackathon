import random
from datetime import date

import pytest

from recommendation.profile_pipeline import ProfilePipeline
from recommendation.v5_llm_replace_pilot import case, case_with_controls, sample, validate


def test_payload_uses_local_option_keys_and_validator_rejects_foreign_ids():
    rows = [('owned', 'person', '2026-08-03', 'owned-item')]
    rows += [(f'basket{i}', f'peer{i}', '2026-08-03', f'private-id{i}') for i in range(18)]
    pipe = ProfilePipeline(rows, date(2026, 8, 31))
    base, options, payload = case(pipe, 'person', {'owned-item':'Owned product'}, random.Random(0))
    assert len(base) == 3 and len(options) == 9
    assert not (set(base) | {'owned-item'}) & set(options.values())
    assert 'person' not in repr(payload) and 'private-id' not in repr(payload)
    batch = {'q1':('person', base, options, payload)}
    assert validate('{"q1":"c1"}', batch) == {'q1':'c1'}
    assert validate('{"q1":null}', batch) == {'q1':None}
    for answer in ('{"q1":"c10"}', '{"q2":"c1"}', '{"q1":true}'):
        with pytest.raises(ValueError):
            validate(answer, batch)


def test_control_sampling_excludes_prior_and_reuses_the_same_pool():
    hist = {str(i): {'a'} if i < 30 else {'a', 'b'} for i in range(60)}
    targets = {u: {'c'} for u in hist}
    prior = sample(hist, targets)
    fresh = sample(hist, targets, seed=3, size=16, excluded=prior)
    assert len(set(fresh)) == 32 and not set(prior) & set(fresh)
    assert sum(len(hist[u]) == 1 for u in fresh) == 16
    rows = [('owned', 'person', '2026-08-03', 'owned-item')]
    rows += [(f'basket{i}', f'peer{i}', '2026-08-03', f'item{i:02}') for i in range(18)]
    pipe = ProfilePipeline(rows, date(2026, 8, 31))
    base, options, payload, first, popular = case_with_controls(pipe, 'person', {}, random.Random(0))
    assert (base, options, payload) == case(pipe, 'person', {}, random.Random(0))
    assert first == 'item03' and first in options.values() and popular in options.values()