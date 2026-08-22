"""Cascade suite — tiers G, F and B ported from spike A, config as data.

Four proofs, all offline over vendored artifacts (no network, no LLM):

1. CONFIG SCHEMA — the typed loader REJECTS unknown keys at every level
   (a typo'd key raises ConfigError naming the key and path, never
   silently nulls a field), validates routes, regexes, references,
   field-to-join-kind wiring and anchor wiring; the four benchmark
   configs load clean.

2. GOLDEN REGRESSION — running the cascade over the four benchmark
   universities' vendored artifacts reproduces spike A's E3 emissions
   exactly: all 94 records (value, segments, artifact, method, tier),
   the spike's measured tier split (G:55, F:27, B:12 — the anchor tier
   is ported as per-site config until the ticket-02 LLM tail replaces
   it), no extra emissions, and the remaining fields (the key's explicit
   nulls) fall through as None.

3. RUNNER SEAM — every emission carries the provenance quintuple and
   passes crawler.provenance.gate against the store-resolved artifact it
   names. The cascade itself never gates; this suite plays the runner.

4. COLUMN-AWARE RESOLVER — fee-row joins read the ACTUAL TSV artifact
   files; columns are located by header text, never by position; an alias
   row with an empty value cell yields nothing (the resolver-side fix for
   the gate's truthful-snippet-wrong-column blind spot); emitted segments
   are literal lines of the rendered table artifact.
"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import cascade, config  # noqa: E402
from crawler.provenance import Status, gate  # noqa: E402
from crawler.tests import provenance_fixtures as fx  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent
CASCADE_FIXTURES = TESTS_DIR / "fixtures" / "cascade"
CONFIGS_DIR = TESTS_DIR.parents[0] / "configs"
# The golden-regression/benchmark-pin tests run against the FROZEN
# benchmark configs (see fixtures_benchmark_configs/README.md) -- the
# living configs evolve, the audited acceptance does not.
FROZEN_CONFIGS_DIR = TESTS_DIR / "fixtures_benchmark_configs"

BENCHMARK_UNIS = ("AUBG", "MUPleven", "SofiaUniversity", "VUM")

_cache = {}


def benchmark_docs():
    """The docs mapping a runner would build from the artifact store:
    HTML pages keyed by URL, shared sources by config source id, every
    TextSource/TableSource ref set to the vendored-artifact key so the
    gate suite can resolve the exact artifact each emission names."""
    if "docs" not in _cache:
        docs = {}
        for ref, m in fx.manifest().items():
            if m["renderer_id"].startswith("bs4"):
                docs[m["source_ref"]] = cascade.TextSource(
                    ref=ref, text=fx.load_artifact(ref).text)
        docs["su-adm"] = cascade.TextSource(
            ref="pdftext-su-adm",
            text=fx.load_artifact("pdftext-su-adm").text,
            layout=(CASCADE_FIXTURES / "layout-su-adm.txt").read_text())
        docs["mu-sprav"] = cascade.TextSource(
            ref="pdftext-mu-sprav",
            text=fx.load_artifact("pdftext-mu-sprav").text)
        docs["su-compchem"] = cascade.TextSource(
            ref="pdftext-su-compchem",
            text=fx.load_artifact("pdftext-su-compchem").text)
        docs["aubg-catalog"] = cascade.TextSource(
            ref="pdftext-aubg-catalog",
            text=fx.load_artifact("pdftext-aubg-catalog").text)
        docs["su-fees"] = cascade.TableSource.from_tsv_dir(
            "docling-tsv-su-fees", CASCADE_FIXTURES / "docling" / "su-fees")
        docs["mu-fees"] = cascade.TableSource.from_tsv_dir(
            "docling-tsv-mu-fees", CASCADE_FIXTURES / "docling" / "mu-fees")
        # shared sources are addressed by config source id: resolve each
        # id to its URL's artifact, exactly as the runner does
        for site in config.load_configs_dir(CONFIGS_DIR).values():
            for sid, src in site.sources.items():
                if sid not in docs and src.url in docs:
                    docs[sid] = docs[src.url]
        _cache["docs"] = docs
    return _cache["docs"]


def benchmark_extractions():
    """{(program_id, field): Extraction-or-None} over all four unis."""
    if "extractions" not in _cache:
        configs = config.load_configs_dir(FROZEN_CONFIGS_DIR)
        docs = benchmark_docs()
        out = {}
        for uni_id in BENCHMARK_UNIS:
            for pid, fields in cascade.extract_site(configs[uni_id],
                                                    docs).items():
                for field_name, extraction in fields.items():
                    out[(pid, field_name)] = extraction
        _cache["extractions"] = out
    return _cache["extractions"]


# =========================================================== 1. config schema
def valid_config():
    """A minimal valid site config exercising every node type."""
    return {
        "uni_id": "TestUni",
        "cookies": {"region": "bg"},
        "sources": {
            "fees": {
                "url": "https://example.bg/fees.pdf",
                "route": "table-pdf",
                "join": {
                    "kind": "fee-row",
                    "name": "test",
                    "match_header": "специалност",
                    "value_headers": ["бакалавър", "редовно"],
                    "value_pattern": "((?:\\d+ EUR)|освободени)",
                    "context": {"year": "2026/2027"},
                },
            },
            "adm": {
                "url": "https://example.bg/adm.pdf",
                "route": "prose-pdf",
                "join": {
                    "kind": "ordinance",
                    "name": "test",
                    "row_pattern": "^{alias}\\s{2,}(изпит по [а-я]+)",
                    "row_value_group": 1,
                },
            },
        },
        "programs": [
            {
                "id": "test-prog",
                "name": "Тестова специалност",
                "page": "https://example.bg/prog",
                "tuition_join": {"source": "fees", "alias": "Тестова"},
                "admission_join": {"source": "adm", "alias": "Тестова"},
            },
        ],
    }


class TestConfigSchema(unittest.TestCase):
    def parse(self, data):
        return config.parse_site_config(data, origin="test.json")

    def assert_rejects(self, data, *needles):
        with self.assertRaises(config.ConfigError) as ctx:
            self.parse(data)
        for needle in needles:
            self.assertIn(needle, str(ctx.exception))

    def test_valid_config_parses(self):
        site = self.parse(valid_config())
        self.assertEqual(site.uni_id, "TestUni")
        self.assertEqual(site.programs[0].tuition_join.alias, "Тестова")
        self.assertEqual(site.sources["fees"].join.kind, "fee-row")

    def test_unknown_top_level_key_raises(self):
        data = valid_config()
        data["politeness"] = 1
        self.assert_rejects(data, "politeness", "unknown key")

    def test_typoed_program_key_raises_not_nulls(self):
        """THE schema guarantee: 'tution_join' must raise at load time,
        never silently null tuition at refresh time."""
        data = valid_config()
        data["programs"][0]["tution_join"] = data["programs"][0].pop(
            "tuition_join")
        self.assert_rejects(data, "tution_join", "programs[0]")

    def test_unknown_source_key_raises(self):
        data = valid_config()
        data["sources"]["fees"]["cokies"] = {}
        self.assert_rejects(data, "cokies", "sources['fees']")

    def test_unknown_join_key_raises(self):
        data = valid_config()
        data["sources"]["fees"]["join"]["value_hdrs"] = ["x"]
        self.assert_rejects(data, "value_hdrs", ".join")

    def test_unknown_join_ref_key_raises(self):
        data = valid_config()
        data["programs"][0]["tuition_join"]["aliass"] = "X"
        self.assert_rejects(data, "aliass")

    def test_unknown_route_raises(self):
        data = valid_config()
        data["sources"]["adm"]["route"] = "headless"
        self.assert_rejects(data, "headless", "route")

    def test_unknown_join_kind_raises(self):
        data = valid_config()
        data["sources"]["fees"]["join"]["kind"] = "fee-table"
        self.assert_rejects(data, "fee-table", "kind")

    def test_alias_and_alias_pattern_together_raise(self):
        data = valid_config()
        data["programs"][0]["tuition_join"]["alias_pattern"] = "Тест.*"
        self.assert_rejects(data, "not both")

    def test_fee_row_join_requires_literal_alias(self):
        data = valid_config()
        data["programs"][0]["tuition_join"] = {
            "source": "fees", "alias_pattern": "Тест.*"}
        self.assert_rejects(data, "alias")

    def test_dangling_source_reference_raises(self):
        data = valid_config()
        data["programs"][0]["tuition_join"]["source"] = "feees"
        self.assert_rejects(data, "feees", "not a configured source")

    def test_wrong_join_kind_wiring_raises(self):
        data = valid_config()
        data["programs"][0]["admission_join"]["source"] = "fees"
        self.assert_rejects(data, "ordinance")

    def test_duplicate_program_id_raises(self):
        data = valid_config()
        data["programs"].append(copy.deepcopy(data["programs"][0]))
        self.assert_rejects(data, "duplicate program id")

    def test_invalid_regex_raises(self):
        data = valid_config()
        data["sources"]["fees"]["join"]["value_pattern"] = "(unclosed"
        self.assert_rejects(data, "invalid regex")

    def test_template_without_alias_placeholder_raises(self):
        data = valid_config()
        data["sources"]["adm"]["join"]["row_pattern"] = "^X\\s+(изпит)"
        self.assert_rejects(data, "{alias}")

    def test_spravochnik_requires_named_groups(self):
        data = valid_config()
        data["sources"]["adm"]["join"] = {
            "kind": "spravochnik", "name": "t",
            "sentence_pattern": "{alias} - (ОКС [а-я]+)"}
        data["programs"][0].pop("admission_join")
        self.assert_rejects(data, "named group")

    def test_fees_page_value_pattern_must_be_group_free(self):
        data = valid_config()
        data["sources"]["fees"]["join"] = {
            "kind": "fees-page", "name": "t",
            "value_pattern": "(\\d+ € per semester)"}
        data["programs"][0].pop("tuition_join")
        self.assert_rejects(data, "group-free")

    def anchored_config(self):
        data = valid_config()
        data["anchors"] = {
            "test-free": {
                "source": "https://example.bg/prog",
                "pattern": "обучението е (безплатно)",
            },
        }
        data["programs"][0]["field_anchors"] = {"tuition": "test-free"}
        return data

    def test_anchor_config_parses(self):
        site = self.parse(self.anchored_config())
        self.assertEqual(site.anchors["test-free"].id, "test-free")
        self.assertEqual(site.programs[0].field_anchors,
                         {"tuition": "test-free"})

    def test_unknown_anchor_key_raises(self):
        data = self.anchored_config()
        data["anchors"]["test-free"]["regex"] = "x"
        self.assert_rejects(data, "regex", "anchors['test-free']")

    def test_anchor_pattern_needs_a_group(self):
        data = self.anchored_config()
        data["anchors"]["test-free"]["pattern"] = "безплатно"
        self.assert_rejects(data, "capturing group")

    def test_anchor_source_must_be_url_or_source_id(self):
        data = self.anchored_config()
        data["anchors"]["test-free"]["source"] = "not-a-source"
        self.assert_rejects(data, "not-a-source",
                            "neither a URL nor a configured source")

    def test_field_anchor_must_name_a_declared_anchor(self):
        data = self.anchored_config()
        data["programs"][0]["field_anchors"]["tuition"] = "no-such"
        self.assert_rejects(data, "no-such", "not a declared anchor")

    def test_field_anchor_key_must_be_a_program_field(self):
        data = self.anchored_config()
        data["programs"][0]["field_anchors"] = {"tutorial": "test-free"}
        self.assert_rejects(data, "tutorial", "not a Program field")

    def test_benchmark_configs_load_and_cover_20_programs(self):
        # The configs dir may carry more than the benchmark four: issue 01's
        # scope change runs e2e on fresh universities (SHU, TUG, ANIS,
        # MUVarna), whose onboarded configs live here too. Every config must
        # LOAD (load_configs_dir validates all of them); the benchmark pin
        # stays exact over the FROZEN benchmark four and their 20 programs
        # (the living configs keep growing -- VUM gained 11 programs
        # 2026-08-17 -- so the audited pin reads the frozen copies).
        config.load_configs_dir(CONFIGS_DIR)   # living: must still LOAD
        configs = config.load_configs_dir(FROZEN_CONFIGS_DIR)
        self.assertLessEqual(set(BENCHMARK_UNIS), set(configs))
        program_ids = [p.id for uni_id in BENCHMARK_UNIS
                       for p in configs[uni_id].programs]
        self.assertEqual(len(program_ids), 20)
        self.assertEqual(len(set(program_ids)), 20)
        self.assertEqual(configs["AUBG"].cookies,
                         {"aubg_location": "bulgaria"})


# ======================================================= 2. golden regression
class TestGoldenRegression(unittest.TestCase):
    """Spike A cache replay is the golden regression (issue 01): the
    cascade over vendored artifacts must reproduce E3's emissions —
    G/F shared code plus the anchor tier B ported as config."""

    def golden(self, tiers):
        return [r for r in fx.golden_records() if r["tier"] in tiers]

    def test_label_library_is_30_shared_patterns(self):
        # 30 = 27 + bg-lang-label (2026-08-17 VUM benchmark repair)
        #         + bg-programme-level (2026-08-21 NBU build-out)
        #         + bg-kandidatstva-se (2026-08-22 UniRuse admission)
        n = sum(len(v) for v in cascade.LABEL_PATTERNS.values())
        self.assertEqual(n, 30)

    def test_reproduces_every_golden_record(self):
        extractions = benchmark_extractions()
        for record in self.golden("GFB"):
            key = (record["program_id"], record["field"])
            with self.subTest(program=key[0], field=key[1]):
                got = extractions.get(key)
                self.assertIsNotNone(
                    got, "cascade emitted nothing for {0}".format(key))
                self.assertEqual(got.value, record["value"], key)
                self.assertEqual(list(got.segments), record["segments"], key)
                self.assertEqual(got.method, record["method"], key)
                self.assertEqual(got.tier, record["tier"], key)
                self.assertEqual(got.artifact_ref, record["artifact"], key)

    def test_no_emissions_beyond_the_golden_set(self):
        emitted = {key for key, e in benchmark_extractions().items()
                   if e is not None}
        expected = {(r["program_id"], r["field"])
                    for r in self.golden("GFB")}
        self.assertEqual(emitted, expected)

    def test_only_the_keys_explicit_nulls_fall_through(self):
        """With the anchor tier ported as config, the only None fields
        left on the benchmark are the answer key's six explicit language
        nulls (SU programs whose pages state no language) — NULL_OK
        territory for the runner, not misses."""
        extractions = benchmark_extractions()
        nones = {key for key, e in extractions.items() if e is None}
        self.assertEqual(nones, {
            ("su-kn", "language"), ("su-si", "language"),
            ("su-is", "language"), ("su-stat", "language"),
            ("su-ch", "language"), ("su-psy", "language"),
        })

    def test_spike_a_tier_split_g55_f27_b12(self):
        """Spike A's measured per-tier split (e3-results: G:55 F:27 B:12);
        the anchor tier is config now, so the cascade emits all 94."""
        emitted = [e for e in benchmark_extractions().values()
                   if e is not None]
        by_tier = {}
        for e in emitted:
            by_tier[e.tier] = by_tier.get(e.tier, 0) + 1
        self.assertEqual(by_tier, {"G": 55, "F": 27, "B": 12})
        self.assertEqual(len(emitted), 94)
        # cross-check against the vendored golden fixture itself
        golden_tiers = {}
        for r in self.golden("GFB"):
            golden_tiers[r["tier"]] = golden_tiers.get(r["tier"], 0) + 1
        self.assertEqual(golden_tiers, {"G": 55, "F": 27, "B": 12})


# ============================================================ 3. runner seam
class TestRunnerGateSeam(unittest.TestCase):
    """The cascade never gates; the runner does. Every emission must carry
    a quintuple that PASSes the pure gate against the exact artifact its
    artifact_ref names."""

    def test_every_emission_passes_the_provenance_gate(self):
        passed = 0
        for key, e in benchmark_extractions().items():
            if e is None:
                continue
            with self.subTest(program=key[0], field=key[1]):
                verdict = gate(e.value, list(e.segments),
                               fx.load_artifact(e.artifact_ref))
                self.assertEqual(
                    verdict.status, Status.PASS,
                    "{0} [{1}] {2!r}: {3}".format(key, e.method, e.value,
                                                  verdict.detail))
                passed += 1
        self.assertEqual(passed, 94)

    def test_quintuple_is_complete_on_every_emission(self):
        for key, e in benchmark_extractions().items():
            if e is None:
                continue
            with self.subTest(program=key[0], field=key[1]):
                self.assertTrue(e.value)
                self.assertTrue(e.segments)
                self.assertTrue(all(isinstance(s, str) and s
                                    for s in e.segments))
                self.assertTrue(e.artifact_ref)
                self.assertTrue(e.method)
                self.assertIn(e.tier, (cascade.TIER_G, cascade.TIER_F,
                                       cascade.TIER_B))


# ============================================== 4. column-aware fee resolver
def synthetic_join(**overrides):
    kwargs = dict(name="test", match_header="специалност",
                  value_headers=("бакалавър", "редовно"),
                  value_pattern=r'((?:\d+ EUR)|освободени|няма)')
    kwargs.update(overrides)
    return config.FeeRowJoin(**kwargs)


def table_source(*tables):
    return cascade.TableSource(
        ref="synthetic-table",
        tables=tuple(tuple(tuple(row) for row in table)
                     for table in tables))


class TestColumnAwareFeeResolver(unittest.TestCase):
    HEADERS_SWAPPED = [
        # value column moved to index 1, alias column to index 3 — a blind
        # positional port (spike A read r[2]/r[3]) would misread this table
        ["форма", 'ОКС "бакалавър" и ОКС "магистър"', "факултет",
         "специалност", 'ОНС "доктор"'],
        ["", "редовно", "", "", "редовно"],
        ["x", "460 EUR", "ФМИ", "Компютърни науки", "920 EUR"],
    ]

    def test_columns_resolved_by_header_text_not_position(self):
        r = cascade.fee_row_join("tuition",
                                 table_source(self.HEADERS_SWAPPED),
                                 synthetic_join(), "Компютърни науки")
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "460 EUR")
        self.assertEqual(r.tier, "F")
        self.assertEqual(r.method, "fee-join:test")

    def test_empty_value_cell_never_bleeds_into_neighbour_column(self):
        """The resolver-side fix for the gate's documented blind spot
        (truthful snippet, wrong column — RFC v2 Q4): an alias row whose
        value cell is EMPTY yields nothing, even though a truthful 460
        sits one column over and would pass the gate."""
        table = [
            ["специалност", 'ОКС "бакалавър" редовно', 'ОНС "доктор" редовно'],
            ["Компютърни науки", "", "460 EUR"],
        ]
        r = cascade.fee_row_join("tuition", table_source(table),
                                 synthetic_join(), "Компютърни науки")
        self.assertIsNone(r)

    def test_merged_cell_value_pattern_takes_first_fee_token(self):
        table = [
            ["специалност", 'ОКС "бакалавър" редовно'],
            ["Физика и информатика", "310 EUR освободени"],
        ]
        r = cascade.fee_row_join("tuition", table_source(table),
                                 synthetic_join(), "Физика и информатика")
        self.assertEqual(r.value, "310 EUR")

    def test_reads_actual_tsv_artifact_files(self):
        source = cascade.TableSource.from_tsv_dir(
            "docling-tsv-su-fees", CASCADE_FIXTURES / "docling" / "su-fees")
        self.assertEqual(len(source.tables), 5)
        r = cascade.fee_row_join(
            "tuition", source,
            config.load_configs_dir(CONFIGS_DIR)["SofiaUniversity"]
            .sources["su-fees"].join, "Компютърни науки")
        self.assertEqual(r.value, "460 EUR")

    def test_segments_are_lines_of_the_rendered_table_artifact(self):
        """Store-consistency: a fee-join segment must be a literal line of
        crawler.render.tsv_artifact_text over the SAME TSV files — the one
        text table-join provenance is checked against."""
        from crawler.render import tsv_artifact_text
        tsv_dir = CASCADE_FIXTURES / "docling" / "su-fees"
        paths = sorted(tsv_dir.glob("*.tsv"))
        artifact_lines = tsv_artifact_text(
            [p.read_text() for p in paths]).split("\n")
        source = cascade.TableSource.from_tsv_files("docling-tsv-su-fees",
                                                    paths)
        join = config.load_configs_dir(CONFIGS_DIR)["SofiaUniversity"] \
            .sources["su-fees"].join
        for alias in ("Компютърни науки", "Статистика", "Психология"):
            r = cascade.fee_row_join("tuition", source, join, alias)
            self.assertIsNotNone(r, alias)
            for segment in r.segments:
                self.assertIn(segment, artifact_lines)


class TestSectionedFeeJoin(unittest.TestCase):
    JOIN = config.SectionedFeeRowJoin(
        name="test",
        sections=(
            config.SectionSpec(track="обучение на български език",
                               match="обучение на български език"),
            config.SectionSpec(track="Обучение на английски език",
                               match="Обучение на английски език"),
            config.SectionSpec(track="чл. 95 (чуждестранни)",
                               match="по чл. 95", foreign=True),
        ),
        fee_pattern=r"\b(\d{3,4})\b",
        currency_suffix="евро")

    TABLE = [
        ["", "обучение на български език (в евро)", ""],
        ["1", "Тестова специалност", "500"],
        ["", "Чуждестранни граждани по чл. 95 (в евро)", ""],
        ["2", "Тестова специалност", "900"],
    ]

    def test_fee_comes_from_first_hit_with_header_segment(self):
        r = cascade.sectioned_fee_join("tuition", table_source(self.TABLE),
                                       self.JOIN, "Тестова специалност")
        self.assertEqual(r.value, "500 евро")
        self.assertEqual(list(r.segments),
                         ["обучение на български език (в евро)",
                          "1 Тестова специалност 500"])

    def test_foreign_sections_never_become_language_tracks(self):
        r = cascade.sectioned_language_join(table_source(self.TABLE),
                                            self.JOIN, "Тестова специалност")
        self.assertEqual(r.value, "обучение на български език")
        self.assertEqual(r.context["tracks"],
                         ["обучение на български език"])


class TestOrdinanceJoin(unittest.TestCase):
    def test_layout_surface_is_required_loudly(self):
        join = config.OrdinanceJoin(name="su",
                                    row_pattern="^{alias}\\s{2,}(изпит)",
                                    row_value_group=1)
        source = cascade.TextSource(ref="pdftext-x", text="whatever")
        with self.assertRaises(ValueError) as ctx:
            cascade.ordinance_join(source, join, "Тест")
        self.assertIn("layout", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class SuppressLabelsHarvestTest(unittest.TestCase):
    """harvest_labels(skip=...) disables exactly the named label ids."""

    SRC = cascade.TextSource(
        "html:https://x.example/p",
        "Curriculum overview Semester fee: 1100 leva Admission")

    def test_unsuppressed_label_still_fires(self):
        r = cascade.harvest_labels("tuition", self.SRC)
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "1100 leva")

    def test_suppressed_label_yields_none(self):
        r = cascade.harvest_labels("tuition", self.SRC,
                                   skip=("en-semfee-label",))
        self.assertIsNone(r)


class BgLanguageLabelTest(unittest.TestCase):
    """The BG structured label outranks the EN boilerplate prose match
    (the 2026-08-17 VUM benchmark wrong: a Bulgarian-taught program
    shipped "English" from leftover EN copy)."""

    def test_bg_label_wins_over_en_boilerplate(self):
        src = cascade.TextSource(
            "html:https://x.example/bg",
            "Език на преподаване \\nБЪЛГАРСКИ\\n Начало на учебния процес "
            "... The courses are taught entirely in English.")
        r = cascade.harvest_labels("language", src)
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "БЪЛГАРСКИ")
        self.assertEqual(r.method, "label:bg-lang-label")


class ProgrammeLevelLabelTest(unittest.TestCase):
    """The title-only degree fallback must never pre-empt a real labelled
    degree statement -- it is weaker evidence and is ordered last."""

    def test_title_level_is_read_when_nothing_else_states_it(self):
        src = cascade.TextSource(
            "html:https://ecatalog.example/p",
            "Анимационно кино - бакалавърска програма НБУ Кратко представяне")
        r = cascade.harvest_labels("degree", src)
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "бакалавърска програма")
        self.assertEqual(r.method, "label:bg-programme-level")

    def test_a_labelled_degree_statement_still_wins(self):
        src = cascade.TextSource(
            "html:https://ecatalog.example/p",
            'Нещо - магистърска програма НБУ. Степен: ОКС „магистър"')
        r = cascade.harvest_labels("degree", src)
        self.assertEqual(r.method, "label:bg-oks-label")


class ProgramRegionTest(unittest.TestCase):
    """program_region(text, name, sibling_names) -> ordered [(start, end)]
    spans of the page a program's tier-G harvest may read on a SHARED page.

    Shapes are lifted from measured contamination (2026-08-22 benchmark):
    nav listings repeat names before the real heading (UniRuse), a
    program mentioned inside a sibling's sentence must not open the
    whole rest of the page (MUVarna), and same-named variants must not
    bound each other (VUM pb/b twins)."""

    def test_span_runs_from_name_to_next_distinct_sibling(self):
        text = "intro АЛФА alpha body БЕТА beta body"
        spans = cascade.program_region(text, "АЛФА", ["БЕТА"])
        self.assertEqual(spans, [(6, 22)])  # «АЛФА alpha body » up to БЕТА

    def test_last_program_extends_to_end_of_text(self):
        text = "intro АЛФА alpha body БЕТА beta body"
        spans = cascade.program_region(text, "БЕТА", ["АЛФА"])
        self.assertEqual(spans, [(22, 36)])

    def test_every_occurrence_anchors_a_span_nav_then_heading(self):
        # UniRuse shape: the name appears in a nav list (alongside its
        # sibling) before its real heading. Both occurrences anchor
        # spans; the nav span is tiny, the real one carries the content.
        text = "nav: БЕТА АЛФА | БЕТА real content АЛФА end"
        spans = cascade.program_region(text, "БЕТА", ["АЛФА"])
        self.assertEqual(spans, [(5, 10), (17, 35)])

    def test_absent_name_yields_no_spans(self):
        self.assertEqual(
            cascade.program_region("no such program here", "АЛФА", ["БЕТА"]),
            [])

    def test_matching_is_case_insensitive(self):
        # MUVarna shape: config says «Медицина», the heading shouts
        # «МЕДИЦИНА».
        text = "преди МЕДИЦИНА тяло на секцията АКУШЕРКА след"
        spans = cascade.program_region(text, "Медицина", ["Акушерка"])
        self.assertEqual(spans, [(6, 32)])

    def test_same_named_sibling_is_not_a_boundary(self):
        # VUM shape: pb/b variants share one name on one page — the twin
        # must not truncate the region.
        text = "ЗАГЛАВИЕ Международен бизнес body body"
        spans = cascade.program_region(
            text, "Международен бизнес", ["Международен бизнес"])
        self.assertEqual(spans, [(9, len(text))])

    def test_span_is_capped_at_the_region_window(self):
        # A span with no following sibling must NOT run to the end of an
        # arbitrarily long page: configured programs do not tile real
        # pages, and the measured UniRuse leak sat 3,602 chars past the
        # last configured heading. Proximity is the attribution signal.
        text = "АЛФА " + ("x" * (cascade.REGION_WINDOW + 500))
        spans = cascade.program_region(text, "АЛФА", ["БЕТА"])
        self.assertEqual(spans, [(0, cascade.REGION_WINDOW)])

    def test_a_near_sibling_bounds_tighter_than_the_window(self):
        text = "АЛФА тяло БЕТА " + ("y" * cascade.REGION_WINDOW)
        spans = cascade.program_region(text, "АЛФА", ["БЕТА"])
        self.assertEqual(spans, [(0, 10)])

    def test_name_inside_a_longer_word_does_not_anchor(self):
        # measured on ANIS: «Финанси» matched inside «финансиране» and
        # «финансите», opening a span next to another block's degree
        # text 7,600 chars from the program's real section.
        text = "проекти – финансиране и контрол върху финансите на фонда"
        self.assertEqual(cascade.program_region(text, "Финанси", ["Бета"]),
                         [])

    def test_name_inside_a_longer_word_does_not_bound_either(self):
        # a sibling name embedded in a longer word must not truncate
        # a real span
        text = "АЛФА тяло с финансиране на дейността и още текст"
        spans = cascade.program_region(text, "АЛФА", ["Финанси"])
        self.assertEqual(spans, [(0, len(text))])

    def test_overlapping_spans_merge(self):
        # two adjacent occurrences of the name with no boundary between
        # them collapse into one span, not two overlapping ones
        text = "АЛФА x АЛФА y БЕТА z"
        spans = cascade.program_region(text, "АЛФА", ["БЕТА"])
        self.assertEqual(spans, [(0, 14)])


def _shared_site(programs):
    return config.parse_site_config(
        {"uni_id": "X", "sources": {}, "programs": programs},
        origin="shared-page-test")


class SharedPageAttributionTest(unittest.TestCase):
    """On a page shared by >=2 configured programs, tier-G harvest reads
    only the program's own region (program_region). A label match that
    exists on the page but outside the region must NOT ship — that is
    exactly the measured MUVarna/UniRuse contamination.

    Fixture texts are miniatures of the real pages the defects shipped
    from; expected values come from the labeller's frozen keys."""

    URL = "https://x.example/specialnosti"

    def _docs(self, text):
        return {self.URL: cascade.TextSource(ref="html:" + self.URL,
                                             text=text)}

    def test_out_of_region_match_does_not_ship(self):
        # UniRuse shape: two bachelor programs share the page; the only
        # «ОКС „...“» text lives in a third, unconfigured master's block.
        site = _shared_site([
            {"id": "se", "name": "Софтуерно инженерство", "page": self.URL},
            {"id": "cs", "name": "Компютърни науки", "page": self.URL},
        ])
        # geometry mirrors the measured page: the poison block sat
        # thousands of chars past the last configured heading (5,703 on
        # the real UniRuse page), well outside the attribution window.
        filler = "друга неконфигурирана специалност и общи текстове. " * 80
        text = ("Специалности: Софтуерно инженерство Компютърни науки | "
                "СОФТУЕРНО ИНЖЕНЕРСТВО студентите се обучават по съвременни "
                "езици и среди за програмиране. "
                "КОМПЮТЪРНИ НАУКИ изучават се алгоритми и структури от данни. "
                + filler +
                "МАГИСТЪРСКА ФИЛОЛОГИЯ Завършилите получават диплома за "
                'ОКС „магистър" с квалификация „магистър по филология".')
        self.assertGreater(text.find("ОКС"),
                           text.find("КОМПЮТЪРНИ") + cascade.REGION_WINDOW)
        docs = self._docs(text)
        for pid in ("se", "cs"):
            p = next(x for x in site.programs if x.id == pid)
            r = cascade.resolve_field(site, p, "degree", docs)
            self.assertIsNone(
                r, "out-of-region ОКС text leaked into {0}: {1!r}".format(
                    pid, getattr(r, "value", None)))

    def test_in_region_match_ships_and_stays_with_its_program(self):
        site = _shared_site([
            {"id": "a", "name": "Медицина", "page": self.URL},
            {"id": "b", "name": "Акушерка", "page": self.URL},
        ])
        text = ("МЕДИЦИНА студентите придобиват образователно-квалификационна "
                'степен „магистър" след шест години. '
                "АКУШЕРКА обучението е практическо и няма посочена степен тук.")
        docs = self._docs(text)
        a = next(x for x in site.programs if x.id == "a")
        b = next(x for x in site.programs if x.id == "b")
        ra = cascade.resolve_field(site, a, "degree", docs)
        self.assertIsNotNone(ra)
        self.assertIn("магистър", ra.value)
        # the sibling must NOT inherit Медицина's degree
        self.assertIsNone(cascade.resolve_field(site, b, "degree", docs))

    def test_sole_program_page_is_not_scoped(self):
        site = _shared_site([
            {"id": "only", "name": "Право", "page": self.URL},
        ])
        # the program's name never appears in the text — on a sole page
        # that must not matter (today's behavior, preserved)
        text = 'Тук се придобива ОКС „магистър" по право.'
        p = site.programs[0]
        r = cascade.resolve_field(site, p, "degree", self._docs(text))
        self.assertIsNotNone(r)

    def test_explicitly_routed_pages_stay_unscoped(self):
        # adm_page is deliberate human routing (VUM/AUBG wire page-wide
        # admission sources on purpose) — never region-scoped, even when
        # the main page is shared.
        adm = "https://x.example/how-to-apply"
        site = _shared_site([
            {"id": "a", "name": "Алфа", "page": self.URL, "adm_page": adm},
            {"id": "b", "name": "Бета", "page": self.URL, "adm_page": adm},
        ])
        docs = self._docs("Алфа текст. Бета текст.")
        docs[adm] = cascade.TextSource(
            ref="html:" + adm,
            text=("Applicants must demonstrate English language proficiency "
                  "equivalent to IELTS 6.5 or above"))
        for pid in ("a", "b"):
            p = next(x for x in site.programs if x.id == pid)
            r = cascade.resolve_field(site, p, "admission", docs)
            self.assertIsNotNone(r, pid)
            self.assertIn("IELTS", r.value)

    def test_first_span_value_beats_later_span_poison(self):
        # MUVarna Медицина shape: the program's own section (first span)
        # carries a correct окс-степен statement; the program's name is
        # ALSO mentioned inside a later joint-program sentence whose
        # «ОКС „бакалавър"» must not win even though bg-oks-inline is
        # ordered before bg-okstepen in the pattern list.
        site = _shared_site([
            {"id": "med", "name": "Медицина", "page": self.URL},
            {"id": "mil", "name": "Военно осигуряване", "page": self.URL},
        ])
        text = ("МЕДИЦИНА придобиват образователно-квалификационна степен "
                '„магистър" след пълния курс. '
                "Военно осигуряване приемът на курсанти за степени по "
                'специалността „Медицина" от МУ и ОКС „бакалавър" по '
                "военното дело е отделен.")
        med = next(x for x in site.programs if x.id == "med")
        r = cascade.resolve_field(site, med, "degree", self._docs(text))
        self.assertIsNotNone(r)
        self.assertIn("магистър", r.value)
        self.assertNotIn("бакалавър", r.value)

    def test_program_never_named_on_shared_page_ships_nothing(self):
        site = _shared_site([
            {"id": "a", "name": "Алфа", "page": self.URL},
            {"id": "b", "name": "Бета", "page": self.URL},
        ])
        text = 'Алфа: придобива се ОКС „магистър". И нищо за другата.'
        b = next(x for x in site.programs if x.id == "b")
        self.assertIsNone(cascade.resolve_field(site, b, "degree",
                                                self._docs(text)))

    def test_suppress_labels_still_applies_inside_a_region(self):
        site = _shared_site([
            {"id": "a", "name": "Алфа", "page": self.URL,
             "suppress_labels": {"tuition": ["en-semfee-label"]}},
            {"id": "b", "name": "Бета", "page": self.URL},
        ])
        text = "Алфа Semester fee: 1100 leva. Бета друг текст."
        a = next(x for x in site.programs if x.id == "a")
        self.assertIsNone(cascade.resolve_field(site, a, "tuition",
                                                self._docs(text)))


class DegreeFromNameTest(unittest.TestCase):
    """degree_from_name: the ANIS shape — a sentence that names BOTH the
    degree level and the program («придобиват образователната степен
    „магистър“ по специалността „Криминалистика“») is attributed by
    construction, wherever it sits on a shared page."""

    def test_sentence_naming_the_program_yields_its_level(self):
        src = cascade.TextSource(
            "html:https://x.example/m",
            "Успешно завършилите студенти придобиват образователната "
            "степен „магистър“ по специалността „Криминалистика“. "
            "Киберсигурност Специалност „Киберсигурност“ обучава ...")
        r = cascade.degree_from_name("Криминалистика", src)
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "магистър")
        self.assertEqual(r.tier, cascade.TIER_G)

    def test_sentence_naming_another_program_does_not_fire(self):
        src = cascade.TextSource(
            "html:https://x.example/m",
            "придобиват образователната степен „магистър“ по "
            "специалността „Финанси“.")
        self.assertIsNone(cascade.degree_from_name("Криминалистика", src))

    def test_okvalifikacionna_variant_also_matches(self):
        src = cascade.TextSource(
            "html:https://x.example/m",
            "завършилите придобиват образователно-квалификационна степен "
            "„магистър“ по специалност „Бизнес администрация“.")
        r = cascade.degree_from_name("Бизнес администрация", src)
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "магистър")


class DegreeFromNameResolveTest(unittest.TestCase):
    """resolve_field falls back to the name-adjacent rule when a shared
    page's region yields nothing — the measured ANIS regression risk:
    region scoping alone would have turned 15 correct degrees to null."""

    URL = "https://x.example/priem"

    def test_region_empty_but_own_sentence_ships(self):
        site = _shared_site([
            {"id": "m03", "name": "Криминалистика", "page": self.URL},
            {"id": "m11", "name": "Финанси", "page": self.URL},
        ])
        text = ("Криминалистика Специалност „Криминалистика“ обучава "
                "висококвалифицирани специалисти. Успешно завършилите "
                "придобиват образователната степен „магистър“ по "
                "специалността „Криминалистика“. "
                "Финанси Специалност „Финанси“ обучава икономисти без "
                "посочена степен в блока.")
        m03 = next(x for x in site.programs if x.id == "m03")
        m11 = next(x for x in site.programs if x.id == "m11")
        docs = {self.URL: cascade.TextSource(ref="html:" + self.URL,
                                             text=text)}
        r = cascade.resolve_field(site, m03, "degree", docs)
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "магистър")
        # Финанси has no degree statement of its own -> honest null,
        # never Криминалистика's value
        self.assertIsNone(cascade.resolve_field(site, m11, "degree", docs))


class ProgramRegionOverlapTest(unittest.TestCase):
    """Longest-name-wins where names nest: «Финанси» is a whole word
    inside «Международни финанси», so a naive word-boundary match anchors
    the short-named program at its SIBLING's heading (review finding,
    2026-08-22; latent live case: SHU's «Английска филология» vs
    «Английска филология: Лингвистика и превод»)."""

    def test_short_name_does_not_anchor_inside_longer_sibling_name(self):
        text = "МЕЖДУНАРОДНИ ФИНАНСИ секция на другата програма и още текст"
        self.assertEqual(
            cascade.program_region(text, "Финанси",
                                   ["Международни финанси"]),
            [])

    def test_longer_sibling_occurrence_does_not_bound_my_span(self):
        # converse: the short sibling name inside MY longer heading must
        # not truncate my own span at my own heading
        text = "МЕЖДУНАРОДНИ ФИНАНСИ моята секция и съдържание"
        spans = cascade.program_region(text, "Международни финанси",
                                       ["Финанси"])
        self.assertEqual(spans, [(0, len(text))])

    def test_short_name_still_anchors_at_its_own_heading(self):
        text = "МЕЖДУНАРОДНИ ФИНАНСИ чужда секция. ФИНАНСИ моята секция тук"
        spans = cascade.program_region(text, "Финанси",
                                       ["Международни финанси"])
        self.assertEqual(spans, [(35, 59)])

    def test_suffix_embedded_name_does_not_anchor(self):
        # pins the BEFORE-char half of the word-boundary guard: «бизнес»
        # as the tail of «агробизнес» is not an occurrence
        text = "секция за агробизнес и земеделие"
        self.assertEqual(cascade.program_region(text, "бизнес", ["Бета"]),
                         [])

    def test_case_expanding_characters_degrade_safely(self):
        # str.lower() can change string length ('İ' -> 2 chars); indexes
        # from the lowered text must never mis-slice the original
        text = ("İ" * 4) + " АЛФА тяло БЕТА край"
        spans = cascade.program_region(text, "АЛФА", ["БЕТА"])
        for s0, e0 in spans:
            self.assertEqual(text[s0:s0 + 4], "АЛФА")


class DegreeFromNameBoundaryTest(unittest.TestCase):
    def test_name_must_end_at_a_word_boundary(self):
        # «Финанси» must not match inside «Финансови науки»
        src = cascade.TextSource(
            "html:https://x.example/m",
            "придобиват образователната степен „магистър“ по "
            "специалността „Финансови науки“.")
        self.assertIsNone(cascade.degree_from_name("Финанси", src))

    def test_resolve_ships_via_the_name_rule_method(self):
        # pins that the ANIS recovery actually flows through
        # degree-from-name (review: the earlier test asserted only value)
        site = _shared_site([
            {"id": "a", "name": "Криминалистика", "page": "https://x/p"},
            {"id": "b", "name": "Финанси", "page": "https://x/p"},
        ])
        text = ("Криминалистика блок без етикетни съвпадения. Успешно "
                "завършилите придобиват образователната степен „магистър“ "
                "по специалността „Криминалистика“. Финанси друг блок.")
        a = next(x for x in site.programs if x.id == "a")
        r = cascade.resolve_field(site, a, "degree",
                                  {"https://x/p": cascade.TextSource(
                                      ref="html:x", text=text)})
        self.assertIsNotNone(r)
        self.assertEqual(r.method, "degree-from-name")


class ScopingBypassTest(unittest.TestCase):
    def test_shared_page_duplicated_as_extra_page_stays_scoped(self):
        # config listing the SAME url in extra_pages must not smuggle the
        # unscoped full text back into the harvest
        url = "https://x.example/shared"
        site = _shared_site([
            {"id": "a", "name": "Алфа", "page": url, "extra_pages": [url]},
            {"id": "b", "name": "Бета", "page": url},
        ])
        filler = "неутрален текст на страницата между секциите. " * 60
        text = ("Алфа секция без степен. Бета секция. " + filler +
                'НЕКОНФИГУРИРАНА Завършилите получават ОКС „магистър".')
        a = next(x for x in site.programs if x.id == "a")
        r = cascade.resolve_field(site, a, "degree",
                                  {url: cascade.TextSource(ref="html:s",
                                                           text=text)})
        self.assertIsNone(r)


class ContinuationGuardTest(unittest.TestCase):
    """«могат да продължат образованието си в ОКС „магистър“» states where
    graduates may go NEXT, not this program's award — live on the real
    UniRuse faculty page, where it shipped a master's degree for a
    bachelor program (socped)."""

    def test_continuation_sentence_does_not_ship_as_degree(self):
        src = cascade.TextSource(
            "html:https://x.example/f",
            "Завършилите могат да продължат образованието си в "
            'ОКС „магистър"; да получат второ висше образование.')
        self.assertIsNone(cascade.harvest_labels("degree", src))

    def test_award_sentence_still_ships(self):
        src = cascade.TextSource(
            "html:https://x.example/f",
            "Завършилите успешно студенти получават диплома за "
            'ОКС „магистър" с професионална квалификация.')
        r = cascade.harvest_labels("degree", src)
        self.assertIsNotNone(r)
        self.assertEqual(r.value, 'ОКС „магистър"')


class ScopedEmissionGateTest(unittest.TestCase):
    def test_scoped_emission_passes_the_gate_against_the_full_artifact(self):
        # segments cut from a span view must stay verbatim substrings of
        # the FULL page artifact the ref names (review: this seam was
        # only exercised via frozen configs, which have no shared pages)
        from crawler.provenance import Artifact, Status as PStatus, gate
        url = "https://x.example/shared"
        site = _shared_site([
            {"id": "a", "name": "Медицина", "page": url},
            {"id": "b", "name": "Акушерка", "page": url},
        ])
        text = ("МЕДИЦИНА    придобиват   образователно-квалификационна "
                "степен „магистър“ след курса. АКУШЕРКА друго.")
        a = next(x for x in site.programs if x.id == "a")
        r = cascade.resolve_field(site, a, "degree",
                                  {url: cascade.TextSource(ref="html:s",
                                                           text=text)})
        self.assertIsNotNone(r)
        artifact = Artifact(text=text, renderer_id="t",
                            renderer_version="1", ref="html:s")
        verdict = gate(r.value, list(r.segments), artifact)
        self.assertIs(verdict.status, PStatus.PASS, verdict)


class ScopingBypassOtherRoutesTest(unittest.TestCase):
    def test_adm_page_equal_to_shared_page_stays_scoped(self):
        url = "https://x.example/shared"
        site = _shared_site([
            {"id": "a", "name": "Алфа", "page": url, "adm_page": url},
            {"id": "b", "name": "Бета", "page": url},
        ])
        filler = "неутрален текст между секциите на страницата. " * 60
        text = ("Алфа секция. Бета секция. " + filler +
                "ЧУЖД БЛОК Applicants must demonstrate English language "
                "proficiency equivalent to IELTS 6.5 or above")
        a = next(x for x in site.programs if x.id == "a")
        r = cascade.resolve_field(site, a, "admission",
                                  {url: cascade.TextSource(ref="html:s",
                                                           text=text)})
        self.assertIsNone(r)


class KandidatstvaSeLabelTest(unittest.TestCase):
    """UniRuse family: per-program admission formulas on faculty pages,
    introduced by «Кандидатства се с:» — 20 graded-MISS cells whose
    existence the frozen key proves (labeller quoted these formulas)."""

    def test_formula_is_captured_after_the_marker(self):
        src = cascade.TextSource(
            "html:https://x.example/f",
            "и други. Кандидатства се с: 1) оценка от държавен зрелостен "
            "изпит или един избираем изпит по: математика, български "
            "език, ИИТ или ОТП; 2) оценка от дипломата по математика; "
            "3) оценка от дипломата по български език. Успешно "
            "завършилите се реализират като инженери.")
        r = cascade.harvest_labels("admission", src)
        self.assertIsNotNone(r)
        self.assertTrue(r.value.startswith("1) оценка от държавен"))
        self.assertIn("3) оценка от дипломата по български език", r.value)
        self.assertNotIn("Успешно", r.value)

    def test_no_marker_no_match(self):
        src = cascade.TextSource(
            "html:https://x.example/f",
            "Свободен текст за приема без формулата за балообразуване.")
        self.assertIsNone(cascade.harvest_labels("admission", src))
