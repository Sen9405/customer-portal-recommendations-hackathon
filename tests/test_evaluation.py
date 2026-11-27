import json
import subprocess
import sys
import unittest

from evaluation import Event, chronological_split, evaluate, popularity_recommendations


class EvaluationTests(unittest.TestCase):
    def test_precision_unique_hits_and_fixed_denominator(self):
        result = evaluate({'a': ['x', 'x', 'y', 'z']}, {'a': {'x', 'y'}})
        self.assertAlmostEqual(result['precision_at_3'], 2 / 3)
        self.assertAlmostEqual(result['fill_rate'], 1.0)
        self.assertAlmostEqual(result['slot_fill_rate'], 2 / 3)

    def test_empty_truth_excluded_and_missing_predictions_are_zero(self):
        result = evaluate({'empty': ['x'], 'one': ['x']},
                          {'empty': set(), 'one': {'x'}, 'missing': {'y'}})
        self.assertEqual(result['evaluated_users'], 2)
        self.assertAlmostEqual(result['precision_at_3'], 1 / 6)
        self.assertAlmostEqual(result['fill_rate'], 2 / 3)
        self.assertAlmostEqual(result['slot_fill_rate'], 2 / 9)
        self.assertEqual(evaluate({}, {'empty': set()})['precision_at_3'], None)
        self.assertEqual(evaluate({}, {})['fill_rate'], None)

    def test_chronology_equal_timestamp_never_split(self):
        events = [Event('a', 'x', 1), Event('a', 'y', 2),
                  Event('b', 'z', 2), Event('b', 'x', 3)]
        train, test = chronological_split(events, 0.5)
        self.assertEqual([x.timestamp for x in train], [1])
        self.assertEqual([x.timestamp for x in test], [2, 2, 3])
        self.assertLess(max(x.timestamp for x in train), min(x.timestamp for x in test))
        same = [Event('a', 'x', 1), Event('b', 'y', 1)]
        self.assertEqual(chronological_split(same, 0.5), ([], same))
        with self.assertRaises(ValueError):
            chronological_split(events, 0)

    def test_popularity_train_only_ties_and_seen_exclusion(self):
        train = [Event('a', 'x', 1), Event('b', 'x', 2),
                 Event('a', 'y', 2), Event('b', 'z', 2)]
        recs = popularity_recommendations(train, ['a', 'cold'])
        self.assertEqual(recs['a'], ['z'])
        self.assertEqual(recs['cold'], ['x', 'y', 'z'])
        self.assertNotIn('future', recs['cold'])
        self.assertEqual(popularity_recommendations([], ['cold']), {'cold': []})

    def test_cli_synthetic(self):
        result = subprocess.run([sys.executable, '-B', '-m', 'evaluation'],
                                check=True, text=True, capture_output=True)
        data = json.loads(result.stdout)
        self.assertEqual(data['data'], 'synthetic')
        self.assertGreater(data['evaluated_users'], 0)
        self.assertIn('precision_at_3', data)


if __name__ == '__main__':
    unittest.main()
