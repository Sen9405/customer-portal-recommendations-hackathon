"""Checks on a public-only, explicitly unreviewed renewal lead."""
import json
from pathlib import Path
import sqlite3
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


class PublicRenewalCandidateTest(unittest.TestCase):
    def setUp(self):
        self.record = json.loads((ROOT / 'sources/public_renewal_candidate.json').read_text())

    def test_candidate_is_not_a_review(self):
        self.assertEqual(self.record['status'], 'public_evidence_candidate_not_human_reviewed')
        self.assertNotIn('reviewed_by', self.record)
        self.assertEqual([o['role'] for o in self.record['offers']],
                         ['existing_license_reference', 'potential_renewal_offer'])
        self.assertEqual(len({o['offer_id'] for o in self.record['offers']}), 2)
        self.assertEqual(urlsplit(self.record['manufacturer_evidence']['url']).hostname,
                         'support.kaspersky.com')
        for offer in self.record['offers']:
            self.assertEqual(urlsplit(offer['url']).hostname, 'store.softline.ru')
            self.assertIn(self.record['family'], offer['page_title_excerpt'])
            self.assertNotIn('reviewed_by', offer)
        self.assertTrue(self.record['limitations'])

    def test_public_feed_occurrences_if_index_available(self):
        index = ROOT / 'data/catalog-20261126.sqlite'
        if not index.exists():
            self.skipTest('local public feed index not available')
        conn = sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)
        self.addCleanup(conn.close)
        for offer in self.record['offers']:
            rows = conn.execute('SELECT name,vendor_code,url FROM offers WHERE offer_id=?',
                                (offer['offer_id'],)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0], (offer['page_title_excerpt'], offer['sku'], offer['url']))


if __name__ == '__main__':
    unittest.main()
