"""crawler.adjudication suite (ticket 05) -- zero network.

Fake ArtifactStore/docs, real crawler.provenance.gate() (never a local
containment check, per ADR-0002) -- same discipline as test_llm_tail.py.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.adjudication import (  # noqa: E402
    AdjudicationStatus,
    adjudicate,
    covered_codes,
    propose_enrolling,
    read_repair_queue,
    read_resolutions,
    resolve_repair_entry,
    run_adjudication,
    unresolve,
    write_repair_queue,
    RepairEntry,
    Resolution,
)
from crawler.cascade import TextSource  # noqa: E402
from crawler.provenance import Artifact  # noqa: E402
from crawler.registry import RegistryExport, RegistryRow  # noqa: E402


def make_row(id, code, name, major_id=1, major_name="Направление",
            degree_code=3, degree_name="Бакалавър"):
    return RegistryRow(id=id, code=code, name=name, major_id=major_id,
                       major_name=major_name, degree_code=degree_code,
                       degree_name=degree_name)


class FakeDoc:
    def __init__(self, source_url, retrieved_at="2026-08-15T00:00:00Z"):
        self.source_url = source_url
        self.retrieved_at = retrieved_at


class FakeStore:
    """Mirrors the two accessors adjudication.py actually calls:
    store.artifact(ref) and store.doc(ref)."""

    def __init__(self, artifacts, docs):
        self._artifacts = artifacts
        self._docs = docs

    def artifact(self, ref):
        return self._artifacts[ref]

    def doc(self, ref):
        return self._docs[ref]


def make_program(id, name, rsvu_code=None):
    return SimpleNamespace(id=id, name=name, rsvu_code=rsvu_code)


def make_site(programs, rsvu_id=None):
    return SimpleNamespace(programs=programs, rsvu_id=rsvu_id)


class CoveredCodesTest(unittest.TestCase):
    def test_only_programs_with_a_code_count(self):
        site = make_site([
            make_program("p1", "P1", rsvu_code="AAA"),
            make_program("p2", "P2", rsvu_code=None),
        ])
        self.assertEqual(covered_codes(site), {"AAA"})


class ProposeEnrollingTest(unittest.TestCase):
    def test_pass_when_name_found_verbatim_in_a_fetched_page(self):
        text = ("Нашето училище предлага Специалност Софтуерни системи и "
               "технологии като бакалавърска програма от следващата година.")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/programs")})
        row = make_row(1, "40600931", "Софтуерни системи и технологии")
        resolution = propose_enrolling(row, docs, store)
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.status, AdjudicationStatus.ENROLLING)
        self.assertIn("Софтуерни системи и технологии", resolution.segments[0])
        self.assertEqual(resolution.source_url, "https://x.example/programs")

    def test_none_when_name_absent_from_every_fetched_page(self):
        text = "This page says nothing about any specialty."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/other")})
        row = make_row(1, "40600931", "Софтуерни системи и технологии")
        self.assertIsNone(propose_enrolling(row, docs, store))

    def test_name_split_across_a_tag_boundary_still_matches(self):
        # bs4's get_text("\n") (crawler/render.py's HTML renderer) joins
        # every text node with a newline -- a name split by inline markup
        # (<span>/<strong> inside a heading) lands in the canonical text
        # with an embedded "\n" mid-name. cascade.norm() collapses that
        # back to a single space, same as harvest_labels/anchor_probe.
        text = "Предлагаме\nСпециалност\nСофтуерни системи\nи технологии\nкато програма."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/programs")})
        row = make_row(1, "40600931", "Софтуерни системи и технологии")
        resolution = propose_enrolling(row, docs, store)
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.status, AdjudicationStatus.ENROLLING)

    def test_table_sources_are_skipped_not_crashed_on(self):
        from crawler.cascade import TableSource
        docs = {"t1": TableSource(ref="t1", tables=())}
        store = FakeStore({}, {})
        row = make_row(1, "X", "Каквото и да е")
        self.assertIsNone(propose_enrolling(row, docs, store))

    def test_name_does_not_match_as_substring_of_a_longer_word(self):
        # Regression: found live on UniRuse -- "Маркетинг" (a real
        # registry row name) matched inside "маркетингови проучвания"
        # ("marketing research", an unrelated adjective) before
        # propose_enrolling word-bounded its search pattern. A row's name
        # appearing only as a fragment of a longer word is never a real
        # mention of that row.
        text = ("Завършилите могат да извършват маркетингови проучвания "
               "и стратегически анализи.")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/programs")})
        row = make_row(1, "40600931", "Маркетинг")
        self.assertIsNone(propose_enrolling(row, docs, store))

    def test_name_still_matches_as_a_standalone_word(self):
        text = "Специалност Маркетинг се обучава в редовна форма."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/programs")})
        row = make_row(1, "40600931", "Маркетинг")
        resolution = propose_enrolling(row, docs, store)
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.status, AdjudicationStatus.ENROLLING)


class DeclarationMarkerProximityTest(unittest.TestCase):
    """Regression: found live on UniRuse 2026-08-15. adjudicate() auto-
    resolved 10 rows as "enrolling" via name-match; only 3 were real. The
    other 7 matched a word-bounded but otherwise incidental mention of the
    row's name -- a different faculty's own name, a list of subjects a
    DIFFERENT program teaches, an admission-exam subject list. Every real
    match was immediately preceded by "Специалност" (the site's own
    heading marker); every false positive was not. These fixtures use the
    real segment shapes found that day, not synthetic ones."""

    def test_name_inside_a_different_faculty_name_does_not_resolve(self):
        # Real segment: "...Факултет Електротехника, електроника и
        # автоматика Транспортен факултет..." -- "Електроника" matched
        # inside the FACULTY's own name, not any program declaration.
        text = ("Машинно-технологичен факултет Факултет Електротехника, "
               "електроника и автоматика Транспортен факултет Факултет "
               "Бизнес и мениджмънт")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        row = make_row(1, "X", "Електроника")
        self.assertIsNone(propose_enrolling(row, docs, store))

    def test_name_inside_a_different_programs_subject_list_does_not_resolve(self):
        # Real segment: "...основни напра­вления: икономика, право,
        # маркетинг, финанси и счетоводство..." -- a list of subjects
        # TAUGHT by a different program, not a declaration that "Право"
        # or "Маркетинг" is itself offered here.
        text = ("умения по следните основни напра­вления: икономика, "
               "право, маркетинг, финанси и счетоводство, бизнес "
               "планиране и проектиране")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        for name in ("Право", "Маркетинг"):
            row = make_row(1, "X", name)
            self.assertIsNone(propose_enrolling(row, docs, store),
                             "{0!r} should not auto-resolve".format(name))

    def test_name_inside_an_admission_exam_subject_list_does_not_resolve(self):
        # Real segment: "...един избираем изпит по: български език,
        # история на България или биология..." -- an admission-exam
        # subject list, not a program listing.
        text = ("оценка от държавен зрелостен изпит или един избираем "
               "изпит по: български език, история на България или "
               "биология")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        for name in ("История на България", "Български език"):
            row = make_row(1, "X", name)
            self.assertIsNone(propose_enrolling(row, docs, store),
                             "{0!r} should not auto-resolve".format(name))

    def test_real_specialty_heading_still_resolves(self):
        # Real segment: "...Специалност ИНДУСТРИАЛЕН МЕНИДЖМЪНТ - в
        # редовна и задочна форма на обучение..." -- a genuine heading.
        text = ("Професионално направление 5.13. Общо инженерство "
               "Специалност ИНДУСТРИАЛЕН МЕНИДЖМЪНТ - в редовна и "
               "задочна форма на обучение Обучението осигурява")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        row = make_row(1, "X", "Индустриален мениджмънт")
        resolution = propose_enrolling(row, docs, store)
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.status, AdjudicationStatus.ENROLLING)

    def test_social_work_specialty_heading_resolves(self):
        # The third genuine match from the same live data (row 32030).
        text = ("Професионално направление 3.4. Социални дейности "
               "Специалност СОЦИАЛНИ ДЕЙНОСТИ - в редовна и задочна "
               "форма на обучение")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        row = make_row(1, "X", "Социални дейности")
        resolution = propose_enrolling(row, docs, store)
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.status, AdjudicationStatus.ENROLLING)

    def test_both_industrial_management_rows_resolve_off_the_same_heading(self):
        # Rows 3142 and 30901 are distinct registry rows (different
        # degree codes) sharing one name and one real page heading; both
        # must resolve, matching the live result.
        text = ("Професионално направление 5.13. Общо инженерство "
               "Специалност ИНДУСТРИАЛЕН МЕНИДЖМЪНТ - в редовна и "
               "задочна форма на обучение")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        for row_id, degree_code in ((3142, 3), (30901, 4)):
            row = make_row(row_id, "X{0}".format(row_id),
                          "Индустриален мениджмънт", degree_code=degree_code)
            resolution = propose_enrolling(row, docs, store)
            self.assertIsNotNone(
                resolution, "row {0} should resolve".format(row_id))
            self.assertEqual(resolution.row_id, row_id)

    def test_an_english_language_declaration_still_resolves(self):
        # Regression: the first version of this check hardcoded the
        # Bulgarian "Специалност" only, which silently made every
        # English-medium university (AUBG, VUM -- both already in
        # crawler/configs/) unable to auto-resolve anything at all.
        text = ("The Department offers a BA programme in Computer "
               "Science in full-time form.")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        row = make_row(1, "X", "Computer Science")
        resolution = propose_enrolling(row, docs, store)
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.status, AdjudicationStatus.ENROLLING)

    def test_a_site_can_override_the_marker_vocabulary(self):
        text = "Учебно направление Роботика - редовна форма."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        row = make_row(1, "X", "Роботика")
        self.assertIsNone(propose_enrolling(row, docs, store),
                         "not a default marker")
        resolution = propose_enrolling(row, docs, store,
                                       markers=("учебно направление",))
        self.assertIsNotNone(resolution)

    def test_second_occurrence_with_marker_resolves_even_after_a_bare_first_occurrence(self):
        # The exact shape found for "Икономика": a bare, unmarked mention
        # earlier in the text (inside another program's subject list)
        # must not block finding a REAL "Специалност ИКОНОМИКА" heading
        # later in the SAME source.
        text = ("правни науки, икономика, организация и управление на "
               "социалните дейности. Друга специалност: Специалност "
               "ИКОНОМИКА - в редовна и дистанционна форма на обучение")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        row = make_row(1, "X", "Икономика")
        resolution = propose_enrolling(row, docs, store)
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.status, AdjudicationStatus.ENROLLING)


class AdjudicateTest(unittest.TestCase):
    def test_config_covered_rows_never_touch_the_gate_or_queue(self):
        site = make_site([make_program("p1", "P1", rsvu_code="AAA")])
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Специалност А"),))
        report = adjudicate("X", site, registry, {}, FakeStore({}, {}))
        self.assertEqual(report.covered_by_config, (1,))
        self.assertEqual(report.resolved, ())
        self.assertEqual(report.queue, ())
        self.assertEqual(report.coverage, 1.0)

    def test_unmatched_row_with_no_page_evidence_queues_not_falsely_passes(self):
        site = make_site([])
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Специалност А"),))
        report = adjudicate("X", site, registry, {}, FakeStore({}, {}))
        self.assertEqual(report.covered_by_config, ())
        self.assertEqual(report.resolved, ())
        self.assertEqual(len(report.queue), 1)
        self.assertEqual(report.queue[0].row_id, 1)
        self.assertEqual(report.coverage, 0.0)

    def test_unmatched_row_found_on_a_fetched_page_resolves_and_counts(self):
        site = make_site([])
        text = "Предлагаме Специалност Програма А от тази година."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Програма А"),))
        report = adjudicate("X", site, registry, docs, store)
        self.assertEqual(len(report.resolved), 1)
        self.assertEqual(report.queue, ())
        self.assertEqual(report.coverage, 1.0)

    def test_rsvu_id_mismatch_between_config_and_export_raises(self):
        site = make_site([], rsvu_id=999)
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=125, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Специалност А"),))
        with self.assertRaises(ValueError):
            adjudicate("X", site, registry, {}, FakeStore({}, {}))

    def test_orphan_rsvu_code_matching_no_registry_row_raises(self):
        site = make_site([make_program("p1", "P1", rsvu_code="NOT-A-REAL-CODE")])
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Специалност А"),))
        with self.assertRaises(ValueError):
            adjudicate("X", site, registry, {}, FakeStore({}, {}))

    def test_resolved_by_id_is_treated_as_settled_not_requeued(self):
        site = make_site([])
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Специалност А"),))
        prior = {1: Resolution(row_id=1, status=AdjudicationStatus.NOT_ENROLLING,
                               value="v", segments=("s",), source_url="https://x",
                               retrieved_at="2026-08-15T00:00:00Z",
                               method="human-review")}
        report = adjudicate("X", site, registry, {}, FakeStore({}, {}),
                            resolved_by_id=prior)
        self.assertEqual(report.queue, ())
        self.assertEqual(len(report.resolved), 1)
        self.assertEqual(report.resolved[0].status, AdjudicationStatus.NOT_ENROLLING)
        self.assertEqual(report.coverage, 1.0)

    def test_prior_opened_at_is_preserved_for_a_still_open_row(self):
        site = make_site([])
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Специалност А"),))
        report = adjudicate("X", site, registry, {}, FakeStore({}, {}),
                            prior_opened_at={1: "2026-01-01T00:00:00Z"},
                            now="2026-08-15T00:00:00Z")
        self.assertEqual(report.queue[0].opened_at, "2026-01-01T00:00:00Z")

    def test_real_vum_export_matches_every_configured_rsvu_code(self):
        # Derives the expectation from the LIVING config rather than
        # pinning a count -- this file pinned "4" until VUM gained 11
        # programs (2026-08-17) and the pin went stale. The invariant
        # that matters: every configured rsvu_code matches exactly one
        # registry row, and nothing else is covered.
        from crawler.config import load_configs_dir
        from crawler.registry import load_captured_export
        site = load_configs_dir("crawler/configs")["VUM"]
        registry = load_captured_export("VUM")
        report = adjudicate("VUM", site, registry, {}, FakeStore({}, {}))
        configured = {p.rsvu_code for p in site.programs if p.rsvu_code}
        self.assertEqual(len(report.covered_by_config), len(configured))
        self.assertEqual(len(report.queue),
                         len(registry.rows) - len(configured))


class NearMissCandidateTest(unittest.TestCase):
    """Regression: propose_enrolling only ever reports a PASS; a row whose
    name IS found on a fetched page but fails the gate used to produce a
    repair-queue entry byte-identical to a row found on no page at all --
    both just said "no fetched page mentions this row's name", which was
    true for one and false for the other."""

    def test_candidate_captured_when_name_found_but_gate_rejects(self):
        # The row's name is in the searched TextSource but NOT in the
        # artifact the store hands back for the same ref -- a real gate
        # REJECT_CONTAINMENT, not a contrived one (this is exactly
        # ADR-0002's wrong-artifact failure class, just simulated via
        # FakeStore instead of a real mismatch).
        text = "Предлагаме Специалност Б от тази година."
        mismatched_artifact_text = "Съвсем различен текст, не съдържа нищо."
        artifact = Artifact(text=mismatched_artifact_text, renderer_id="test",
                            renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Специалност Б"),))
        report = adjudicate("X", make_site([]), registry, docs, store)
        self.assertEqual(report.resolved, ())
        self.assertEqual(len(report.queue), 1)
        entry = report.queue[0]
        self.assertIsNotNone(entry.candidate)
        self.assertEqual(entry.candidate["gate_status"], "REJECT_CONTAINMENT")
        self.assertEqual(entry.candidate["artifact_ref"], "page1")
        self.assertEqual(entry.candidate["source_url"], "https://x.example/p")
        self.assertIn("Специалност Б", entry.candidate["segment"])
        self.assertIn("candidate", entry.reason)

    def test_a_marker_less_gate_pass_is_reported_as_a_candidate_not_as_absent(self):
        # Regression for the contract break the declaration-marker check
        # introduced: propose_enrolling now skips a gate-PASSing match
        # that has no marker near it. _find_near_miss used to skip every
        # PASS too, so such a row queued with candidate=None and the
        # reason "no fetched page mentions this row's name" -- flatly
        # false when the name is plainly, verbatim on a fetched page.
        text = ("Факултет Електротехника, електроника и автоматика "
               "Транспортен факултет")
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="page1")
        docs = {"page1": TextSource(ref="page1", text=text)}
        store = FakeStore({"page1": artifact},
                          {"page1": FakeDoc("https://x.example/p")})
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Електроника"),))
        report = adjudicate("X", make_site([]), registry, docs, store)

        self.assertEqual(report.resolved, (), "must not auto-resolve")
        entry = report.queue[0]
        self.assertIsNotNone(
            entry.candidate,
            "the name IS on a fetched page -- reporting nothing lies to "
            "whoever reviews the queue")
        self.assertEqual(entry.candidate["gate_status"], "PASS")
        self.assertFalse(entry.candidate["declaration_marker_near"])
        self.assertIn("електроника", entry.candidate["segment"].lower())
        self.assertIn("not next to any program-declaration marker",
                      entry.reason)
        self.assertNotIn("no fetched page mentions", entry.reason)

    def test_candidate_is_none_when_name_appears_on_no_page(self):
        site = make_site([])
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Специалност А"),))
        report = adjudicate("X", site, registry, {}, FakeStore({}, {}))
        self.assertIsNone(report.queue[0].candidate)
        self.assertNotIn("candidate", report.queue[0].reason)


class RepairQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_then_read_round_trips(self):
        entries = (RepairEntry(row_id=1, row_code="A", row_name="Спец А",
                               reason="r", opened_at="2026-08-15T00:00:00Z"),)
        write_repair_queue(self.out, "X", entries)
        loaded = read_repair_queue(self.out, "X")
        self.assertEqual(loaded, list(entries))

    def test_write_then_read_round_trips_a_candidate(self):
        candidate = {"segment": "s", "artifact_ref": "page1",
                    "source_url": "https://x.example/p",
                    "retrieved_at": "2026-08-15T00:00:00Z",
                    "gate_status": "REJECT_CONTAINMENT", "gate_detail": "d"}
        entries = (RepairEntry(row_id=1, row_code="A", row_name="Спец А",
                               reason="r", opened_at="2026-08-15T00:00:00Z",
                               candidate=candidate),)
        write_repair_queue(self.out, "X", entries)
        loaded = read_repair_queue(self.out, "X")
        self.assertEqual(loaded[0].candidate, candidate)

    def test_repair_entry_is_frozen(self):
        from dataclasses import FrozenInstanceError
        entry = RepairEntry(row_id=1, row_code="A", row_name="Спец А",
                            reason="r", opened_at="2026-08-15T00:00:00Z")
        with self.assertRaises(FrozenInstanceError):
            entry.reason = "mutated"

    def test_missing_queue_reads_as_empty_not_an_error(self):
        self.assertEqual(read_repair_queue(self.out, "NoSuchUni"), [])

    def test_resolve_removes_entry_and_returns_a_resolution_on_gate_pass(self):
        entries = (RepairEntry(row_id=1, row_code="A", row_name="Спец А",
                               reason="r", opened_at="2026-08-15T00:00:00Z"),)
        write_repair_queue(self.out, "X", entries)
        text = "Университетът обяви, че Спец А вече не се предлага."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="news1")
        resolution = resolve_repair_entry(
            self.out, "X", row_id=1, status="not-enrolling",
            value="Спец А вече не се предлага", segment=text,
            artifact=artifact, source_url="https://x.example/news",
            retrieved_at="2026-08-15T00:00:00Z")
        self.assertEqual(resolution.status, AdjudicationStatus.NOT_ENROLLING)
        self.assertEqual(read_repair_queue(self.out, "X"), [])

    def test_resolve_persists_the_resolution_durably(self):
        # Regression: resolve_repair_entry used to only remove the queue
        # entry, with no durable record anywhere -- a later adjudicate()
        # call had no way to know row 1 had already been resolved and
        # would silently reopen it.
        entries = (RepairEntry(row_id=1, row_code="A", row_name="Спец А",
                               reason="r", opened_at="2026-08-15T00:00:00Z"),)
        write_repair_queue(self.out, "X", entries)
        text = "Университетът обяви, че Спец А вече не се предлага."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="news1")
        resolve_repair_entry(
            self.out, "X", row_id=1, status="not-enrolling",
            value="Спец А вече не се предлага", segment=text,
            artifact=artifact, source_url="https://x.example/news",
            retrieved_at="2026-08-15T00:00:00Z")
        stored = read_resolutions(self.out, "X")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].row_id, 1)
        self.assertEqual(stored[0].status, AdjudicationStatus.NOT_ENROLLING)

    def test_a_second_adjudicate_call_does_not_reopen_a_human_resolution(self):
        # End-to-end version of the durability bug: resolve, then re-run
        # adjudicate() the way run_adjudication would (feeding it
        # read_resolutions()'s output) -- row 1 must stay resolved, not
        # come back as a fresh queue entry.
        entries = (RepairEntry(row_id=1, row_code="A", row_name="Спец А",
                               reason="r", opened_at="2026-08-15T00:00:00Z"),)
        write_repair_queue(self.out, "X", entries)
        text = "Университетът обяви, че Спец А вече не се предлага."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="news1")
        resolve_repair_entry(
            self.out, "X", row_id=1, status="not-enrolling",
            value="Спец А вече не се предлага", segment=text,
            artifact=artifact, source_url="https://x.example/news",
            retrieved_at="2026-08-15T00:00:00Z")

        site = make_site([])
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "A", "Спец А"),))
        resolved_by_id = {r.row_id: r for r in read_resolutions(self.out, "X")}
        report = adjudicate("X", site, registry, {}, FakeStore({}, {}),
                            resolved_by_id=resolved_by_id)
        self.assertEqual(report.queue, (), "row 1 must not be reopened")
        self.assertEqual(len(report.resolved), 1)
        self.assertEqual(report.resolved[0].status, AdjudicationStatus.NOT_ENROLLING)

    def test_resolve_rejects_an_invalid_status_before_mutating_anything(self):
        # Regression: resolve_repair_entry used to build the Resolution
        # (and validate `status`) AFTER removing the entry from the open
        # queue -- a typo'd status string raised only after the entry was
        # already gone, leaving it neither open nor resolved.
        entries = (RepairEntry(row_id=1, row_code="A", row_name="Спец А",
                               reason="r", opened_at="2026-08-15T00:00:00Z"),)
        write_repair_queue(self.out, "X", entries)
        artifact = Artifact(text="anything", renderer_id="t", renderer_version="1", ref="r1")
        with self.assertRaises(ValueError):
            resolve_repair_entry(
                self.out, "X", row_id=1, status="not-enrolling-TYPO",
                value="anything", segment="anything", artifact=artifact,
                source_url="https://x.example", retrieved_at="2026-08-15T00:00:00Z")
        self.assertEqual(len(read_repair_queue(self.out, "X")), 1,
                         "the entry must still be open, not vanished")
        self.assertEqual(read_resolutions(self.out, "X"), [])

    def test_resolve_rejects_a_fabricated_segment_and_leaves_queue_untouched(self):
        entries = (RepairEntry(row_id=1, row_code="A", row_name="Спец А",
                               reason="r", opened_at="2026-08-15T00:00:00Z"),)
        write_repair_queue(self.out, "X", entries)
        artifact = Artifact(text="Съвсем различен текст.",
                            renderer_id="test", renderer_version="1", ref="news2")
        with self.assertRaises(ValueError):
            resolve_repair_entry(
                self.out, "X", row_id=1, status="not-enrolling",
                value="Спец А вече не се предлага",
                segment="Спец А вече не се предлага (measured nowhere)",
                artifact=artifact, source_url="https://x.example/news",
                retrieved_at="2026-08-15T00:00:00Z")
        self.assertEqual(len(read_repair_queue(self.out, "X")), 1)

    def test_resolve_unknown_row_id_raises_key_error(self):
        write_repair_queue(self.out, "X", ())
        artifact = Artifact(text="anything", renderer_id="t", renderer_version="1", ref="r1")
        with self.assertRaises(KeyError):
            resolve_repair_entry(
                self.out, "X", row_id=999, status="page-gone",
                value="anything", segment="anything", artifact=artifact,
                source_url="https://x.example", retrieved_at="2026-08-15T00:00:00Z")


class UnresolveTest(unittest.TestCase):
    """Ticket 09: resolve_repair_entry could only ADD a resolution --
    nothing in this module could take one back. A bad auto-resolution
    (ticket 10's false positives, or a human's own mistake) was durably
    permanent; adjudicate() treats any resolved row as settled forever
    (test_a_second_adjudicate_call_does_not_reopen_a_human_resolution
    above), so there was no path back to "open" for a row once resolved
    wrongly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _resolve_row(self, row_id=1):
        entries = (RepairEntry(row_id=row_id, row_code="A", row_name="Спец А",
                               reason="r", opened_at="2026-08-15T00:00:00Z"),)
        write_repair_queue(self.out, "X", entries)
        text = "Университетът обяви, че Спец А вече не се предлага."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="news1")
        return resolve_repair_entry(
            self.out, "X", row_id=row_id, status="not-enrolling",
            value="Спец А вече не се предлага", segment=text,
            artifact=artifact, source_url="https://x.example/news",
            retrieved_at="2026-08-15T00:00:00Z")

    def test_unresolve_removes_the_resolution_and_returns_it(self):
        original = self._resolve_row()
        removed = unresolve(self.out, "X", 1)
        self.assertEqual(removed, original)
        self.assertEqual(read_resolutions(self.out, "X"), [])

    def test_unresolve_unknown_row_id_raises_key_error_before_mutating(self):
        self._resolve_row()
        with self.assertRaises(KeyError):
            unresolve(self.out, "X", 999)
        self.assertEqual(len(read_resolutions(self.out, "X")), 1,
                         "an unresolve call for a different row must not "
                         "touch the real one's resolution")

    def test_unresolve_only_removes_the_named_row(self):
        self._resolve_row(row_id=1)
        self._resolve_row(row_id=2)
        unresolve(self.out, "X", 1)
        remaining = read_resolutions(self.out, "X")
        self.assertEqual([r.row_id for r in remaining], [2])

    def test_row_is_open_again_on_the_next_adjudicate_run(self):
        # Regression for the actual point of this ticket: after
        # unresolve(), a fresh adjudicate() must re-attempt the row, not
        # silently treat it as still settled.
        self._resolve_row(row_id=1)
        unresolve(self.out, "X", 1)
        resolved_by_id = {r.row_id: r for r in read_resolutions(self.out, "X")}
        registry = RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="", rows=(make_row(1, "AAA", "Спец А"),))
        report = adjudicate("X", make_site([]), registry, {}, FakeStore({}, {}),
                            resolved_by_id=resolved_by_id)
        self.assertEqual(report.resolved, ())
        self.assertEqual(len(report.queue), 1)
        self.assertEqual(report.queue[0].row_id, 1)


class VariantOfTest(unittest.TestCase):
    """AdjudicationStatus.VARIANT_OF existed with no field naming WHICH row
    a resolution was a variant of -- Resolution.variant_of_row_id fixes
    that, validated against a real loaded registry row (ADR-0002: no
    exemption for humans) BEFORE any gate call or queue mutation, same
    ordering discipline as the status/gate validation above."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _registry(self):
        return RegistryExport(
            uni_id="X", rsvu_uni_id=1, rsvu_uni_name="", captured_at="",
            source="",
            rows=(make_row(1, "AAA", "Спец А"), make_row(2, "BBB", "Спец Б")))

    def _open_queue(self, row_id=2):
        entries = (RepairEntry(row_id=row_id, row_code="BBB", row_name="Спец Б",
                               reason="r", opened_at="2026-08-15T00:00:00Z"),)
        write_repair_queue(self.out, "X", entries)

    def test_resolves_with_a_real_target_row(self):
        self._open_queue()
        text = "Университетът обяви, че Спец Б е само друг вариант на Спец А."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="news1")
        resolution = resolve_repair_entry(
            self.out, "X", row_id=2, status="variant-of",
            value="Спец Б е само друг вариант на Спец А", segment=text,
            artifact=artifact, source_url="https://x.example/news",
            retrieved_at="2026-08-15T00:00:00Z",
            variant_of_row_id=1, registry=self._registry())
        self.assertEqual(resolution.status, AdjudicationStatus.VARIANT_OF)
        self.assertEqual(resolution.variant_of_row_id, 1)
        self.assertEqual(read_repair_queue(self.out, "X"), [])

    def test_variant_of_row_id_survives_resolutions_round_trip(self):
        self._open_queue()
        text = "Университетът обяви, че Спец Б е само друг вариант на Спец А."
        artifact = Artifact(text=text, renderer_id="test", renderer_version="1", ref="news1")
        resolve_repair_entry(
            self.out, "X", row_id=2, status="variant-of",
            value="Спец Б е само друг вариант на Спец А", segment=text,
            artifact=artifact, source_url="https://x.example/news",
            retrieved_at="2026-08-15T00:00:00Z",
            variant_of_row_id=1, registry=self._registry())
        stored = read_resolutions(self.out, "X")
        self.assertEqual(stored[0].variant_of_row_id, 1)

    def test_missing_target_id_raises_before_mutating_anything(self):
        self._open_queue()
        artifact = Artifact(text="anything", renderer_id="t", renderer_version="1", ref="r1")
        with self.assertRaises(ValueError):
            resolve_repair_entry(
                self.out, "X", row_id=2, status="variant-of",
                value="anything", segment="anything", artifact=artifact,
                source_url="https://x.example", retrieved_at="2026-08-15T00:00:00Z",
                registry=self._registry())
        self.assertEqual(len(read_repair_queue(self.out, "X")), 1,
                         "the entry must still be open, not vanished")
        self.assertEqual(read_resolutions(self.out, "X"), [])

    def test_target_not_in_registry_raises_before_mutating_anything(self):
        self._open_queue()
        artifact = Artifact(text="anything", renderer_id="t", renderer_version="1", ref="r1")
        with self.assertRaises(ValueError):
            resolve_repair_entry(
                self.out, "X", row_id=2, status="variant-of",
                value="anything", segment="anything", artifact=artifact,
                source_url="https://x.example", retrieved_at="2026-08-15T00:00:00Z",
                variant_of_row_id=999, registry=self._registry())
        self.assertEqual(len(read_repair_queue(self.out, "X")), 1)
        self.assertEqual(read_resolutions(self.out, "X"), [])

    def test_missing_registry_raises(self):
        self._open_queue()
        artifact = Artifact(text="anything", renderer_id="t", renderer_version="1", ref="r1")
        with self.assertRaises(ValueError):
            resolve_repair_entry(
                self.out, "X", row_id=2, status="variant-of",
                value="anything", segment="anything", artifact=artifact,
                source_url="https://x.example", retrieved_at="2026-08-15T00:00:00Z",
                variant_of_row_id=1)
        self.assertEqual(len(read_repair_queue(self.out, "X")), 1)

    def test_self_reference_raises(self):
        self._open_queue()
        artifact = Artifact(text="anything", renderer_id="t", renderer_version="1", ref="r1")
        with self.assertRaises(ValueError):
            resolve_repair_entry(
                self.out, "X", row_id=2, status="variant-of",
                value="anything", segment="anything", artifact=artifact,
                source_url="https://x.example", retrieved_at="2026-08-15T00:00:00Z",
                variant_of_row_id=2, registry=self._registry())

    def test_target_id_rejected_for_a_non_variant_status(self):
        self._open_queue()
        artifact = Artifact(text="anything", renderer_id="t", renderer_version="1", ref="r1")
        with self.assertRaises(ValueError):
            resolve_repair_entry(
                self.out, "X", row_id=2, status="page-gone",
                value="anything", segment="anything", artifact=artifact,
                source_url="https://x.example", retrieved_at="2026-08-15T00:00:00Z",
                variant_of_row_id=1, registry=self._registry())
        self.assertEqual(len(read_repair_queue(self.out, "X")), 1)


@unittest.skipUnless(
    Path(".scratch/sta-78/spikes/a/cache").exists(),
    "spike-A cache not present (gathered data, not shipped)")
class RunAdjudicationDurabilityTest(unittest.TestCase):
    """End-to-end (real VUM config + registry export + replay cache, still
    zero network): a human resolution made after one run_adjudication()
    call must survive a second one, not get silently reopened."""

    REPLAY_DIR = ".scratch/sta-78/spikes/a/cache"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_second_run_preserves_a_resolution_made_after_the_first(self):
        if not Path(self.REPLAY_DIR).exists():
            self.skipTest("spike-A replay cache not present in this checkout")

        first = run_adjudication("VUM", configs_dir=str(
                                     Path(__file__).parent /
                                     "fixtures_benchmark_configs"),
                                 replay_dir=self.REPLAY_DIR,
                                 out_dir=self.out)
        self.assertGreater(len(first.queue), 0)
        target = first.queue[0]

        artifact = Artifact(text=target.row_name + " вече не се предлага.",
                            renderer_id="test", renderer_version="1", ref="news1")
        resolve_repair_entry(
            self.out, "VUM", row_id=target.row_id, status="not-enrolling",
            value=target.row_name + " вече не се предлага",
            segment=target.row_name + " вече не се предлага.",
            artifact=artifact, source_url="https://vum.bg/news",
            retrieved_at="2026-08-15T00:00:00Z")

        second = run_adjudication("VUM", configs_dir=str(
                                      Path(__file__).parent /
                                      "fixtures_benchmark_configs"),
                                  replay_dir=self.REPLAY_DIR,
                                  out_dir=self.out)
        self.assertNotIn(target.row_id, [e.row_id for e in second.queue],
                         "resolved row must not be reopened by a later run")
        resolved_ids = [r.row_id for r in second.resolved]
        self.assertIn(target.row_id, resolved_ids)


if __name__ == "__main__":
    unittest.main()
