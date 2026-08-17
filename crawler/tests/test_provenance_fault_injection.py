"""PINNED FAULT INJECTIONS — the gate's designed behavior under known faults.

These pin what the gate MUST catch (missing year support, composed segments)
and what it deliberately does NOT catch (the wrong-column blind spot, whose
fix belongs in the table resolver). All segments here are genuine rows of the
vendored su-fees Docling-TSV artifact, so containment always holds and the
verdicts isolate the value-support rule.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.provenance import Status, gate  # noqa: E402
from crawler.tests import provenance_fixtures as fx  # noqa: E402

# Genuine rows of fixtures/artifacts/docling-tsv-su-fees.txt (verbatim):
KN_ROW = ("Информатика и компютърни науки ФМИ Компютърни науки "
          "460 EUR няма 460 EUR няма 920 EUR 460 EUR")
YEAR_ROW = "Педагогика на по: ФХФ Химия и английски език 2026/2027 - 2026/2027 -"
STAT_ROW = ("Математика ФМИ Статистика освободени освободени освободени "
            "освободени 920 EUR 460 EUR")


class TestYearTokenRemoval(unittest.TestCase):
    """RFC v2 §3 Q9: dated values carry valid_for, year tokens gate-enforced.
    Removing the year-bearing segment from a tuition value's segments context
    must flip PASS -> REJECT_SUPPORT."""

    def setUp(self):
        self.artifact = fx.load_artifact("docling-tsv-su-fees")

    def test_with_year_segment_passes(self):
        v = gate("460 EUR 2026/2027", [KN_ROW, YEAR_ROW], self.artifact)
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_year_tokens_removed_rejects_support(self):
        """Same value, segments context stripped of its year-bearing segment:
        the fee row still supports 460/EUR, but 2026 and 2027 have no support
        => REJECT_SUPPORT naming the missing year tokens."""
        v = gate("460 EUR 2026/2027", [KN_ROW], self.artifact)
        self.assertIs(v.status, Status.REJECT_SUPPORT, v.detail)
        self.assertIn("2026", v.detail)
        self.assertIn("2027", v.detail)


class TestWrongColumnBlindSpot(unittest.TestCase):
    """THE DOCUMENTED BLIND SPOT (RFC v2 §3 Q4, measured twice in STA-78):
    a TRUTHFUL segment from the wrong row/column passes the gate.

    Value "460 EUR" with the Статистика fee-row segment PASSes even though
    Статистика's actual редовно fee is "освободени" — the row genuinely
    contains 460 EUR (in a different fee column), so containment and support
    both hold. This is pinned as PASS on purpose: the gate checks literal
    evidence, not table semantics. THE FIX BELONGS IN THE COLUMN-AWARE TABLE
    RESOLVER (tier F: the LLM/agent proposes the ROW only; the value cell is
    taken deterministically from the Docling TSV column) — NEVER in this gate.
    Tightening the gate to catch this would require it to understand table
    structure, violating ADR-0002's pure-function contract."""

    def test_wrong_column_value_passes_the_gate(self):
        v = gate("460 EUR", [STAT_ROW], fx.load_artifact("docling-tsv-su-fees"))
        self.assertIs(v.status, Status.PASS, v.detail)


class TestComposedSegmentRejectsContainment(unittest.TestCase):
    """The v1 post-mortem failure class (and spike A's first-run 15-null
    incident): a snippet COMPOSED from multiple places is not literally
    contained in the artifact and must be REJECT_CONTAINMENT. Joined values
    must ship their pieces as separate segments instead."""

    def test_composed_segment_rejected(self):
        composed = KN_ROW + " ⧺ " + YEAR_ROW  # two real rows glued into one
        v = gate("460 EUR 2026/2027", [composed],
                 fx.load_artifact("docling-tsv-su-fees"))
        self.assertIs(v.status, Status.REJECT_CONTAINMENT, v.detail)

    def test_segment_from_other_artifact_rejected(self):
        """A segment that is genuine text of a DIFFERENT artifact must fail
        containment — the wrong-artifact failure class ADR-0002 kills."""
        mu_row = "5.5 Помощник-фармацевт 410"  # real mu-fees row
        v = gate("410 евро", [mu_row], fx.load_artifact("docling-tsv-su-fees"))
        self.assertIs(v.status, Status.REJECT_CONTAINMENT, v.detail)


if __name__ == "__main__":
    unittest.main()
