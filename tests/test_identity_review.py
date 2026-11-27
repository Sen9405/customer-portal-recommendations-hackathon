import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from catalog.identity_review import create, decide, counts, attestations


class IdentityReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'queue.sqlite'
        self.audit = {'items': {
            'a': {'matches': {'all_union': [{'offer_id': 'one'}]}},
            'b': {'matches': {'all_union': [{'offer_id': 'two'}, {'offer_id': 'three'}]}},
            'c': {'matches': {'all_union': []}}}, 'feed': {'date': 'test'}}
        create(self.path, self.audit)
        self.evidence = {'source': 'local document', 'exact_excerpt': 'synthetic', 'rationale': 'synthetic review'}

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_and_lifecycle(self):
        self.assertEqual(counts(self.path), {'total': 3, 'pending': 3, 'reviewed': 0, 'rejected': 0, 'unmatched': 1, 'unique_candidate': 1, 'ambiguous_or_capped': 1})
        self.assertEqual(attestations(self.path), {})
        decide(self.path, {'item_id':'a', 'status':'reviewed', 'offer_id':'one', 'reviewer':'human', 'evidence':self.evidence})
        decide(self.path, {'item_id':'b', 'status':'reviewed', 'offer_id':'two', 'reviewer':'human', 'evidence':self.evidence})
        self.assertEqual(attestations(self.path), {'a': 'one'})
        decide(self.path, {'item_id':'a', 'status':'rejected', 'reviewer':'human', 'evidence':self.evidence})
        self.assertEqual(attestations(self.path), {})
        self.assertEqual(counts(self.path)['pending'], 1)
        conn = sqlite3.connect(self.path)
        self.assertEqual(conn.execute('select count(*) from events').fetchone()[0], 3)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('delete from events')
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('update cases set candidate_count=1')
        conn.close()

    def test_fail_closed(self):
        for d in [
            {'item_id':'a','status':'reviewed','offer_id':'wrong','reviewer':'human','evidence':self.evidence},
            {'item_id':'c','status':'reviewed','offer_id':'one','reviewer':'human','evidence':self.evidence},
            {'item_id':'a','status':'reviewed','offer_id':'one','reviewer':'human','evidence':{}},
            {'item_id':'a','status':'pending','reviewer':'human','evidence':self.evidence}]:
            with self.assertRaises(ValueError):
                decide(self.path, d)
        self.assertEqual(counts(self.path)['pending'], 3)
        with self.assertRaises(FileExistsError):
            create(self.path, self.audit)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        os.chmod(self.path, 0o644)
        with self.assertRaises(ValueError):
            counts(self.path)

    def test_audit_evidence_preserved(self):
        conn = sqlite3.connect(self.path)
        raw = conn.execute('select evidence_json from cases where item_id=?', ('a',)).fetchone()[0]
        self.assertEqual(json.loads(raw)['detail'], self.audit['items']['a'])
        conn.close()


if __name__ == '__main__':
    unittest.main()
