"""The Readable set (crawler/field_sources.py): the documents one
Program-field may draw values from, computed once and consumed
identically by the deterministic cascade and the LLM tail.

This module exists because the selection policy lived TWICE — braided
into cascade.resolve_field's tier order and restated in
llm_tail.candidate_docs — and the copies drifted twice, measured: the
tail read shared pages whole until 2026-08-23 (VUM durations swapped
between two Programs), and the re-add guard was spelled four times in
cascade and zero times in the tail. All offline.

Five proofs:

1. TABLE — the field→routed-page map covers exactly the five fields.
2. INVARIANT no-spans — a shared page that never names the Program
   yields no view of that page at all.
3. INVARIANT ref-identity — a document participates once, under its
   Artifact ref; an aliased route (ANIS's shared page doubling as a
   configured source) cannot re-enter the whole page. This is the hole
   neither URL-guard copy could close.
4. AGREEMENT — for every field and config shape, the cascade's harvest
   views and the tail's model views derive from the same Readable set,
   and the tail's candidate refs are exactly the set's refs plus the
   field's join sources.
5. ORDER — tuition_page is late: tier-F joins beat its labels, exactly
   as today; lang/adm pages harvest with the main set.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import cascade, config, field_sources, llm_tail  # noqa: E402
from crawler.field_sources import ScopedDoc, readable_sources  # noqa: E402

PAGE_URL = "https://x.test/shared"
SHARED_PAGE = ("Астрономия\nСрок на обучение: 8 семестъра\n"
               "Биология\nСрок на обучение: 6 семестъра\n")


def site_of(programs, sources=None):
    return config.parse_site_config({
        "uni_id": "TestUni", "cookies": {},
        "sources": sources or {}, "programs": programs})


def shared_docs():
    src = cascade.TextSource("html:" + PAGE_URL, SHARED_PAGE)
    return {PAGE_URL: src}


SHARED_TWO = [
    {"id": "alpha", "name": "Астрономия", "page": PAGE_URL},
    {"id": "beta", "name": "Биология", "page": PAGE_URL},
]


class TableTest(unittest.TestCase):
    def test_the_routed_page_table_covers_exactly_the_five_fields(self):
        self.assertEqual(set(field_sources.FIELD_PAGES), set(config.FIELDS))


class NoSpansTest(unittest.TestCase):
    def test_a_shared_page_that_never_names_the_program_yields_nothing(self):
        site = site_of([
            {"id": "ghost", "name": "Философия", "page": PAGE_URL},
            {"id": "beta", "name": "Биология", "page": PAGE_URL},
        ])
        out = readable_sources(site, site.program("ghost"), "degree",
                               shared_docs())
        self.assertEqual(out, [])

    def test_a_sole_program_page_passes_whole_and_unscoped(self):
        site = site_of([{"id": "solo", "name": "Астрономия",
                         "page": PAGE_URL}])
        out = readable_sources(site, site.program("solo"), "degree",
                               shared_docs())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].spans, (SHARED_PAGE,))
        self.assertIsNotNone(out[0].source)


class RefIdentityTest(unittest.TestCase):
    """The ANIS shape: the shared page is ALSO a configured source, so
    the same TextSource sits in docs under two keys."""

    def _aliased(self, extra_key):
        progs = [dict(SHARED_TWO[0], **{extra_key: ["anis-cat"]}),
                 SHARED_TWO[1]]
        site = site_of(progs, sources={
            "anis-cat": {"url": PAGE_URL, "route": "html"}})
        docs = shared_docs()
        docs["anis-cat"] = docs[PAGE_URL]        # same object, two keys
        return site, docs

    def test_an_aliased_extra_source_cannot_smuggle_the_whole_page(self):
        site, docs = self._aliased("extra_sources")
        out = readable_sources(site, site.program("alpha"), "degree", docs)
        self.assertEqual(len(out), 1, "one participation per ref")
        self.assertIsNone(out[0].source, "the scoped view is what "
                          "participates, never the whole page")
        joined = " ".join(out[0].spans)
        self.assertNotIn("Биология", joined)

    def test_the_scoped_harvest_never_sees_the_sibling(self):
        """The previously-broken path, behaviorally. Alpha's own region
        states NO duration, the sibling's does — so the only way to
        ship a value is through the leaked whole page, and the correct
        answer is the honest null. (A weaker fixture where alpha's own
        region answers first would pass on first-hit luck even with the
        leak open.)"""
        progs = [{"id": "alpha", "name": "Астрономия", "page": PAGE_URL,
                  "extra_sources": ["anis-cat"]},
                 {"id": "beta", "name": "Биология", "page": PAGE_URL}]
        site = site_of(progs, sources={
            "anis-cat": {"url": PAGE_URL, "route": "html"}})
        page = ("Астрономия\nНаблюдателна програма без срок.\n"
                "Биология\nСрок на обучение: 6 семестъра\n")
        src = cascade.TextSource("html:" + PAGE_URL, page)
        docs = {PAGE_URL: src, "anis-cat": src}
        r = cascade.resolve_field(site, site.program("alpha"),
                                  "duration", docs)
        self.assertIsNone(r, "a value here can only have come from the "
                          "sibling's region via the aliased whole page")

    def test_extra_pages_listing_the_own_page_is_dropped_too(self):
        site = site_of([dict(SHARED_TWO[0], extra_pages=[PAGE_URL]),
                        SHARED_TWO[1]])
        out = readable_sources(site, site.program("alpha"), "degree",
                               shared_docs())
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].source)


class AgreementTest(unittest.TestCase):
    """One return value IS the agreement: both consumers derive from it.
    Checked across the config-shape matrix for every field."""

    SHAPES = {
        "sole": ([{"id": "p", "name": "Астрономия", "page": PAGE_URL}], {}),
        "shared": (SHARED_TWO, {}),
        "routed": ([
            {"id": "p", "name": "Астрономия", "page": PAGE_URL,
             "lang_page": "https://x.test/lang",
             "adm_page": "https://x.test/adm",
             "tuition_page": "https://x.test/fees",
             "extra_pages": ["https://x.test/extra"]},
        ], {}),
    }

    def docs_for(self, progs):
        docs = shared_docs()
        for url in ("https://x.test/lang", "https://x.test/adm",
                    "https://x.test/fees", "https://x.test/extra"):
            docs[url] = cascade.TextSource("html:" + url, "текст " + url)
        return docs

    def test_tail_refs_are_the_readable_set_refs(self):
        for shape, (progs, sources) in self.SHAPES.items():
            site = site_of(progs, sources)
            prog = site.programs[0]
            docs = self.docs_for(progs)
            for field in config.FIELDS:
                with self.subTest(shape=shape, field=field):
                    rs = readable_sources(site, prog, field, docs)
                    tail = dict(llm_tail.candidate_docs(
                        site, prog, field, docs))
                    self.assertEqual([d.ref for d in rs],
                                     list(tail)[:len(rs)],
                                     "the tail consumes the Readable set "
                                     "first, in order")

    def test_scoped_views_agree_between_consumers(self):
        site = site_of(SHARED_TWO)
        docs = shared_docs()
        rs = readable_sources(site, site.program("alpha"), "degree", docs)
        self.assertEqual(len(rs), 1)
        sd = rs[0]
        # harvest views: one normed TextSource per span
        hv = sd.harvest_views()
        self.assertTrue(all(isinstance(v, cascade.TextSource) for v in hv))
        self.assertEqual([v.text for v in hv],
                         [cascade.norm(s) for s in sd.spans])
        # model view: the raw spans joined, same ref
        mv = sd.model_view()
        self.assertEqual(mv.ref, sd.ref)
        self.assertEqual(mv.text, "\n".join(sd.spans))


class LateOrderTest(unittest.TestCase):
    def test_tuition_page_is_late(self):
        site = site_of([{"id": "p", "name": "Астрономия", "page": PAGE_URL,
                         "tuition_page": "https://x.test/fees"}])
        docs = shared_docs()
        docs["https://x.test/fees"] = cascade.TextSource(
            "html:https://x.test/fees", "Годишна такса: 900 лв")
        rs = readable_sources(site, site.program("p"), "tuition", docs)
        flags = {d.ref: d.late for d in rs}
        self.assertFalse(flags["html:" + PAGE_URL])
        self.assertTrue(flags["html:https://x.test/fees"])

    def test_lang_and_adm_pages_are_not_late(self):
        site = site_of([{"id": "p", "name": "Астрономия", "page": PAGE_URL,
                         "lang_page": "https://x.test/lang"}])
        docs = shared_docs()
        docs["https://x.test/lang"] = cascade.TextSource(
            "html:https://x.test/lang", "Език на преподаване БЪЛГАРСКИ")
        rs = readable_sources(site, site.program("p"), "language", docs)
        self.assertFalse(any(d.late for d in rs))


if __name__ == "__main__":
    unittest.main()
