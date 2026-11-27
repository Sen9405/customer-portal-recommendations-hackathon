import sqlite3
import unittest

from recommendation.experimental_graph import fit
from recommendation.hybrid import Hybrid, possible_from_audit
from recommendation.provenance_links import demo_inputs


class HybridTest(unittest.TestCase):
    def setUp(self):
        self.snapshot, self.registry, self.mappings = demo_inputs()
        self.conn = sqlite3.connect(':memory:')
        self.conn.executescript('''CREATE TABLE metadata(key TEXT,value TEXT);
          CREATE TABLE offers(row_id INTEGER PRIMARY KEY,offer_id TEXT,name TEXT,vendor TEXT,vendor_code TEXT,category_id TEXT,url TEXT,available TEXT,params_json TEXT);
          CREATE INDEX offers_id ON offers(offer_id); CREATE INDEX offers_category_row ON offers(category_id,row_id);''')
        self.conn.executemany('INSERT INTO offers VALUES (?,?,?,?,?,?,?,?,?)', [
            (1,'offer-a','a','v',None,'cat',None,'true','[]'),
            (2,'offer-b','b','v',None,'cat',None,'true','[]'),
            (3,'item-a','collision','v',None,'cat',None,'true','[]'),
            (4,'off','off','v',None,'cat',None,'false','[]'),
            (5,'dup','dup','v',None,'cat',None,'true','[]'),
            (6,'dup','dup2','v',None,'cat',None,'true','[]')])
        self.model = fit([('order','user','2026-01-01','item-a'), ('order','user','2026-01-01','other')])
        self.identities = {'item-a': 'offer-a', 'item-b': 'offer-b', 'other': 'offer-b'}

    def tearDown(self):
        self.conn.close()

    def engine(self, **kwargs):
        kwargs.setdefault('verified_identities', self.identities)
        kwargs.setdefault('availability_check', lambda _: True)
        return Hybrid(self.model, self.conn, self.snapshot, self.registry, self.mappings, **kwargs)

    def test_sources_are_offers_and_cross_namespace_deduplicated(self):
        r = self.engine().recommend(['item-a'], history_complete=True)
        self.assertEqual([x['id'] for x in r['recommendations']], ['offer-b', 'item-a'])
        self.assertEqual([x['source'] for x in r['recommendations']], ['transaction_graph', 'catalog_similarity'])
        self.assertTrue(all(x['namespace'] == 'catalog_offer' and x['availability'] == 'live_check_true_and_snapshot_true' for x in r['recommendations']))

    def test_no_attestation_or_unique_candidate_does_not_prove_identity(self):
        self.assertEqual(self.engine().recommend(['item-a'])['blocked_reason'], 'history_not_attested_complete')
        r = self.engine(verified_identities={}).recommend(['item-a'], history_complete=True)
        self.assertEqual(r['blocked_reason'], 'history_identity_unresolved')
        self.assertEqual(r['recommendations'], [])

    def test_ambiguous_and_missing_history_blocks_all(self):
        for possible, hist in [({'item-a': ['offer-a', 'item-a']}, ['item-a']), ({}, ['item-a', 'unknown'])]:
            r = self.engine(possible_matches=possible).recommend(hist, history_complete=True)
            self.assertEqual(r['blocked_reason'], 'history_identity_unresolved')
            self.assertEqual(r['recommendations'], [])

    def test_missing_or_duplicate_history_catalog_id_blocks(self):
        for oid in ('missing', 'dup'):
            r = self.engine(verified_identities={'history': oid}).recommend(['history'], history_complete=True)
            self.assertEqual(r['blocked_reason'], 'history_offer_missing_or_duplicate')
            self.assertEqual(r['recommendations'], [])

    def test_only_available_unique_feed_rows_and_live_confirmation(self):
        for check in (lambda _: False, lambda _: None, lambda _: 1):
            self.assertEqual(self.engine(availability_check=check).recommend(['item-a'], history_complete=True)['recommendations'], [])
        def error(_):
            raise RuntimeError('not available')
        self.assertEqual(self.engine(availability_check=error).recommend(['item-a'], history_complete=True)['recommendations'], [])
        r = self.engine(availability_check=None).recommend(['item-a'], history_complete=True)
        self.assertEqual(r['blocked_reason'], 'live_availability_unavailable')
        ids = [x['id'] for x in self.engine().recommend(['item-a'], history_complete=True)['recommendations']]
        self.assertNotIn('off', ids)
        self.assertNotIn('dup', ids)

    def test_unverified_and_conflicting_graph_targets_are_not_offered(self):
        self.conn.execute("UPDATE offers SET available='false' WHERE offer_id='offer-b'")
        r = self.engine(verified_identities={'item-a': 'offer-a'}).recommend(['item-a'], history_complete=True)
        self.assertNotIn('transaction_graph', [x['source'] for x in r['recommendations']])
        r = self.engine(possible_matches={'other': ['offer-b', 'item-a']}).recommend(['item-a'], history_complete=True)
        self.assertNotIn('transaction_graph', [x['source'] for x in r['recommendations']])

    def test_fallback_is_available_offer_and_dedupes(self):
        self.model['pairs'].clear()
        self.conn.execute("UPDATE offers SET category_id=NULL WHERE offer_id='offer-a'")
        r = self.engine().recommend(['item-a'], history_complete=True)
        self.assertEqual([(x['id'], x['source']) for x in r['recommendations']], [('offer-b', 'reviewed_family_relation')])
        r = Hybrid(self.model, self.conn, self.snapshot, self.registry, [], verified_identities=self.identities,
                   availability_check=lambda _: True).recommend(['item-a'], history_complete=True)
        self.assertEqual(r['recommendations'][0]['source'], 'popularity_fallback')
        self.assertTrue(r['fallback_used'])

    def test_input_limits_and_audit(self):
        e = self.engine()
        for history in [list(map(str, range(101))), [''], 'item-a']:
            with self.assertRaises(ValueError): e.recommend(history)
        with self.assertRaises(ValueError): e.recommend([], limit=4)
        with self.assertRaises(ValueError): e.recommend([], history_complete=1)
        with self.assertRaises(ValueError): self.engine(verified_identities={'x': ''})
        self.assertEqual(possible_from_audit({'items': {'a': {'matches': {'all_union': [{'offer_id':'x'}, {'offer_id':'y'}]}}}}), {'a':['x','y']})

    def test_unreviewed_edges_rejected(self):
        self.mappings[0]['status'] = 'pending'
        with self.assertRaises(ValueError): self.engine()
