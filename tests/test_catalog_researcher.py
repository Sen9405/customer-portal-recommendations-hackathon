import pytest
from recommendation.catalog_researcher import explore


def setup():
    class Catalog:
        items = {'1': {'candidates': [{'offer_id': 'bought'}]}}
        def _unique(self, item):
            return {'category_id': 'c', 'vendor': 'Vendor'} if item == '1' else None
    c = Catalog()
    a = {'offer_id': 'bought', 'name': 'Past', 'vendor': 'Vendor', 'category_id':'c', 'availability':'true'}
    b = {'offer_id': 'existing', 'name': 'Main', 'vendor': 'Vendor', 'category_id':'c', 'availability':'true'}
    d = {'offer_id': 'new', 'name': 'New', 'vendor': 'Vendor', 'category_id':'c', 'availability':'true'}
    unavailable = {'offer_id': 'no', 'name':'No', 'vendor':'Vendor','category_id':'c','availability':'false'}
    c.by_id = {o['offer_id']:o for o in (a,b,d,unavailable)}
    c.by_category = {'c':[a,b,d,unavailable]}
    c.by_vendor = {'vendor':[a,b,d,unavailable]}
    profile = {'as_of':'2026-08-31', 'item_history':[{'item_id':1,'label':'Past','week_dates':['2026-08-24'],'order_count':1}],
               'contest':{'candidates':[{'offer_id':'existing'}]}}
    return c, profile


def test_two_stages_separate_and_exclude_known():
    catalog, profile = setup()
    calls = []
    def rank(p):
        cs = p['contest']['candidates']; calls.append(cs)
        return {'candidates': [{'offer_id': c['offer_id'], 'assessment': {'category':'possible_new_product','history_ids':['history-1'],'basis':['apparent_product_role'],'uncertainties':['relation_unverified'],'sources':[]}} for c in cs]}
    result = explore(profile, catalog, rank)
    assert len(calls) == 2
    assert [x['offer_id'] for x in result['findings']] == ['new']
    assert result['findings'][0]['grounding_status'] == 'customer_task_unknown'
    assert result['examined'] == 1 and result['delivery'] == 'blocked'
    assert len(result['actions']) == 2
    assert calls[1][0]['evidence']['history_item_ids'] == [1]
    assert profile['contest']['candidates'] == [{'offer_id':'existing'}]


def test_uncertain_not_promoted():
    catalog, profile = setup()
    def rank(p):
        return {'candidates':[{'offer_id':c['offer_id'],'assessment':{'category':'uncertain'}} for c in p['contest']['candidates']]}
    assert explore(profile,catalog,rank)['findings'] == []


def test_bad_path_order_rejected():
    catalog, profile = setup()
    with pytest.raises(ValueError):
        explore(profile,catalog,lambda p:{'candidates':[{'offer_id':'path-999'}]})


def test_no_candidates_retains_first_phase_usage():
    catalog, profile = setup()
    catalog.by_category = {}; catalog.by_vendor = {}
    def rank(p):
        return {'candidates':[{'offer_id': c['offer_id']} for c in p['contest']['candidates']],
                'token_usage':{'input': 42, 'output': 3, 'total': 45}, 'model_seconds':2.1}
    result = explore(profile, catalog, rank)
    assert result['status'] == 'no_candidates'
    assert result['model_usage']['total'] == 45 and result['model_seconds'] == 2.1


def test_missing_mapping_returns_no_paths_without_model():
    catalog, profile = setup(); catalog.items = {}
    catalog._unique = lambda x: None
    assert explore(profile, catalog, lambda p: pytest.fail('called model'))['status'] == 'no_paths'


def test_path_history_must_match_model_hypothesis_and_weak_basis_is_counted():
    catalog, profile = setup()
    profile['item_history'].append({'item_id': 2, 'label': 'Other'})
    def rank_for(history_id):
        def rank(p):
            return {'candidates': [{'offer_id': c['offer_id'], 'assessment': {
                'category': 'possible_new_product', 'history_ids': [history_id],
                'basis': ['name_difference', 'apparent_product_role']}}
                for c in p['contest']['candidates']]}
        return rank
    wrong = explore(profile, catalog, rank_for('history-2'))
    assert wrong['findings'] == [] and wrong['weak_basis_count'] == 0
    matched = explore(profile, catalog, rank_for('history-1'))
    assert len(matched['findings']) == 1 and matched['weak_basis_count'] == 1
    assert matched['delivery'] == 'blocked'
    assert any('не обосновывают связь' in x for x in matched['limitations'])
