"""crawler.onboarding suite (ticket 06, reshaped by ADR-0006) -- zero network.

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
    def test_schema_restricts_urls_to_the_candidate_list(self):
        schema = build_schema(["https://x/a", "https://x/b"])
        item = schema["properties"]["programs"]["items"]
        self.assertEqual(item["properties"]["url"]["enum"],
                         ["https://x/a", "https://x/b"])

    def test_prompt_includes_uni_and_candidates(self):
        prompt = build_prompt("X", [("https://x/a", "CS Programme")])
        self.assertIn("X", prompt)
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

    def test_title_language_rule_fires_via_the_proposed_name(self):
        # cascade.py defines tier G as harvest_labels PLUS the
        # title-language rule (language_from_name) -- verify_page must
        # exercise both, not just the label library. language_from_name's
        # value comes from the NAME argument, but the gate still requires
        # the artifact TEXT to literally print the same phrase (a page
        # heading showing the full title, e.g.) -- a name implying English
        # instruction that the page text never actually states must NOT
        # pass (that would be exactly the fabrication ADR-0002 forbids).
        name = "Информатика (с частично обучение на английски език)"
        text = ("Учебен план за студенти от специалност Информатика "
               "(с частично обучение на английски език).")
        store = FakeVerifyStore({"https://x/p": text})
        doc, verified = verify_page(store, "https://x/p", name)
        self.assertIn("language", verified)
        self.assertEqual(verified["language"]["method"], "title-language")

    def test_title_language_rule_does_not_fabricate_when_page_text_lacks_it(self):
        name = "Информатика (с частично обучение на английски език)"
        text = "Учебен план за студенти от специалност Информатика."
        store = FakeVerifyStore({"https://x/p": text})
        doc, verified = verify_page(store, "https://x/p", name)
        self.assertNotIn("language", verified)


class ProposeOnboardingTest(unittest.TestCase):
    def test_no_candidates_skips_the_adapter_entirely(self):
        proposals, cost = propose_onboarding(
            "X", [], FakeAdapter({}), FakeVerifyStore({}))
        self.assertEqual(proposals, [])
        self.assertEqual(cost, 0.0)

    def test_assignment_verified_is_always_false_even_on_a_confident_match(self):
        text = 'Степен: ОКС "Бакалавър" по Софтуерни системи и технологии.'
        adapter = FakeAdapter({
            "X:survey": {"programs": [
                {"url": "https://x/cs",
                 "name": "Софтуерни системи и технологии",
                 "reasoning": "program page"}]},
        })
        store = FakeVerifyStore({"https://x/cs": text})
        proposals, _cost = propose_onboarding(
            "X", [("https://x/cs", "CS")], adapter, store,
            tag_prefix="X:")
        p = proposals[0]
        self.assertEqual(p.proposed_url, "https://x/cs")
        self.assertFalse(p.assignment_verified,
                         "page-is-a-program judgment must NEVER be verified")
        self.assertGreater(p.field_pass_count, 0)
        self.assertIn("degree", p.gate_verified_fields)

    def test_assignment_verified_has_no_way_to_be_set_true(self):
        # Structural check: assignment_verified is a property, not a
        # constructor field -- there is no argument that could flip it.
        p = ProposedProgram("Спец А", "https://x/p", "match", {}, 0)
        self.assertFalse(p.assignment_verified)
        with self.assertRaises(TypeError):
            ProposedProgram("Спец А", "https://x/p", True, "match", {}, 0,
                            None, "extra")  # extra positional -> TypeError

    def test_model_selecting_nothing_is_respected(self):
        adapter = FakeAdapter({"X:survey": {"programs": []}})
        proposals, _cost = propose_onboarding(
            "X", [("https://x/other", "Other")], adapter,
            FakeVerifyStore({}), tag_prefix="X:")
        self.assertEqual(proposals, [])

    def test_a_url_outside_the_candidate_list_is_ignored_and_never_fetched(self):
        # Defense-in-depth: even if the schema enum somehow failed to
        # constrain the model, propose_onboarding must not let an
        # off-list URL trigger a real fetch.
        adapter = FakeAdapter({
            "X:survey": {"programs": [
                {"url": "https://evil.example/not-a-candidate",
                 "name": "Evil", "reasoning": "hallucinated"}]},
        })
        store = FakeVerifyStore({"https://evil.example/not-a-candidate": "text"})
        proposals, _cost = propose_onboarding(
            "X", [("https://x/real", "Real")], adapter, store,
            tag_prefix="X:")
        self.assertIsNone(proposals[0].proposed_url)
        self.assertIn("outside the candidate list", proposals[0].match_reasoning)
        self.assertEqual(store.resolve_calls, [],
                         "an off-list URL must never be fetched")

    def test_adapter_error_is_caught_and_recorded_not_raised(self):
        def boom(prompt, schema, model, tag):
            raise RuntimeError("CLI timeout")
        adapter = FakeAdapter({})
        adapter.call = boom
        proposals, _cost = propose_onboarding(
            "X", [("https://x/a", "A")], adapter, FakeVerifyStore({}),
            tag_prefix="X:")
        self.assertEqual(len(proposals), 1)
        self.assertIsNone(proposals[0].proposed_url)
        self.assertIn("adapter error", proposals[0].match_reasoning)
        self.assertIsNotNone(proposals[0].adapter_error,
                             "a transport failure must be programmatically "
                             "distinguishable from a genuine decline")

    def test_verify_failure_on_a_selected_url_falls_back_to_no_match(self):
        adapter = FakeAdapter({
            "X:survey": {"programs": [
                {"url": "https://x/broken", "name": "Broken",
                 "reasoning": "looked right"}]},
        })
        proposals, _cost = propose_onboarding(
            "X", [("https://x/broken", "Broken")], adapter,
            FakeVerifyStore({}),  # page not in store -> resolve() raises
            tag_prefix="X:")
        self.assertIsNone(proposals[0].proposed_url)
        self.assertIn("verify failed", proposals[0].match_reasoning)

    def test_max_pages_caps_how_many_selections_are_verified(self):
        adapter = FakeAdapter({
            "X:survey": {"programs": [
                {"url": "https://x/p{0}".format(i),
                 "name": "P{0}".format(i), "reasoning": "r"}
                for i in range(1, 6)]},
        })
        store = FakeVerifyStore({"https://x/p{0}".format(i): "no labels"
                                 for i in range(1, 6)})
        proposals, _cost = propose_onboarding(
            "X", [("https://x/p{0}".format(i), "P") for i in range(1, 6)],
            adapter, store, tag_prefix="X:", max_pages=2)
        self.assertEqual(len(proposals), 2)

    def test_cost_comes_from_the_single_survey_call(self):
        adapter = FakeAdapter({"X:survey": {"programs": []}})
        real_call = adapter.call
        def call_with_cost(prompt, schema, model, tag):
            structured, usage = real_call(prompt, schema, model, tag)
            return structured, dict(usage, cost_usd=0.05)
        adapter.call = call_with_cost
        _proposals, cost = propose_onboarding(
            "X", [("https://x/a", "A")], adapter,
            FakeVerifyStore({}), tag_prefix="X:")
        self.assertAlmostEqual(cost, 0.05)


class ValidateAsDraftConfigTest(unittest.TestCase):
    def test_no_proposed_urls_returns_none_none(self):
        p = ProposedProgram("Спец А", None, "no match", {}, 0)
        valid, error = validate_as_draft_config("X", [p])
        self.assertIsNone(valid)
        self.assertIsNone(error)

    def test_a_valid_proposed_url_parses_clean(self):
        p = ProposedProgram("Спец А", "https://x/p", "match", {}, 0)
        valid, error = validate_as_draft_config("X", [p])
        self.assertTrue(valid)
        self.assertIsNone(error)


class WriteProposalTest(unittest.TestCase):
    def test_writes_readable_json_with_the_unverified_note(self):
        p = ProposedProgram("Спец А", "https://x/p", "match",
                            {"degree": {"value": "v"}}, 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_proposal(tmp, "X", [p])
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(data["uni_id"], "X")
        self.assertFalse(data["proposals"][0]["assignment_verified"])
        self.assertIn("UNVERIFIED", data["note"])
        self.assertIsNone(data["proposals"][0]["adapter_error"])

    def test_adapter_error_round_trips_through_the_written_json(self):
        p = ProposedProgram("Спец А", None, "adapter error: boom",
                            {}, 0, adapter_error="boom")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_proposal(tmp, "X", [p])
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(data["proposals"][0]["adapter_error"], "boom")


class FakeSurveyAdapter:
    """Selects every candidate as a program page, named by link text."""

    def call(self, prompt, schema, model, tag):
        urls = schema["properties"]["programs"]["items"][
            "properties"]["url"]["enum"]
        return {"programs": [
            {"url": u, "name": "Prog " + u.rsplit("/", 1)[-1],
             "reasoning": "n/a"} for u in urls
        ]}, {"cost_usd": 0.0}


class RunOnboardingTest(unittest.TestCase):
    """Regression coverage for the config-loading bug found in review:
    run_onboarding used to load_configs_dir() the WHOLE directory, so an
    unrelated broken sibling config made it treat the target uni as
    unconfigured (silently re-proposing pages it already covers)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.configs_dir = self.root / "configs"
        self.out_dir = self.root / "out"
        self.configs_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_configured_page_is_skipped_even_with_a_broken_sibling_config(self):
        (self.configs_dir / "TARGET.json").write_text(json.dumps({
            "uni_id": "TARGET", "sources": {}, "programs": [
                {"id": "p1", "name": "P1",
                 "page": "https://uni.example/p1"},
            ],
        }), encoding="utf-8")
        (self.configs_dir / "ZZZ_BROKEN.json").write_text(
            "{not valid json", encoding="utf-8")
        seed_pages = {
            "https://uni.example/seed":
                b'<a href="/p1">P1</a><a href="/p2">P2</a>',
        }
        report = run_onboarding_with_fakes(
            "TARGET", ["https://uni.example/seed"], seed_pages,
            configs_dir=self.configs_dir, out_dir=self.out_dir)
        urls = {p.proposed_url for p in report.proposals}
        self.assertNotIn("https://uni.example/p1", urls,
                         "an already-configured page must be skipped")
        self.assertIn("https://uni.example/p2", urls)

    def test_no_config_file_at_all_proposes_for_every_selected_page(self):
        seed_pages = {
            "https://uni.example/seed": b'<a href="/p1">P1</a>',
        }
        report = run_onboarding_with_fakes(
            "FRESH", ["https://uni.example/seed"], seed_pages,
            configs_dir=self.configs_dir, out_dir=self.out_dir)
        self.assertEqual({p.proposed_url for p in report.proposals},
                         {"https://uni.example/p1"})

    def test_target_uni_s_own_malformed_config_still_raises(self):
        (self.configs_dir / "TARGET.json").write_text(
            "{not valid json", encoding="utf-8")
        with self.assertRaises(Exception):
            run_onboarding_with_fakes(
                "TARGET", ["https://uni.example/seed"], {},
                configs_dir=self.configs_dir, out_dir=self.out_dir)


def run_onboarding_with_fakes(uni_id, seeds, seed_pages, *, configs_dir,
                              out_dir):
    """run_onboarding with the network swapped out: the link fetcher and
    the verify store are both fakes (verify always fails, which is fine —
    these tests care about candidate selection, not verification)."""
    import crawler.onboarding as onboarding_mod

    fetcher = FakeLinkFetcher(seed_pages)
    store = FakeVerifyStore({
        "https://uni.example/p1": "plain page text",
        "https://uni.example/p2": "plain page text",
    })
    real = onboarding_mod.build_fetcher_and_store
    onboarding_mod.build_fetcher_and_store = (
        lambda *a, **k: (fetcher, store))
    try:
        return run_onboarding(uni_id, seeds, FakeSurveyAdapter(),
                              configs_dir=configs_dir, out_dir=out_dir)
    finally:
        onboarding_mod.build_fetcher_and_store = real


if __name__ == "__main__":
    unittest.main()
