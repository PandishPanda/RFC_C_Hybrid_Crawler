"""Two Programs that share a page AND a name (VUM's professional-
bachelor / bachelor twins), and the tail's shared-page scoping.

Measured 2026-08-23: vum-hm-pb and vum-hm-b are config-identical except
for their id — same name, same page, same fees_section — so NOTHING told
the pipeline them apart. The LLM tail, handed the whole page twice, came
back with the durations swapped and with a complete admission answer for
one twin and a fragment for the other. The gate passed all of it: every
string is verbatim on the shared page.

Two proofs, because the two halves fix different things:

1. TWINS — a same-named twin is separated by a tier-B anchor on the
   degree-level row of the page's own table. program_region() cannot do
   this by construction: it refuses to let same-named siblings bound
   each other, so both twins get the same span.
2. TAIL SCOPING — where siblings have DIFFERENT names, the tail now
   reads only the program's own region, the same slice tier G gets.
   This closes the general gap; it does NOT fix the twins.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import cascade, config, llm_tail  # noqa: E402

PAGE = ("Хотелски мениджмънт\n"
        "Прием\nОбразователна степен\nСептемврийски прием\nФевруарски прием\n"
        "Професионален бакалавър\n3 години / 6 семестъра\n"
        "3.5 години / 6 семестъра\n"
        "Бакалавър\n4 години / 8 семестъра\n4.5 години / 8 семестъра\n"
        "Приемът във ВУМ се основава на образователната подготовка.")

TWINS = {
    "uni_id": "TwinUni", "cookies": {}, "sources": {},
    "anchors": {
        "hm-pb-duration": {
            "source": "https://x.test/hm",
            "pattern": r"Професионален бакалавър\s+(\d+(?:[.,]\d+)? години / \d+ семестъра)"},
        "hm-b-duration": {
            "source": "https://x.test/hm",
            "pattern": r"(?<!Професионален )Бакалавър\s+(\d+(?:[.,]\d+)? години / \d+ семестъра)"},
    },
    "programs": [
        {"id": "hm-pb", "name": "Хотелски мениджмънт",
         "page": "https://x.test/hm",
         "field_anchors": {"duration": "hm-pb-duration"}},
        {"id": "hm-b", "name": "Хотелски мениджмънт",
         "page": "https://x.test/hm",
         "field_anchors": {"duration": "hm-b-duration"}},
    ],
}


def docs():
    return {"https://x.test/hm": cascade.TextSource("html:https://x.test/hm",
                                                    PAGE)}


class TwinTest(unittest.TestCase):
    def setUp(self):
        self.site = config.parse_site_config(TWINS)

    def test_region_scoping_alone_cannot_separate_twins(self):
        """Why an anchor is needed: program_region refuses to let a
        same-named sibling bound a span, so both twins see the same
        page. Region scoping is not the fix here, and a fix that relied
        on it would silently not work."""
        p = self.site.program("hm-b")
        siblings = [q.name for q in self.site.programs
                    if q.page == p.page and q.id != p.id]
        self.assertEqual(siblings, ["Хотелски мениджмънт"])
        spans_b = cascade.program_region(PAGE, p.name, siblings)
        spans_pb = cascade.program_region(
            PAGE, self.site.program("hm-pb").name, siblings)
        self.assertEqual(spans_b, spans_pb)

    def test_each_twin_reads_its_own_degree_level_row(self):
        pb = cascade.resolve_field(self.site, self.site.program("hm-pb"),
                                   "duration", docs())
        b = cascade.resolve_field(self.site, self.site.program("hm-b"),
                                  "duration", docs())
        self.assertEqual(pb.value, "3 години / 6 семестъра")
        self.assertEqual(b.value, "4 години / 8 семестъра")
        self.assertEqual(pb.tier, "B")

    def test_the_bachelor_anchor_does_not_match_the_professional_row(self):
        """The whole risk in one assertion: «Бакалавър» is a substring of
        «Професионален бакалавър» only case-insensitively, but the rows
        sit adjacent, so a careless pattern takes the wrong one."""
        b = cascade.resolve_field(self.site, self.site.program("hm-b"),
                                  "duration", docs())
        self.assertNotEqual(b.value, "3 години / 6 семестъра")

    def test_resolving_deterministically_keeps_the_tail_out(self):
        """A tier-B hit means resolve_field returns a value, so the
        runner never calls the tail for this cell — which is what makes
        it stable run to run."""
        self.assertIsNotNone(
            cascade.resolve_field(self.site, self.site.program("hm-b"),
                                  "duration", docs()))


SHARED = {
    "uni_id": "SharedUni", "cookies": {}, "sources": {},
    "programs": [
        {"id": "alpha", "name": "Астрономия", "page": "https://x.test/s"},
        {"id": "beta", "name": "Биология", "page": "https://x.test/s"},
    ],
}

SHARED_PAGE = ("Астрономия\nСрок на обучение: 8 семестъра\n"
               "Биология\nСрок на обучение: 6 семестъра\n")


class TailScopingTest(unittest.TestCase):
    """The general gap: the tail used to receive the whole shared page."""

    def setUp(self):
        self.site = config.parse_site_config(SHARED)
        self.docs = {"https://x.test/s": cascade.TextSource(
            "html:https://x.test/s", SHARED_PAGE)}

    def test_the_tail_sees_only_its_own_region(self):
        pairs = llm_tail.candidate_docs(
            self.site, self.site.program("alpha"), "duration", self.docs)
        self.assertEqual(len(pairs), 1)
        text = pairs[0][1].text
        self.assertIn("Астрономия", text)
        self.assertNotIn("Биология", text)
        self.assertNotIn("6 семестъра", text)

    def test_a_sole_program_page_is_not_scoped(self):
        site = config.parse_site_config({
            "uni_id": "SoloUni", "cookies": {}, "sources": {},
            "programs": [{"id": "solo", "name": "Астрономия",
                          "page": "https://x.test/s"}]})
        pairs = llm_tail.candidate_docs(site, site.program("solo"),
                                        "duration", self.docs)
        self.assertEqual(pairs[0][1].text, SHARED_PAGE)

    def test_the_artifact_ref_is_preserved(self):
        """Provenance must still resolve: the tail quotes back a ref the
        store indexes, and a scoped view is not a new artifact."""
        pairs = llm_tail.candidate_docs(
            self.site, self.site.program("alpha"), "duration", self.docs)
        self.assertEqual(pairs[0][0], "html:https://x.test/s")
        self.assertEqual(pairs[0][1].ref, "html:https://x.test/s")


if __name__ == "__main__":
    unittest.main()
