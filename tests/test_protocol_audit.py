import unittest
from evaluation.protocol_audit import analyze


class ProtocolAuditTests(unittest.TestCase):
    def test_split_new_items_groups_ties_and_short_lists(self):
        sales = [
            ('o1', 'u1', '2026-08-31', 'a'),
            ('o1', 'u1', '2026-08-31', 'b'),
            ('o2', 'u2', '2026-08-31', 'a'),
            ('o3', 'u3', '2026-09-01', 'gap'),
            ('o4', 'u1', '2026-09-07', 'a'),
            ('o4', 'u1', '2026-09-07', 'c'),
            ('o5', 'u2', '2026-09-07', 'b'),
            ('o6', 'u4', '2026-09-07', 'c'),
        ]
        result = analyze(sales, {'u1': 'B2B', 'u2': 'B2C'})
        self.assertEqual(result['rows'], {'train': 3, 'gap': 0, 'test': 5})
        self.assertEqual(result['cohorts']['A']['size'], 2)
        self.assertEqual(result['cohorts']['B']['size'], 1)
        self.assertEqual(result['cohorts']['A']['B2B'], 1)
        self.assertEqual(result['cohorts']['A']['B2C'], 1)
        self.assertAlmostEqual(result['metrics']['popularity']['A']['precision_at_3'], 1/6)
        self.assertEqual(result['metrics']['copurchase']['A']['short_lists'], 2)
        self.assertAlmostEqual(result['metrics']['copurchase']['A']['fill_rate'], 0.5)

    def test_boundary_ties_counted_deterministically_and_future_never_ranked(self):
        sales = [('o1', 'u', '2026-08-31', 'a')]
        for i in ['b', 'c', 'd', 'e']:
            sales.append(('shared', 'other', '2026-08-31', i))
            sales.append(('shared', 'other', '2026-08-31', 'a'))
        sales += [('test', 'u', '2026-09-07', 'b'),
                  ('test', 'u', '2026-09-07', 'future')]
        r = analyze(sales, {'u': 'B2B'})
        self.assertEqual(r['metrics']['copurchase']['A']['boundary_ties'], 1)
        self.assertAlmostEqual(r['metrics']['copurchase']['A']['precision_at_3'], 1/3)
        self.assertAlmostEqual(r['metrics']['popularity']['A']['precision_at_3'], 1/3)


if __name__ == '__main__':
    unittest.main()
