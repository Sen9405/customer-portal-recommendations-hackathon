import json
import pytest
from recommendation.hermes_carousel import run, ReviewError


def profile():
    return {'as_of':'2026-08-31', 'item_history':[{'item_id':'h', 'label':'Synthetic', 'week_dates':['2026-08-24'], 'order_count':1}],
            'contest':{'candidates':[{'offer_id':str(i), 'snapshot_available':True, 'name':'Synthetic', 'vendor':'', 'source':'related_feed', 'evidence':{'history_item_ids':['h'], 'same_category':True}} for i in range(5)]}}


def answer(payload):
    ids = [x['id'] for x in payload['candidates']]
    return {'order':ids[::-1], 'source_ids':[], 'assessments':[{'candidate_id':i, 'category':'uncertain', 'history_ids':[], 'basis':['insufficient_name_detail'], 'uncertainties':['relation_unverified'], 'source_ids':[]} for i in ids]}


def test_full_permutation_and_feed():
    result = run(profile(), lambda p: json.dumps(answer(p)))
    assert [x['offer_id'] for x in result['candidates']] == ['4','3','2','1','0']
    assert result['delivery'] == 'blocked'


@pytest.mark.parametrize('kind', ['duplicate','foreign','empty','source','unavailable'])
def test_rejects_bad_results(kind):
    p = profile()
    if kind == 'unavailable': p['contest']['candidates'][0]['snapshot_available'] = False
    def fake(payload):
        a = answer(payload)
        if kind == 'duplicate': a['order'][0] = a['order'][1]
        if kind == 'foreign': a['order'][0] = 'candidate-999'
        if kind == 'empty': return ''
        if kind == 'source': a['assessments'][0]['source_ids'] = ['invented']
        return json.dumps(a)
    with pytest.raises(ReviewError): run(p, fake)


def test_duplicate_source_is_transmitted_once():
    p=profile()
    for c in p['contest']['candidates']:
        c['name']='РБД Монитор'
    def fake(payload):
        assert len(payload['sources'])==1
        assert all(c['source_ids']==['redsoft-monitor-product'] for c in payload['candidates'])
        return json.dumps(answer(payload))
    assert len(run(p,fake)['candidates'])==5


def test_five_manufacturers_sources_reach_model_and_validate():
    p=profile()
    names=['Dr.Web Enterprise Security Suite','PRO32 Endpoint Security','Astra Linux Special Edition','Платформа nanoCAD','КОМПАС-3D v24']
    for candidate,name in zip(p['contest']['candidates'],names): candidate['name']=name
    def fake(payload):
        assert len(payload['sources'])==5
        assert all(c['source_ids'] for c in payload['candidates'])
        result=answer(payload)
        for i,c in enumerate(payload['candidates']): result['assessments'][i]['source_ids']=[c['source_ids'][0]]
        return json.dumps(result)
    result=run(p,fake)
    assert all(c['assessment']['sources'] for c in result['candidates'])


def test_source_is_scoped_to_candidate():
    p = profile(); p['contest']['candidates'][0]['offer_id'] = '298271'
    def fake(payload):
        a = answer(payload)
        source_id = payload['candidates'][0]['source_ids'][0]
        assert any(source['id'] == source_id for source in payload['sources'])
        a['assessments'][0]['source_ids'] = [source_id]
        return json.dumps(a)
    result = run(p, fake)
    item = next(x for x in result['candidates'] if x['offer_id'] == '298271')
    assert item['assessment']['sources'][0]['url'].startswith('https://support.kaspersky.com/')
    assert item['assessment']['sources'][0]['permitted_claim']
    assert item['assessment']['sources'][0]['limitation']


@pytest.mark.parametrize('category', ['possible_variant', 'questionable_relation'])
@pytest.mark.parametrize('history_ids', [[], ['history-1']])
def test_relation_requires_history_or_is_downgraded(category, history_ids):
    def fake(payload):
        result = answer(payload)
        result['assessments'][0].update(category=category, history_ids=history_ids)
        return json.dumps(result)
    result = run(profile(), fake)
    assessment = next(c['assessment'] for c in result['candidates'] if c['offer_id'] == '0')
    assert assessment['category'] == (category if history_ids else 'uncertain')
    assert assessment['history_ids'] == history_ids
    assert result['delivery'] == 'blocked'


@pytest.mark.parametrize('defect', ['foreign_history', 'duplicate_history', 'empty_basis', 'empty_uncertainties', 'foreign_source', 'missing_field'])
def test_downgrade_does_not_hide_other_defects(defect):
    def fake(payload):
        result = answer(payload)
        a = result['assessments'][0]
        a['category'] = 'possible_variant'
        if defect == 'foreign_history': a['history_ids'] = ['history-999']
        if defect == 'duplicate_history': a['history_ids'] = ['history-1', 'history-1']
        if defect == 'empty_basis': a['basis'] = []
        if defect == 'empty_uncertainties': a['uncertainties'] = []
        if defect == 'foreign_source': a['source_ids'] = ['invented']
        if defect == 'missing_field': del a['history_ids']
        return json.dumps(result)
    with pytest.raises(ReviewError):
        run(profile(), fake)


def test_carousel_prompt_requires_history_and_allows_uncertain(monkeypatch):
    from recommendation import hermes_candidate_review as review
    class Captured(Exception): pass
    def capture(fd, data):
        text = data.decode()
        assert 'For possible_variant and questionable_relation' in text
        assert 'at least one relevant supplied history ID' in text
        assert 'use uncertain with an empty history_ids list' in text
        assert 'never invent an ID' in text
        raise Captured
    monkeypatch.setattr(review.os, 'write', capture)
    with pytest.raises(Captured):
        review.call_hermes({'_carousel_mode': True})
