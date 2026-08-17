"""Phase-0 worksheet generator suite (ticket 13) -- zero network.

The one property that matters most here is a NEGATIVE one: no
pipeline-extracted value may ever appear in a generated worksheet. The
worksheet exists so a human labels the real pages independently; leaking
run-report values into it would recreate exactly the contamination
ticket 07's gate exists to measure against (crawler/grader.py's module
docstring).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import labelkit  # noqa: E402
from crawler.config import FIELDS, load_configs_dir  # noqa: E402


class BuildWorksheetTest(unittest.TestCase):
    def setUp(self):
        self.site = load_configs_dir("crawler/configs")["VUM"]
        self.worksheet = labelkit.build_worksheet(self.site)
        # Everything after the instructions separator: the per-program
        # blocks a human fills in. The header above it is fixed
        # instructional prose and legitimately contains example words
        # ("English equivalents", "бакалавър"), so a leak check run over
        # the whole document would flag its own instructions.
        self.body = self.worksheet.split("\n---\n", 1)[1]

    def test_every_program_and_field_appears_exactly_once(self):
        for program in self.site.programs:
            self.assertEqual(
                self.worksheet.count("— {0} —".format(program.id)), 1,
                "program {0} should have exactly one heading".format(
                    program.id))
            # (a shared-page program legitimately appears AGAIN as a
            # "### id" subsection -- counting bare ids pinned the
            # no-shared-pages shape VUM had before 2026-08-17)
        expected_field_lines = len(self.site.programs) * len(FIELDS)
        actual = sum(self.worksheet.count("- field: {0}".format(f))
                     for f in FIELDS)
        self.assertEqual(actual, expected_field_lines)

    def test_every_value_line_is_blank(self):
        for line in self.worksheet.splitlines():
            if line.startswith("  value:"):
                self.assertEqual(
                    line.strip(), "value:",
                    "a generated worksheet must never pre-fill a value")

    def test_no_run_report_values_leak_in(self):
        # The real VUM run-report ships these exact strings. None may
        # appear in the part of the worksheet a human fills in.
        for shipped in ("Professional Bachelor", "3 years / 6 semesters",
                        "English", "1500 €", "1100 leva"):
            self.assertNotIn(shipped, self.body)

    def test_the_degree_instruction_does_not_repeat_the_conflation_that_failed_the_gate(self):
        # The hand-built worksheet this generator replaced described
        # degree as "the qualification/degree level awarded (e.g.
        # 'бакалавър', 'бизнес мениджър')" -- treating a degree level
        # and a qualification title as interchangeable, which is the
        # exact confusion that produced the 2026-08-16 gate FAIL. A
        # labeler following that wording could write the qualification
        # title into the frozen key and make the key disagree with the
        # now-fixed pipeline.
        self.assertIn("DEGREE LEVEL", self.worksheet)
        self.assertNotIn('"бизнес мениджър")', self.worksheet)

    def test_carries_the_blind_labeling_instructions(self):
        self.assertIn("without looking at", self.worksheet)
        self.assertIn("run-report.json", self.worksheet)
        self.assertIn("NOT STATED", self.worksheet)
        self.assertIn("verbatim quote", self.worksheet)

    def test_every_configured_page_is_listed(self):
        for program in self.site.programs:
            self.assertIn(program.page, self.worksheet)

    def test_a_field_whose_page_differs_names_that_page(self):
        # VUM's vum-sst/vum-gca read admission from a separate adm_page.
        # Without naming it, a labeler fills admission in from the
        # program page, which does not state it -- producing a key entry
        # that disagrees with the pipeline for a reason that has nothing
        # to do with extraction quality.
        with_adm = [p for p in self.site.programs if p.adm_page]
        self.assertTrue(with_adm, "fixture assumption")
        for program in with_adm:
            self.assertIn(program.adm_page, self.worksheet)


class SharedPageGroupingTest(unittest.TestCase):
    """UniRuse's 3 business-faculty programs share one page -- the
    worksheet must list that page ONCE (so a human visits it once), with
    each program broken out underneath, and must flag that a shared page
    may not distinguish per-program values at all."""

    def setUp(self):
        self.site = load_configs_dir("crawler/configs")["UniRuse"]
        self.worksheet = labelkit.build_worksheet(self.site)

    def test_a_page_shared_by_several_programs_is_listed_once(self):
        shared = ("https://www.uni-ruse.bg/admission/bachelors/guide/"
                  "specialities/faculty-of-business-and-management")
        sharing = [p for p in self.site.programs if p.page == shared]
        self.assertGreater(len(sharing), 1, "fixture assumption")
        self.assertEqual(self.worksheet.count(shared), 1)

    def test_shared_page_carries_the_per_program_caveat(self):
        self.assertIn("describes multiple programs", self.worksheet)
        self.assertIn("generically", self.worksheet)

    def test_each_program_on_a_shared_page_still_gets_its_own_fields(self):
        shared = ("https://www.uni-ruse.bg/admission/bachelors/guide/"
                  "specialities/faculty-of-business-and-management")
        sharing = [p for p in self.site.programs if p.page == shared]
        for program in sharing:
            self.assertIn("### {0}".format(program.id), self.worksheet)
        expected = len(self.site.programs) * len(FIELDS)
        actual = sum(self.worksheet.count("- field: {0}".format(f))
                     for f in FIELDS)
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
