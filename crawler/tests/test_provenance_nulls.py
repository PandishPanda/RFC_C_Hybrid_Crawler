"""NULL_OK / PARSE_FAILURE entry points and the locked interface contract.

The cascade speaks one status vocabulary (ADR-0002). A null is an affirmative
outcome that must carry its reason; a record the gate cannot even check
(empty value, no segments, no checkable tokens) is PARSE_FAILURE — never a
silent PASS and never graded as a correct null downstream.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.provenance import (  # noqa: E402
    Artifact, Status, Verdict, gate, gate_null)
from crawler.tests.provenance_fixtures import synthetic_artifact  # noqa: E402


class TestNullEntryPoints(unittest.TestCase):
    def test_gate_null_helper(self):
        v = gate_null("no tuition row for this program in the fee order")
        self.assertIs(v.status, Status.NULL_OK)
        self.assertEqual(v.detail,
                         "no tuition row for this program in the fee order")

    def test_gate_with_none_value_routes_to_null_ok(self):
        """gate(None, ...) is the uniform-call-site spelling of gate_null;
        the artifact is never consulted on the null path."""
        v = gate(None, [], synthetic_artifact("whatever"),
                 null_reason="field not present on program page")
        self.assertIs(v.status, Status.NULL_OK)
        self.assertEqual(v.detail, "field not present on program page")

    def test_null_path_needs_no_artifact(self):
        v = gate(None, [], None, null_reason="page affirmatively gone (404)")
        self.assertIs(v.status, Status.NULL_OK)


class TestParseFailure(unittest.TestCase):
    def test_empty_value_is_parse_failure(self):
        seg = "Семестриална такса: 460 EUR"
        v = gate("", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.PARSE_FAILURE, v.detail)

    def test_whitespace_value_is_parse_failure(self):
        seg = "Семестриална такса: 460 EUR"
        v = gate("   ", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.PARSE_FAILURE, v.detail)

    def test_no_segments_is_parse_failure(self):
        v = gate("460 EUR", [], synthetic_artifact("Такса: 460 EUR"))
        self.assertIs(v.status, Status.PARSE_FAILURE, v.detail)

    def test_blank_segments_are_parse_failure(self):
        v = gate("460 EUR", ["", "   "], synthetic_artifact("Такса: 460 EUR"))
        self.assertIs(v.status, Status.PARSE_FAILURE, v.detail)

    def test_tokenless_value_is_parse_failure(self):
        """A value with no number/word/currency tokens has nothing to
        support; the spike gate rejected it, v2 names it PARSE_FAILURE
        instead of letting the support check pass vacuously."""
        seg = "Семестриална такса: 460 EUR"
        v = gate("—?!", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.PARSE_FAILURE, v.detail)


class TestLockedInterface(unittest.TestCase):
    def test_status_vocabulary(self):
        self.assertEqual(
            {s.name for s in Status},
            {"PASS", "REJECT_CONTAINMENT", "REJECT_SUPPORT",
             "NULL_OK", "PARSE_FAILURE"})

    def test_verdict_is_frozen_with_default_detail(self):
        v = Verdict(Status.PASS)
        self.assertEqual(v.detail, "")
        with self.assertRaises(Exception):
            v.status = Status.NULL_OK  # type: ignore[misc]

    def test_artifact_is_frozen(self):
        a = synthetic_artifact("text")
        self.assertEqual(a.renderer_id, "test-synthetic")
        with self.assertRaises(Exception):
            a.text = "edited"  # type: ignore[misc]

    def test_gate_refuses_raw_text_for_artifact(self):
        """ADR-0002: a gate that accepts raw text reproduces the
        wrong-artifact failure class. Passing a str must raise TypeError."""
        with self.assertRaises(TypeError):
            gate("460 EUR", ["Такса: 460 EUR"], "Такса: 460 EUR")

    def test_verdicts_are_verdicts(self):
        seg = "Семестриална такса: 460 EUR"
        v = gate("460 EUR", [seg], synthetic_artifact(seg))
        self.assertIsInstance(v, Verdict)
        self.assertIsInstance(v.status, Status)
        self.assertIsInstance(v.detail, str)
        self.assertIsInstance(Artifact, type)


if __name__ == "__main__":
    unittest.main()
