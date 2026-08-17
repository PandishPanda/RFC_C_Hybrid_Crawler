"""The validate harness (ticket 23) -- zero network for everything except
what it deliberately re-measures offline."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import validate  # noqa: E402


class VerdictLogicTest(unittest.TestCase):
    def test_upper_bound_matches_clopper_pearson(self):
        self.assertAlmostEqual(validate._upper_bound(33), 8.68, places=1)
        self.assertAlmostEqual(validate._upper_bound(7), 34.8, places=1)
        self.assertAlmostEqual(validate._upper_bound(4), 52.7, places=1)

    def test_a_wrong_sample_verdict_fails_the_row(self):
        rows = []
        data = {"date": "d", "judge": "j", "items": [
            {"sheet": "A", "verdict": "ok"},
            {"sheet": "A", "verdict": "wrong"}]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "v.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            original = validate.VERDICTS
            validate.VERDICTS = path
            try:
                validate._read_sample_verdicts(rows)
            finally:
                validate.VERDICTS = original
        sheet_a = next(r for r in rows if "resolution" in r["metric"])
        self.assertEqual(sheet_a["verdict"], "FAIL")

    def test_missing_verdicts_file_is_pending_never_pass(self):
        rows = []
        original = validate.VERDICTS
        validate.VERDICTS = Path("/nonexistent/v.json")
        try:
            validate._read_sample_verdicts(rows)
        finally:
            validate.VERDICTS = original
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["verdict"], "PENDING")


class RenderTest(unittest.TestCase):
    def test_marks_distinguish_knowing_from_assuming(self):
        rows = [validate._row("1 X", "m1", "v", "b", "PASS"),
                validate._row("1 X", "m2", "v", "b", "PROXY"),
                validate._row("1 X", "m3", "v", "b", "PENDING"),
                validate._row("1 X", "m4", "v", "b", "FAIL")]
        text = validate.render_scorecard(rows)
        for mark in ("✓", "≈", "…", "✗"):
            self.assertIn(mark, text)
        self.assertIn("assumption made visible", text)

    def test_no_university_named_verdict_anywhere_in_a_full_run(self):
        # Alexander's locked-format reaction: precision verdicts attach
        # to seed batches, never universities. Checked over the FULL real
        # row set, not two hardcoded literals (review finding: the first
        # version was vacuous).
        from crawler.config import load_configs_dir
        unis = set(load_configs_dir("crawler/configs"))
        for r in validate.run_checks():
            for uni in unis:
                self.assertNotIn(uni, r["verdict"],
                                 "verdict must never name a university")


class ArchiveTest(unittest.TestCase):
    def test_archive_never_overwrites(self):
        # Review finding: the first version never wrote twice, so the
        # collision guard could be deleted and this still passed. Now the
        # second write uses the SAME stamp and must refuse.
        rows = [validate._row("1 X", "m", "v", "b", "PASS")]
        with tempfile.TemporaryDirectory() as td:
            original = validate.OUT_DIR
            validate.OUT_DIR = Path(td)
            try:
                p1, m1 = validate.write_archive(rows, "text", stamp="S1")
                self.assertTrue(p1.exists() and m1.exists())
                with self.assertRaises(RuntimeError):
                    validate.write_archive(rows, "text", stamp="S1")
                data = json.loads(p1.read_text(encoding="utf-8"))
                self.assertEqual(data["rows"][0]["metric"], "m")
            finally:
                validate.OUT_DIR = original

    def test_dossier_carries_evidence_per_row(self):
        rows = [validate._row("1 X", "m", "v", "b", "PASS",
                              source="the-archive")]
        with tempfile.TemporaryDirectory() as td:
            original = validate.OUT_DIR
            validate.OUT_DIR = Path(td)
            try:
                _, md = validate.write_archive(rows, "sc")
                text = md.read_text(encoding="utf-8")
            finally:
                validate.OUT_DIR = original
        self.assertIn("evidence: the-archive", text)


if __name__ == "__main__":
    unittest.main()
