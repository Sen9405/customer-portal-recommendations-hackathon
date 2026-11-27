import unittest
from recommendation.catalog_similarity import build_index, recommend, backtest


def item(offer, category, vendor):
    return {'candidate': {'offer_id': offer, 'category_id': category, 'vendor': vendor}}


class SimilarityTest(unittest.TestCase):
    def test_cold_evidence_and_namespace(self):
        index = build_index({'items': {'1': item('a', 'x', 'v'), '2': item('b', 'x', 'v'), '3': item('c', 'x', 'w')}})
        result = recommend(index, {'1'})
        self.assertEqual([r['item_id'] for r in result], ['2', '3'])
        self.assertEqual(result[0]['evidence'], ['same_category', 'same_vendor'])
        self.assertEqual(result[1]['evidence'], ['same_category'])
        self.assertEqual(recommend(index, {'unknown'}), [])

    def test_ambiguous_identity_and_missing_features(self):
        index = build_index({'items': {'a': item('same', 'x', 'v'), 'b': item('same', 'x', 'v'), 'c': item(None, 'x', 'v'), 'd': item('d', None, None)}})
        self.assertEqual(set(index['items']), {'d'})
        self.assertEqual(recommend(index, {'d'}), [])

    def test_split_cold_relevance_and_gap(self):
        sales = [('o1', 'u', '2026-08-31', 'a'), ('o2', 'u', '2026-09-02', 'gap'), ('o3', 'u', '2026-09-07', 'b')]
        snapshot = {'items': {'a': item('a', 'x', 'v'), 'b': item('b', 'x', 'v')}}
        report = backtest(sales, {'u': 'B2B'}, snapshot)
        self.assertEqual(report['cohorts']['A']['n'], 1)
        self.assertEqual(report['cohorts']['A']['hits'], 1)
        self.assertEqual(report['cohorts']['A']['cold_hits'], 1)
        self.assertEqual(report['rows'], {'train': 1, 'gap': 1, 'test': 1})
