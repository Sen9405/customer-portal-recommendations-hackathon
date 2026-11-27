from types import SimpleNamespace

import pytest

from recommendation.v5_sku_audit import stage, title_key


@pytest.mark.parametrize('known,in_train,candidates,claims,availability,owned,expected', [
    ({'x'}, True, [], {}, 'true', set(), 'reachable'),
    (set(), False, [], {}, 'true', set(), 'not_in_train'),
    (set(), True, [], {}, 'true', set(), 'no_mapping'),
    (set(), True, [{'offer_id': 'a'}, {'offer_id': 'b'}], {}, 'true', set(), 'multiple_feed_rows'),
    (set(), True, [{'offer_id': 'a'}], {'x', 'y'}, 'true', set(), 'shared_offer_id'),
    (set(), True, [{'offer_id': 'a'}], {'x'}, 'false', set(), 'unavailable_or_unknown'),
    (set(), True, [{'offer_id': 'a'}], {'x'}, 'true', {'a'}, 'owned_offer'),
    (set(), True, [{'offer_id': 'a'}], {'x'}, 'true', set(), 'renewal_title'),
])
def test_first_gate(known, in_train, candidates, claims, availability, owned, expected):
    model = SimpleNamespace(train_items={'x'} if in_train else set(), items={'x': {'candidates': candidates}},
                            claims={'a': claims}, by_id={'a': {'availability': availability, 'params': '[]',
                                                               'name': 'ПО: продление лицензии'}})
    assert stage(model, 'x', known, owned, 'B2B') == expected


def test_key_does_not_remove_edition_count_or_architecture():
    assert title_key(' ПО Pro  25 ARMv8 ') == 'по pro 25 armv8'
    assert title_key('ПО Pro 25 ARMv8') != title_key('ПО Pro 50 x86')
