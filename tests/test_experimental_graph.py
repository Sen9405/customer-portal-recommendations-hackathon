import unittest
from recommendation.experimental_graph import fit, recommend, backtest


class ExperimentalGraphTests(unittest.TestCase):
    def test_graph_distinct_orders_fallback_and_determinism(self):
        train = [('o1', 'u', '2026-08-31', 'a'),
                 ('o1', 'u', '2026-08-31', 'b'),
                 ('o1', 'u', '2026-08-31', 'b'),
                 ('o2', 'v', '2026-08-31', 'c')]
        model = fit(train)
        self.assertEqual(model['pairs']['a']['b'], 1)
        self.assertEqual(recommend(model, {'a'}, 'count'), ['b'])
        self.assertEqual(recommend(model, {'a'}, 'count_fallback'), ['b', 'c'])
        self.assertEqual(recommend(model, set(), 'count_fallback'), ['b', 'a', 'c'])
        self.assertEqual(recommend(model, {'a'}, 'cosine_fallback'), ['b', 'c'])

    def test_split_cohorts_leakage_and_fixed_denominator(self):
        rows = [('shared', 'u', '2026-08-31', 'a'),
                ('shared', 'v', '2026-08-31', 'b'),
                ('shared', 'u', '2026-09-07', 'future'),
                ('gap', 'v', '2026-09-01', 'future'),
                ('x', 'u', '2026-09-07', 'b')]
        result = backtest(rows, {'u': 'B2B'})
        self.assertEqual(result['cohorts']['A']['size'], 1)
        self.assertEqual(result['cohorts']['B']['size'], 1)
        self.assertEqual(result['metrics']['count']['A']['precision_at_3'], 1/3)
        self.assertEqual(result['metrics']['count']['A']['full_coverage'], 0)
        self.assertEqual(result['metrics']['count']['A']['partial_coverage'], 1)
        self.assertEqual(result['metrics']['count']['B_B2C']['precision_at_3'], None)


if __name__ == '__main__':
    unittest.main()
