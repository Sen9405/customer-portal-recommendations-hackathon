import http.client
import json
import threading
import unittest
import tempfile
from pathlib import Path
from datetime import date
from unittest.mock import patch
import closed_review_server as app


def fixture():
    rows = [('1','u',date(2026,1,1),'a',None,'A'), ('2','v',date(2026,1,1),'a',None,'A'), ('2','v',date(2026,1,1),'b',None,'B'), ('3','u',date(2026,9,1),'future',None,'Future')]
    return app.build_data(rows, {}, {}, customers={**{'u': {'group':'test'}}, **{f'cold{i}': {} for i in range(25)}})

class ClosedReviewTests(unittest.TestCase):
    def setUp(self):
        self.data=fixture(); self.server=app.LocalServer(0,self.data)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.host=f'127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
    def request(self,method,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3); c.request(method,path,body=body,headers=headers or {}); r=c.getresponse(); result=(r.status,dict(r.getheaders()),r.read()); c.close(); return result
    def test_http_boundaries_and_paging(self):
        status,headers,html=self.request('GET','/'); self.assertEqual(status,200); self.assertIn(b'local-token',html); self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(self.request('GET','/',headers={'Host':'evil.test'})[0],403)
        self.assertEqual(self.request('GET','/',headers={'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.request('GET','/data/dataset.xlsx')[0],404)
        good={'Origin':'http://'+self.host,'Content-Type':'application/json','X-Local-Token':self.server.token}
        self.assertEqual(self.request('POST','/api/options','{"page":0}',{'Origin':'http://'+self.host,'Content-Type':'application/json'})[0],403)
        self.assertEqual(self.request('POST','/api/options','{"page":0}',{**good,'Origin':'https://evil.test'})[0],403)
        status,_,raw=self.request('POST','/api/options','{"page":0}',good); self.assertEqual(status,200)
        page=json.loads(raw); self.assertEqual(len(page['options']),20); self.assertTrue(page['has_more'])
        self.assertNotIn(b'cold',raw); handle=page['options'][0]['id']
        self.assertEqual(self.request('POST','/api/review',json.dumps({'id':handle}),good)[0],200)
        self.assertEqual(self.request('POST','/api/review','{"id":"x","id":"x"}',good)[0],400)
        self.assertEqual(self.request('POST','/api/options','{"page":-1}',good)[0],400)
        request_body=json.dumps({'id':handle})
        with patch.object(app.json,'dumps',side_effect=RuntimeError('private error')):
            status,_,body=self.request('POST','/api/review',request_body,good); self.assertEqual(status,500); self.assertNotIn(b'private',body)
    def test_review_includes_contest_results_without_test_targets(self):
        class FakeContest:
            def recommend(self, user, client_type, *, limit=20):
                assert user == 'u' and client_type is None and limit == 20
                return {'status':'preliminary_demo','candidates':[{'offer_id':str(i)} for i in range(6)]}
        self.data.contest=FakeContest()
        handle=next(h for h,u in self.data.handles.items() if u=='u')
        profile=self.data.review(handle)
        self.assertEqual(len(profile['contest']['candidates']), 6)
        self.assertNotIn('future', str(profile['contest']))

    def test_profile_cutoff_and_cold(self):
        handle=next(h for h,u in self.data.handles.items() if u=='u'); profile=self.data.review(handle)
        self.assertEqual(profile['history']['order_count'],1); self.assertNotIn('future',str(profile))
        cold=next(h for h,u in self.data.handles.items() if str(u).startswith('cold'))
        self.assertEqual(self.data.review(cold)['status'],'cold_no_observed_history')
        self.assertEqual(len(self.data.options),27)

    def test_http_three_methods_and_future_invariance(self):
        handle=next(h for h,u in self.data.handles.items() if u=='u')
        good={'Origin':'http://'+self.host,'Content-Type':'application/json','X-Local-Token':self.server.token}
        status,_,raw=self.request('POST','/api/review',json.dumps({'id':handle}),good)
        self.assertEqual(status,200)
        result=json.loads(raw); methods=result['methods']
        self.assertEqual(set(methods),{'profile','popularity','copurchase'})
        self.assertEqual([x['item_id'] for x in methods['profile']], [x['item_id'] for x in result['candidates']])
        self.assertEqual([x['item_id'] for x in methods['popularity']], ['b'])
        self.assertEqual([x['item_id'] for x in methods['copurchase']], ['b'])
        self.assertNotIn('future',str(methods))
        self.assertTrue(all(len(v)<3 for v in methods.values()))
        cold=next(h for h,u in self.data.handles.items() if str(u).startswith('cold'))
        status,_,raw=self.request('POST','/api/review',json.dumps({'id':cold}),good)
        self.assertEqual(status,200); self.assertEqual(json.loads(raw)['methods']['copurchase'],[])

    def test_future_changes_do_not_change_methods_and_official_baskets(self):
        from evaluation.protocol_audit import fit_official, recommend_official
        train=[('1','u',date(2026,1,1),'a'),('shared','v',date(2026,1,1),'a'),
               ('shared','w',date(2026,1,1),'b'),('3','x',date(2026,1,1),'c')]
        first=app.build_data(train+[('future','u',date(2026,9,7),'b')],{}, {},customers={'u':{}})
        second=app.build_data(train+[('future','u',date(2026,9,7),'c'),('other','v',date(2026,9,7),'z')],{}, {},customers={'u':{}})
        def review(data): return data.review(next(h for h,u in data.handles.items() if u=='u'))
        a,b=review(first),review(second)
        self.assertEqual(a['methods'],b['methods'])
        official=fit_official(train)
        for method in ('popularity','copurchase'):
            self.assertEqual([x['item_id'] for x in a['methods'][method]],recommend_official(official,{'a'},method))
        self.assertEqual(a['methods']['copurchase'][0]['item_id'],'b')
        self.assertEqual(a['methods']['copurchase'][0]['coorder'][0]['basket_count'],1)

    def test_loader_uses_v5_and_never_reads_legacy_mapping(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wb = openpyxl.Workbook(); sales = wb.active; sales.title = 'Продажи'
            sales.append(['order', 'client', 'week', 'item', 'other', 'name'])
            sales.append(['order', 'client', date(2026, 1, 1), 'item', None, 'Synthetic'])
            customers = wb.create_sheet('Клиенты'); customers.append(['id', 'group', 'other', 'industry', 'segment'])
            wb.save(root / 'dataset_v5.xlsx')
            (root / 'softline-features.json').write_text('invalid legacy mapping')
            (root / 'softline-audit.json').write_text('invalid legacy mapping')
            data = app.load_data(root)
            self.assertEqual(data.data_version, 'v5')
            self.assertEqual(data.pipeline.catalog, {})
            profile = data.review(next(iter(data.handles)))
            self.assertEqual(profile['data_version'], 'v5')
            self.assertEqual(profile['catalog_scope'], 'unavailable_for_v5')

    def test_v5_mapping_is_bound_to_v5_workbook_and_not_ranking(self):
        rows=[('1','u',date(2026,1,1),'a',None,'A'),('2','v',date(2026,1,1),'a',None,'A'),('2','v',date(2026,1,1),'b',None,'B')]
        mapping={'b': {'status':'unique','identity':'unverified','candidates':[]}}
        data=app.build_data(rows, {}, {}, customers={'u':{}}, data_version='v5', v4_mapping=mapping)
        profile=data.review(next(k for k,v in data.handles.items() if v=='u'))
        self.assertEqual(profile['catalog_scope'], 'v5_snapshot')
        self.assertEqual(profile['candidates'][0]['catalog']['identity'], 'unverified')
        self.assertEqual(profile['candidates'][0]['item_id'], 'b')

    def test_catalog_metadata_fetched_from_matching_snapshot(self):
        import sqlite3
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE offers (row_id INTEGER, offer_id TEXT, name TEXT, vendor TEXT, vendor_code TEXT, available TEXT)')
        db.execute('INSERT INTO offers VALUES (1, ?, ?, ?, ?, ?)', ('id','Product <b>','Vendor','ABC','true'))
        evidence={'a':{'candidates':[{'row_id':1,'offer_id':'id','snapshot_available':'true'}]}}
        app.enrich_catalog_snapshot(evidence,db)
        c=evidence['a']['candidates'][0]
        self.assertEqual((c['snapshot_name'], c['snapshot_vendor'], c['snapshot_vendor_code']), ('Product <b>', 'Vendor', 'ABC'))
        c['offer_id']='wrong'
        with self.assertRaises(ValueError): app.enrich_catalog_snapshot(evidence,db)
        db.close()

    def test_loader_does_not_fallback_to_v3(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'dataset.xlsx').touch()
            with self.assertRaises(FileNotFoundError): app.load_data(folder)

    def test_v4_mapping_provenance_ambiguity_and_malformed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); workbook = root/'wb'; index = root/'index'; mapping = root/'mapping'
            workbook.write_bytes(b'synthetic workbook'); index.write_bytes(b'synthetic index')
            def candidate(row, offer):
                return {'row_id':row, 'offer_id':offer, 'match_channels':['vendorCode'], 'available':'true', 'duplicate_feed_offer_id':False}
            source = {'provenance':{'workbook_sha256':app.digest(workbook), 'index_sha256':app.digest(index), 'index_metadata':{'feed_date_as_supplied':'snapshot'}},
                      'items':{'a':{'rows':1,'candidates':[candidate(1,'shared')]}, 'b':{'rows':1,'candidates':[candidate(1,'shared')]}, 'c':{'rows':1,'candidates':[]}, 'd':{'rows':1,'candidates':[candidate(2,'other')]}, 'e':{'rows':1,'candidates':[candidate(3,'x'),candidate(4,'y')]}}}
            def save(): mapping.write_text(json.dumps(source))
            save(); evidence=app.load_v4_mapping(workbook,index,mapping)
            self.assertEqual([evidence[x]['status'] for x in 'abcde'],['ambiguous','ambiguous','no_candidate','unique','ambiguous'])
            self.assertEqual(evidence['a']['candidates'][0]['offer_claim_count'],2)
            self.assertEqual(evidence['d']['identity'],'unverified')
            data=app.build_data([('1','u',date(2026,1,1),'d',None,'D'),('2','v',date(2026,1,1),'d',None,'D'),('2','v',date(2026,1,1),'c',None,'C')], {}, {}, customers={'u':{}}, data_version='v4', v4_mapping=evidence)
            profile=data.review(next(k for k,v in data.handles.items() if v=='u'))
            self.assertEqual(profile['item_history'][0]['catalog']['status'],'unique')
            self.assertEqual(profile['candidates'][0]['catalog']['status'],'no_candidate')
            index.write_bytes(b'changed')
            with self.assertRaises(ValueError): app.load_v4_mapping(workbook,index,mapping)
            index.write_bytes(b'synthetic index'); workbook.write_bytes(b'changed')
            with self.assertRaises(ValueError): app.load_v4_mapping(workbook,index,mapping)
            workbook.write_bytes(b'synthetic workbook'); source['items']['a']['candidates'][0]['match_channels']=['bogus']; save()
            with self.assertRaises(ValueError): app.load_v4_mapping(workbook,index,mapping)
