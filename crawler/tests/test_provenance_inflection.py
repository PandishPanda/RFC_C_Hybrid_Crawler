"""INFLECTION — Bulgarian word-inflection leniency, words only.

Bulgarian labels and values inflect ("8 семестра" vs "брой семестри: 8"), so
word tokens match on a 5-character prefix. The leniency applies to WORDS
ONLY: numbers always compare exactly (separator-stripped), so an inflected
label can never launder a wrong number through the gate.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.provenance import Status, gate  # noqa: E402
from crawler.tests.provenance_fixtures import synthetic_artifact  # noqa: E402

# the SU program-page label shape (real golden segment shape, tier G)
SEG = "Продължителност на обучението (брой семестри): 8"


class TestInflectionLeniency(unittest.TestCase):
    def test_inflected_word_with_right_number_passes(self):
        """'семестра' matches 'семестри' via the 5-char prefix rule;
        the number 8 is exactly supported => PASS."""
        v = gate("8 семестра", [SEG], synthetic_artifact(SEG))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_inflected_word_with_wrong_number_rejects(self):
        """Same word shapes, wrong number: prefix leniency must not extend
        to numbers => REJECT_SUPPORT."""
        v = gate("6 семестра", [SEG], synthetic_artifact(SEG))
        self.assertIs(v.status, Status.REJECT_SUPPORT, v.detail)
        self.assertIn("6", v.detail)

    def test_uninflected_pair_still_passes(self):
        v = gate("8 семестри", [SEG], synthetic_artifact(SEG))
        self.assertIs(v.status, Status.PASS, v.detail)

    def test_unrelated_word_rejects(self):
        """Prefix leniency is 5 chars, not anything-goes: a word sharing no
        5-char prefix with any segment word must reject."""
        v = gate("8 години", [SEG], synthetic_artifact(SEG))
        self.assertIs(v.status, Status.REJECT_SUPPORT, v.detail)

    def test_prefix_boundary_is_five_chars(self):
        """'семейни' shares a 4-char prefix ('семе') with 'семестри' but
        diverges at char 5 — must reject; the leniency window is exactly 5."""
        v = gate("8 семейни", [SEG], synthetic_artifact(SEG))
        self.assertIs(v.status, Status.REJECT_SUPPORT, v.detail)

    def test_short_words_need_exact_match(self):
        """Words shorter than 5 chars get no prefix leniency."""
        seg = "Срок на обучение: 4 години"
        v = gate("до 4 години", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.REJECT_SUPPORT, v.detail)  # 'до' absent
        v2 = gate("на 4 години", [seg], synthetic_artifact(seg))
        self.assertIs(v2.status, Status.PASS, v2.detail)


class TestNumbersCompareExactly(unittest.TestCase):
    """No prefix/substring leniency of any kind for numbers: a value number
    must equal a segment number token after separator stripping."""
    SEG = "Информатика и компютърни науки ФМИ Компютърни науки 460 EUR"

    def test_prefix_of_a_segment_number_rejects(self):
        v = gate("46 EUR", [self.SEG], synthetic_artifact(self.SEG))
        self.assertIs(v.status, Status.REJECT_SUPPORT, v.detail)
        self.assertIn("46", v.detail)

    def test_superstring_of_a_segment_number_rejects(self):
        v = gate("4600 EUR", [self.SEG], synthetic_artifact(self.SEG))
        self.assertIs(v.status, Status.REJECT_SUPPORT, v.detail)

    def test_separator_variants_of_same_number_match(self):
        """Separator stripping equates 6,900 / 6900 / 6.900 — same digits,
        different thousands punctuation."""
        seg = "Годишна такса 6.900 лв за програмата"
        v = gate("6,900 лв", [seg], synthetic_artifact(seg))
        self.assertIs(v.status, Status.PASS, v.detail)


if __name__ == "__main__":
    unittest.main()
