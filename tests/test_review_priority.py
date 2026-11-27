import os
from pathlib import Path
import tempfile
import unittest

from catalog.identity_review import create, decide, counts
from catalog.review_priority import prioritize, private_output, read_cases


class PriorityTest(unittest.TestCase):
    def test_train_only_dedup_cold_and_status(self):
        cases = [('a', 1, None), ('b', 1, None), ('c', 0, None), ('d', 1, 'reviewed')]
        groups = {'u': 'B2B', 'v': 'B2B', 'w': 'B2C'}
        rows = [('u', '2026-08-31', 'a'), ('u', '2026-08-31', 'a'),
                ('v', '2026-08-01', 'b'), ('v', '2026-08-01', 'c'),
                ('w', '2026-08-01', 'b')]
        ranked, aggregate = prioritize(rows, groups, cases)
        self.assertEqual([r['item_id'] for r in ranked], ['a', 'b', 'c'])
        self.assertEqual(ranked[0]['b2b_cold_train_users'], 1)
        self.assertEqual(ranked[0]['train_users'], 1)
        self.assertEqual(aggregate['pending']['distinct_train_users'], 3)
        future = [('u', '2026-09-01', 'b'), ('new', '2026-09-07', 'c')]
        self.assertEqual((ranked, aggregate), prioritize(rows + future, groups, cases))
        self.assertEqual(prioritize([], {}, cases)[1]['pending']['distinct_train_users'], 0)

    def test_read_only_latest_and_safe_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / 'queue.sqlite'
            create(db, {'items': {'a': {'matches': {'all_union': [{'offer_id': 'x'}]}},
                                  'b': {'matches': {'all_union': []}}}})
            decide(db, {'item_id': 'a', 'status': 'rejected', 'reviewer': 'human',
                        'evidence': {'source': 'x', 'exact_excerpt': 'x', 'rationale': 'x'}})
            before = counts(db)
            records, _ = prioritize([], {}, read_cases(db))
            self.assertEqual([r['item_id'] for r in records], ['b'])
            self.assertEqual(counts(db), before)
            dest = root / 'queue.json'
            private_output(dest, records, root)
            self.assertEqual(os.stat(dest).st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                private_output(dest, [], root)
            with self.assertRaises(ValueError):
                private_output(root.parent / 'bad.json', [], root)
            link = root / 'link'
            link.symlink_to(dest)
            with self.assertRaises(FileExistsError):
                private_output(link, [], root)


if __name__ == '__main__':
    unittest.main()
