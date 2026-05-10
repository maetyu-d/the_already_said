from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import normalize_citation_style
from engine import compose_quotation_text, normalize_match_options
from scripts.build_index import build


ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "corpus"


class ComposeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tempdir.name) / "gutenberg.db"
        build(CORPUS_DIR, cls.db_path, chunk_size=950, overlap=180)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tempdir.cleanup()

    def test_empty_draft_has_empty_contract_lists(self) -> None:
        payload = compose_quotation_text("", "oxford", db_path=self.db_path)

        self.assertEqual(payload["matches"], [])
        self.assertEqual(payload["segments"], [])
        self.assertEqual(payload["notes"], [])

    def test_segments_report_matched_and_missing_sentences(self) -> None:
        payload = compose_quotation_text(
            "Call me Ishmael. Marley was dead: to begin with.",
            "oxford",
            db_path=self.db_path,
        )

        self.assertEqual(len(payload["segments"]), 2)
        self.assertEqual(payload["segments"][0]["status"], "matched")
        self.assertEqual(payload["segments"][0]["components"][0]["title"], "Moby-Dick; or, The Whale")
        self.assertEqual(payload["segments"][1]["status"], "missing")
        self.assertEqual(payload["segments"][1]["components"], [])
        self.assertEqual(len(payload["matches"]), 1)
        self.assertEqual(len(payload["notes"]), 1)

    def test_match_components_include_quality_metadata(self) -> None:
        payload = compose_quotation_text("Call me Ishmael.", "harvard", db_path=self.db_path)
        component = payload["matches"][0]["components"][0]

        self.assertGreater(component["quality"], 0)
        self.assertEqual(component["matchedBy"], "search")
        self.assertEqual(component["lens"]["matchType"], "exact quotation")
        self.assertIn("sourceExcerpt", component["lens"])
        self.assertIn("alternatives", component)

    def test_composite_recovery_exposes_clause_parts(self) -> None:
        payload = compose_quotation_text(
            "Call me Ishmael, a single man in possession of a good fortune must be in want of a wife.",
            "harvard",
            db_path=self.db_path,
            options={"mode": "uncanny", "allow_composite": True, "min_confidence": 2.8},
        )
        match = payload["matches"][0]

        self.assertTrue(match["composite"])
        self.assertEqual(len(match["clauses"]), 2)
        self.assertEqual(match["components"][0]["clauseIndex"], 0)
        self.assertEqual(match["components"][0]["clauseCount"], 2)
        self.assertEqual(match["components"][0]["title"], "Moby-Dick; or, The Whale")
        self.assertEqual(match["components"][1]["title"], "Pride and Prejudice")

    def test_strict_options_preserve_exact_recovery(self) -> None:
        strict = compose_quotation_text(
            "Call me Ishmael.",
            "harvard",
            db_path=self.db_path,
            options={"mode": "strict", "prefer_exact": True, "min_confidence": 4.7},
        )

        self.assertEqual(strict["options"]["mode"], "strict")
        self.assertTrue(strict["options"]["prefer_exact"])
        self.assertEqual(len(strict["matches"]), 1)
        self.assertEqual(strict["matches"][0]["components"][0]["lens"]["matchType"], "exact quotation")


class AppContractTest(unittest.TestCase):
    def test_invalid_citation_style_falls_back_to_harvard(self) -> None:
        self.assertEqual(normalize_citation_style("oxford"), "oxford")
        self.assertEqual(normalize_citation_style("nope"), "harvard")
        self.assertEqual(normalize_citation_style(None), "harvard")

    def test_match_options_are_normalized(self) -> None:
        options = normalize_match_options(
            {
                "mode": "elsewhere",
                "allow_composite": False,
                "min_confidence": 99,
                "prefer_exact": True,
            }
        )

        self.assertEqual(options["mode"], "associative")
        self.assertFalse(options["allow_composite"])
        self.assertEqual(options["min_confidence"], 8.0)
        self.assertTrue(options["prefer_exact"])


if __name__ == "__main__":
    unittest.main()
