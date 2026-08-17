"""The gated LLM tail suite (ticket 02) — FakeAdapter only, zero network.

Five proofs:

1. CANDIDATE DOCS — per-field document selection mirrors resolve_field's
   own routing (own page + extra pages/sources always; field-specific
   join/section sources added only for the field that uses them).
2. GATE INTEGRATION — a PASSing structured response becomes an
   Extraction whose segments actually gate against the store artifact;
   a REJECTing response does not.
3. RETRY WITH FEEDBACK — a same-tier retry happens on REJECT_* (with the
   gate's detail folded into the second prompt) but not on PASS.
4. ESCALATION — a field that fails twice at Haiku gets one Sonnet
   attempt; a PARSE_FAILURE (no feedback to act on) skips the retry and
   escalates immediately.
5. ADMISSION MULTI-SEGMENT — a joined value with independent per-segment
   containment, same shape a tier-F join already produces.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import cascade, config, llm_tail  # noqa: E402
from crawler.provenance import Artifact, Status  # noqa: E402


def artifact(text, ref):
    return Artifact(text=text, renderer_id="test-synthetic",
                    renderer_version="0", ref=ref)


class FakeStore:
    """store.artifact(ref) stand-in — the tail never constructs Artifacts
    itself (ADR-0002); it only resolves refs through whatever the runner
    hands it, exactly like the real ArtifactStore."""

    def __init__(self, artifacts):
        self._artifacts = artifacts

    def artifact(self, ref):
        return self._artifacts[ref]


def make_program(**kw):
    base = dict(id="p1", name="Информатика", page="page-url")
    base.update(kw)
    return config.ProgramConfig(**base)


def make_site(programs=(), **kw):
    base = dict(uni_id="TestUni", sources={}, programs=tuple(programs))
    base.update(kw)
    return config.SiteConfig(**base)


class CandidateDocsTest(unittest.TestCase):
    def test_own_page_and_extras_always_included(self):
        program = make_program(extra_pages=("extra1",), extra_sources=("src1",))
        docs = {"page-url": cascade.TextSource(ref="page-url", text="a"),
                "extra1": cascade.TextSource(ref="extra1", text="b"),
                "src1": cascade.TextSource(ref="src1", text="c"),
                "unrelated": cascade.TextSource(ref="unrelated", text="d")}
        site = make_site([program])
        got = dict(llm_tail.candidate_docs(site, program, "degree", docs))
        self.assertEqual(set(got), {"page-url", "extra1", "src1"})

    def test_language_field_adds_lang_page_only(self):
        program = make_program(lang_page="lang-url", adm_page="adm-url")
        docs = {"page-url": cascade.TextSource(ref="page-url", text="a"),
                "lang-url": cascade.TextSource(ref="lang-url", text="b"),
                "adm-url": cascade.TextSource(ref="adm-url", text="c")}
        site = make_site([program])
        got = dict(llm_tail.candidate_docs(site, program, "language", docs))
        self.assertEqual(set(got), {"page-url", "lang-url"})
        got_adm = dict(llm_tail.candidate_docs(site, program, "admission", docs))
        self.assertEqual(set(got_adm), {"page-url", "adm-url"})

    def test_missing_docs_are_skipped_not_errored(self):
        program = make_program(lang_page="never-fetched")
        docs = {"page-url": cascade.TextSource(ref="page-url", text="a")}
        site = make_site([program])
        got = dict(llm_tail.candidate_docs(site, program, "language", docs))
        self.assertEqual(set(got), {"page-url"})

    def test_no_candidates_at_all(self):
        program = make_program(page="never-fetched")
        docs = {}
        site = make_site([program])
        self.assertEqual(llm_tail.candidate_docs(site, program, "degree", docs), [])

    def test_returned_ref_is_source_ref_not_the_docs_dict_key(self):
        # Regression: every other fixture in this class sets
        # TextSource.ref EQUAL to its own docs-dict key ("page-url" both
        # as the dict key and as ref=), which is NOT how runner.build_docs
        # actually builds `docs` in production -- there, `docs` is keyed
        # by URL/source-id purely for lookup, while TextSource.ref holds
        # the real ArtifactStore ref (e.g. "html:" + url), a DIFFERENT
        # string. candidate_docs() used to return the docs-dict key as if
        # it were the artifact ref -- store.artifact(ref) in
        # resolve_via_tail's attempt() would then KeyError on any field
        # that actually PASSed, live-verified against a real
        # ArtifactStore (2026-08-15).
        program = make_program(
            page="https://example.bg/program",
            extra_sources=("fee-table",))
        docs = {
            "https://example.bg/program": cascade.TextSource(
                ref="html:https://example.bg/program", text="a"),
            "fee-table": cascade.TextSource(
                ref="pdftext:fee-table", text="b"),
        }
        site = make_site([program])
        got_refs = {ref for ref, _ in
                   llm_tail.candidate_docs(site, program, "degree", docs)}
        self.assertEqual(
            got_refs, {"html:https://example.bg/program", "pdftext:fee-table"})


class GateIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.program = make_program()
        self.site = make_site([self.program])
        self.docs = {"page-url": cascade.TextSource(
            ref="page-url", text="Продължителност на обучението (брой семестри): 8")}
        self.store = FakeStore({"page-url": artifact(self.docs["page-url"].text, "page-url")})

    def test_pass_becomes_extraction(self):
        adapter = llm_tail.FakeAdapter({
            "p1:duration": {"source_ref": "page-url", "value": "8",
                            "segments": ["Продължителност на обучението (брой семестри): 8"],
                            "null_reason": None},
        })
        result = llm_tail.resolve_via_tail(adapter, self.store, self.site,
                                           self.program, "duration", self.docs)
        self.assertEqual(result.verdict.status, Status.PASS)
        self.assertIsNotNone(result.extraction)
        self.assertEqual(result.extraction.value, "8")
        self.assertEqual(result.extraction.tier, "llm-tail")
        self.assertEqual(result.attempts, 1)
        self.assertFalse(result.escalated)

    def test_fabricated_segment_is_rejected_not_shipped(self):
        adapter = llm_tail.FakeAdapter({
            # every attempt (initial, retry, escalation) returns the same
            # fabricated segment — must never pass, however many tiers
            "p1:duration": {"source_ref": "page-url", "value": "12",
                            "segments": ["this text is not in the document"],
                            "null_reason": None},
        })
        result = llm_tail.resolve_via_tail(adapter, self.store, self.site,
                                           self.program, "duration", self.docs)
        self.assertEqual(result.verdict.status, Status.REJECT_CONTAINMENT)
        self.assertIsNone(result.extraction)
        self.assertTrue(result.escalated)

    def test_affirmative_null(self):
        adapter = llm_tail.FakeAdapter({
            "p1:duration": {"source_ref": None, "value": None, "segments": [],
                            "null_reason": "not stated anywhere"},
        })
        result = llm_tail.resolve_via_tail(adapter, self.store, self.site,
                                           self.program, "duration", self.docs)
        self.assertEqual(result.verdict.status, Status.NULL_OK)
        self.assertEqual(result.verdict.detail, "not stated anywhere")
        self.assertIsNone(result.extraction)
        self.assertEqual(result.attempts, 1)


class RetryEscalationTest(unittest.TestCase):
    def setUp(self):
        self.program = make_program()
        self.site = make_site([self.program])
        self.docs = {"page-url": cascade.TextSource(
            ref="page-url", text="Продължителност на обучението (брой семестри): 8")}
        self.store = FakeStore({"page-url": artifact(self.docs["page-url"].text, "page-url")})

    def test_reject_then_retry_pass(self):
        calls = []

        def responder(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return {"source_ref": "page-url", "value": "8",
                       "segments": ["fabricated, not in doc"], "null_reason": None}
            return {"source_ref": "page-url", "value": "8",
                   "segments": ["Продължителност на обучението (брой семестри): 8"],
                   "null_reason": None}

        adapter = llm_tail.FakeAdapter({"p1:duration": responder})
        result = llm_tail.resolve_via_tail(adapter, self.store, self.site,
                                           self.program, "duration", self.docs)
        self.assertEqual(result.verdict.status, Status.PASS)
        self.assertEqual(result.attempts, 2)
        self.assertFalse(result.escalated)
        self.assertEqual(len(calls), 2)
        # second call must carry the first attempt'''s gate feedback
        self.assertIn("REJECTED", calls[1])

    def test_two_rejects_escalate_to_sonnet(self):
        adapter = llm_tail.FakeAdapter({
            ("haiku", "p1:duration:1"): {"source_ref": "page-url", "value": "8",
                                        "segments": ["nope"], "null_reason": None},
            ("haiku", "p1:duration:2"): {"source_ref": "page-url", "value": "8",
                                        "segments": ["still nope"], "null_reason": None},
            ("sonnet", "p1:duration:3"): {"source_ref": "page-url", "value": "8",
                                         "segments": ["Продължителност на обучението (брой семестри): 8"],
                                         "null_reason": None},
        })
        result = llm_tail.resolve_via_tail(adapter, self.store, self.site,
                                           self.program, "duration", self.docs)
        self.assertEqual(result.verdict.status, Status.PASS)
        self.assertEqual(result.attempts, 3)
        self.assertTrue(result.escalated)
        models_called = [c["model"] for c in adapter.calls]
        self.assertEqual(models_called, ["haiku", "haiku", "sonnet"])

    def test_parse_failure_skips_retry_goes_straight_to_escalation(self):
        def blow_up(prompt):
            raise RuntimeError("CLI timed out")

        adapter = llm_tail.FakeAdapter({
            ("haiku", "p1:duration:1"): blow_up,
            ("sonnet", "p1:duration:3"): {"source_ref": "page-url", "value": "8",
                                         "segments": ["Продължителност на обучението (брой семестри): 8"],
                                         "null_reason": None},
        })
        result = llm_tail.resolve_via_tail(adapter, self.store, self.site,
                                           self.program, "duration", self.docs)
        self.assertEqual(result.verdict.status, Status.PASS)
        self.assertEqual(result.attempts, 2)  # 1 failed attempt + 1 escalated, NO middle retry
        self.assertTrue(result.escalated)
        models_called = [c["model"] for c in adapter.calls]
        self.assertEqual(models_called, ["haiku", "sonnet"])


class RealArtifactRefShapeTest(unittest.TestCase):
    """End-to-end through the actual seam that broke live (2026-08-15):
    candidate_docs's ref -> build_prompt's displayed ref -> the model's
    returned source_ref -> store.artifact(ref). docs is keyed differently
    from source.ref here (production shape, runner.build_docs), and the
    responder reads the ref the PROMPT actually displays rather than a
    hardcoded canned string -- every other test in this file hardcodes
    "page-url" as both the docs-dict key AND source.ref, which is exactly
    what let the original bug hide from every one of them."""

    def setUp(self):
        self.program = make_program(page="https://example.bg/program")
        self.site = make_site([self.program])
        self.text = "Продължителност на обучението (брой семестри): 8"
        self.real_ref = "html:https://example.bg/program"
        self.docs = {"https://example.bg/program": cascade.TextSource(
            ref=self.real_ref, text=self.text)}
        self.store = FakeStore({self.real_ref: artifact(self.text, self.real_ref)})

    def test_model_echoing_the_prompts_displayed_ref_resolves_via_the_real_store_ref(self):
        import re

        def responder(prompt):
            shown_ref = re.search(r"ref=('.*?')\)", prompt).group(1)[1:-1]
            return {"source_ref": shown_ref, "value": "8",
                   "segments": [self.text], "null_reason": None}

        adapter = llm_tail.FakeAdapter({"p1:duration": responder})
        result = llm_tail.resolve_via_tail(adapter, self.store, self.site,
                                           self.program, "duration", self.docs)
        self.assertEqual(result.verdict.status, Status.PASS)

    def test_source_ref_outside_the_candidate_set_is_parse_failure_not_a_crash(self):
        # Schema enforcement should make this impossible, but the
        # membership backstop must catch it anyway -- if it didn't,
        # store.artifact() would raise a raw, uncaught KeyError here
        # (self.real_ref is the only key in self.store's dict).
        adapter = llm_tail.FakeAdapter({
            "p1:duration": {"source_ref": "some-other-ref-the-model-invented",
                            "value": "8", "segments": [self.text],
                            "null_reason": None}})
        result = llm_tail.resolve_via_tail(adapter, self.store, self.site,
                                           self.program, "duration", self.docs)
        self.assertEqual(result.verdict.status, Status.PARSE_FAILURE)


class AdmissionMultiSegmentTest(unittest.TestCase):
    def test_joined_value_each_segment_checked_independently(self):
        program = make_program(adm_page="adm-url")
        site = make_site([program])
        text = ("Конкурс: изпит по биология. Кандидатите могат също да "
               "участват с оценка от олимпиада по химия.")
        docs = {"page-url": cascade.TextSource(ref="page-url", text="x"),
               "adm-url": cascade.TextSource(ref="adm-url", text=text)}
        store = FakeStore({"adm-url": artifact(text, "adm-url")})
        adapter = llm_tail.FakeAdapter({
            "p1:admission": {
                "source_ref": "adm-url",
                "value": "изпит по биология; оценка от олимпиада по химия",
                "segments": ["Конкурс: изпит по биология",
                            "оценка от олимпиада по химия"],
                "null_reason": None,
            },
        })
        result = llm_tail.resolve_via_tail(adapter, store, site, program,
                                           "admission", docs)
        self.assertEqual(result.verdict.status, Status.PASS)
        self.assertEqual(len(result.extraction.segments), 2)

    def test_one_fabricated_segment_among_real_ones_rejects_the_whole_value(self):
        program = make_program(adm_page="adm-url")
        site = make_site([program])
        text = "Конкурс: изпит по биология."
        docs = {"page-url": cascade.TextSource(ref="page-url", text="x"),
               "adm-url": cascade.TextSource(ref="adm-url", text=text)}
        store = FakeStore({"adm-url": artifact(text, "adm-url")})
        adapter = llm_tail.FakeAdapter({
            "p1:admission": {
                "source_ref": "adm-url",
                "value": "изпит по биология; some invented route",
                "segments": ["Конкурс: изпит по биология", "invented segment"],
                "null_reason": None,
            },
        })
        result = llm_tail.resolve_via_tail(adapter, store, site, program,
                                           "admission", docs)
        self.assertEqual(result.verdict.status, Status.REJECT_CONTAINMENT)
        self.assertIsNone(result.extraction)


class SourceRefEnumTest(unittest.TestCase):
    def test_schema_restricts_source_ref_to_real_candidates(self):
        schema = llm_tail.build_schema(["doc-a", "doc-b"])
        enum = schema["properties"]["source_ref"]["enum"]
        self.assertEqual(set(enum), {None, "doc-a", "doc-b"})


class DegreeFieldHintTest(unittest.TestCase):
    """Regression for ticket 07's gate FAIL: uniruse-bizmgmt's degree
    extraction picked "бизнес мениджър" (a professional-qualification
    title) over "Бакалавър" (the degree level actually being asked for)
    -- and it survived escalation to Sonnet, so the fix is telling the
    model which one the field means, not a mechanical disambiguator."""

    # The real page text behind the 2026-08-16 gate FAIL: it prints BOTH
    # the degree level and a professional-qualification title, close
    # together. The tail (at Sonnet, after escalation) picked the title.
    REAL_SHAPE = ('Обучението завършва с придобиване на образователно-'
                  'квалификационна степен "бакалавър" с професионална '
                  'квалификация "бизнес мениджър".')

    def test_prompt_tells_the_model_to_prefer_degree_level_over_qualification_title(self):
        docs = [("page-url", cascade.TextSource(ref="page-url", text="x"))]
        prompt = llm_tail.build_prompt("Бизнес мениджмънт", "UniRuse",
                                       "degree", docs)
        self.assertIn("DEGREE LEVEL", prompt)
        self.assertIn("qualification title", prompt.lower())

    def test_degree_level_answer_resolves_against_the_real_two_term_shape(self):
        program = make_program(name="Бизнес мениджмънт")
        site = make_site([program])
        docs = {"page-url": cascade.TextSource(ref="page-url",
                                               text=self.REAL_SHAPE)}
        store = FakeStore({"page-url": artifact(self.REAL_SHAPE, "page-url")})
        adapter = llm_tail.FakeAdapter({
            "p1:degree": {
                "source_ref": "page-url",
                "value": "бакалавър",
                "segments": ['образователно-квалификационна степен '
                             '"бакалавър"'],
                "null_reason": None,
            },
        })
        result = llm_tail.resolve_via_tail(adapter, store, site, program,
                                           "degree", docs)
        self.assertEqual(result.verdict.status, Status.PASS)
        self.assertEqual(result.extraction.value, "бакалавър")

    def test_the_qualification_title_the_gate_run_shipped_is_still_gate_checkable(self):
        # Both terms are verbatim on the page, so gate() PASSes either --
        # which is exactly why this had to be fixed in the prompt and
        # could never have been caught by the gate. This test pins that
        # asymmetry so nobody later "fixes" it by tightening gate().
        program = make_program(name="Бизнес мениджмънт")
        site = make_site([program])
        docs = {"page-url": cascade.TextSource(ref="page-url",
                                               text=self.REAL_SHAPE)}
        store = FakeStore({"page-url": artifact(self.REAL_SHAPE, "page-url")})
        adapter = llm_tail.FakeAdapter({
            "p1:degree": {
                "source_ref": "page-url",
                "value": "бизнес мениджър",
                "segments": ['професионална квалификация "бизнес мениджър"'],
                "null_reason": None,
            },
        })
        result = llm_tail.resolve_via_tail(adapter, store, site, program,
                                           "degree", docs)
        self.assertEqual(result.verdict.status, Status.PASS)


if __name__ == "__main__":
    unittest.main()
