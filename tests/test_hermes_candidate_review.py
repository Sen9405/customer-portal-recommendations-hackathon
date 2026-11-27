import json
import threading
import time
import http.client
from unittest.mock import patch
import pytest
from playwright.sync_api import sync_playwright
import closed_review_server as app
from recommendation import hermes_candidate_review as agent
from test_closed_review_server import fixture


def profile(data):
    handle = next(h for h, u in data.handles.items() if data.pipeline.history.get(u) and data.review(h)['candidates'])
    return handle, data.review(handle)


def test_allowlist_and_fail_closed():
    data=fixture(); _, p=profile(data)
    def fake(payload):
        assert ' [товар продаж: ' not in json.dumps(payload, ensure_ascii=False)
        return json.dumps({'diagnostics':[{'candidate_id': x['id'], 'category':'uncertain', 'basis_ids':[]} for x in payload['candidates']], 'source_ids':[]})
    result=agent.run(p, fake)
    assert result['status']=='blocked' and result['recommendations']==[]
    assert result['manager_suggestions'] == []
    assert all(x['decision']=='blocked' for x in result['agent_review']['candidates'])
    for bad in ['{"candidate_ids":["unknown"],"source_ids":[]}', '{"candidate_ids":[],"source_ids":["https://evil.test"]}', '{"candidate_ids":[],"candidate_ids":[],"source_ids":[]}', '{"candidate_ids":[],"source_ids":[],"decision":"allow"}']:
        with pytest.raises((agent.ReviewError, ValueError)): agent.parse_selection(bad, ['candidate-1'], [])


def test_manager_suggestions_only_include_valid_statistical_signals():
    data = fixture(); _, p = profile(data)
    assert p['candidates']
    captured = []
    def provider(payload):
        captured.append(payload)
        return json.dumps({'diagnostics': [
            {'candidate_id': c['id'], 'category': 'statistical_signal' if i == 0 else 'uncertain',
             'basis_ids': [c['evidence'][0]['id']]}
            for i, c in enumerate(payload['candidates'])], 'source_ids': []})
    result = agent.run(p, provider)
    assert result['manager_suggestions'] == [{
        'item_id': p['candidates'][0]['item_id'], 'category': 'statistical_signal',
        'basis': [captured[0]['candidates'][0]['evidence'][0]],
    }]
    assert result['status'] == 'blocked' and result['recommendations'] == []
    assert all(c['decision'] == 'blocked' for c in result['agent_review']['candidates'])


def test_manager_suggestions_empty_candidates():
    data = fixture(); _, p = profile(data)
    p['candidates'] = []
    result = agent.run(p, lambda _: json.dumps({'diagnostics': [], 'source_ids': []}))
    assert result['manager_suggestions'] == []
    assert result['status'] == 'blocked' and result['recommendations'] == []


@pytest.mark.parametrize('basis_ids', [[], ['unknown'], 'not-a-list'])
def test_malformed_signal_never_yields_suggestions(basis_ids):
    data = fixture(); _, p = profile(data)
    assert p['candidates']
    def provider(payload):
        diagnostics = [{'candidate_id': c['id'], 'category': 'uncertain', 'basis_ids': []}
                       for c in payload['candidates']]
        diagnostics[0] = {'candidate_id': payload['candidates'][0]['id'],
                          'category': 'statistical_signal', 'basis_ids': basis_ids}
        return json.dumps({'diagnostics': diagnostics, 'source_ids': []})
    with pytest.raises(agent.ReviewError):
        agent.run(p, provider)


def test_timeout_kills_process_group():
    import subprocess, sys
    original = subprocess.Popen
    spawned = []
    def sleeper(*args, **kwargs):
        proc = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        spawned.append(proc)
        return proc
    with patch.object(agent.subprocess, 'Popen', sleeper):
        with pytest.raises(agent.ReviewError, match='timeout'):
            agent.call_hermes({}, timeout=0.2)
    assert spawned[0].poll() is not None


def test_concurrency():
    data=fixture(); _, p=profile(data); entered=threading.Event(); release=threading.Event()
    def provider(_):
        entered.set(); release.wait(5); return json.dumps({'diagnostics':[{'candidate_id': x['id'], 'category':'uncertain', 'basis_ids':[]} for x in _['candidates']], 'source_ids':[]})
    thread=threading.Thread(target=lambda: agent.run(p,provider)); thread.start(); assert entered.wait(5)
    try:
        with pytest.raises(agent.ReviewError, match='busy'): agent.run(p,provider)
    finally: release.set(); thread.join()


def test_http_carousel_opt_in_and_order():
    from recommendation import hermes_carousel as carousel
    data=fixture(); handle, _=profile(data)
    original_review = data.review
    def with_contest(handle, platform=None):
        p=original_review(handle, platform)
        p['contest']={'snapshot_date':None, 'candidates':[{'offer_id':str(i), 'snapshot_available':True, 'name':'Synthetic '+str(i), 'vendor':'', 'source':'related_feed', 'evidence':{'history_item_ids':[], 'same_category':True}} for i in range(5)]}
        return p
    data.review=with_contest
    server=app.LocalServer(0,data); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'; calls=[]
    def fake(payload):
        calls.append(payload); ids=[c['id'] for c in payload['candidates']]
        return json.dumps({'order':ids[::-1], 'source_ids':[], 'assessments':[{'candidate_id':i,'category':'uncertain','history_ids':[],'basis':['insufficient_name_detail'],'uncertainties':['relation_unverified'],'source_ids':[]} for i in ids]})
    try:
        original=carousel.run
        with patch.object(carousel,'run',lambda p: original(p,fake)):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            headers={'Origin':origin,'Content-Type':'application/json','X-Local-Token':server.token}
            c.request('POST','/api/hermes-review',json.dumps({'id':handle,'consent':False}),headers)
            r=c.getresponse(); assert r.status==400; r.read(); c.close(); assert not calls
            c.request('POST','/api/hermes-review',json.dumps({'id':handle,'consent':True}),headers)
            r=c.getresponse(); assert r.status==200; result=json.loads(r.read()); c.close()
            assert result['status']=='model_ordered'
            assert [x['offer_id'] for x in result['candidates']]==['4','3','2','1','0']
            assert result['candidates'][0]['assessment']['category']=='uncertain' and len(calls)==1
    finally:
        server.shutdown(); server.server_close(); thread.join()
