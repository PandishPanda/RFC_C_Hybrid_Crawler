"""Phase-0 labeling worksheet generator (ticket 13, RFC v2 Phase-0 protocol).

Ticket 07's grading oracle (crawler/grader.py) needs a frozen answer key
built by a human reading the real pages independently, with no view of
run-report.json -- the same author who builds the extraction pipeline
cannot also author the "ground truth" it is graded against without
recreating exactly the contamination the blind-benchmark ticket exists to
prevent (see grader.py's own module docstring). This module only builds
the BLANK worksheet a human fills in by hand; it never proposes a value,
never reads a run-report, and carries no opinion about what any page
says -- the same "propose, never decide" discipline as onboarding.py's
ProposedProgram.assignment_verified.

Grouped by PAGE, not by program: a page shared by several programs
(common on faculty-wide specialty pages) is listed once, with each
program's fields broken out underneath, so filling in the worksheet
means visiting each real page exactly once.
"""
from collections import OrderedDict

from crawler.config import FIELDS

__all__ = ["build_worksheet"]

_FIELD_MEANINGS = (
    "**Field meanings** (same 5 fields the pipeline extracts for every "
    "program, `crawler/config.py FIELDS`):\n"
    "- `degree` -- the DEGREE LEVEL awarded (\"бакалавър\", \"магистър\", "
    "\"доктор\", or their English equivalents). If the page ALSO prints a "
    "professional-qualification title nearby (e.g. \"професионална "
    "квалификация електроинженер\"), the degree level is what belongs "
    "here, not the qualification title -- record the level. These are "
    "different claims, and conflating them is what failed the "
    "2026-08-16 gate run.\n"
    "- `duration` -- length of study (semesters/years)\n"
    "- `language` -- language of instruction\n"
    "- `tuition` -- the fee, per year/semester\n"
    "- `admission` -- the admission requirement(s)/exam(s) -- if the page "
    "lists several valid routes (e.g. \"matriculation exam OR an entrance "
    "exam\"), note all of them, not just one")

_HEADER_TEMPLATE = (
    "# Phase-0 labeling worksheet -- {uni_id}\n"
    "\n"
    "Why this exists and why it has to be a human who did NOT build the "
    "extraction pipeline being graded: building the pipeline that "
    "produces run-report.json and building the \"ground truth\" it is "
    "graded against, in the same session, is the exact contamination "
    "ticket 07 exists to prevent -- every accuracy number is otherwise "
    "an upper bound. This file has to be filled in by reading the real "
    "pages, without looking at `crawler-out/{uni_id}/run-report.json` "
    "first -- otherwise you'll anchor on what the pipeline already said "
    "instead of judging the page independently.\n"
    "\n"
    "**How to fill this in:** for each `field:` line below, open the "
    "page listed above it, find whether that field is actually stated, "
    "and write:\n"
    "- if stated: the value, in a short verbatim quote copied straight "
    "from the page (not paraphrased -- the grader checks token-for-token "
    "support, so an inexact quote will look like a mismatch even when "
    "you're right)\n"
    "- if not stated: write `NOT STATED` and, if it's not obvious why, a "
    "one-line reason (e.g. \"page covers 3 programs generically, "
    "doesn't break out tuition per program\")\n"
    "\n"
    "{field_meanings}\n"
    "\n"
    "**When you're done:** convert this into `crawler/grader.py`'s "
    "frozen `KeyEntry` format and run `python3 -m crawler grade` for a "
    "real PASS/FAIL verdict. Once converted, the key is frozen -- "
    "re-opening it to \"fix\" a grade after seeing a bad result would be "
    "the same contamination this worksheet exists to avoid.\n"
    "\n"
    "---\n")


def _program_heading(uni_id, program):
    return "## {0} — {1} — {2}".format(
        uni_id, program.id, program.name)


# Per-field pages the schema exposes: a field whose value lives on a
# DIFFERENT page than program.page must name that page, or a labeler
# fills the field in from the wrong document (VUM's vum-sst/vum-gca both
# carry an adm_page distinct from page).
_FIELD_PAGE_ATTRS = {
    "language": "lang_page",
    "admission": "adm_page",
    "tuition": "tuition_page",
}


def _field_block(program):
    lines = []
    for field_name in FIELDS:
        lines.append("- field: {0}".format(field_name))
        page = getattr(program, _FIELD_PAGE_ATTRS.get(field_name, ""), None)
        if page:
            lines.append("  (read this field from: {0})".format(page))
        lines.append("  value:")
    return lines


def build_worksheet(site):
    # type: (object) -> str
    """Blank Phase-0 worksheet for every program in SITE, grouped by
    page. No pipeline-extracted values anywhere in the output -- the
    whole point is a human reads the real page independently, not a
    review of what the pipeline already said."""
    by_page = OrderedDict()
    for program in site.programs:
        by_page.setdefault(program.page, []).append(program)

    parts = [_HEADER_TEMPLATE.format(
        uni_id=site.uni_id, field_meanings=_FIELD_MEANINGS)]

    for page, programs in by_page.items():
        block = [_program_heading(site.uni_id, p) for p in programs]
        if len(programs) > 1:
            block.append("Page (shared by all {0} above): {1}".format(
                len(programs), page))
            block.append("")
            block.append(
                "This page describes multiple programs -- check whether "
                "it actually distinguishes what applies to each specific "
                "program vs. what it says generically for the whole "
                "page/faculty. If a value is stated only generically, "
                "not tied to one specific program, say so -- that's a "
                "real distinction the grader needs (a program-specific "
                "claim vs. a page-wide one aren't the same kind of "
                "evidence).")
        else:
            block.append("Page: {0}".format(page))
        block.append("")

        for program in programs:
            if len(programs) > 1:
                block.append("### {0}".format(program.id))
            block.extend(_field_block(program))
            block.append("")

        parts.append("\n".join(block))

    return "\n".join(parts)
