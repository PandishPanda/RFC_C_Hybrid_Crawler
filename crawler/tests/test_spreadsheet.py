"""Spreadsheet route + fee-row join over it (ticket 12) -- zero network.

The .xlsx fixtures here are built in-process into real OOXML zips
(_build_xlsx below), derived from the shape of UniRuse's actual published
fee schedule (Zapoved_taksi_2025_2026...xlsx, 11 sheets): a title/preamble
block, a stacked two-level header, one row per specialty, and dash
placeholders where a program isn't offered in that attendance form. Real
values from that workbook are used verbatim, so a change in behaviour
shows up against numbers a human already verified by hand.
"""
import io
import sys
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import render, runner  # noqa: E402
from crawler.cascade import TableSource, fee_row_join  # noqa: E402
from crawler.config import ConfigError, FeeRowJoin, parse_site_config  # noqa: E402
from crawler.provenance import Artifact, Status, gate  # noqa: E402
from crawler.render import RenderError  # noqa: E402

_CONTENT_TYPES = (
    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
    'package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>')

_ROOT_RELS = (
    '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
    '.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
    'schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocum'
    'ent" Target="xl/workbook.xml"/></Relationships>')

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _col_letters(idx):
    letters = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _build_xlsx(sheets):
    """Real OOXML zip from {sheet_name: [[cell, ...], ...]}.

    Every cell is written as an inline string, and EMPTY cells are
    omitted from the row entirely -- which is what real spreadsheet
    writers do, and the reason the reader must address cells by their
    r="B4" ref rather than by position.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)

        sheet_tags, rel_tags = [], []
        for i, name in enumerate(sheets, start=1):
            sheet_tags.append(
                '<sheet name="{0}" sheetId="{1}" r:id="rId{1}"/>'.format(
                    name, i))
            rel_tags.append(
                '<Relationship Id="rId{0}" Type="http://schemas.openxml'
                'formats.org/officeDocument/2006/relationships/worksheet" '
                'Target="worksheets/sheet{0}.xml"/>'.format(i))
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="{0}" xmlns:r="{1}">'
            '<sheets>{2}</sheets></workbook>'.format(
                _MAIN_NS, _REL_NS, "".join(sheet_tags)))
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships">{0}'
            '</Relationships>'.format("".join(rel_tags)))

        for i, rows in enumerate(sheets.values(), start=1):
            row_xml = []
            for r, row in enumerate(rows, start=1):
                cells = []
                for c, value in enumerate(row):
                    if value == "":
                        continue  # omitted, exactly like a real writer
                    cells.append(
                        '<c r="{0}{1}" t="inlineStr"><is><t>{2}</t></is>'
                        '</c>'.format(_col_letters(c), r, value))
                if cells:
                    row_xml.append('<row r="{0}">{1}</row>'.format(
                        r, "".join(cells)))
            zf.writestr(
                "xl/worksheets/sheet{0}.xml".format(i),
                '<?xml version="1.0"?><worksheet xmlns="{0}"><sheetData>{1}'
                '</sheetData></worksheet>'.format(_MAIN_NS, "".join(row_xml)))
    return buf.getvalue()


# Two sheets in the same shape and order as UniRuse's real workbook:
# Приложение 1 (state-subsidized) BEFORE Приложение 2 (paid places).
_TITLE_1 = ("СЕМЕСТРИАЛНИ ТАКСИ съгласно Решение № 362 от 05.06.2025 г. на "
            "Министерския съвет, за специалностите, по които Русенският "
            "университет ще провежда обучение на държавна издръжка")
_TITLE_2 = ("СЕМЕСТРИАЛНИ ТАКСИ съгласно Решение на Академичния съвет "
            "№ 8.15, за специалностите срещу заплащане")

_FEE_SHEETS = {
    "Прил. 1": [
        ["", "", "Приложение 1"],
        [_TITLE_1],
        ["№", "СПЕЦИАЛНОСТ", "Семестриална такса, лв./евро"],
        ["", "", "Редовно обучение", "", "Задочно обучение"],
        ["", "", "лв.", "евро", "лв.", "евро"],
        ["4", "Бизнес мениджмънт", "400", "204.51", "-", ""],
        ["9", "Електроенергетика и електрообзавеждане", "470", "240.30",
         "470", "240.30"],
        ["37", "Публична администрация", "-", "", "400", "204.51"],
    ],
    "Прил. 2": [
        ["", "", "Приложение 2"],
        [_TITLE_2],
        ["№", "ПН", "СПЕЦИАЛНОСТ", "Семестриална такса, лв./евро"],
        ["", "", "", "Редовно обучение", "", "Дистанционна форма"],
        ["", "", "", "лв.", "евро", "лв.", "евро"],
        ["10", "307", "Бизнес мениджмънт", "1320", "674.90", "760", "388.58"],
        ["11", "307", "Дигитален мениджмънт и иновации", "-", "", "760",
         "388.58"],
    ],
}


def _fee_join(**kw):
    base = dict(name="uniruse-fees", match_header="специалност",
                value_headers=("редовно", "лв."), value_pattern=None,
                context={})
    base.update(kw)
    return FeeRowJoin(**base)


class SpreadsheetGridsTest(unittest.TestCase):
    def setUp(self):
        self.grids = render.spreadsheet_grids(_build_xlsx(_FEE_SHEETS))

    def test_one_table_per_sheet_in_workbook_order(self):
        self.assertEqual(len(self.grids), 2)
        self.assertIn("Приложение 1", " ".join(self.grids[0][0]))
        self.assertIn("Приложение 2", " ".join(self.grids[1][0]))

    def test_omitted_empty_cells_do_not_shift_later_columns_left(self):
        # The regression this addressing exists for: "Публична
        # администрация" has an EMPTY euro cell after its "-" lv cell.
        # Read positionally, its 400 would land in the full-time column
        # and ship as a state-subsidized full-time fee that does not
        # exist.
        row = next(r for t in self.grids for r in t
                   if r and r[1:2] == ("Публична администрация",))
        self.assertEqual(row[2], "-")
        self.assertEqual(row[4], "400")

    def test_not_a_zip_raises_render_error(self):
        with self.assertRaises(RenderError):
            render.spreadsheet_grids(b"this is not a workbook")

    def test_artifact_text_is_the_same_grid_view_the_gate_checks(self):
        text = render.grid_artifact_text(self.grids)
        self.assertIn("Бизнес мениджмънт 400", text)
        self.assertIn("Публична администрация", text)


class SpreadsheetRouteTest(unittest.TestCase):
    def test_render_routes_spreadsheet_and_stamps_renderer_identity(self):
        artifact = render.render(_build_xlsx(_FEE_SHEETS), "",
                                 render.ROUTE_SPREADSHEET, ref="xlsx:fees")
        self.assertEqual(artifact.renderer_id, render.RENDERER_SPREADSHEET)
        self.assertEqual(artifact.ref, "xlsx:fees")
        self.assertIn("Бизнес мениджмънт", artifact.text)

    def test_spreadsheet_is_never_sniffed_only_config_opt_in(self):
        # Same discipline as table-pdf: an .xlsx with no route_hint must
        # not be guessed into the spreadsheet route.
        with self.assertRaises(RenderError):
            render.render(_build_xlsx(_FEE_SHEETS), "application/octet-stream")


class SpreadsheetFeeJoinTest(unittest.TestCase):
    def setUp(self):
        grids = render.spreadsheet_grids(_build_xlsx(_FEE_SHEETS))
        self.source = TableSource(ref="xlsx:uniruse-fees", tables=grids)

    def test_resolves_a_state_subsidised_full_time_fee(self):
        ex = fee_row_join("tuition", self.source, _fee_join(),
                          "Бизнес мениджмънт")
        self.assertIsNotNone(ex)
        self.assertEqual(ex.value, "400")

    def test_state_subsidised_sheet_wins_over_the_paid_sheet(self):
        # Бизнес мениджмънт is in BOTH sheets (400 subsidised / 1320
        # paid). Sheets are in workbook order and the join returns on
        # first match, so the documented default rule -- subsidised
        # where it exists -- holds without a separate precedence
        # mechanism.
        ex = fee_row_join("tuition", self.source, _fee_join(),
                          "Бизнес мениджмънт")
        self.assertEqual(ex.value, "400")
        self.assertNotEqual(ex.value, "1320")

    def test_program_absent_from_the_subsidised_sheet_falls_through_to_paid(self):
        # A join's value_headers live on the SOURCE, shared by every
        # program joining it -- so this is NOT the same source as the
        # full-time one above. The realistic wiring for a workbook whose
        # programs are offered in different attendance forms is one
        # source per form, all pointing at the same URL; see
        # RealisticTwoSourceWiringTest below for that config end to end.
        ex = fee_row_join("tuition", self.source,
                          _fee_join(value_headers=("дистанционна", "лв.")),
                          "Дигитален мениджмънт и иновации")
        self.assertIsNotNone(ex)
        self.assertEqual(ex.value, "760")

    def test_one_shared_join_cannot_serve_programs_in_different_forms(self):
        # The honest limit behind the note above: with the full-time
        # join, a distance-only program resolves to nothing. It must not
        # silently read a neighbouring column to find *a* number.
        ex = fee_row_join("tuition", self.source, _fee_join(),
                          "Дигитален мениджмънт и иновации")
        self.assertIsNone(ex)

    def test_a_dash_placeholder_is_never_shipped_as_a_fee(self):
        # "Публична администрация" prints "-" in the full-time column
        # (no full-time seats). A dash IS verbatim in the artifact, so
        # gate() would PASS it -- it has to be refused at the resolver.
        ex = fee_row_join("tuition", self.source, _fee_join(),
                          "Публична администрация")
        self.assertIsNone(ex)

    def test_the_same_program_resolves_in_the_form_it_is_actually_offered(self):
        ex = fee_row_join("tuition", self.source,
                          _fee_join(value_headers=("задочно", "лв.")),
                          "Публична администрация")
        self.assertIsNotNone(ex)
        self.assertEqual(ex.value, "400")

    def test_a_prose_title_mentioning_the_match_header_never_becomes_the_header(self):
        # Both fixture titles contain "специалностите". If the title row
        # were absorbed into the header zone, column 0 would resolve as
        # the match column and every lookup would read the wrong cell.
        ex = fee_row_join("tuition", self.source, _fee_join(),
                          "Електроенергетика и електрообзавеждане")
        self.assertIsNotNone(ex)
        self.assertEqual(ex.value, "470")

    def test_segment_is_the_verbatim_row_line_from_the_artifact(self):
        ex = fee_row_join("tuition", self.source, _fee_join(),
                          "Бизнес мениджмънт")
        artifact_text = render.grid_artifact_text(self.source.tables)
        self.assertIn(ex.segments[0], artifact_text)


class RealisticTwoSourceWiringTest(unittest.TestCase):
    """The config a human would actually write for UniRuse's workbook:
    one source PER ATTENDANCE FORM, all pointing at the same .xlsx,
    because value_headers lives on the source and its programs are
    offered in different forms. Proves the ticket's headline case
    (Бизнес мениджмънт 400 / Дигитален мениджмънт и иновации 760) holds
    under one loadable config, not by swapping a join per call."""

    CONFIG = {
        "uni_id": "UniRuseTest",
        "sources": {
            "fees-full-time": {
                "url": "https://x.example/fees.xlsx",
                "route": "spreadsheet",
                "join": {"kind": "fee-row", "name": "fees-full-time",
                         "match_header": "специалност",
                         "value_headers": ["редовно", "лв."]},
            },
            "fees-distance": {
                "url": "https://x.example/fees.xlsx",
                "route": "spreadsheet",
                "join": {"kind": "fee-row", "name": "fees-distance",
                         "match_header": "специалност",
                         "value_headers": ["дистанционна", "лв."]},
            },
        },
        "programs": [
            {"id": "bizmgmt", "name": "Бизнес мениджмънт",
             "page": "https://x.example/bizmgmt",
             "tuition_join": {"source": "fees-full-time",
                             "alias": "Бизнес мениджмънт"}},
            {"id": "digmgmt", "name": "Дигитален мениджмънт и иновации",
             "page": "https://x.example/digmgmt",
             "tuition_join": {"source": "fees-distance",
                             "alias": "Дигитален мениджмънт и иновации"}},
        ],
    }

    def setUp(self):
        self.site = parse_site_config(self.CONFIG, origin="<test>")
        grids = render.spreadsheet_grids(_build_xlsx(_FEE_SHEETS))
        self.grids = grids

    def _resolve(self, program_id):
        program = self.site.program(program_id)
        join_ref = program.tuition_join
        source = self.site.sources[join_ref.source]
        return fee_row_join(
            "tuition",
            TableSource(ref="xlsx:" + source.id, tables=self.grids),
            source.join, join_ref.alias)

    def test_two_sources_on_the_same_url_load_as_valid_config(self):
        self.assertEqual(self.site.sources["fees-full-time"].url,
                         self.site.sources["fees-distance"].url)

    def test_each_program_resolves_its_own_forms_fee_under_one_config(self):
        self.assertEqual(self._resolve("bizmgmt").value, "400")
        self.assertEqual(self._resolve("digmgmt").value, "760")


class RunnerBuildDocsTest(unittest.TestCase):
    """Regression: every test above builds a TableSource by hand, so
    none exercised the seam the real pipeline uses -- runner.build_docs
    turning a resolved doc into the cascade's source. It branched on the
    literal route name "table-pdf", so a spreadsheet source arrived at
    the column-aware join as a TextSource and the whole run died with
    `TypeError: fee-row join needs a TableSource`. Found only by running
    the real pipeline, exactly like ticket 02's candidate_docs bug."""

    class _FakeResolved:
        def __init__(self, ref, route, tables, text):
            self.ref = ref
            self.route = route
            self.tables = tables
            self.layout = None
            self.source_url = "https://x.example/fees.xlsx"
            self.retrieved_at = "2026-08-16T00:00:00Z"
            self.sha256 = "abc"
            self.artifact = SimpleNamespace(
                text=text, renderer_id="xlsx-sheet-grids",
                renderer_version="1")

    class _FakeStore:
        def __init__(self, resolved):
            self._resolved = resolved

        def resolve(self, url, route, cookies=None, source_id=None,
                    label=None, want_grid=False):
            return self._resolved

    def test_a_spreadsheet_doc_becomes_a_TableSource_not_a_TextSource(self):
        grids = render.spreadsheet_grids(_build_xlsx(_FEE_SHEETS))
        site = parse_site_config(
            RealisticTwoSourceWiringTest.CONFIG, origin="<test>")
        resolved = self._FakeResolved(
            "xlsx:fees-full-time", "spreadsheet", grids,
            render.grid_artifact_text(grids))
        report = {"documents": [], "document_failures": []}
        docs = runner.build_docs(site, self._FakeStore(resolved),
                                 replay=False, report=report)
        self.assertTrue(docs, "build_docs produced nothing")
        for key, source in docs.items():
            self.assertIsInstance(
                source, TableSource,
                "{0!r} must reach the column-aware join as a "
                "TableSource".format(key))

    def test_the_wired_join_resolves_through_build_docs_end_to_end(self):
        grids = render.spreadsheet_grids(_build_xlsx(_FEE_SHEETS))
        site = parse_site_config(
            RealisticTwoSourceWiringTest.CONFIG, origin="<test>")
        resolved = self._FakeResolved(
            "xlsx:fees-full-time", "spreadsheet", grids,
            render.grid_artifact_text(grids))
        report = {"documents": [], "document_failures": []}
        docs = runner.build_docs(site, self._FakeStore(resolved),
                                 replay=False, report=report)
        program = site.program("bizmgmt")
        source = docs[program.tuition_join.source]
        ex = fee_row_join("tuition", source,
                          site.sources[program.tuition_join.source].join,
                          program.tuition_join.alias)
        self.assertIsNotNone(ex)
        self.assertEqual(ex.value, "400")


class SpreadsheetConfigWiringTest(unittest.TestCase):
    def _config(self, route):
        return {
            "uni_id": "X",
            "sources": {
                "fees": {
                    "url": "https://x.example/fees.xlsx",
                    "route": route,
                    "join": {
                        "kind": "fee-row",
                        "name": "x-fees",
                        "match_header": "специалност",
                        "value_headers": ["редовно", "лв."],
                    },
                },
            },
            "programs": [{
                "id": "p1", "name": "P1", "page": "https://x.example/p1",
                "tuition_join": {"source": "fees", "alias": "P1"},
            }],
        }

    def test_a_spreadsheet_source_can_carry_a_fee_row_join(self):
        site = parse_site_config(self._config("spreadsheet"), origin="<test>")
        self.assertEqual(site.sources["fees"].route, "spreadsheet")
        self.assertEqual(site.programs[0].tuition_join.source, "fees")

    def test_a_grid_join_on_a_flow_text_route_is_rejected_at_load_time(self):
        # Without this the mismatch surfaces as a TypeError deep in the
        # resolver at refresh time; config.py's stated philosophy is that
        # tier-F wiring must break visibly, at load. html left this list
        # deliberately (fill-rate ticket 01): it is text-first but
        # grid-capable — its <table> elements resolve as cell grids when
        # a grid join asks (test_html_grids.py owns that contract).
        for route in ("prose-pdf",):
            with self.assertRaises(ConfigError):
                parse_site_config(self._config(route), origin="<test>")


if __name__ == "__main__":
    unittest.main()


class TableMarkerFundingBandTest(unittest.TestCase):
    """A workbook prices the SAME program several times -- once per funding
    band (state-subsidised / paid / second-higher-education / ...), in
    separate sheets whose column headers are spelled identically. Without
    naming which table it may read, a fee-row join takes the first hit in
    document order and ships a gate-green number from the wrong band.

    Measured on University of Ruse's real published workbook 2026-08-16:
    value_headers ["задочна","лв."] + "Бизнес мениджмънт" resolves to 850
    from Приложение 5 (второ висше образование). The live config escaped
    that only because it happened to use the neuter "задочно", which
    appears in Приложение 1 alone."""

    STATE = ('СЕМЕСТРИАЛНИ ТАКСИ на държавна издръжка за учебната '
             '2025/2026 г.')
    PAID = ('СЕМЕСТРИАЛНИ ТАКСИ срещу заплащане за учебната '
            '2025/2026 г.')

    SHEETS = {
        "1": [
            ["", "", "Приложение 1"],
            [STATE],
            ["№", "СПЕЦИАЛНОСТ", "Семестриална такса"],
            ["", "", "Задочно обучение"],
            ["", "", "лв."],
            ["4", "Бизнес мениджмънт", "400"],
        ],
        "5": [
            ["", "", "Приложение 5"],
            [PAID],
            ["№", "СПЕЦИАЛНОСТ", "Семестриална такса"],
            ["", "", "Задочно обучение"],
            ["", "", "лв."],
            ["12", "Бизнес мениджмънт", "850"],
        ],
    }

    def setUp(self):
        self.source = TableSource(
            ref="xlsx:fees",
            tables=render.spreadsheet_grids(_build_xlsx(self.SHEETS)))

    def _join(self, **kw):
        base = dict(name="fees", match_header="специалност",
                    value_headers=("задочно", "лв."), value_pattern=None,
                    context={})
        base.update(kw)
        return FeeRowJoin(**base)

    def test_without_a_marker_the_first_table_wins_and_the_band_is_unrecorded(self):
        ex = fee_row_join("tuition", self.source, self._join(),
                          "Бизнес мениджмънт")
        self.assertEqual(ex.value, "400")
        self.assertNotIn("funding", ex.context)

    def test_a_marker_selects_the_band_and_the_other_table_is_unreachable(self):
        state = fee_row_join("tuition", self.source,
                             self._join(table_marker=self.STATE,
                                        funding="на държавна издръжка"),
                             "Бизнес мениджмънт")
        paid = fee_row_join("tuition", self.source,
                            self._join(table_marker=self.PAID,
                                       funding="срещу заплащане"),
                            "Бизнес мениджмънт")
        self.assertEqual(state.value, "400")
        self.assertEqual(paid.value, "850")
        self.assertEqual(state.context["funding"], "на държавна издръжка")
        self.assertEqual(paid.context["funding"], "срещу заплащане")

    def test_the_marker_row_ships_as_a_real_provenance_segment(self):
        ex = fee_row_join("tuition", self.source,
                          self._join(table_marker=self.STATE,
                                     funding="на държавна издръжка"),
                          "Бизнес мениджмънт")
        self.assertEqual(len(ex.segments), 2)
        artifact = Artifact(
            text=render.grid_artifact_text(self.source.tables),
            renderer_id="xlsx-sheet-grids", renderer_version="1",
            ref="xlsx:fees")
        verdict = gate(ex.value, list(ex.segments), artifact)
        self.assertEqual(verdict.status, Status.PASS,
                         "the band must be QUOTED from the artifact, not "
                         "asserted alongside it")
        self.assertTrue(any("държавна издръжка" in s for s in ex.segments))

    def test_a_marker_matches_a_whole_cell_never_a_substring(self):
        # "Приложение 1" is a substring of "Приложение 10" in the real
        # workbook; substring matching would select the wrong table and
        # every fee read from it would still gate green.
        ex = fee_row_join("tuition", self.source,
                          self._join(table_marker="Приложение 1"),
                          "Бизнес мениджмънт")
        self.assertEqual(ex.value, "400")
        self.assertIsNone(
            fee_row_join("tuition", self.source,
                         self._join(table_marker="Приложение"),
                         "Бизнес мениджмънт"),
            "a partial marker must match no table at all")

    def test_an_unmatched_marker_resolves_nothing_rather_than_falling_back(self):
        self.assertIsNone(
            fee_row_join("tuition", self.source,
                         self._join(table_marker="Приложение 99"),
                         "Бизнес мениджмънт"))


class FundingLabelMustBeBackedTest(unittest.TestCase):
    def _cfg(self, join_extra):
        join = {"kind": "fee-row", "name": "f", "match_header": "специалност",
                "value_headers": ["редовно", "лв."]}
        join.update(join_extra)
        return {"uni_id": "X",
                "sources": {"fees": {"url": "https://x/f.xlsx",
                                     "route": "spreadsheet", "join": join}},
                "programs": [{"id": "p", "name": "P", "page": "https://x/p"}]}

    def test_funding_must_be_a_substring_of_the_marker_it_claims(self):
        with self.assertRaises(ConfigError):
            parse_site_config(
                self._cfg({"table_marker": "Приложение 1",
                           "funding": "на държавна издръжка"}),
                origin="<test>")

    def test_funding_backed_by_the_marker_text_loads(self):
        site = parse_site_config(
            self._cfg({"table_marker": "ТАКСИ на държавна издръжка 2025",
                       "funding": "на държавна издръжка"}), origin="<test>")
        self.assertEqual(site.sources["fees"].join.funding,
                         "на държавна издръжка")

    def test_funding_without_a_marker_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_site_config(self._cfg({"funding": "на държавна издръжка"}),
                              origin="<test>")


class DisplayedValueAndMergedHeaderTest(unittest.TestCase):
    """Two renderer-fidelity defects found while making UniRuse's euro
    figures gradeable (2026-08-16). Both shipped a WRONG VALUE gate-green
    rather than crashing, which is the failure class this project keeps
    finding by running real data through real code.

    1. A fee cell stores 204.5167524784874 and DISPLAYS 204.52 under the
       built-in "0.00" format. Rendering the stored float put a string in
       the Artifact that appears nowhere on screen, so a human-sourced key
       could never match it and gate() could never bridge the gap.
    2. "Редовно обучение" is MERGED across the лв. and евро columns, so its
       text reached only the лв. column and the евро column was literally
       unaddressable -- while the spanning title "Семестриална такса,
       лв./евро" put the word "евро" into the лв. column's header. Asking
       for ["редовно","евро"] therefore returned the LEV figure.
    """

    XML_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'

    def _workbook(self):
        """Hand-built so it carries real styles + mergeCells, which
        _build_xlsx (inline strings, no styles) cannot express."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
            zf.writestr("_rels/.rels", _ROOT_RELS)
            zf.writestr("xl/workbook.xml",
                        '<?xml version="1.0"?><workbook {0} xmlns:r="{1}">'
                        '<sheets><sheet name="S" sheetId="1" r:id="rId1"/>'
                        '</sheets></workbook>'.format(self.XML_NS, _REL_NS))
            zf.writestr("xl/_rels/workbook.xml.rels",
                        '<?xml version="1.0"?><Relationships xmlns="http://'
                        'schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1" Type="http://schemas.openxml'
                        'formats.org/officeDocument/2006/relationships/worksheet"'
                        ' Target="worksheets/sheet1.xml"/></Relationships>')
            # style 1 -> numFmtId 2 == built-in "0.00"
            zf.writestr("xl/styles.xml",
                        '<?xml version="1.0"?><styleSheet {0}><cellXfs count="2">'
                        '<xf numFmtId="0"/><xf numFmtId="2"/></cellXfs>'
                        '</styleSheet>'.format(self.XML_NS))
            rows = (
                '<row r="1"><c r="A1" t="inlineStr"><is><t>СПЕЦИАЛНОСТ</t></is>'
                '</c><c r="B1" t="inlineStr"><is><t>Семестриална такса, '
                'лв./евро</t></is></c></row>'
                '<row r="2"><c r="B2" t="inlineStr"><is><t>Редовно обучение'
                '</t></is></c></row>'
                '<row r="3"><c r="B3" t="inlineStr"><is><t>лв.</t></is></c>'
                '<c r="C3" t="inlineStr"><is><t>евро</t></is></c></row>'
                '<row r="4"><c r="A4" t="inlineStr"><is><t>Бизнес мениджмънт'
                '</t></is></c><c r="B4"><v>400</v></c>'
                '<c r="C4" s="1"><v>204.5167524784874</v></c></row>')
            zf.writestr("xl/worksheets/sheet1.xml",
                        '<?xml version="1.0"?><worksheet {0}><sheetData>{1}'
                        '</sheetData><mergeCells count="2">'
                        '<mergeCell ref="B1:C1"/><mergeCell ref="B2:C2"/>'
                        '</mergeCells></worksheet>'.format(self.XML_NS, rows))
        return buf.getvalue()

    def setUp(self):
        self.grids = render.spreadsheet_grids(self._workbook())
        self.source = TableSource(ref="xlsx:f", tables=self.grids)

    def _join(self, currency):
        return FeeRowJoin(name="f", match_header="специалност",
                          value_headers=("редовно", currency),
                          value_pattern=None, context={})

    def test_a_number_renders_as_the_cell_displays_it_not_as_stored(self):
        flat = render.grid_artifact_text(self.grids)
        self.assertIn("204.52", flat)
        self.assertNotIn("204.5167524784874", flat)

    def test_an_unformatted_number_is_left_alone(self):
        self.assertIn("400", render.grid_artifact_text(self.grids))

    def test_a_merged_header_reaches_every_column_it_spans(self):
        header = self.grids[0][1]
        self.assertEqual(header[1], "Редовно обучение")
        self.assertEqual(header[2], "Редовно обучение",
                         "the merge must reach the euro column, or that "
                         "column can never be addressed")

    def test_the_euro_column_resolves_to_the_euro_figure(self):
        ex = fee_row_join("tuition", self.source, self._join("евро"),
                          "Бизнес мениджмънт")
        self.assertIsNotNone(ex)
        self.assertEqual(ex.value, "204.52")

    def test_the_lev_column_still_resolves_to_the_lev_figure(self):
        ex = fee_row_join("tuition", self.source, self._join("лв."),
                          "Бизнес мениджмънт")
        self.assertEqual(ex.value, "400")

    def test_a_spanning_title_mentioning_both_currencies_does_not_win(self):
        # B1 "Семестриална такса, лв./евро" spans BOTH columns, so after
        # merge expansion both stacks contain "евро". The euro column wins
        # because it states "евро" as a header cell of its own; the lev
        # column only mentions it inside the title.
        ex = fee_row_join("tuition", self.source, self._join("евро"),
                          "Бизнес мениджмънт")
        self.assertNotEqual(ex.value, "400",
                            "matching on containment alone ships the wrong "
                            "currency, gate-green")
