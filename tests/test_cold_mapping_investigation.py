import unittest

from recommendation.cold_mapping_investigation import classify, license_markers, retrieve, targets
from tests.test_cold_target_diagnostics import fixture


class ColdMappingInvestigationTests(unittest.TestCase):
    def test_distinct_pairs_and_unmatched(self):
        rows, _, audit, snapshot = fixture([])
        rows += [('3', 'u', '2026-06-08', 'z'), ('4', 'v', '2026-05-01', 's'),
                 ('5', 'v', '2026-06-01', 'z'), ('6', 'new', '2026-06-01', 'z')]
        result = targets(rows, audit, snapshot)
        self.assertEqual(len(result['no_candidate']), 2)
        self.assertEqual({item for _, item in result['no_candidate']}, {'z'})

    def test_duplicate_global_claimant_not_necessarily_cold(self):
        rows, _, audit, snapshot = fixture([('dup', 'C')], extras=[('other', [('dup', 'C')])])
        result = targets(rows, audit, snapshot)
        self.assertEqual(result['unique_duplicate_offer_id'], [('u', 'z')])

    def test_name_retrieval_ranking_and_no_overlap(self):
        result = retrieve(['Special Product 20', 'яяяяяя'], ['Other offer', 'Special Product 20'])
        self.assertEqual(result[0][0][0], 1)
        self.assertEqual(result[1], [])

    def test_markers_are_literal_and_do_not_confirm_identity(self):
        markers = license_markers('License Standard 12 user')
        self.assertIn('12', markers['numbers'])
        label, comparison = classify(.8, 'License 12 user', 'License 20 device')
        self.assertEqual(label, 'strong_lexical_candidate')
        self.assertEqual(comparison['sales_only_numbers'], ['12'])
        self.assertEqual(comparison['offer_only_numbers'], ['20'])


if __name__ == '__main__': unittest.main()
