import copy
import unittest

from recommendation.provenance_links import build_graph, demo_inputs


class ProvenanceLinksTests(unittest.TestCase):
    def setUp(self):
        self.snapshot, self.registry, self.mappings = demo_inputs()

    def graph(self):
        return build_graph(self.snapshot, self.registry, self.mappings)

    def test_explicit_reviewed_pair_only(self):
        graph = self.graph()
        self.assertEqual(len(graph), 1)
        self.assertEqual(graph[0]['relation']['permitted_claim'], self.registry['relations'][0]['permitted_claim'])
        self.assertIn('not compatibility', graph[0]['limitations'])
        self.mappings.pop()
        self.assertEqual(self.graph(), [])

    def test_bad_review_and_evidence_fail_closed(self):
        for field, value in [('status', 'draft'), ('sku', ''), ('edition_caveats', ''),
                             ('reviewed_by', ''), ('reviewed_at', 'bad'),
                             ('evidence_url', 'http://example.org'), ('evidence_locator', ''),
                             ('evidence_excerpt', ''), ('snapshot_sha256', 'bad')]:
            with self.subTest(field=field):
                old = self.mappings[0][field]
                self.mappings[0][field] = value
                with self.assertRaises(ValueError): self.graph()
                self.mappings[0][field] = old

    def test_no_name_matching_or_stale_or_ambiguous_binding(self):
        self.snapshot['items']['item-a']['candidate']['offer_id'] = 'different'
        with self.assertRaises(ValueError): self.graph()
        self.snapshot, self.registry, self.mappings = demo_inputs()
        self.snapshot['items']['other'] = copy.deepcopy(self.snapshot['items']['item-a'])
        with self.assertRaises(ValueError): self.graph()

    def test_invalid_relation_or_duplicate_mapping(self):
        self.registry['relations'][0]['url'] = 'https://example.org/fake'
        with self.assertRaises(ValueError): self.graph()
        self.snapshot, self.registry, self.mappings = demo_inputs()
        self.mappings.append(copy.deepcopy(self.mappings[0]))
        with self.assertRaises(ValueError): self.graph()

    def test_unknown_relation_or_side_rejected(self):
        for key, value in [('relation_id', 'unknown'), ('side', 'both'), ('item_id', 'unknown')]:
            with self.subTest(key=key):
                old = self.mappings[0][key]
                self.mappings[0][key] = value
                with self.assertRaises(ValueError): self.graph()
                self.mappings[0][key] = old

    def test_historical_mode_rejected(self):
        with self.assertRaises(ValueError):
            build_graph(self.snapshot, self.registry, self.mappings, cutoff='2026-08-31T23:59:59Z')


if __name__ == '__main__':
    unittest.main()
