import unittest
from recommendation.profile_pipeline import ProfilePipeline


class PipelineTest(unittest.TestCase):
    def test_history_unique_and_dates_and_cold(self):
        rows = [('o','u','2026-01-05','a'), ('o','u','2026-01-05','a'),
                ('o','u','2026-01-12','b'), ('x','u','2026-01-12','b'),
                ('z','u','2026-02-01','future')]
        p = ProfilePipeline(rows, '2026-01-20')
        r = p.profile('u')
        self.assertEqual(r['history_items'], ['a', 'b'])
        self.assertEqual(r['history']['order_count'], 3)
        self.assertEqual(r['history']['week_count'], 2)
        self.assertEqual(r['item_history'][1]['week_count'], 1)
        self.assertEqual(r['item_history'][1]['order_count'], 2)
        self.assertEqual(r['history']['week_gaps_days'], [7])
        self.assertEqual(r['history']['recency_days'], 8)
        self.assertEqual(p.profile('missing')['status'], 'cold_no_observed_history')
        self.assertNotIn('future', [x['item_id'] for x in r['candidates']])

    def test_graph_scoping_no_future_determinism_and_fallback(self):
        rows = [('same','u','2026-01-05','a'), ('same','v','2026-01-05','b'),
                ('same','v','2026-01-12','c'), ('q','v','2026-01-19','d'),
                ('z','w','2026-01-05','a'), ('z','w','2026-01-05','e'),
                ('z','w','2026-02-02','future')]
        p = ProfilePipeline(rows,'2026-01-20')
        r = p.profile('u')
        e = next(x for x in r['candidates'] if x['item_id']=='e')
        self.assertEqual(e['evidence']['coorder'][0]['order_count'], 1)
        self.assertNotIn('b', [x['item_id'] for x in r['candidates'] if x['method']=='association'])
        self.assertNotIn('future', [x['item_id'] for x in r['candidates']])
        self.assertEqual(r, ProfilePipeline(list(reversed(rows)),'2026-01-20').profile('u'))
        self.assertTrue(any(x['method']=='popularity_fallback' for x in r['candidates']))
        self.assertLessEqual(len(r['candidates']),3)

    def test_sequence_catalog_ambiguity_and_attributes(self):
        rows = [('1','u','2026-01-01','a'), ('2','v','2026-01-01','a'), ('3','v','2026-01-08','b')]
        cat = {'b': {'candidate': {'offer_id':'9', 'category':'cat'}, 'matches': ['9','10'], 'offer_claim_count': 2}}
        p = ProfilePipeline(rows, '2026-01-10', catalog=cat, customers={'u': {'group':'B2B','industry':'industry'}})
        r = p.profile('u')
        b = next(x for x in r['candidates'] if x['item_id']=='b')
        self.assertEqual(b['evidence']['sequential'][0]['transition_count'], 1)
        self.assertTrue(b['catalog']['ambiguous'])
        self.assertEqual(b['catalog']['identity'], 'unverified')
        self.assertEqual(r['customer_attributes']['segment']['value'], None)
        self.assertEqual(r['customer_attributes']['industry']['temporal_status'], 'undated')
        self.assertEqual(len(list(p.profiles(['u','unknown']))), 2)

    def test_recency_and_frequency_change_coorder_ranking(self):
        links = [('x','v','2026-01-01','a'), ('x','v','2026-01-01','x'),
                 ('y','w','2026-01-01','b'), ('y','w','2026-01-01','y')]
        def winner(history):
            return ProfilePipeline(history + links, '2026-04-01').profile('u')['candidates'][0]['item_id']
        self.assertEqual(winner([('1','u','2026-03-01','a'), ('2','u','2026-01-01','b')]), 'x')
        self.assertEqual(winner([('1','u','2026-01-01','a'), ('2','u','2026-03-01','b')]), 'y')
        self.assertEqual(winner([('1','u','2026-03-01','a'), ('2','u','2026-03-01','b'), ('3','u','2026-03-01','b')]), 'y')
        self.assertEqual(winner([('1','u','2026-03-01','a'), ('2','u','2026-03-01','b'), ('2','u','2026-03-01','b')]), 'x')

    def test_invalid_dates_and_nulls(self):
        with self.assertRaises(ValueError): ProfilePipeline([('1','u',None,'a')], '2026-01-01')
        with self.assertRaises(ValueError): ProfilePipeline([('1',None,'2026-01-01','a')], '2026-01-01')

if __name__ == '__main__': unittest.main()
