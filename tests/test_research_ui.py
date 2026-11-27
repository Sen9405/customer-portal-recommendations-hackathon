import json
import threading
import http.client
from unittest.mock import patch

import closed_review_server as app
from recommendation import catalog_researcher
from recommendation.hermes_carousel import run as carousel_run
from test_closed_review_server import fixture
from test_catalog_researcher import setup


def test_research_http_and_bounded_model_sequence():
    data = fixture()
    handle = next(h for h,u in data.handles.items() if data.pipeline.history.get(u))
    catalog, profile = setup()
    profiles = {h: data.review(h) for h in data.handles}
    def review(handle, platform=None):
        p = dict(profiles[handle])
        p['item_history'] = profile['item_history']
        p['contest'] = {'snapshot_date':None, 'candidates':[{'offer_id':'existing','snapshot_available':True,'name':'Main','vendor':'Vendor','source':'related_feed','evidence':{'history_item_ids':[],'same_category':True}}]}
        return p
    data.review = review
    data.contest = catalog
    calls = []
    def provider(payload):
        calls.append(payload)
        ids = [c['id'] for c in payload['candidates']]
        return json.dumps({'order':ids,'source_ids':[], 'assessments':[{'candidate_id':c['id'],'category':'possible_new_product','history_ids':['history-1'],'basis':['apparent_product_role'],'uncertainties':['relation_unverified'],'source_ids':c['source_ids']} for c in payload['candidates']]})
    real_explore = catalog_researcher.explore
    def fake_explore(p, c, **kwargs):
        return real_explore(p, c, lambda p:carousel_run(p,provider), **kwargs)
    server = app.LocalServer(0,data)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:
        def evidence(candidates):
            return {c['offer_id']:[{'id':'synthetic','url':'https://example.com/doc','claim':'Synthetic claim','limitation':'Synthetic limit'}] for c in candidates if c.get('name') == 'New'}
        with patch.object(catalog_researcher,'explore',fake_explore), patch('recommendation.public_evidence_agent.evidence_for_offers', evidence):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            headers={'Origin':origin,'Content-Type':'application/json','X-Local-Token':server.token}
            c.request('POST','/api/research',json.dumps({'id':handle,'consent':False}),headers)
            r=c.getresponse(); assert r.status==400; r.read(); c.close(); assert not calls
            c.request('POST','/api/research',json.dumps({'id':handle,'consent':True}),headers)
            r=c.getresponse(); assert r.status==200; result=json.loads(r.read()); c.close()
            assert result['status']=='explored' and result['examined']==1 and len(calls)==2
            finding, = result['findings']
            assert finding['name']=='New' and finding['assessment']['basis']==['apparent_product_role']
            source, = finding['assessment']['sources']
            assert source['permitted_claim']=='Synthetic claim' and source['limitation']=='Synthetic limit'
            assert data.review(handle)['contest']['candidates'][0]['name']=='Main'
    finally:
        server.shutdown(); server.server_close(); thread.join()
