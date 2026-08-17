"""FOUND-BUG REGRESSIONS — bugs found and fixed during STA-78, pinned forever.

1. Glued currency: spike gates tokenized '€' as a standalone token, so a
   snippet carrying only glued "€6,900" made the euro check a no-op (verified
   bug). v2 rule: currency tokens (7-token vocabulary) match by SUBSTRING in
   the segments, never by token membership.
2. Case folding: value/segment case differences must not reject.
3. Unicode NFC: visually identical Cyrillic with different composition
   (precomposed й U+0439 vs и U+0438 + combining breve U+0306) must not
   reject; the policy normalizes both sides to NFC.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.provenance import Status, gate  # noqa: E402
from crawler.tests.provenance_fixtures import synthetic_artifact  # noqa: E402


class TestGluedCurrency(unittest.TestCase):
    def test_glued_euro_symbol_is_supported_by_substring(self):
        """Value '€6,900/semester' with a snippet that has only glued
        '€6,900' — '€' never appears as a standalone token — must PASS."""
        seg = ("For the upcoming academic year the undergraduate tuition is "
               "€6,900/semester for all admitted students.")
        v = gate("€6,900/semester", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_missing_currency_symbol_still_rejects(self):
        """Substring leniency must not erase the check: a snippet with the
        number but no euro mark anywhere fails value support."""
        seg = ("For the upcoming academic year the undergraduate tuition is "
               "6,900/semester for all admitted students.")
        v = gate("€6,900/semester", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.REJECT_SUPPORT, v.detail)
        self.assertIn("€", v.detail)

    def test_glued_bulgarian_lv(self):
        seg = "Годишна такса: 1200лв. за учебната година"
        v = gate("1200 лв", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.PASS, v.detail)


class TestCaseFolding(unittest.TestCase):
    def test_latin_case_pair(self):
        seg = "Информатика и компютърни науки ФМИ Компютърни науки 460 EUR"
        v = gate("460 eur", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_cyrillic_case_pair(self):
        seg = "Степен: ОКС „БАКАЛАВЪР“ Специалност: Компютърни науки"
        v = gate("ОКС „бакалавър“", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.PASS, v.detail)


class TestUnicodeNFC(unittest.TestCase):
    # 'английски' spelled with precomposed й (U+0439, NFC) vs the visually
    # identical и (U+0438) + combining breve (U+0306) decomposition.
    NFC_SEG = "Обучението се провежда на английски език"
    NFD_SEG = "Обучението се провежда на англи\u0438\u0306ски език"
    NFC_VAL = "на английски език"
    NFD_VAL = "на англи\u0438\u0306ски език"

    def test_fixture_is_a_real_composition_pair(self):
        import unicodedata
        self.assertNotEqual(self.NFC_SEG, self.NFD_SEG)
        self.assertEqual(unicodedata.normalize("NFC", self.NFD_SEG),
                         self.NFC_SEG)

    def test_decomposed_value_against_precomposed_segment(self):
        v = gate(self.NFD_VAL, [self.NFC_SEG],
                 synthetic_artifact(self.NFC_SEG))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_precomposed_value_against_decomposed_segment(self):
        v = gate(self.NFC_VAL, [self.NFD_SEG],
                 synthetic_artifact(self.NFD_SEG))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_decomposed_segment_against_precomposed_artifact(self):
        """Containment itself must also be NFC-insensitive, not just support."""
        v = gate(self.NFC_VAL, [self.NFD_SEG],
                 synthetic_artifact(self.NFC_SEG))
        self.assertIs(v.status, Status.PASS, v.detail)


class TestQuoteAndDashFolding(unittest.TestCase):
    """Quote/apostrophe/dash folding is only observable at the CONTAINMENT
    boundary (tokenization ignores punctuation), so these pairs put the
    variant glyph in the segment and the plain glyph in the artifact —
    exactly the cross-renderer drift the folding exists for."""

    def test_quote_variants_fold(self):
        seg = 'Степен: ОКС "бакалавър" Специалност: Статистика'
        v = gate("ОКС „бакалавър”", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_quote_folding_bridges_segment_and_artifact(self):
        """Segment quoted „…“, artifact quoted "…": containment must hold."""
        art = 'Степен: ОКС "бакалавър" Специалност: Статистика'
        v = gate("ОКС бакалавър", ["Степен: ОКС „бакалавър“"],
                 synthetic_artifact(art))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_apostrophe_folding_bridges_segment_and_artifact(self):
        art = "open to candidates holding a Bachelor's degree from any field"
        v = gate("Bachelor’s degree",
                 ["candidates holding a Bachelor’s degree"],
                 synthetic_artifact(art))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_minus_sign_folds_to_dash(self):
        # U+2212 MINUS SIGN in the value vs ASCII hyphen in the segment
        seg = "Срок на обучение: 3-годишен период на обучение"
        v = gate("3−годишен период на обучение", [seg],
                 synthetic_artifact(seg))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_dash_folding_bridges_segment_and_artifact(self):
        """Segment with EN DASH (U+2013), artifact with ASCII hyphen."""
        art = "Продължителност: 3 - 4 семестъра редовна форма"
        v = gate("3 - 4 семестъра", ["Продължителност: 3 – 4 семестъра"],
                 synthetic_artifact(art))
        self.assertIs(v.status, Status.PASS, v.detail)


class TestWhitespaceCollapse(unittest.TestCase):
    def test_collapsed_segment_contained_in_ragged_artifact(self):
        """A snippet stored whitespace-collapsed must still be contained in
        an artifact whose raw text carries newlines, tabs and runs of
        spaces (line-wrapped PDF text, indented HTML)."""
        art = ("Форма на обучение: редовно\n"
               "Продължителност на обучението\n\t(брой  семестри):   8\n"
               "Професионална квалификация")
        seg = "Продължителност на обучението (брой семестри): 8"
        v = gate("8 семестра", [seg], synthetic_artifact(art))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_nbsp_collapses_like_space(self):
        # U+00A0 NO-BREAK SPACE in the artifact (common in CMS HTML)
        art = "Годишна такса: 460 EUR за учебната година"
        seg = "Годишна такса: 460 EUR"
        v = gate("460 EUR", [seg], synthetic_artifact(art))
        self.assertIs(v.status, Status.PASS, v.detail)


if __name__ == "__main__":
    unittest.main()
