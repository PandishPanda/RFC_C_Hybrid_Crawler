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
            {"sheet": "B", "verdict": "ok"},
            {"sheet": "B", "verdict": "wrong"}]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "v.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            original = validate.VERDICTS
            validate.VERDICTS = path
            try:
                validate._read_sample_verdicts(rows)
            finally:
                validate.VERDICTS = original
        fee_row = next(r for r in rows if "fee" in r["metric"])
        self.assertEqual(fee_row["verdict"], "FAIL")

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


class UpperBoundTest(unittest.TestCase):
    """The generalized Clopper-Pearson bound. The k=0 closed form is the
    regression anchor; nonzero k must widen the bound, not reuse it (the
    scorecard used to quote the k=0 bound beside a nonzero wrong count)."""

    def test_k_zero_matches_the_closed_form(self):
        for n in (7, 33, 115):
            self.assertAlmostEqual(validate._upper_bound_k(n, 0),
                                   100.0 * (1.0 - 0.05 ** (1.0 / n)),
                                   places=6)

    def test_nonzero_k_widens_the_bound(self):
        self.assertGreater(validate._upper_bound_k(115, 5),
                           validate._upper_bound_k(115, 0))

    def test_bound_falls_as_n_grows_at_a_fixed_rate(self):
        # same 4% observed rate, more evidence -> tighter bound
        self.assertGreater(validate._upper_bound_k(25, 1),
                           validate._upper_bound_k(250, 10))

    def test_all_wrong_is_a_hundred_percent(self):
        self.assertEqual(validate._upper_bound_k(10, 10), 100.0)
        self.assertEqual(validate._upper_bound_k(0, 0), 100.0)


class Tier2RowTest(unittest.TestCase):
    """tier-2 was hardcoded PENDING and could never leave that state
    however large n grew -- permanently-green-by-omission."""

    def _rows_for(self, n, wrong):
        rows = []
        payload = {"steps": {
            "6-grader": {"blind_grade_n33": {
                "date": "d", "n": n, "wrong": wrong, "fabrications": 0}},
            "3-llm-tail": {"fill": {"value": {}, "reproduce": "r"}},
            "4-onboarding": {}}}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "b.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            original = validate.BASELINE
            validate.BASELINE = path
            try:
                validate._read_baseline(rows)
            finally:
                validate.BASELINE = original
        return rows

    def test_below_the_population_it_is_pending(self):
        row = next(r for r in self._rows_for(75, 1)
                   if "tier-2" in r["metric"])
        self.assertEqual(row["verdict"], "PENDING")

    def test_at_the_population_it_measures(self):
        row = next(r for r in self._rows_for(115, 5)
                   if "tier-2" in r["metric"])
        self.assertIn(row["verdict"], ("PASS", "FAIL"))
        self.assertNotEqual(row["verdict"], "PENDING")

    def test_a_clean_large_run_passes_tier_2(self):
        row = next(r for r in self._rows_for(500, 0)
                   if "tier-2" in r["metric"])
        self.assertEqual(row["verdict"], "PASS")
