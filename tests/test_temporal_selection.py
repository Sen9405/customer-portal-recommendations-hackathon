import unittest
from recommendation.temporal_selection import fit, recommend, evaluate, select, split_rows


class TemporalSelectionTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            ('o1', 'u1', '2026-01-01', 'a'),
            ('o1', 'u1', '2026-01-01', 'b'),
            ('o2', 'u1', '2026-01-08', 'c'),
            ('o3', 'u2', '2026-01-01', 'a'),
            ('o4', 'u2', '2026-01-08', 'c'),
            ('o5', 'u3', '2026-01-01', 'a'),
        ]

    def test_split_boundary_and_no_future(self):
        train, future = split_rows(self.rows + [('f','u3','2026-01-15','z')], '2026-01-08', '2026-01-15')
        m = fit(train)
        self.assertNotIn('z', m['pop'])
        self.assertEqual(len(future), 1)
        with self.assertRaises(ValueError):
            split_rows(self.rows, '2026-01-08', '2026-01-08')

    def test_weekly_sequential_not_simultaneous(self):
        m = fit(self.rows)
        self.assertEqual(m['seq']['a']['c'], 2)
        self.assertEqual(m['seq']['b']['c'], 1)
        self.assertNotIn('b', m['seq']['a'])
        self.assertEqual(m['co']['a']['b'], 1)
        self.assertEqual(recommend(m, 'u3', 'sequential')[0], 'c')
        self.assertNotIn('a', recommend(m, 'u3', 'sequential'))

    def test_grouping_reused_orders_and_fallback(self):
        m = fit([('same','x','2026-01-01','a'), ('same','y','2026-01-01','b'), ('same','x','2026-01-08','c')])
        self.assertNotIn('b', m['co']['a'])
        self.assertEqual(recommend(m, 'y', 'coorder'), ['a', 'c'])

    def test_metrics_and_selection_tie(self):
        m = fit(self.rows)
        out = evaluate(m, [('f','u3','2026-01-15','c')], 'sequential')
        self.assertEqual(out['A']['hits'], 1)
        self.assertEqual(out['A']['precision_at_3'], 1/3)
        self.assertEqual(select({'popularity': out, 'coorder': out, 'sequential': out}), 'popularity')
        with self.assertRaises(ValueError):
            select({'popularity': {'A': {'n': 0, 'hits': 0}}})


if __name__ == '__main__':
    unittest.main()
