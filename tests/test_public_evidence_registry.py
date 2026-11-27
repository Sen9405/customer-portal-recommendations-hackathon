import copy
import json
from pathlib import Path
import unittest

from sources.registry import validate_registry, permits_claim, usable_for_strict_history


class PublicEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((Path(__file__).resolve().parents[1] / 'sources' / 'manufacturer_relations.json').read_text())

    def test_public_registry(self):
        self.assertEqual(len(self.registry['relations']), 3)
        self.assertEqual(validate_registry(self.registry), [])
        for relation in self.registry['relations']:
            self.assertTrue(permits_claim(relation, relation['permitted_claim'], relation['source_version']))
            self.assertFalse(permits_claim(relation, 'SKU confirmed', relation['source_version']))
            self.assertFalse(permits_claim(relation, relation['permitted_claim'], 'unverified version'))
            self.assertFalse(usable_for_strict_history(relation, '2026-08-31T23:59:59Z'))

    def test_missing_and_bad_evidence_rejected(self):
        for field in ('url', 'observed_at', 'page_updated', 'source_version', 'excerpt', 'permitted_claim', 'caveats'):
            with self.subTest(field=field):
                data = copy.deepcopy(self.registry)
                data['relations'][0][field] = ''
                self.assertTrue(validate_registry(data))
        data = copy.deepcopy(self.registry)
        data['relations'][0]['observed_at'] = 'not-a-date'
        self.assertTrue(validate_registry(data))
        data = copy.deepcopy(self.registry)
        data['relations'][0]['url'] = 'https://example.com/unrelated'
        self.assertTrue(validate_registry(data))

    def test_cutoff_is_fail_closed_without_contemporaneous_snapshot(self):
        relation = copy.deepcopy(self.registry['relations'][0])
        self.assertFalse(usable_for_strict_history(relation, '2027-01-01T00:00:00Z'))
        self.assertFalse(usable_for_strict_history(relation, 'invalid'))
        self.assertFalse(usable_for_strict_history(relation, '2026-08-31'))

    def test_page_updated_after_cutoff_blocks_even_if_snapshot_claimed(self):
        relation = copy.deepcopy(self.registry['relations'][1])
        relation['historical_snapshot_verified_at'] = '2026-08-01T00:00:00Z'
        self.assertFalse(usable_for_strict_history(relation, '2026-08-31T23:59:59Z'))


if __name__ == '__main__':
    unittest.main()
