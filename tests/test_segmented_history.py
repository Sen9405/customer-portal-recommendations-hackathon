import unittest
from recommendation.segmented_history import fit, recommend, split_rows


class SegmentedHistoryTests(unittest.TestCase):
    def test_split_respects_boundaries(self):
        rows = [('a', 'u', '2026-05-25', 'x'), ('b', 'u', '2026-06-01', 'y'), ('c', 'u', '2026-09-07', 'z')]
        train, test = split_rows(rows, '2026-05-25', '2026-06-01', '2026-08-31')
        self.assertEqual([r[3] for r in train], ['x'])
        self.assertEqual([r[3] for r in test], ['y'])

    def test_profile_support_and_b2c_no_profile(self):
        rows = [('o1', 'a', '2026-01-01', 'x'), ('o2', 'b', '2026-01-01', 'y'), ('o3', 'c', '2026-01-01', 'z')]
        profiles = {'a': ('B2B', 's', 'i'), 'b': ('B2B', 's', 'j'), 'c': ('B2C', 's', 'i')}
        model = fit(rows, profiles, min_support=2)
        self.assertEqual(recommend(model, 'a', profiles['a'], 'segment_pop')[0], 'y')
        self.assertEqual(recommend(model, 'a', ('B2B', 'unknown', 'i'), 'segment_pop'), recommend(model, 'a', profiles['a'], 'group_pop'))
        self.assertEqual(recommend(model, 'c', profiles['c'], 'segment_pop'), recommend(model, 'c', profiles['c'], 'group_pop'))
        self.assertNotIn('x', recommend(model, 'a', profiles['a'], 'segment_pop'))

    def test_graph_fallback_and_no_cross_user_order_join(self):
        rows = [('o', 'a', '2026-01-01', 'x'), ('o', 'b', '2026-01-01', 'y'), ('p', 'b', '2026-01-01', 'z')]
        profiles = {'a': ('B2B', None, None), 'b': ('B2B', None, None)}
        model = fit(rows, profiles, min_support=2)
        self.assertFalse(model['graphs']['B2B'].get('x'))
        self.assertEqual(recommend(model, 'a', profiles['a'], 'group_graph'), ['y', 'z'])

if __name__ == '__main__':
    unittest.main()
