import json
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / 'sources/public_catalog_redsoft_usergate_20261126.json'
SNAPSHOT = ROOT / 'data/catalog-20261126.sqlite'


class PublicEvidenceTest(unittest.TestCase):
    def test_relations_are_conditional_and_linked(self):
        data = json.loads(ARTIFACT.read_text(encoding='utf-8'))
        self.assertEqual(len(data['relations']), 2)
        self.assertFalse(data['snapshot']['historical_snapshot_verified'])
        self.assertEqual({data['offers'][r['subject_offer_id']]['vendor'] for r in data['relations']}, {'РЕД СОФТ', 'UserGate'})
        for relation in data['relations']:
            with self.subTest(relation=relation['id']):
                self.assertEqual(relation['status'], 'conditional_product_level_evidence_not_attested')
                self.assertIn(relation['subject_offer_id'], data['offers'])
                self.assertIn(relation['object_offer_id'], data['offers'])
                self.assertTrue(relation['conditions_and_unknowns'])
                for key in relation['sources']:
                    source = data['manufacturer_sources'][key]
                    self.assertTrue(source['url'].startswith('https://'))
                    self.assertTrue(source['document_version'])
                    self.assertTrue(source['observed_at_utc'])
                    self.assertTrue(source['quotes'])
        self.assertIn('7.5.1', data['manufacturer_sources']['ug_hardware']['observed_matrix_row']['unmarked'])
        self.assertIn('blocker', data['relations'][1]['conditions_and_unknowns'][0])

    @unittest.skipUnless(SNAPSHOT.is_file(), 'public feed snapshot not available locally')
    def test_offer_fields_match_public_snapshot(self):
        data = json.loads(ARTIFACT.read_text(encoding='utf-8'))
        with sqlite3.connect(f'file:{SNAPSHOT}?mode=ro', uri=True) as db:
            for offer_id, offer in data['offers'].items():
                with self.subTest(offer_id=offer_id):
                    rows = db.execute('SELECT name, vendor, vendor_code, url, available FROM offers WHERE offer_id=?', (offer_id,)).fetchall()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0], (offer['name'], offer['vendor'], offer['vendor_code'], offer['url'], 'true' if offer['snapshot_available'] else 'false'))
