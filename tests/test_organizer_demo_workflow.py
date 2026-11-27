"""Integration contract: no private handle can cross the public research boundary."""
import json
from http.client import HTTPConnection
from threading import Thread
from types import SimpleNamespace

from closed_review_server import LocalServer
from recommendation.public_research import Evidence, PublicCandidate, ResearchResult, public_anchor
from recommendation.contest_recommender import _offer_policy, documented_demo_upgrade
from test_closed_review_server import fixture


def test_upgrade_card_requires_verified_terms_and_eligible_exact_offer():
    title = 'КриптоПро CSP версии 5.0 (Номер ЕРРП: 4332), Лицензия на обновление СКЗИ КриптоПро CSP до версии 5.0 на сервере'
    source = {'url':'https://cryptopro.ru/order/OrderForm.aspx?button=csp_all_positions', 'quote':'test evidence'}
    candidate = {'status':'confirmed', 'product_id':'cryptopro-csp-5-server-upgrade', 'relation':'upgrade', 'evidence':[source, source]}
    research = {'anchor_id':'cryptopro-csp-server-upgrade','candidates':[candidate]}
    offer = {'name':title,'vendor':'КриптоПро','offer_id':'197895','url':'https://store.softline.ru/kripto-pro/-197895/', 'availability':'true',
             'params':json.dumps([{'name':'Тип лицензии','value':'Обновление'}, {'name':'Тип организации','value':'Коммерческая'}])}
    contest = SimpleNamespace(by_id={'197895':offer}, metadata={'feed_date_as_supplied':'2026-11-26'})
    assert _offer_policy(offer, 'B2B', None)[0] is None
    card = documented_demo_upgrade(contest, research)
    assert card is not None and card['offer_id'] == '197895'
    assert documented_demo_upgrade(contest, {**research, 'candidates':[{**candidate, 'evidence':[source]}]}) is None
    assert documented_demo_upgrade(contest, {**research, 'candidates':[{**candidate, 'status':'needs_check'}]}) is None
    offer['availability'] = 'false'
    assert documented_demo_upgrade(contest, research) is None
    offer.update(availability='true', params='[{"name":"Тип лицензии","value":"Продление"}]')
    assert _offer_policy(offer, 'B2B', None) == ('renewal_catalog_attribute', None)
    assert documented_demo_upgrade(contest, research) is None


def test_anchor_resolution_is_local_and_conservative():
    assert public_anchor('Veeam Backup & Replication Enterprise') == 'veeam-backup-replication'
    assert public_anchor('Microsoft SQL Server Management Studio') is None
    assert public_anchor('Kaspersky Endpoint Security') is None
    assert public_anchor('Veeam Backup & Replication / Microsoft SQL Server') is None


def test_public_research_and_zero_history_post_contract():
    data = fixture()
    calls = []

    class Research:
        def run(self, anchor, *, allow_network):
            calls.append(('research', anchor, allow_network))
            quote = 'Interaction with Kaspersky Endpoint Detection and Response Optimum version 1.0 is not supported.'
            finding = PublicCandidate('public-product', 'Public product', 'documented_integration', 'confirmed', 'documented_family_relation', (Evidence('https://example.test/vendor', quote, '2026-11-27'),))
            return ResearchResult(anchor, 'completed', (finding,))

    class Catalog:
        def recommend(self, user, audience, **kw):
            assert user not in data.handles.values()
            calls.append(('cold', audience, kw))
            return {'selection_mode': 'cold_start_goal', 'candidates': []}

    data.contest = Catalog()
    with LocalServer(0, data) as server:
        server.public_research = Research()
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            def post(path, body):
                with_connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    headers = {'Origin': f'http://127.0.0.1:{server.server_port}', 'X-Local-Token': server.token, 'Content-Type':'application/json'}
                    with_connection.request('POST', path, json.dumps(body), headers)
                    r = with_connection.getresponse()
                    return r.status, r.read()
                finally:
                    with_connection.close()

            anchor = 'kaspersky-endpoint-security-windows'
            for invalid in ({'anchor': anchor, 'consent': False}, {'anchor': 'https://example.test', 'consent': True}, {'anchor': anchor, 'consent': True, 'id':'opaque'}):
                assert post('/api/public-research', invalid)[0] == 400
            assert not calls
            status, result = post('/api/public-research', {'anchor':anchor, 'consent':True, 'legacy':True})
            assert status == 200 and json.loads(result)['candidates'][0]['status'] == 'rejected'
            assert post('/api/cold-start', {'goal':['backup'], 'audience':'B2B', 'platform':None})[0] == 400
            assert post('/api/cold-start', {'goal':'backup', 'audience':'B2B', 'platform':'x86-64'})[0] == 200
            assert calls == [('research', anchor, True), ('cold', 'B2B', {'goal':'backup', 'platform':'x86-64', 'limit':3})]
        finally:
            server.shutdown()
            worker.join(timeout=5)
