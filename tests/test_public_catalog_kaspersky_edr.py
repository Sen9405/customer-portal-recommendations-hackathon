"""Checks public candidate evidence and optional exact catalog snapshot, not licensing."""
import json
import sqlite3
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'data/catalog-20261126.sqlite'
ARTIFACT = ROOT / 'sources/public_catalog_kaspersky_edr_20261126.json'


class KasperskyPublicEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = json.loads(ARTIFACT.read_text(encoding='utf-8'))

    def test_conditional_provenance_and_scope(self):
        d = self.evidence
        self.assertFalse(d['snapshot']['historical_snapshot_verified'])
        self.assertEqual(len(d['offers']), 2)
        self.assertEqual(len(d['relations']), 1)
        relation = d['relations'][0]
        self.assertEqual(relation['status'], 'conditional_product_level_evidence_not_attested')
        self.assertNotEqual(relation['subject_offer_id'], relation['object_offer_id'])
        self.assertEqual(set(d['offers']), {relation['subject_offer_id'], relation['object_offer_id']})
        self.assertTrue(relation['conditions_and_unknowns'])
        self.assertTrue(d['common_limitations'])
        self.assertEqual(set(relation['sources']), set(d['manufacturer_sources']))
        for key in relation['sources']:
            source = d['manufacturer_sources'][key]
            self.assertEqual(urlparse(source['url']).scheme, 'https')
            self.assertEqual(urlparse(source['url']).hostname, 'support.kaspersky.com')
            self.assertTrue(source['quotes'])
        self.assertIn('11.7.0–12.10', relation['conditions_and_unknowns'][0])
        self.assertIn('conflict', d['offers']['4853'])
        for offer in d['offers'].values():
            self.assertIs(offer['snapshot_available'], True)

    @unittest.skipUnless(CATALOG.exists(), 'optional public catalog snapshot absent')
    def test_exact_public_snapshot_offers(self):
        with sqlite3.connect(f'file:{CATALOG}?mode=ro', uri=True) as db:
            meta = dict(db.execute('SELECT key, value FROM metadata'))
            self.assertEqual(meta['retrieval_started_utc'], self.evidence['snapshot']['retrieval_started_utc'])
            self.assertEqual(meta['retrieval_finished_utc'], self.evidence['snapshot']['retrieval_finished_utc'])
            for offer_id, offer in self.evidence['offers'].items():
                with self.subTest(offer_id=offer_id):
                    rows = db.execute('SELECT name, vendor_code, url, available FROM offers WHERE offer_id = ?', (offer_id,)).fetchall()
                    self.assertEqual(len(rows), 1)
                    name, code, url, available = rows[0]
                    self.assertEqual(' '.join(name.split()), ' '.join(offer['name'].split()))
                    self.assertEqual(code, offer['vendor_code'])
                    self.assertEqual(url, offer['url'])
                    self.assertEqual(available, 'true')


if __name__ == '__main__':
    unittest.main()
