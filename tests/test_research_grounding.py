import json

from recommendation.catalog_researcher import explore
from recommendation.hermes_carousel import run
from test_catalog_researcher import setup


def test_reviewed_document_prioritizes_but_does_not_prove_relation():
    catalog, profile = setup()
    history = {'offer_id': 'bought', 'name': 'PRO32 Endpoint Security', 'vendor': 'Vendor', 'category_id': 'c', 'availability': 'true'}
    catalog._unique = lambda item: history
    other = [{'offer_id': f'{i:02}', 'name': 'Unrelated', 'vendor': 'Vendor', 'category_id': 'c', 'availability': 'true'} for i in range(15)]
    supported = {'offer_id': 'z', 'name': 'PRO32 Endpoint Security Advanced', 'vendor': 'Vendor', 'category_id': 'c', 'availability': 'true'}
    for obj in [*other, supported]:
        catalog.by_id[obj['offer_id']] = obj
    catalog.by_category['c'] = [*other, supported]
    catalog.by_vendor = {}
    calls = []
    def provider(payload):
        calls.append(payload)
        cs = payload['candidates']
        return json.dumps({'order': [c['id'] for c in cs], 'source_ids': [], 'assessments': [
            {'candidate_id': c['id'], 'category': 'uncertain', 'history_ids': [],
             'basis': ['apparent_product_role'], 'uncertainties': ['relation_unverified'],
             'source_ids': c['source_ids']} for c in cs]})
    result = explore(profile, catalog, lambda p: run(p, provider))
    assert result['delivery'] == 'blocked' and result['findings'] == []
    assert calls[1]['candidates'][0]['name'] == supported['name']
    assert calls[1]['candidates'][0]['source_ids'] == ['pro32-endpoint-product']
    assert any('не подтверждение связи' in limitation for limitation in result['limitations'])
    assert profile['contest']['candidates'] == [{'offer_id': 'existing'}]


def test_lookup_failure_retains_fallback(monkeypatch):
    from recommendation import catalog_researcher
    catalog, profile = setup()
    def fail(_):
        raise OSError('registry unavailable')
    monkeypatch.setattr(catalog_researcher, 'evidence_for_offers', fail)
    def rank(p):
        return {'candidates': [{'offer_id': c['offer_id'], 'assessment': {'category': 'uncertain'}} for c in p['contest']['candidates']]}
    result = explore(profile, catalog, rank)
    assert result['status'] == 'explored' and result['examined'] == 1


def test_specific_words_break_shared_document_ties_without_claiming_relation(monkeypatch):
    from recommendation import catalog_researcher
    catalog, profile = setup()
    source = {'offer_id': 'bought', 'name': 'Vendor Alpha Monitor', 'vendor': 'Vendor',
              'category_id': 'c', 'availability': 'true'}
    catalog._unique = lambda item: source
    broad = [{'offer_id': f'{i:02}', 'name': 'Vendor Other Product', 'vendor': 'Vendor',
              'category_id': 'c', 'availability': 'true'} for i in range(12)]
    specific = {'offer_id': 'z', 'name': 'Vendor Alpha Monitor Advanced', 'vendor': 'Vendor',
                'category_id': 'c', 'availability': 'true'}
    for obj in broad + [specific]:
        catalog.by_id[obj['offer_id']] = obj
    catalog.by_category['c'] = broad + [specific]
    catalog.by_vendor = {}
    monkeypatch.setattr(catalog_researcher, 'evidence_for_offers', lambda items:
                        {o['offer_id']: [{'id': 'same-document'}] for o in items})
    calls = []
    def rank(p):
        cs = p['contest']['candidates']; calls.append(cs)
        return {'candidates': [{'offer_id': c['offer_id'], 'assessment': {'category': 'uncertain'}} for c in cs]}
    result = explore(profile, catalog, rank)
    assert calls[1][0]['offer_id'] == 'z'
    assert len(calls[1]) == 10 and result['findings'] == []
    assert result['delivery'] == 'blocked'


def test_vendor_and_generic_words_do_not_create_product_match():
    from recommendation.catalog_researcher import _product_words
    assert not (_product_words('Vendor Security лицензия', 'Vendor') &
                _product_words('Vendor Security продление', 'Vendor'))
    assert _product_words('Vendor Alpha Monitor', 'Vendor') == {'alpha', 'monitor'}
