import sqlite3
import unittest

from recommendation.experimental_graph import fit
from recommendation.restricted_dry_run import audit


class RestrictedDryRunTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE offers(row_id INTEGER PRIMARY KEY,offer_id TEXT,available TEXT);')
        self.db.executemany('INSERT INTO offers VALUES (?,?,?)', [(1, 'oa', 'true'), (2, 'ob', 'true'), (3, 'oc', 'false')])
        self.model = fit([('o', 'u', '2026-01-01', 'a'), ('o', 'v', '2026-01-01', 'b')])

    def tearDown(self):
        self.db.close()

    def test_zero_reviews_refuses(self):
        result = audit(self.model, self.db, {}, {})
        self.assertEqual(result['reviewed_histories'], 0)
        self.assertEqual(result['preview_candidates'], 0)
        self.assertEqual(result['production_blocked']['history_not_attested_complete'], 2)

    def test_reviewed_only_and_never_live(self):
        result = audit(self.model, self.db, {'a': 'oa', 'b': 'ob', 'c': 'oc'}, {'a': ['oa'], 'b': ['ob'], 'c': ['oc']})
        self.assertEqual(result['reviewed_histories'], 2)
        self.assertEqual(result['preview_candidates'], 2)
        self.assertEqual(result['preview_explanations']['co_order_association'], 2)
        self.assertEqual(result['production_recommendations'], 0)
        self.assertEqual(result['availability'], 'NOT_CHECKED_NO_PROVIDER')

    def test_ambiguity_and_missing_duplicate_block_preview(self):
        self.db.execute('INSERT INTO offers VALUES (4,?,?)', ('oa', 'true'))
        result = audit(self.model, self.db, {'a': 'oa', 'b': 'ob'}, {'a': ['oa', 'other']})
        self.assertEqual(result['reviewed_histories'], 1)
        self.assertEqual(result['preview_candidates'], 0)
        self.assertEqual(result['preview_blocked']['history_identity_unresolved_or_offer_invalid'], 1)
