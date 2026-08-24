"""HTML tables as grids — text + optional grid under one ref
(fill-rate ticket 01).

The html route flattened <table> elements to text, so the column-aware
FeeRowJoin — the module built to prevent wrong-column fabrications —
could not run on HTML fee tables (VVVU's tuition cost three review
rounds of hand-transcribed regex anchors). The seam already exists:
ResolvedDoc.tables feeds cascade.TableSource for table-pdf and
spreadsheet; this adds the html adapter.

Design points under test:
- grids come from the SAME stripped soup that produced the canonical
  text (mode-consistent), so every " ".join(row) segment is
  gate-containable against the html artifact by construction;
- grid routes are untouched: only an html source whose config join
  wants a grid gains the dual view (TextSource at the url key for
  tier G, TableSource at the source-id key for joins);
- the loader's route/join pairing becomes asymmetric: grid joins may
  ride html, but html sources never REQUIRE one.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import render as R  # noqa: E402
from crawler import cascade  # noqa: E402
from crawler.artifact_store import ArtifactStore  # noqa: E402
from crawler.config import ConfigError, parse_site_config  # noqa: E402
from crawler.provenance import Status, gate  # noqa: E402
from crawler.runner import build_docs  # noqa: E402

FEE_HTML = ("<html><body><h1>Такси</h1>"
            "<table>"
            "<tr><th>Специалност</th><th>Такса лв.</th><th>Такса евро</th></tr>"
            "<tr><td>Медицина</td><td>1200</td><td>614 евро</td></tr>"
            "<tr><td>Фармация</td><td>1000</td><td>511 евро</td></tr>"
            "</table>"
            "<p>Приложение 1 към заповедта.</p></body></html>").encode()


class HtmlTextAndGridsTest(unittest.TestCase):
    def test_parses_tables_into_the_grid_shape(self):
        text, mode, grids = R.html_text_and_grids(FEE_HTML)
        self.assertEqual(len(grids), 1)
        self.assertEqual(grids[0][0],
                         ("Специалност", "Такса лв.", "Такса евро"))
        self.assertEqual(grids[0][1], ("Медицина", "1200", "614 евро"))

    def test_text_matches_the_html_renderer_exactly(self):
        text, mode, grids = R.html_text_and_grids(FEE_HTML)
        art = R.render(FEE_HTML, "text/html", "html")
        self.assertEqual(text, art.text)

    def test_colspan_expands_by_padding(self):
        # NOT docling's repeat-the-text convention: html canonical text
        # renders a merged cell ONCE, so a repeated grid row could never
        # be gate-contained. Padding keeps column alignment AND the
        # containment invariant (measured on VVVU's fee table, whose
        # funding-band heading is a 6-column merged cell).
        html = (b"<table><tr><td colspan='2'>span</td><td>c</td></tr>"
                b"<tr><td>a</td><td>b</td><td>c2</td></tr></table>")
        _, _, grids = R.html_text_and_grids(html)
        self.assertEqual(grids[0][0], ("span", "", "c"))

    def test_rowspan_expands_down_with_padding(self):
        html = (b"<table><tr><td rowspan='2'>r</td><td>x</td></tr>"
                b"<tr><td>y</td></tr></table>")
        _, _, grids = R.html_text_and_grids(html)
        self.assertEqual(grids[0][0], ("r", "x"))
        self.assertEqual(grids[0][1], ("", "y"))

    def test_no_tables_yields_empty_grids(self):
        _, _, grids = R.html_text_and_grids(b"<p>no tables here</p>")
        self.assertEqual(grids, ())

    def test_grids_come_from_the_same_stripped_soup(self):
        # A nav-classed table the aggressive strip removes must not
        # reappear in the grids — else its rows would fail gate
        # containment against the canonical text.
        html = ("<html><body>"
                + "<p>" + "x" * 500 + "</p>"
                + "<table class='menu'><tr><td>ЧУЖД</td></tr></table>"
                + "<table><tr><td>Медицина</td><td>614</td></tr></table>"
                + "</body></html>").encode()
        text, mode, grids = R.html_text_and_grids(html)
        self.assertEqual(mode, R.MODE_AGGRESSIVE)
        self.assertEqual(len(grids), 1)
        self.assertEqual(grids[0][0], ("Медицина", "614"))


class _Snap:
    ok = True
    status = 200
    error = None
    content_type = "text/html"
    retrieved_at = "2026-08-24T00:00:00Z"
    sha256 = None

    def __init__(self, body):
        self._body = body

    def read_bytes(self):
        return self._body


class _Fetcher:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url, opts):
        return _Snap(self.pages[url])


class StoreWantGridTest(unittest.TestCase):
    URL = "https://x.example/fees"

    def _store(self):
        return ArtifactStore(_Fetcher({self.URL: FEE_HTML}))

    def test_want_grid_resolves_tables(self):
        doc = self._store().resolve(self.URL, "html", want_grid=True)
        self.assertIsNotNone(doc.tables)
        self.assertEqual(doc.tables[0][1], ("Медицина", "1200", "614 евро"))
        # the text view is untouched — same artifact text as ever
        self.assertIn("Медицина", doc.artifact.text)

    def test_without_want_grid_html_stays_text_only(self):
        doc = self._store().resolve(self.URL, "html")
        self.assertIsNone(doc.tables)


class ConfigPairingTest(unittest.TestCase):
    def _site(self, route):
        return {
            "uni_id": "X",
            "sources": {"x-fees": {
                "url": "https://x.example/fees", "route": route,
                "join": {"kind": "fee-row", "name": "x",
                         "match_header": "Специалност",
                         "value_headers": ["Такса евро"]}}},
            "programs": [{"id": "x1", "name": "Медицина",
                          "page": "https://x.example/p",
                          "tuition_join": {"source": "x-fees",
                                           "alias": "Медицина"}}],
        }

    def test_fee_row_join_now_loads_on_an_html_source(self):
        cfg = parse_site_config(self._site("html"))
        self.assertEqual(cfg.sources["x-fees"].join.kind, "fee-row")

    def test_html_without_a_join_still_loads(self):
        site = self._site("html")
        del site["sources"]["x-fees"]["join"]
        del site["programs"][0]["tuition_join"]
        parse_site_config(site)

    def test_grid_routes_still_require_grid_joins(self):
        site = self._site("table-pdf")
        site["sources"]["x-fees"]["join"] = {
            "kind": "fees-page", "name": "x", "value_pattern": "\\d+"}
        del site["programs"][0]["tuition_join"]
        with self.assertRaises(ConfigError):
            parse_site_config(site)


class BuildDocsDualViewTest(unittest.TestCase):
    def _site(self, with_join):
        source = {"url": "https://x.example/fees", "route": "html"}
        if with_join:
            source["join"] = {"kind": "fee-row", "name": "x",
                              "match_header": "Специалност",
                              "value_headers": ["евро"]}
        prog = {"id": "x1", "name": "Медицина",
                "page": "https://x.example/p"}
        if with_join:
            prog["tuition_join"] = {"source": "x-fees", "alias": "Медицина"}
        return parse_site_config({
            "uni_id": "X", "sources": {"x-fees": source},
            "programs": [prog]})

    def _docs(self, site):
        store = ArtifactStore(_Fetcher({
            "https://x.example/fees": FEE_HTML,
            "https://x.example/p": b"<p>program page text here</p>",
        }))
        report = {"documents": [], "document_failures": []}
        return build_docs(site, store, replay=False, report=report)

    def test_grid_join_source_gets_the_dual_view(self):
        docs = self._docs(self._site(with_join=True))
        self.assertIsInstance(docs["https://x.example/fees"],
                              cascade.TextSource)
        self.assertIsInstance(docs["x-fees"], cascade.TableSource)
        # one ref, two views
        self.assertEqual(docs["x-fees"].ref,
                         docs["https://x.example/fees"].ref)

    def test_plain_html_source_stays_text_at_both_keys(self):
        docs = self._docs(self._site(with_join=False))
        self.assertIsInstance(docs["x-fees"], cascade.TextSource)
        self.assertIsInstance(docs["https://x.example/fees"],
                              cascade.TextSource)


class ProductionSeamTest(unittest.TestCase):
    """render -> store -> build_docs -> fee_row_join -> gate(), no
    hand-fed inputs anywhere (attribution-review.md step 4)."""

    def test_html_fee_row_value_passes_the_gate(self):
        site = parse_site_config({
            "uni_id": "X",
            "sources": {"x-fees": {
                "url": "https://x.example/fees", "route": "html",
                "join": {"kind": "fee-row", "name": "x",
                         "match_header": "Специалност",
                         "value_headers": ["Такса евро"]}}},
            "programs": [{"id": "x1", "name": "Медицина",
                          "page": "https://x.example/p",
                          "tuition_join": {"source": "x-fees",
                                           "alias": "Медицина"}}],
        })
        store = ArtifactStore(_Fetcher({
            "https://x.example/fees": FEE_HTML,
            "https://x.example/p": b"<p>page</p>",
        }))
        report = {"documents": [], "document_failures": []}
        docs = build_docs(site, store, replay=False, report=report)
        extraction = cascade.fee_row_join(
            "tuition", docs["x-fees"],
            site.sources["x-fees"].join, "Медицина")
        self.assertIsNotNone(extraction)
        self.assertEqual(extraction.value, "614 евро")
        artifact = store.resolve("https://x.example/fees", "html",
                                 want_grid=True).artifact
        verdict = gate(extraction.value, list(extraction.segments),
                       artifact)
        self.assertEqual(verdict.status, Status.PASS)


if __name__ == "__main__":
    unittest.main()
