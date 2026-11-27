import unittest
from recommendation.sequential_diagnostics import audit_window, lexical_variant, structures, transitions


class SequentialDiagnosticsTest(unittest.TestCase):
    def test_reuse_transition_loo_and_fallback(self):
        rows = [
            ('same', 'u', '2026-01-01', 'a'),
            ('same', 'u', '2026-01-08', 'b'),
            ('third', 'u', '2026-01-15', 'a'),
            ('same', 'u', '2026-01-22', 'b'),
            ('o', 'v', '2026-01-01', 'c'),
        ]
        out = audit_window(rows, '2026-01-15', '2026-01-22', '2026-01-29')
        self.assertEqual(out['order_ids_multiple_weeks'], 1)
        self.assertEqual(out['order_ids_across_split'], 1)
        self.assertEqual(out['repeated_identical_client_week_baskets_after_first'], 2)
        # b is already seen, hence no novel target; no cohort leakage.
        self.assertEqual(out['cohort_A'], 0)
        weeks, _ = structures(rows)
        self.assertEqual(transitions(weeks['u'])['a']['b'], 2)

    def test_loo_graph_and_attribution(self):
        rows = [('1','u','2026-01-01','a'), ('2','u','2026-01-08','b'),
                ('3','u','2026-01-15','a'), ('4','u','2026-01-22','c'),
                ('5','v','2026-01-01','c'), ('6','v','2026-01-08','a'),
                ('7','w','2026-01-01','a')]
        out = audit_window(rows, '2026-01-15', '2026-01-22', '2026-01-29')
        self.assertEqual(out['cohort_A'], 1)
        self.assertEqual(out['counts']['hits'], 1)
        self.assertEqual(out['counts']['graph_hits'] + out['counts']['fallback_hits'], 1)
        self.assertEqual(out['counts']['graph_slots'] + out['counts']['fallback_slots'], 1)

    def test_name_proxy_and_bad_window(self):
        self.assertTrue(lexical_variant('License product edition 100', 'License product edition 200'))
        self.assertFalse(lexical_variant(None, None))
        with self.assertRaises(ValueError):
            audit_window([], '2026-01-01', '2026-01-01', '2026-02-01')

    def test_lexical_ablation_and_future_isolation(self):
        rows = [('1','x','2026-01-01','a'), ('2','x','2026-01-08','b'),
                ('3','u','2026-01-01','a'), ('4','v','2026-01-01','c'),
                ('5','v','2026-01-01','c'), ('6','v','2026-01-01','c'),
                ('7','v','2026-01-01','d'), ('8','v','2026-01-01','d'),
                ('9','v','2026-01-01','d'), ('10','v','2026-01-01','e'),
                ('11','v','2026-01-01','e'), ('12','v','2026-01-01','e'),
                ('13','u','2026-01-15','b')]
        names = {'a':'license edition 100', 'b':'license edition 200'}
        out = audit_window(rows, '2026-01-08', '2026-01-15', '2026-01-22', names)
        self.assertEqual(out['counts']['hits'], 1)
        self.assertEqual(out['counts']['lexical_ablation_hits'], 0)
        self.assertEqual(out['counts']['lexical_ablation_changed_lists'], 1)
        extra = rows + [('later','u','2026-02-01','z')]
        self.assertEqual(out, audit_window(extra, '2026-01-08', '2026-01-15', '2026-01-22', names))


if __name__ == '__main__':
    unittest.main()
