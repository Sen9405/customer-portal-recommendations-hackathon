import unittest

from recommendation.variant_sensitivity import audit


class VariantSensitivityTests(unittest.TestCase):
    def test_novel_item_id_is_target_even_if_lexically_similar(self):
        rows = [
            ('o1', 'u1', '2026-01-01', 'a'),
            ('o2', 'u2', '2026-01-01', 'a'),
            ('o3', 'u2', '2026-01-08', 'b'),
            ('o4', 'u1', '2026-01-15', 'b'),
        ]
        names = ['Synthetic license month 01', 'Synthetic license month 01',
                 'Synthetic license month 02', 'Synthetic license month 02']
        result = audit(rows, names, '2026-01-08', '2026-01-15')['B']
        self.assertEqual(result['targets_similar'], 1)
        self.assertEqual(result['base_hits'], 1)
        self.assertEqual(result['all_candidate_filter_hits'], 0)
        self.assertEqual(result['all_candidate_filter_lost_hits'], 1)

    def test_future_only_name_is_unknown_not_looked_up(self):
        rows = [('o1', 'u', '2026-01-01', 'a'),
                ('o2', 'u', '2026-01-15', 'b')]
        names = ['Synthetic license month 01', 'Synthetic license month 02']
        result = audit(rows, names, '2026-01-08', '2026-01-15')['A']
        self.assertEqual(result['targets_unknown'], 1)
        self.assertEqual(result.get('targets_similar', 0), 0)


if __name__ == '__main__':
    unittest.main()
