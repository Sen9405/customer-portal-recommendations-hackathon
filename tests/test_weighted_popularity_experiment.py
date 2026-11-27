import unittest
from recommendation.weighted_popularity_experiment import weighted_rank, run
from recommendation.profile_pipeline import ProfilePipeline
from recommendation.profile_v4_evaluation import official_models
from recommendation.profile_v4_diagnostics import rank
from recommendation.early_popularity_experiment import CUTOFF

class WeightedTest(unittest.TestCase):
    def setUp(self):
        self.train=[('a','u','2026-05-04','a'),('a','u','2026-05-04','b'),
                    ('b','v','2026-05-04','a'),('b','v','2026-05-04','c'),
                    ('c','w','2026-05-04','c'),('d','x','2026-05-04','d')]
        self.p=ProfilePipeline(self.train,CUTOFF)
        self.pop,_=official_models(self.train,self.p.history)
    def test_seen_excluded_and_train_only(self):
        result=weighted_rank(self.p,'u',self.pop)
        self.assertTrue(set(result)<=set(self.pop)-self.p.history['u'])
        self.assertEqual(len(result),len(set(result)))
    def test_zero_personal_and_invalid_weight(self):
        result=weighted_rank(self.p,'unknown',self.pop)
        self.assertEqual(result[0], 'a')
        with self.assertRaises(ValueError): weighted_rank(self.p,'u',self.pop,1.5)
    def test_window_excludes_official_future(self):
        base=self.train+[('e','u','2026-06-01','c')]
        a=run(base,{'u':'B2B'})
        b=run(base+[('f','u','2026-09-07','d')],{'u':'B2B'})
        self.assertEqual(a,b)
    def test_personal_only_agrees_with_profile_graph_ranking(self):
        # With all alternatives connected, equal-personal tie rules are not needed.
        self.assertEqual(weighted_rank(self.p,'u',self.pop,1)[:2],rank(self.p,'u')[:2])
