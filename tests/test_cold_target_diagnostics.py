import unittest

from recommendation.cold_target_diagnostics import diagnose, BUCKETS


def fixture(target_matches, source_category: str | None = 'C', extras=()):
    candidates = {'s': [('src', source_category)], 'z': target_matches}
    candidates.update(dict(extras))
    audit = {'feed': {'date': 'fixture'}, 'items': {}}
    snapshot = {'feed': {'date': 'fixture'}, 'items': {}}
    for item, matches in candidates.items():
        audit['items'][item] = {'matches': {'all_union': [{'offer_id': offer} for offer, _ in matches]}}
        if len(matches) == 1:
            offer, category = matches[0]
            snapshot['items'][item] = {'candidate': {'offer_id': offer, 'category_id': category, 'vendor': ''}}
    rows = [('1', 'u', '2026-05-01', 's'), ('2', 'u', '2026-06-01', 'z')]
    return rows, ['source', 'target'], audit, snapshot


class ColdTargetDiagnosticsTests(unittest.TestCase):
    def test_first_failure_and_hit_buckets(self):
        cases = [
            ('no_candidate', fixture([])),
            ('ambiguous_candidate', fixture([('a', 'C'), ('b', 'C')])),
            ('unique_missing_offer_id', fixture([(None, 'C')])),
            ('unique_duplicate_offer_id', fixture([('dup', 'C')], extras=[('other', [('dup', 'C')])])),
            ('target_missing_category', fixture([('target', None)])),
            ('source_no_indexed_category', fixture([('target', 'C')], source_category=None)),
            ('source_category_mismatch', fixture([('target', 'D')])),
            ('viable_top3_hit', fixture([('target', 'C')])),
            ('viable_ranking_lost', fixture([('target', 'C')], extras=[('a', [('a', 'C')]), ('b', [('b', 'C')]), ('c', [('c', 'C')])])),
        ]
        self.assertEqual({name for name, _ in cases}, set(BUCKETS))
        for name, args in cases:
            with self.subTest(bucket=name):
                result = diagnose(*args)
                self.assertEqual(result['cold_target_client_items'], 1)
                self.assertEqual(result['disjoint_first_failure_or_hit'][name], 1)
                self.assertEqual(sum(result['disjoint_first_failure_or_hit'].values()), 1)
                self.assertEqual(result['reachable_before_top3'], int(name.startswith('viable_')))
                self.assertEqual(result['hybrid_top3_cold_hits'], int(name == 'viable_top3_hit'))

    def test_pair_dedup_and_outcome_cohort(self):
        rows, names, audit, snapshot = fixture([('target', 'C')])
        rows += [('3', 'u', '2026-06-08', 'z'), ('4', 'new', '2026-06-08', 'z')]
        names += ['target', 'target']
        result = diagnose(rows, names, audit, snapshot)
        self.assertEqual(result['cohort_clients'], 1)
        self.assertEqual(result['cold_target_client_items'], 1)

    def test_reject_different_snapshots(self):
        args = fixture([('target', 'C')])
        args[3]['feed']['date'] = 'other'
        with self.assertRaises(ValueError):
            diagnose(*args)

    def test_reject_inconsistent_candidates(self):
        args = fixture([('target', 'C')])
        args[3]['items']['z']['candidate']['offer_id'] = 'changed'
        with self.assertRaises(ValueError):
            diagnose(*args)


if __name__ == '__main__':
    unittest.main()
