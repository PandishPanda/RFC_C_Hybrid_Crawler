"""crawler.onboarding suite (ticket 06) -- zero network.

FakeAdapter (from llm_tail, same pattern as test_llm_tail.py) + fake
fetcher/store doubles. Real crawler.provenance.gate() throughout --
verify_page() must never hand-wave a field as extracted.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.llm_tail import FakeAdapter  # noqa: E402
from crawler.onboarding import (  # noqa: E402
    ProposedProgram,
    build_prompt,
    build_schema,
    discover_links,
    fetch_links,
    propose_onboarding,
    run_onboarding,
    validate_as_draft_config,
    verify_page,
    write_proposal,
)
from crawler.provenance import Artifact  # noqa: E402
from crawler.registry import RegistryRow  # noqa: E402


def make_row(id, code, name, major_id=1, major_name="Направление",
            degree_code=3, degree_name="Бакалавър"):
    return RegistryRow(id=id, code=code, name=name, major_id=major_id,
                       major_name=major_name, degree_code=degree_code,
                       degree_name=degree_name)


class DiscoverLinksTest(unittest.TestCase):
    HTML = b"""
    <html><body>
      <a href="/programs/cs">Computer Science</a>
      <a href="https://other.example/x">Off-domain</a>
      <a href="#top">Anchor only</a>
      <a href="mailto:a@b.com">Email</a>
      <a href="/programs/cs">Computer Science (dup)</a>
      <a href="/about#history">About</a>
    </body></html>
    """

    def test_same_domain_links_extracted_with_text(self):
        links = discover_links(self.HTML, "https://uni.example/index")
        urls = [u for u, _ in links]
        self.assertIn("https://uni.example/programs/cs", urls)
        self.assertIn("https://uni.example/about", urls)

    def test_off_domain_link_excluded(self):
        links = discover_links(self.HTML, "https://uni.example/index")
        urls = [u for u, _ in links]
        self.assertNotIn("https://other.example/x", urls)

    def test_fragment_only_and_mailto_excluded(self):
        links = discover_links(self.HTML, "https://uni.example/index")
        urls = [u for u, _ in links]
        self.assertFalse(any(u.endswith("#top") for u in urls))
        self.assertFalse(any(u.startswith("mailto:") for u in urls))

    def test_duplicate_hrefs_deduped(self):
        links = discover_links(self.HTML, "https://uni.example/index")
        urls = [u for u, _ in links]
        self.assertEqual(urls.count("https://uni.example/programs/cs"), 1)

    def test_fragment_stripped_from_url(self):
        links = discover_links(self.HTML, "https://uni.example/index")
        urls = [u for u, _ in links]
        self.assertIn("https://uni.example/about", urls)
        self.assertNotIn("https://uni.example/about#history", urls)


class FakeSnap:
    def __init__(self, ok, body=b""):
        self.ok = ok
        self._body = body

    def read_bytes(self):
        return self._body


class FakeLinkFetcher:
    def __init__(self, pages):
        self.pages = pages  # {url: html_bytes}

    def fetch(self, url, site_config=None):
        if url not in self.pages:
            return FakeSnap(False)
        return FakeSnap(True, self.pages[url])


class FetchLinksTest(unittest.TestCase):
    def test_unions_links_across_seed_pages_and_dedups(self):
        fetcher = FakeLinkFetcher({
            "https://uni.example/a": b'<a href="/p1">P1</a><a href="/p2">P2</a>',
            "https://uni.example/b": b'<a href="/p2">P2 again</a><a href="/p3">P3</a>',
        })
        links = fetch_links(["https://uni.example/a", "https://uni.example/b"],
                            fetcher)
        urls = sorted(u for u, _ in links)
        self.assertEqual(urls, ["https://uni.example/p1",
                                "https://uni.example/p2",
                                "https://uni.example/p3"])

    def test_a_failed_seed_fetch_is_skipped_not_fatal(self):
        fetcher = FakeLinkFetcher({})
        links = fetch_links(["https://uni.example/missing"], fetcher)
        self.assertEqual(links, [])


class SchemaPromptTest(unittest.TestCase):
    def test_schema_restricts_url_to_candidates_plus_null(self):
        schema = build_schema(["https://x/a", "https://x/b"])
        self.assertEqual(schema["properties"]["url"]["enum"],
                         [None, "https://x/a", "https://x/b"])

    def test_prompt_includes_row_identity_and_candidates(self):
        row = make_row(1, "AAA", "Софтуерни системи и технологии")
        prompt = build_prompt(row, [("https://x/a", "CS Programme")])
        self.assertIn("Софтуерни системи и технологии", prompt)
        self.assertIn("https://x/a", prompt)
        self.assertIn("CS Programme", prompt)


class FakeDoc:
    def __init__(self, ref, text, source_url, retrieved_at="2026-08-15T00:00:00Z"):
        self.ref = ref
        self.artifact = Artifact(text=text, renderer_id="test",
                                 renderer_version="1", ref=ref)
        self.source_url = source_url
        self.retrieved_at = retrieved_at


class FakeVerifyStore:
    def __init__(self, pages):
        self.pages = pages  # {url: text}
        self._docs = {}
        self.resolve_calls = []

    def resolve(self, url, route, cookies=None, label=""):
        self.resolve_calls.append(url)
        if url not in self.pages:
            raise RuntimeError("no fake page for " + url)
        doc = FakeDoc(ref=url, text=self.pages[url], source_url=url)
        self._docs[doc.ref] = doc
        return doc

    def artifact(self, ref):
        return self._docs[ref].artifact


class VerifyPageTest(unittest.TestCase):
    def test_real_label_text_gate_passes_and_is_reported(self):
        text = 'Степен: ОКС "Бакалавър" по Софтуерни системи.'
        store = FakeVerifyStore({"https://x/p": text})
        doc, verified = verify_page(store, "https://x/p", "Софтуерни системи")
        self.assertIn("degree", verified)
        self.assertEqual(verified["degree"]["source_url"], "https://x/p")
        self.assertEqual(verified["degree"]["retrieved_at"],
                         "2026-08-15T00:00:00Z")
        self.assertEqual(verified["degree"]["artifact_ref"], "https://x/p")

    def test_page_with_no_label_text_verifies_to_nothing(self):
        store = FakeVerifyStore({"https://x/p": "This page has no labels at all."})
        doc, verified = verify_page(store, "https://x/p", "Нещо")
        self.assertEqual(verified, {})

    def test_title_language_rule_fires_via_the_row_name(self):
        # cascade.py defines tier G as harvest_labels PLUS the
        # title-language rule (language_from_name) -- verify_page must
        # exercise both, not just the label library. language_from_name's
        # value comes from the NAME argument, but the gate still requires
        # the artifact TEXT to literally print the same phrase (a page
        # heading showing the full title, e.g.) -- a name implying English
        # instruction that the page text never actually states must NOT
        # pass (that would be exactly the fabrication ADR-0002 forbids).
        row_name = "Информатика (с частично обучение на английски език)"
        text = ("Учебен план за студенти от специалност Информатика "
               "(с частично обучение на английски език).")
        store = FakeVerifyStore({"https://x/p": text})
        doc, verified = verify_page(store, "https://x/p", row_name)
        self.assertIn("language", verified)
        self.assertEqual(verified["language"]["method"], "title-language")

    def test_title_language_rule_does_not_fabricate_when_page_text_lacks_it(self):
        row_name = "Информатика (с частично обучение на английски език)"
        text = "Учебен план за студенти от специалност Информатика."
        store = FakeVerifyStore({"https://x/p": text})
        doc, verified = verify_page(store, "https://x/p", row_name)
        self.assertNotIn("language", verified)


class ProposeOnboardingTest(unittest.TestCase):
    def test_no_candidates_skips_the_adapter_and_queues_reason(self):
        row = make_row(1, "AAA", "Специалност А")
        proposals, cost = propose_onboarding(
            "X", [row], [], FakeAdapter({}), FakeVerifyStore({}))
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertIsNone(p.proposed_url)
        self.assertFalse(p.assignment_verified)
        self.assertIn("no candidate pages", p.match_reasoning)
        self.assertEqual(cost, 0.0)

    def test_assignment_verified_is_always_false_even_on_a_confident_match(self):
        row = make_row(1, "40600931", "Софтуерни системи и технологии")
        text = 'Степен: ОКС "Бакалавър" по Софтуерни системи и технологии.'
        adapter = FakeAdapter({
            "X:1": {"url": "https://x/cs", "reasoning": "exact name match"},
        })
        store = FakeVerifyStore({"https://x/cs": text})
        proposals, _cost = propose_onboarding(
            "X", [row], [("https://x/cs", "CS")], adapter, store,
            tag_prefix="X:")
        p = proposals[0]
        self.assertEqual(p.proposed_url, "https://x/cs")
        self.assertFalse(p.assignment_verified,
                         "row<->page assignment must NEVER be marked verified")
        self.assertGreater(p.field_pass_count, 0)
        self.assertIn("degree", p.gate_verified_fields)

    def test_assignment_verified_has_no_way_to_be_set_true(self):
        # Structural check: assignment_verified is a property, not a
        # constructor field -- there is no argument that could flip it.
        p = ProposedProgram(1, "A", "Спец А", "https://x/p", "match", {}, 0)
        self.assertFalse(p.assignment_verified)
        with self.assertRaises(TypeError):
            ProposedProgram(1, "A", "Спец А", "https://x/p", True,
                            "match", {}, 0, None,
                            "extra")  # extra positional -> TypeError

    def test_model_returning_null_url_is_respected(self):
        row = make_row(1, "AAA", "Специалност А")
        adapter = FakeAdapter({"X:1": {"url": None, "reasoning": "no confident match"}})
        proposals, _cost = propose_onboarding(
            "X", [row], [("https://x/other", "Other")], adapter,
            FakeVerifyStore({}), tag_prefix="X:")
        self.assertIsNone(proposals[0].proposed_url)

    def test_a_url_outside_the_candidate_list_is_ignored_and_never_fetched(self):
        # Defense-in-depth: even if the schema enum somehow failed to
        # constrain the model, propose_onboarding must not let an
        # off-list URL trigger a real fetch.
        row = make_row(1, "AAA", "Специалност А")
        adapter = FakeAdapter({
            "X:1": {"url": "https://evil.example/not-a-candidate",
                   "reasoning": "hallucinated"},
        })
        store = FakeVerifyStore({"https://evil.example/not-a-candidate": "text"})
        proposals, _cost = propose_onboarding(
            "X", [row], [("https://x/real", "Real")], adapter, store,
            tag_prefix="X:")
        self.assertIsNone(proposals[0].proposed_url)
        self.assertIn("outside the candidate list", proposals[0].match_reasoning)
        self.assertEqual(store.resolve_calls, [],
                         "an off-list URL must never be fetched")

    def test_adapter_error_is_caught_and_recorded_not_raised(self):
        row = make_row(1, "AAA", "Специалност А")
        def boom(prompt, schema, model, tag):
            raise RuntimeError("CLI timeout")
        adapter = FakeAdapter({})
        adapter.call = boom
        proposals, _cost = propose_onboarding(
            "X", [row], [("https://x/a", "A")], adapter, FakeVerifyStore({}),
            tag_prefix="X:")
        self.assertIsNone(proposals[0].proposed_url)
        self.assertIn("adapter error", proposals[0].match_reasoning)

    def test_adapter_error_is_distinguishable_from_a_genuine_decline(self):
        # Regression: found live on UniRuse 2026-08-15 -- a 180s adapter
        # timeout was recorded byte-identical in SHAPE to a genuine "no
        # confident match" decline, both just free-text match_reasoning.
        # adapter_error is the field a human/summarizer should check to
        # tell "worth a retry" apart from "the model looked and declined".
        row_timeout = make_row(1, "AAA", "Тайм-аут ред")
        row_decline = make_row(2, "BBB", "Специалност Б")

        def flaky(prompt, schema, model, tag):
            if tag == "X:1":
                raise TimeoutError("adapter timed out after 180s")
            return {"url": None, "reasoning": "no confident match"}, {}

        adapter = FakeAdapter({})
        adapter.call = flaky
        proposals, _cost = propose_onboarding(
            "X", [row_timeout, row_decline], [("https://x/a", "A")],
            adapter, FakeVerifyStore({}), tag_prefix="X:")

        timeout_proposal, decline_proposal = proposals
        self.assertIsNone(timeout_proposal.proposed_url)
        self.assertIsNotNone(timeout_proposal.adapter_error)
        self.assertIn("180s", timeout_proposal.adapter_error)

        self.assertIsNone(decline_proposal.proposed_url)
        self.assertIsNone(decline_proposal.adapter_error,
                         "a genuine decline must not look like an error")

    def test_verify_failure_on_the_proposed_url_falls_back_to_no_match(self):
        row = make_row(1, "AAA", "Специалност А")
        adapter = FakeAdapter({
            "X:1": {"url": "https://x/broken", "reasoning": "looked right"},
        })
        proposals, _cost = propose_onboarding(
            "X", [row], [("https://x/broken", "Broken")], adapter,
            FakeVerifyStore({}),  # page not in store -> resolve() raises
            tag_prefix="X:")
        self.assertIsNone(proposals[0].proposed_url)
        self.assertIn("verify failed", proposals[0].match_reasoning)

    def test_cost_is_summed_across_calls(self):
        row1 = make_row(1, "AAA", "Спец А")
        row2 = make_row(2, "BBB", "Спец Б")
        adapter = FakeAdapter({
            "X:1": lambda prompt: {"url": None, "reasoning": "no match"},
            "X:2": lambda prompt: {"url": None, "reasoning": "no match"},
        })
        # FakeAdapter always reports cost_usd=0.0; patch usage via a
        # wrapping fake that reports a fixed nonzero cost per call.
        real_call = adapter.call
        def call_with_cost(prompt, schema, model, tag):
            structured, usage = real_call(prompt, schema, model, tag)
            return structured, dict(usage, cost_usd=0.05)
        adapter.call = call_with_cost
        _proposals, cost = propose_onboarding(
            "X", [row1, row2], [("https://x/a", "A")], adapter,
            FakeVerifyStore({}), tag_prefix="X:")
        self.assertAlmostEqual(cost, 0.10)


class ValidateAsDraftConfigTest(unittest.TestCase):
    def test_no_proposed_urls_returns_none_none(self):
        p = ProposedProgram(1, "A", "Спец А", None, "no match", {}, 0)
        valid, error = validate_as_draft_config("X", [p])
        self.assertIsNone(valid)
        self.assertIsNone(error)

    def test_a_valid_proposed_url_parses_clean(self):
        p = ProposedProgram(1, "A", "Спец А", "https://x/p", "match", {}, 0)
        valid, error = validate_as_draft_config("X", [p])
        self.assertTrue(valid)
        self.assertIsNone(error)


class WriteProposalTest(unittest.TestCase):
    def test_writes_readable_json_with_the_unverified_note(self):
        p = ProposedProgram(1, "A", "Спец А", "https://x/p", "match",
                            {"degree": {"value": "v"}}, 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_proposal(tmp, "X", [p])
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(data["uni_id"], "X")
        self.assertFalse(data["proposals"][0]["assignment_verified"])
        self.assertIn("UNVERIFIED", data["note"])
        self.assertIsNone(data["proposals"][0]["adapter_error"])

    def test_adapter_error_round_trips_through_the_written_json(self):
        p = ProposedProgram(1, "A", "Спец А", None, "adapter error: boom",
                            {}, 0, adapter_error="boom")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_proposal(tmp, "X", [p])
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(data["proposals"][0]["adapter_error"], "boom")


class FakeOnboardAdapter:
    """Always proposes no match -- run_onboarding tests only care about
    the config<->registry skip logic, not the proposal content."""

    def call(self, prompt, schema, model, tag):
        return {"url": None, "reasoning": "n/a"}, {"cost_usd": 0.0}


class RunOnboardingTest(unittest.TestCase):
    """Regression coverage for the config-loading bug found in review:
    run_onboarding used to load_configs_dir() the WHOLE directory, so an
    unrelated broken sibling config made it treat the target uni as
    unconfigured (silently re-proposing rows it already covers)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.configs_dir = self.root / "configs"
        self.exports_dir = self.root / "exports"
        self.out_dir = self.root / "out"
        self.configs_dir.mkdir()
        self.exports_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_export(self, uni_id, rows):
        (self.exports_dir / "{0}.json".format(uni_id)).write_text(
            json.dumps({
                "uni_id": uni_id, "rsvu_uni_id": 1, "rsvu_uni_name": "",
                "captured_at": "2026-08-15T00:00:00Z", "source": "test",
                "rows": rows,
            }), encoding="utf-8")

    def test_covered_row_is_skipped_even_with_a_broken_sibling_config(self):
        (self.configs_dir / "TARGET.json").write_text(json.dumps({
            "uni_id": "TARGET", "sources": {}, "programs": [
                {"id": "p1", "name": "P1", "page": "https://x/p1",
                 "rsvu_code": "AAA"},
            ],
        }), encoding="utf-8")
        (self.configs_dir / "ZZZ_BROKEN.json").write_text(
            "{not valid json", encoding="utf-8")
        self._write_export("TARGET", [
            {"id": 1, "code": "AAA", "name": "Спец А", "major_id": 1,
             "major_name": "M", "degree_code": 3, "degree_name": "Бакалавър"},
            {"id": 2, "code": "BBB", "name": "Спец Б", "major_id": 1,
             "major_name": "M", "degree_code": 3, "degree_name": "Бакалавър"},
        ])
        report = run_onboarding(
            "TARGET", [], FakeOnboardAdapter(),
            configs_dir=self.configs_dir, out_dir=self.out_dir,
            registry_exports_dir=self.exports_dir)
        proposed_ids = {p.row_id for p in report.proposals}
        self.assertNotIn(1, proposed_ids,
                         "row AAA is config-covered and must be skipped")
        self.assertIn(2, proposed_ids)

    def test_no_config_file_at_all_proposes_for_every_row(self):
        self._write_export("FRESH", [
            {"id": 1, "code": "AAA", "name": "Спец А", "major_id": 1,
             "major_name": "M", "degree_code": 3, "degree_name": "Бакалавър"},
        ])
        report = run_onboarding(
            "FRESH", [], FakeOnboardAdapter(),
            configs_dir=self.configs_dir, out_dir=self.out_dir,
            registry_exports_dir=self.exports_dir)
        self.assertEqual({p.row_id for p in report.proposals}, {1})

    def test_target_uni_s_own_malformed_config_still_raises(self):
        (self.configs_dir / "TARGET.json").write_text(
            "{not valid json", encoding="utf-8")
        self._write_export("TARGET", [
            {"id": 1, "code": "AAA", "name": "Спец А", "major_id": 1,
             "major_name": "M", "degree_code": 3, "degree_name": "Бакалавър"},
        ])
        with self.assertRaises(Exception):
            run_onboarding(
                "TARGET", [], FakeOnboardAdapter(),
                configs_dir=self.configs_dir, out_dir=self.out_dir,
                registry_exports_dir=self.exports_dir)

    def test_max_rows_caps_the_worklist(self):
        self._write_export("FRESH", [
            {"id": i, "code": "C{0}".format(i), "name": "Спец {0}".format(i),
             "major_id": 1, "major_name": "M", "degree_code": 3,
             "degree_name": "Бакалавър"}
            for i in range(1, 6)
        ])
        report = run_onboarding(
            "FRESH", [], FakeOnboardAdapter(),
            configs_dir=self.configs_dir, out_dir=self.out_dir,
            registry_exports_dir=self.exports_dir, max_rows=2)
        self.assertEqual(len(report.proposals), 2)


if __name__ == "__main__":
    unittest.main()
