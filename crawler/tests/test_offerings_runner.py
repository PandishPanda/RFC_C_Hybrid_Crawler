"""Runner-side Offerings (ticket 19) -- zero network, synthetic fixtures.

A Program is not one offering. The registry row enumerates (form,
duration) pairs; config attaches a recipe per form; the runner resolves
each Offering's own tuition and reports them as a SIBLING of the
Program's fields (ADR-0004).

Fixtures are synthetic (test_registry.py's convention) and the fee grid
is built in-process, so these run in milliseconds and never fetch.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import runner  # noqa: E402
from crawler.cascade import TableSource  # noqa: E402
from crawler.config import parse_site_config  # noqa: E402
from crawler.provenance import Artifact  # noqa: E402
from crawler.registry import RegistryRow  # noqa: E402

# One state table and one paid table, same shape as a real fee workbook.
GRIDS = (
    (("Приложение 1",), ("№", "СПЕЦИАЛНОСТ", "Редовно обучение", "Задочно обучение"),
     ("", "", "евро", "евро"),
     ("4", "Бизнес мениджмънт", "204.52", "-")),
    (("Приложение 2",), ("№", "СПЕЦИАЛНОСТ", "Редовно обучение", "Дистанционна форма"),
     ("", "", "евро", "евро"),
     ("10", "Бизнес мениджмънт", "674.91", "388.58")),
)
ARTIFACT_TEXT = "\n".join(" ".join(r) for t in GRIDS for r in t)


class FakeStore:
    def __init__(self, ref):
        self._ref = ref
        self._artifact = Artifact(text=ARTIFACT_TEXT, renderer_id="test",
                                  renderer_version="1", ref=ref)

    def artifact(self, ref):
        return self._artifact

    def doc(self, ref):
        from types import SimpleNamespace
        return SimpleNamespace(source_url="https://x.example/fees.xlsx",
                               retrieved_at="2026-08-16T00:00:00Z")


def source(name, form, marker):
    return {"url": "https://x.example/fees.xlsx", "route": "spreadsheet",
            "join": {"kind": "fee-row", "name": name,
                     "match_header": "специалност",
                     "value_headers": [form, "евро"],
                     "table_marker": marker}}


def site_with(offerings):
    return parse_site_config({
        "uni_id": "X",
        "sources": {
            "state-full": source("s1", "редовно", "Приложение 1"),
            "paid-dist": source("s2", "дистанционна", "Приложение 2"),
            "paid-full": source("s3", "редовно", "Приложение 2"),
            "state-part": source("s4", "задочно", "Приложение 1"),
        },
        "programs": [{
            "id": "biz", "name": "Бизнес мениджмънт",
            "page": "https://x.example/biz", "rsvu_code": "30701182",
            "offerings": offerings,
        }],
    }, origin="<t>")


def recipe(src):
    return {"tuition_join": {"source": src, "alias": "Бизнес мениджмънт"}}


ROW = RegistryRow(
    id=30083, code="30701182", name="Бизнес мениджмънт", major_id=1,
    major_name="Администрация", degree_code=3, degree_name="Бакалавър",
    edu_forms=("редовна - 4, задочна - 5, задочна - 4.5, "
               "дистанционна - 4.5, дистанционна - 4, дистанционна - 5"))


def resolve(offerings, row=ROW):
    site = site_with(offerings)
    store = FakeStore("xlsx:fees")
    docs = {sid: TableSource(ref="xlsx:fees", tables=GRIDS)
            for sid in site.sources}
    report = runner.init_offering_report_keys({})
    records, failures = runner._offering_records(
        site, site.programs[0], row, docs, store, report)
    return records, failures, report


class EnumerationTest(unittest.TestCase):
    """ADR-0004's worked example: six offerings for one registry row."""

    def setUp(self):
        self.records, _, self.report = resolve(
            {"редовна": recipe("state-full"),
             "дистанционна": recipe("paid-dist")})

    def test_the_registry_enumerates_the_offerings_not_config(self):
        self.assertEqual(
            [r["offering_id"].split("#", 1)[1] for r in self.records],
            ["редовна - 4", "задочна - 5", "задочна - 4.5",
             "дистанционна - 4.5", "дистанционна - 4", "дистанционна - 5"])

    def test_each_offering_is_priced_by_its_own_recipe(self):
        got = {r["offering_id"].split("#", 1)[1]:
               r["fields"]["tuition"].get("value") for r in self.records}
        self.assertEqual(got["редовна - 4"], "204.52")
        self.assertEqual(got["дистанционна - 4"], "388.58")

    def test_one_cell_prices_three_offerings_as_a_shared_source(self):
        self.assertEqual(
            [r["fields"]["tuition"]["value"] for r in self.records
             if r["form"] == "дистанционна"], ["388.58"] * 3)

    def test_an_offering_with_no_recipe_is_an_explicit_null(self):
        for record in self.records:
            if record["form"] != "задочна":
                continue
            field = record["fields"]["tuition"]
            self.assertEqual(field["status"], "NULL_OK")
            self.assertIsNone(field["value"])
            self.assertIn("no offering recipe", field["null_reason"])

    def test_each_offering_cites_the_registry_item_it_came_from(self):
        for record in self.records:
            self.assertEqual(record["registry_row_id"], 30083)
        self.assertEqual([r["edu_forms_item"] for r in self.records],
                         ["редовна - 4", "задочна - 5", "задочна - 4.5",
                          "дистанционна - 4.5", "дистанционна - 4",
                          "дистанционна - 5"])


class NoFallbackTest(unittest.TestCase):
    def test_a_priced_recipe_whose_cell_is_a_dash_nulls_with_a_reason(self):
        # "задочна" IS configured here and the state table's задочно cell
        # is a dash. The null must name what was checked, and must never
        # borrow another column's number.
        records, _, _ = resolve({"задочна": recipe("state-part")})
        field = next(r for r in records
                     if r["form"] == "задочна")["fields"]["tuition"]
        self.assertEqual(field["status"], "NULL_OK")
        self.assertIn("Бизнес мениджмънт", field["null_reason"])
        self.assertIn("Приложение 1", field["null_reason"])


class MostSpecificRecipeWinsTest(unittest.TestCase):
    def test_a_duration_specialised_recipe_overrides_the_bare_form(self):
        records, _, _ = resolve({"дистанционна": recipe("paid-dist"),
                                 "дистанционна - 4": recipe("paid-full")})
        got = {r["offering_id"].split("#", 1)[1]: r["recipe_key"]
               for r in records if r["form"] == "дистанционна"}
        self.assertEqual(got["дистанционна - 4"], "дистанционна - 4")
        self.assertEqual(got["дистанционна - 4.5"], "дистанционна")
        self.assertEqual(got["дистанционна - 5"], "дистанционна")

    def test_the_specialised_recipe_actually_changes_the_value(self):
        records, _, _ = resolve({"дистанционна": recipe("paid-dist"),
                                 "дистанционна - 4": recipe("paid-full")})
        got = {r["offering_id"].split("#", 1)[1]:
               r["fields"]["tuition"]["value"] for r in records
               if r["form"] == "дистанционна"}
        self.assertEqual(got["дистанционна - 4"], "674.91")
        self.assertEqual(got["дистанционна - 5"], "388.58")


class UnusedRecipeTest(unittest.TestCase):
    def test_a_recipe_no_offering_matches_is_reported(self):
        # самостоятелна is a real registry form, but this row never
        # states it -- silence would leave a config edit looking
        # effective when it can never fire.
        _, _, report = resolve({"редовна": recipe("state-full"),
                                "самостоятелна": recipe("state-full")})
        self.assertEqual([u["recipe_key"]
                          for u in report["offering_config_unused"]],
                         ["самостоятелна"])

    def test_a_specialised_recipe_for_a_duration_never_stated_is_reported(self):
        _, _, report = resolve({"редовна - 9": recipe("state-full")})
        self.assertEqual([u["recipe_key"]
                          for u in report["offering_config_unused"]],
                         ["редовна - 9"])

    def test_matching_recipes_are_not_reported(self):
        _, _, report = resolve({"редовна": recipe("state-full")})
        self.assertEqual(report["offering_config_unused"], [])


class MalformedRegistryItemTest(unittest.TestCase):
    def test_an_unparsed_item_is_reported_and_does_not_stop_the_rest(self):
        row = RegistryRow(
            id=9, code="30701182", name="X", major_id=1, major_name="m",
            degree_code=3, degree_name="Бакалавър",
            edu_forms="редовна - 4, свободен текст")
        records, _, report = resolve({"редовна": recipe("state-full")}, row)
        self.assertEqual(len(records), 1)
        self.assertEqual(report["offering_unparsed"][0]["item"],
                         "свободен текст")


if __name__ == "__main__":
    unittest.main()


class DuplicateAndMissingRowTest(unittest.TestCase):
    """Two shapes the registry can produce that must not corrupt the report."""

    def test_a_duplicated_form_duration_gets_distinct_ids_and_is_reported(self):
        # parse_edu_forms preserves duplicates deliberately, but the
        # Offering key is IDENTITY (ADR-0004) -- two records sharing one
        # id would silently merge downstream.
        row = RegistryRow(
            id=7, code="30701182", name="X", major_id=1, major_name="m",
            degree_code=3, degree_name="Бакалавър",
            edu_forms="редовна - 4, редовна - 4")
        records, _, report = resolve({"редовна": recipe("state-full")}, row)
        ids = [r["offering_id"] for r in records]
        self.assertEqual(len(set(ids)), 2, ids)
        self.assertEqual(len(report["offering_duplicate_key"]), 1)


class CurriculumBindingTest(unittest.TestCase):
    """The binding check (ticket 21): a gate-checked attestation that the
    fetched plan is THIS offering's, from the plan's own breadcrumb.

    PLAN_TEXT below is the REAL breadcrumb region of the captured
    CB3.7.4.1 v5 snapshot (crawler-out/UniRuse/snapshots, 2026-08-16),
    per the ticket's gate: no pattern written against an unobserved page.
    """

    PLAN_URL = ("https://e-curriculum.uni-ruse.bg/app/View/Curriculum"
                "?code=CB3.7.4.1&version=5")
    PLAN_TEXT = ('Факултет Бизнес и мениджмънт > Бизнес мениджмънт > \n'
                 'ОКС "Бакалавър"\n > \nРедовно\n Година: 1 \n60\n645.00')

    def _resolve_with_plan(self, form_phrase, plan_text=None):
        site = parse_site_config({
            "uni_id": "X", "sources": {},
            "programs": [{"id": "biz", "name": "Бизнес мениджмънт",
                          "page": "https://x.example/biz",
                          "rsvu_code": "30701182",
                          "offerings": {"редовна": {"curriculum": {
                              "url": self.PLAN_URL,
                              "form_phrase": form_phrase,
                              "code": "CB3.7.4.1", "version": "5"}}}}],
        }, origin="<t>")
        text = self.PLAN_TEXT if plan_text is None else plan_text
        plan_artifact = Artifact(text=text, renderer_id="test",
                                 renderer_version="1", ref="html:plan")
        class PlanStore(FakeStore):
            def artifact(self, ref):
                return plan_artifact
        docs = {self.PLAN_URL: TableSource(ref="html:plan", tables=())}
        report = runner.init_offering_report_keys({})
        records, _ = runner._offering_records(
            site, site.programs[0], ROW, docs, PlanStore("html:plan"),
            report)
        return records, report

    def test_a_matching_form_phrase_binds_and_is_quoted(self):
        records, report = self._resolve_with_plan("Редовно")
        bound = records[0]["curriculum"]
        self.assertIsNotNone(bound)
        self.assertEqual(bound["form_phrase"], "Редовно")
        self.assertEqual(bound["segments"], ["Редовно"])
        self.assertEqual(report["curriculum_unbound"], [])

    def test_a_wrong_form_phrase_yields_null_and_a_loud_entry(self):
        # The Задочно plan fetched where the Редовно one was claimed:
        # binding must fail, never silently accept.
        records, report = self._resolve_with_plan("Задочно")
        self.assertIsNone(records[0]["curriculum"])
        self.assertEqual(len(report["curriculum_unbound"]), 1)
        entry = report["curriculum_unbound"][0]
        self.assertIn("Задочно", entry["reason"])
        self.assertEqual(entry["gate_status"], "REJECT_CONTAINMENT")

    def test_an_unfetched_plan_is_reported_not_guessed(self):
        site = parse_site_config({
            "uni_id": "X", "sources": {},
            "programs": [{"id": "biz", "name": "Б",
                          "page": "https://x.example/biz",
                          "rsvu_code": "30701182",
                          "offerings": {"редовна": {"curriculum": {
                              "url": "https://never.fetched/plan",
                              "form_phrase": "Редовно"}}}}],
        }, origin="<t>")
        report = runner.init_offering_report_keys({})
        records, _ = runner._offering_records(
            site, site.programs[0], ROW, {}, FakeStore("x"), report)
        self.assertIsNone(records[0]["curriculum"])
        self.assertIn("not fetched", report["curriculum_unbound"][0]["reason"])

    def test_offerings_without_a_curriculum_recipe_carry_null_silently(self):
        # No recipe -> no claim -> nothing to attest; only a CLAIMED plan
        # that fails to bind is loud.
        records, report = self._resolve_with_plan("Редовно")
        for record in records[1:]:   # only редовна has the recipe
            self.assertIsNone(record["curriculum"])
        self.assertEqual(report["curriculum_unbound"], [])

    def test_no_value_is_ever_extracted_from_the_plan(self):
        # The ticket's hard rules: no duration from Година: counts, no
        # language from marker absence. Structurally: binding returns an
        # attestation dict with NO field values, and offering fields are
        # untouched by it.
        records, _ = self._resolve_with_plan("Редовно")
        bound = records[0]["curriculum"]
        self.assertEqual(set(bound),
                         {"url", "form_phrase", "segments", "artifact_ref",
                          "code", "version", "attests"})
        self.assertEqual(records[0]["fields"]["tuition"]["status"],
                         "NULL_OK")

    def test_document_plan_fetches_a_declared_curriculum_url(self):
        site = parse_site_config({
            "uni_id": "X", "sources": {},
            "programs": [{"id": "biz", "name": "Б",
                          "page": "https://x.example/biz",
                          "rsvu_code": "30701182",
                          "offerings": {"редовна": {"curriculum": {
                              "url": self.PLAN_URL,
                              "form_phrase": "Редовно"}}}}],
        }, origin="<t>")
        plan = runner.document_plan(site)
        self.assertIn(self.PLAN_URL, plan)


class BindingAnchoringTest(unittest.TestCase):
    """Review of ticket 21 measured the degenerate case: with only a
    form_phrase, binding collapses to a one-word substring search, so a
    page saying "изпитите се провеждат редовно" binds "Редовно" -- the
    ticket-10 wrong-occurrence blind spot one level down. Breadcrumb
    anchors close it."""

    URL = ("https://e-curriculum.uni-ruse.bg/app/View/Curriculum"
           "?code=CB3.7.4.1&version=5")
    # Real breadcrumb shape from the captured CB3.7.4.1 v5 artifact.
    GOOD = ('Факултет Бизнес и мениджмънт > Бизнес мениджмънт > \n'
            'ОКС "Бакалавър"\n > \nРедовно\n Година: 1')
    # A wrong plan whose prose merely mentions the word.
    TRAP = ('Факултет Бизнес и мениджмънт > Бизнес мениджмънт > \n'
            'ОКС "Бакалавър"\n > \nЗадочно\n Година: 1 ...'
            + " x" * 200 +
            ' изпитите се провеждат редовно всяка сесия')

    def _bind(self, plan_text, curriculum):
        site = parse_site_config({
            "uni_id": "X", "sources": {},
            "programs": [{"id": "biz", "name": "Бизнес мениджмънт",
                          "page": "https://x.example/biz",
                          "rsvu_code": "30701182",
                          "offerings": {"редовна": {"curriculum": curriculum}}}],
        }, origin="<t>")
        plan_artifact = Artifact(text=plan_text, renderer_id="t",
                                 renderer_version="1", ref="html:plan")
        class PlanStore(FakeStore):
            def artifact(self, ref):
                return plan_artifact
        docs = {self.URL: TableSource(ref="html:plan", tables=())}
        report = runner.init_offering_report_keys({})
        records, _ = runner._offering_records(
            site, site.programs[0], ROW, docs, PlanStore("html:plan"),
            report)
        return records[0]["curriculum"], report

    ANCHORED = {"url": URL, "form_phrase": "Редовно",
                "program_name": "Бизнес мениджмънт",
                "degree_phrase": 'ОКС "Бакалавър"'}

    def test_the_trap_page_binds_without_anchors_and_is_refused_with_them(self):
        bare = {"url": self.URL, "form_phrase": "Редовно"}
        bound, _ = self._bind(self.TRAP, bare)
        self.assertIsNotNone(bound, "the degenerate case: this is WHY "
                                    "anchors exist")
        bound, report = self._bind(self.TRAP, dict(self.ANCHORED))
        self.assertIsNone(bound)
        self.assertIn("not within", report["curriculum_unbound"][0]["reason"])

    def test_the_real_breadcrumb_binds_with_anchors(self):
        bound, report = self._bind(self.GOOD, dict(self.ANCHORED))
        self.assertIsNotNone(bound)
        self.assertEqual(len(bound["segments"]), 3)
        self.assertEqual(report["curriculum_unbound"], [])

    def test_a_missing_anchor_phrase_refuses_to_bind(self):
        wrong = dict(self.ANCHORED, program_name="Друга програма")
        bound, report = self._bind(self.GOOD, wrong)
        self.assertIsNone(bound)


class CurriculumRefCrossCheckTest(unittest.TestCase):
    def test_a_code_contradicting_the_url_is_rejected_at_load(self):
        from crawler.config import ConfigError
        with self.assertRaises(ConfigError) as ctx:
            parse_site_config({
                "uni_id": "X", "sources": {},
                "programs": [{"id": "p", "name": "P", "page": "https://x/p",
                              "rsvu_code": "C",
                              "offerings": {"редовна": {"curriculum": {
                                  "url": "https://e.example/plan?code=CB1&version=5",
                                  "form_phrase": "Редовно",
                                  "code": "WRONG"}}}}],
            }, origin="<t>")
        self.assertIn("contradicts", str(ctx.exception))
