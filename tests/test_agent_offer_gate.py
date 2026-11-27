import json
from types import SimpleNamespace

from recommendation.agent_offer_gate import run


def catalog():
    specs = {'why':'x86-64', 'maybe':None, 'fine':'armv8', 'bad_reserve':'x86-64', 'reserve':'armv8'}
    cards = [{'offer_id': k, 'url': f'https://example.test/{k}', 'name': k, 'item_id': 'private-sales-id'} for k in specs]
    offers = {k: {**card, 'availability':'true', 'params':json.dumps([{'name':'Совместимая архитектура/платформа','value':v}] if v else [])}
              for card, (k, v) in zip(cards, specs.items())}
    return SimpleNamespace(by_id=offers), cards


def test_model_tool_decision_removes_only_verified_conflicts_and_refills():
    contest, cards = catalog()
    calls = []
    def model(_, payload):
        assert 'private-sales-id' not in payload and 'offer_id' not in payload
        calls.append(json.loads(payload))
        return '{"tool":"catalog_architecture"}' if len(calls) == 1 else '{"remove":["c1"]}'
    result = run(contest, cards, 'B2B', 'armv8', model)
    assert len(calls) == 2 and 'catalog_architecture' not in calls[0]
    assert calls[1]['catalog_architecture']['c1']['status'] == 'incompatible'
    assert [c['offer_id'] for c in result['candidates']] == ['maybe','fine','reserve']
    assert result['evidence']['c2']['status'] == 'unknown'
    assert result['outcome'] == 'changed'
    assert run(contest, cards, 'B2B', None, lambda *_: 1/0)['candidates'] == cards


def test_model_cannot_remove_an_unknown_or_keep_a_verified_top_three_conflict():
    contest, cards = catalog()
    for reply in ('{"remove":["c2"]}', '{"remove":[]}', '{"remove":["c1","c1"]}'):
        messages = iter(['{"tool":"catalog_architecture"}', reply])
        try:
            run(contest, cards, 'B2B', 'armv8', lambda *_: next(messages))
        except ValueError:
            continue
        raise AssertionError('invalid exclusions accepted')
