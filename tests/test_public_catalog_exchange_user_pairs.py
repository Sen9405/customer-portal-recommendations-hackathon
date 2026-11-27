"""Structural and public snapshot checks, not independent license attestation."""
import json
import sqlite3
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / 'sources/public_catalog_exchange_user_pairs_20261126.json'
CATALOG = ROOT / 'data/catalog-20261126.sqlite'


class ExchangeUserPairEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(ARTIFACT.read_text(encoding='utf-8'))

    def test_three_new_distinct_conditional_pairs_and_sources(self):
        data = self.data
        pairs = {(r['subject_offer_id'], r['object_offer_id']) for r in data['relations']}
        self.assertEqual(pairs, {('202509', '202512'), ('202510', '202512'), ('202512', '202514')})
        self.assertEqual(len({r['id'] for r in data['relations']}), 3)
        self.assertFalse(data['snapshot']['historical_snapshot_verified'])
        for r in data['relations']:
            with self.subTest(relation=r['id']):
                self.assertEqual(r['status'], 'conditional_product_level_evidence_not_attested')
                self.assertIn(r['subject_offer_id'], data['offers'])
                self.assertIn(r['object_offer_id'], data['offers'])
                self.assertTrue(r['permitted_claim'])
                self.assertTrue(r['conditions_and_unknowns'])
                self.assertIn('base_additive', r['sources'])
                self.assertIn('end_of_support', r['sources'])
                for key in r['sources']:
                    source = data['manufacturer_sources'][key]
                    url = urlsplit(source['url'])
                    self.assertEqual(url.scheme, 'https')
                    self.assertIn(url.hostname, {'www.microsoft.com', 'learn.microsoft.com'})
                    self.assertTrue(source['quotes'])
        self.assertIn('conflict', data['offers']['202512'])
        self.assertIn('conflict', data['offers']['202514'])
        self.assertTrue(all(not o['snapshot_available'] for o in data['offers'].values()))

    @unittest.skipUnless(CATALOG.exists(), 'optional public snapshot absent')
    def test_exact_public_offers_match_snapshot(self):
        with sqlite3.connect(f'file:{CATALOG}?mode=ro', uri=True) as db:
            for offer_id, offer in self.data['offers'].items():
                with self.subTest(offer=offer_id):
                    rows = db.execute('SELECT name, url, available FROM offers WHERE offer_id = ?', (offer_id,)).fetchall()
                    self.assertEqual(len(rows), 1)
                    name, url, available = rows[0]
                    self.assertEqual(' '.join(name.split()), ' '.join(offer['name'].split()))
                    self.assertEqual(url, offer['url'])
                    self.assertEqual(available, str(offer['snapshot_available']).lower())


if __name__ == '__main__':
    unittest.main()
