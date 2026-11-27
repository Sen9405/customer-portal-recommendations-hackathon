"""Validate the public research artifact, not private identity or compatibility."""
import json
import sqlite3
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "sources/public_catalog_pair_evidence_20261126.json"
CATALOG = ROOT / "data/catalog-20261126.sqlite"


@unittest.skipUnless(ARTIFACT.is_file(), 'Requires the local evidence snapshot')
class PublicPairEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(ARTIFACT.read_text(encoding="utf-8"))

    def test_sources_and_relation_limits(self):
        d = self.data
        self.assertEqual(len(d["relations"]), 3)
        self.assertEqual(len({r["id"] for r in d["relations"]}), 3)
        for relation in d["relations"]:
            with self.subTest(relation=relation["id"]):
                self.assertIn(relation["status"], {
                    "conditional_product_level_evidence_not_attested",
                    "limited_candidate_only_not_attested",
                })
                self.assertIn(relation["subject_offer_id"], d["offers"])
                self.assertIn(relation["object_offer_id"], d["offers"])
                self.assertNotEqual(relation["subject_offer_id"], relation["object_offer_id"])
                self.assertTrue(relation["permitted_claim"])
                self.assertTrue(relation["conditions_and_unknowns"])
                self.assertTrue(relation["sources"])
                for key in relation["sources"]:
                    source = d["manufacturer_sources"][key]
                    self.assertEqual(urlparse(source["url"]).scheme, "https")
                    self.assertTrue(source["quotes"])
        self.assertEqual(d["relations"][2]["status"], "limited_candidate_only_not_attested")
        self.assertIn("CU15", d["relations"][2]["permitted_claim"])
        self.assertIn("conflict", d["offers"]["202511"])
        self.assertIn("conflict", d["offers"]["3798"])
        self.assertIn("not verified historical", d["temporal_limit"])

    @unittest.skipUnless(CATALOG.exists(), "optional public catalog snapshot absent")
    def test_public_offer_snapshot_matches(self):
        with sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True) as db:
            for offer_id, expected in self.data["offers"].items():
                with self.subTest(offer_id=offer_id):
                    rows = db.execute(
                        "SELECT name, url, available FROM offers WHERE offer_id = ?",
                        (offer_id,),
                    ).fetchall()
                    self.assertEqual(len(rows), 1)
                    name, url, available = rows[0]
                    self.assertEqual(" ".join(name.split()), " ".join(expected["name"].split()))
                    self.assertEqual(url, expected["url"])
                    self.assertEqual(available, str(expected["snapshot_available"]).lower())


if __name__ == "__main__":
    unittest.main()
