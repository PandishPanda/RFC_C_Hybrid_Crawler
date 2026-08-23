"""Derived values (ADR-0007): a value that is true but appears in no
document, shipped under its own status and never as a gate-verified
extraction. All offline.

Five proofs:

1. STATUS — a derived value is DERIVED, tier D, carries a `derivation`
   block naming the rule, and carries NO source_snippets: there is no
   verbatim support and it must not pretend otherwise.
2. LAST RESORT — derivation fires only where the cascade and the tail
   both produced nothing. A stated language always wins, including a
   stated language that is not the configured default.
3. CONFIG, NOT CODE — a site with no `default_language` derives nothing,
   so AUBG (English) and VUM cannot inherit a fleet-wide Bulgarian.
4. GRADING — the blind key scores a derived value in its own category:
   never FABRICATION (the key answers a different question), never
   OK_VALUE (assumption must not inflate the correctness rate).
5. LEDGER — derived values reach the published dataset, marked, so a
   consumer can take proven values only.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import config, grader, ledger, runner  # noqa: E402
from crawler.provenance import Status  # noqa: E402


class StatusTest(unittest.TestCase):
    def test_derived_is_its_own_status(self):
        self.assertIn("DERIVED", [s.name for s in Status])
        self.assertNotEqual(Status.DERIVED, Status.PASS)

    def test_record_shape(self):
        rec = runner.derived_record("language", "български",
                                    rule="default_language")
        self.assertEqual(rec["status"], "DERIVED")
        self.assertEqual(rec["tier"], "D")
        self.assertEqual(rec["value"], "български")
        self.assertEqual(rec["derivation"]["rule"], "default_language")
        self.assertEqual(rec["derivation"]["input"], "български")
        self.assertNotIn("provenance", rec)
        self.assertNotIn("artifact", rec)

    def test_the_derivation_says_no_document_states_it(self):
        rec = runner.derived_record("language", "български",
                                    rule="default_language")
        self.assertIn("no document", rec["derivation"]["basis"].lower())


class ConfigTest(unittest.TestCase):
    BASE = {"uni_id": "TestUni", "cookies": {}, "sources": {},
            "programs": [{"id": "p1", "name": "P One",
                          "page": "https://x.test/p1"}]}

    def test_default_language_is_optional(self):
        site = config.parse_site_config(dict(self.BASE))
        self.assertIsNone(site.default_language)

    def test_default_language_loads(self):
        data = dict(self.BASE, default_language="български")
        self.assertEqual(
            config.parse_site_config(data).default_language, "български")

    def test_a_typo_is_still_an_error(self):
        data = dict(self.BASE, defualt_language="български")
        with self.assertRaises(config.ConfigError):
            config.parse_site_config(data)


class LastResortTest(unittest.TestCase):
    """derive_fields never displaces something a document states."""

    def test_a_null_field_is_derived(self):
        fields = {"language": {"status": "NULL_OK", "value": None,
                               "null_reason": "cascade found nothing"}}
        out = runner.derive_fields(fields, default_language="български")
        self.assertEqual(out["language"]["status"], "DERIVED")

    def test_a_stated_language_wins(self):
        fields = {"language": {"status": "PASS", "value": "English",
                               "tier": "G", "method": "label:en-lang-label"}}
        out = runner.derive_fields(fields, default_language="български")
        self.assertEqual(out["language"]["status"], "PASS")
        self.assertEqual(out["language"]["value"], "English")

    def test_a_gate_rejected_language_is_not_replaced(self):
        """A REJECT means a value was found and refused. Quietly swapping
        the site default over it would hide the rejection."""
        fields = {"language": {"status": "REJECT_SUPPORT", "value": None}}
        out = runner.derive_fields(fields, default_language="български")
        self.assertEqual(out["language"]["status"], "REJECT_SUPPORT")

    def test_no_default_configured_derives_nothing(self):
        fields = {"language": {"status": "NULL_OK", "value": None}}
        out = runner.derive_fields(fields, default_language=None)
        self.assertEqual(out["language"]["status"], "NULL_OK")

    def test_only_language_is_derived(self):
        """ADR-0007 scopes derivation to language: tuition and admission
        vary within a university, where a default is a guess."""
        fields = {"tuition": {"status": "NULL_OK", "value": None},
                  "admission": {"status": "NULL_OK", "value": None}}
        out = runner.derive_fields(fields, default_language="български")
        self.assertEqual(out["tuition"]["status"], "NULL_OK")
        self.assertEqual(out["admission"]["status"], "NULL_OK")


class GradingTest(unittest.TestCase):
    KEY_NULL = grader.KeyEntry(
        program_id="p1", field="language", expected_value=None,
        null_reason="labeller: not stated on the programme's pages",
        snippet=None, source_url="https://x.test/p1")
    KEY_VALUE = grader.KeyEntry(
        program_id="p1", field="language", expected_value="български",
        null_reason=None, snippet="български",
        source_url="https://x.test/p1")

    DERIVED = {"status": "DERIVED", "value": "български", "tier": "D",
               "derivation": {"rule": "default_language"}}

    def test_against_a_null_key_it_is_not_a_fabrication(self):
        c = grader.grade_field(self.KEY_NULL, self.DERIVED)
        self.assertEqual(c, grader.GradeCategory.DERIVED)
        self.assertNotEqual(c, grader.GradeCategory.FABRICATION)

    def test_against_a_stated_key_it_still_does_not_count_as_correct(self):
        """Even when the derived value is right, it was not extracted —
        letting it grade OK_VALUE would inflate the correctness rate."""
        c = grader.grade_field(self.KEY_VALUE, self.DERIVED)
        self.assertEqual(c, grader.GradeCategory.DERIVED)

    def test_derived_is_outside_the_correct_set_and_the_fabrication_count(self):
        report = grader.grade_report(
            {("p1", "language"): self.KEY_NULL},
            {"uni_id": "TestUni", "programs": [
                {"program_id": "p1", "name": "p1",
                 "fields": {"language": self.DERIVED}}]})
        self.assertEqual(report.fabrication_count, 0)
        self.assertEqual(report.tallies[grader.GradeCategory.DERIVED], 1)
        # outside the correct set: an assumption is not an extraction
        self.assertNotIn(grader.GradeCategory.DERIVED,
                         grader._CORRECT_CATEGORIES)
        # and outside the wrong-rate denominator, which counts values the
        # gate actually proved
        self.assertEqual(report.pass_count, 0)


class LedgerTest(unittest.TestCase):
    def test_derived_values_reach_the_dataset_marked(self):
        report = {"uni_id": "TestUni", "programs": [
            {"program_id": "p1", "name": "p1", "fields": {
                "language": {"status": "DERIVED", "value": "български",
                             "tier": "D", "method": "derive:default_language",
                             "derivation": {"rule": "default_language"}}}}],
            "gate_failures": [], "summary": {}}
        with tempfile.TemporaryDirectory() as tmp:
            ledger.append_run(tmp, "TestUni", "r1", report, "2026/2027")
            rows = ledger.load_run_values(tmp, "TestUni", "r1")
            entry = rows[("p1", "language", "2026/2027", None)]
            self.assertEqual(entry["value"], "български")
            self.assertEqual(entry["status"], "DERIVED")


if __name__ == "__main__":
    unittest.main()
