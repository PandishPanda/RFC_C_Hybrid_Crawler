"""parse_edu_forms (ticket 17) -- zero network, stdlib only.

An Offering is one (attendance form, duration) pair, and the registry row
STATES its own list in `edu_forms`. Config must never restate that list:
config and registry cannot then drift, and nobody hand-maintains 517 rows
(ADR-0004).

Two tiers of test, deliberately separated (test_registry.py's own
convention: synthetic fixtures for behaviour, because the real exports are
hand-refreshed and WILL change):

  * behaviour, on synthetic input -- survives any re-capture;
  * INVARIANTS over the real committed exports, which must hold for every
    export past and future ("parses worse fails loudly"), plus exactly one
    place holding today's exact counts, so a re-capture updates one number
    and not ten assertions.
"""
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.registry import (  # noqa: E402
    DEFAULT_EXPORTS_DIR, EduForm, load_captured_export, parse_edu_forms,
)

# Today's measurement, in ONE place. A hand re-capture updates this dict,
# not the assertions below it.
MEASURED = {
    "AMTII": (67, 86), "AUBG": (10, 10), "MUPleven": (63, 152),
    "SofiaUniversity": (985, 1571), "TUG": (94, 346),
    "UniRuse": (189, 517), "VUM": (18, 25),
}
MEASURED_FORMS = {"редовна": 1417, "задочна": 935,
                  "самостоятелна": 308, "дистанционна": 47}


def _all_exports():
    return sorted(p.stem for p in Path(DEFAULT_EXPORTS_DIR).glob("*.json"))


class ParseEduFormsTest(unittest.TestCase):
    def _pairs(self, text):
        forms, _ = parse_edu_forms(text)
        return [(f.form, f.duration_years) for f in forms]

    def test_the_row_30083_shape(self):
        self.assertEqual(
            self._pairs("редовна - 4, задочна - 5, задочна - 4.5, "
                        "дистанционна - 4.5, дистанционна - 4, "
                        "дистанционна - 5"),
            [("редовна", "4"), ("задочна", "5"), ("задочна", "4.5"),
             ("дистанционна", "4.5"), ("дистанционна", "4"),
             ("дистанционна", "5")])

    def test_order_is_the_registry_s_own(self):
        self.assertEqual([f for f, _ in self._pairs("задочна - 5, редовна - 4")],
                         ["задочна", "редовна"])

    def test_duplicates_are_preserved_with_their_content(self):
        # Deduping would silently change the Offering count, which is the
        # denominator of every completeness figure.
        self.assertEqual(self._pairs("редовна - 4, редовна - 4"),
                         [("редовна", "4"), ("редовна", "4")])

    def test_empty_or_missing_yields_nothing_and_no_error(self):
        for value in ("", None, "   ", " , "):
            self.assertEqual(parse_edu_forms(value), ((), ()))

    def test_a_malformed_item_never_discards_the_good_ones(self):
        forms, unparsed = parse_edu_forms("редовна - 4, свободен текст")
        self.assertEqual([f.form for f in forms], ["редовна"])
        self.assertEqual(unparsed, ("свободен текст",))

    def test_an_unknown_form_still_parses(self):
        # The PARSER stays open even though config is closed: the registry
        # may legitimately name a form no config may yet declare.
        self.assertEqual(self._pairs("вечерна - 5"), [("вечерна", "5")])

    def test_surrounding_whitespace_is_not_significant(self):
        self.assertEqual(self._pairs("  редовна  -  4 ,  задочна - 4.5 "),
                         [("редовна", "4"), ("задочна", "4.5")])

    def test_a_record_carries_its_verbatim_item_and_canonical_key(self):
        # ADR-0004 requires every Offering to carry its edu_forms_item; the
        # key lives on the record so no call site rebuilds the separator.
        forms, _ = parse_edu_forms("задочна - 4.5")
        self.assertEqual(forms[0], EduForm("задочна", "4.5", "задочна - 4.5"))
        self.assertEqual(forms[0].key, "задочна - 4.5")


class NeverGuessesTest(unittest.TestCase):
    """Every one of these produced a WRONG pair or a degenerate form
    before review (2026-08-16). Each must now be reported, not guessed."""

    def test_a_comma_decimal_is_refused_whole_not_truncated(self):
        # "редовна - 4,5" arrives split as "редовна - 4" + "5". The first
        # half matches cleanly and shipped a 4.5-year Offering as a
        # 4-year one -- which in ticket 18 attaches the "задочна - 4"
        # recipe to a 4.5-year Offering. The pair must be WITHDRAWN.
        forms, unparsed = parse_edu_forms("редовна - 4,5")
        self.assertEqual(forms, ())
        self.assertEqual(unparsed, ("редовна - 4,5",))

    def test_a_comma_decimal_does_not_take_its_neighbours_with_it(self):
        forms, unparsed = parse_edu_forms(
            "задочна - 5, редовна - 4,5, дистанционна - 3")
        self.assertEqual([f.key for f in forms],
                         ["задочна - 5", "дистанционна - 3"])
        self.assertEqual(unparsed, ("редовна - 4,5",))

    def test_punctuation_is_never_a_form_name(self):
        for text in ("--4", "- - 4", "- 4"):
            forms, unparsed = parse_edu_forms(text)
            self.assertEqual(forms, (), text)
            self.assertTrue(unparsed, text)

    def test_a_lone_number_with_nothing_before_it_is_reported(self):
        self.assertEqual(parse_edu_forms("5"), ((), ("5",)))

    def test_other_malformed_shapes_are_reported_loudly(self):
        for text in ("редовна", "редовна - ", "редовна - x",
                     "редовна – 4", "редовна - 4 - 5"):
            forms, unparsed = parse_edu_forms(text)
            self.assertEqual(forms, (), text)
            self.assertEqual(len(unparsed), 1, text)


class RealExportInvariantsTest(unittest.TestCase):
    """Must hold for EVERY export, today's and tomorrow's."""

    def setUp(self):
        self.exports = _all_exports()
        self.assertTrue(self.exports, "no committed exports to check")

    def test_every_row_carries_edu_forms(self):
        for uni in self.exports:
            for row in load_captured_export(uni).rows:
                self.assertTrue((row.edu_forms or "").strip(),
                                "{0} row {1}".format(uni, row.id))

    def test_nothing_in_a_committed_export_fails_to_parse(self):
        for uni in self.exports:
            for row in load_captured_export(uni).rows:
                _, unparsed = parse_edu_forms(row.edu_forms)
                self.assertEqual(unparsed, (),
                                 "{0} row {1}".format(uni, row.id))

    def test_every_row_yields_at_least_one_offering(self):
        for uni in self.exports:
            for row in load_captured_export(uni).rows:
                forms, _ = parse_edu_forms(row.edu_forms)
                self.assertTrue(forms, "{0} row {1}".format(uni, row.id))

    def test_durations_are_positive_numbers_and_forms_are_words(self):
        for uni in self.exports:
            for row in load_captured_export(uni).rows:
                for f in parse_edu_forms(row.edu_forms)[0]:
                    self.assertGreater(float(f.duration_years), 0)
                    self.assertTrue(f.form.strip())
                    self.assertNotIn("-", f.form)


class MeasuredTodayTest(unittest.TestCase):
    """Today's exact counts. A hand re-capture updates MEASURED above;
    these assertions do not move."""

    def test_per_export_counts(self):
        for uni, (n_rows, n_offerings) in MEASURED.items():
            rows = load_captured_export(uni).rows
            got = sum(len(parse_edu_forms(r.edu_forms)[0]) for r in rows)
            self.assertEqual((len(rows), got), (n_rows, n_offerings), uni)

    def test_form_vocabulary_matches(self):
        forms = Counter()
        for uni in MEASURED:
            for row in load_captured_export(uni).rows:
                for f in parse_edu_forms(row.edu_forms)[0]:
                    forms[f.form] += 1
        self.assertEqual(dict(forms), MEASURED_FORMS)

    def test_measured_exports_are_still_all_of_them(self):
        # If a fourth export lands, MEASURED is stale -- fail here, once,
        # rather than in every count assertion above.
        self.assertEqual(sorted(MEASURED), _all_exports())


if __name__ == "__main__":
    unittest.main()
