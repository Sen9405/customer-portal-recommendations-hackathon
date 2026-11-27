"""HTTP integration for name-fallback provenance; no real model calls."""
import http.client
import json
import threading
from unittest.mock import patch
import closed_review_server as app
from recommendation import catalog_researcher
from test_closed_review_server import fixture


def test_fallback_provenance_is_explicit():
    data=fixture()

    # ReviewData supplies the complete expected shape; build it before adding catalog.
    data.contest=None
    original={h:data.review(h) for h in data.handles}
    for p in original.values():
        p['contest']={'snapshot_date':None,'candidates':[]}
    data.contest=object(); data.review=lambda handle, platform=None:original[handle]
    server=app.LocalServer(0,data); t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
    def fake(profile, catalog, *, allow_name_fallback):
        assert allow_name_fallback is True
        return {'status':'explored','examined':1,'unsearched_directions':0,'actions':[{'tool':'catalog_name_hint','query':'','returned':1,'bounded':False}],
                'findings':[{'name':'Synthetic new','vendor':'Synthetic','evidence':{'name_hint':True},
                    'assessment':{'category':'possible_new_product','history_ids':[],'basis':[],'uncertainties':[],'sources':[]}}]}
    try:
        with patch.object(catalog_researcher,'explore',fake):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            headers={'Origin':f'http://127.0.0.1:{server.server_port}','Content-Type':'application/json','X-Local-Token':server.token}
            c.request('POST','/api/research',json.dumps({'id':next(iter(original)),'consent':True}),headers)
            response=c.getresponse(); assert response.status==200; result=json.loads(response.read()); c.close()
            assert result['actions'][0]['tool']=='catalog_name_hint'
            assert result['findings'][0]['evidence']=={'name_hint':True}
            assert result['findings'][0]['assessment']['sources']==[]
    finally:
        server.shutdown(); server.server_close(); t.join()
