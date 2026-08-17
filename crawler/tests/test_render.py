"""Renderer-seam tests — crawler/render.py.

Offline suites (always run): route resolution, the two-tier HTML strip with
the volumetric light fallback (incl. the vum.bg Elementor tab-widget case,
vendored from spike B), mode-in-identity, the spike-A tsv_artifact_text
port against a vendored spike-A TSV, and the Docling Serve client against
mocked responses (unified `sources` payload, 504-retry-once).

Live suites (skip, never fail, when the binary/service is absent):
pdftotext -layout on a vendored spike PDF; Docling Serve at localhost:5001.
"""
import hashlib
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

from crawler import render as R
from crawler.provenance import Artifact, Status, gate

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "render"
SHARED_ARTIFACTS = Path(__file__).resolve().parent / "fixtures" / "artifacts"

# Deterministic body filler: > 400 chars after whitespace collapse, so the
# aggressive strip does not trip the volumetric fallback by accident.
FILLER = ("Програмата обучава студенти по информатика, информационни "
          "системи и софтуерно инженерство в редовна форма. " * 6)
assert len(FILLER) > 400


def _render_html(body_html):
    """Render a synthetic page through the real seam (html route)."""
    page = "<html><head><title>t</title></head><body>{0}</body></html>".format(
        body_html)
    return R.render(page.encode("utf-8"), "text/html; charset=utf-8", "html")


# ------------------------------------------------------------------ routing
class TestRouteResolution(unittest.TestCase):
    def test_hint_wins_over_content_type(self):
        self.assertEqual(
            R.resolve_route(b"<html>", "text/html", "table-pdf"),
            R.ROUTE_TABLE_PDF)

    def test_html_content_types(self):
        for ct in ("text/html", "text/html; charset=utf-8",
                   "application/xhtml+xml", "text/plain"):
            self.assertEqual(R.resolve_route(b"", ct), R.ROUTE_HTML, ct)

    def test_pdf_content_type_routes_prose(self):
        self.assertEqual(R.resolve_route(b"", "application/pdf"),
                         R.ROUTE_PROSE_PDF)

    def test_pdf_magic_sniff(self):
        self.assertEqual(
            R.resolve_route(b"%PDF-1.7 x", "application/octet-stream"),
            R.ROUTE_PROSE_PDF)

    def test_table_pdf_is_never_sniffed(self):
        # table-pdf is per-document config opt-in only (RFC v2 Q2)
        self.assertEqual(R.resolve_route(b"%PDF-1.7", "application/pdf"),
                         R.ROUTE_PROSE_PDF)

    def test_unroutable_raises(self):
        with self.assertRaises(R.RenderError):
            R.resolve_route(b"GIF89a", "image/gif")

    def test_bad_hint_raises(self):
        with self.assertRaises(R.RenderError):
            R.resolve_route(b"", "text/html", "docx")

    def test_str_snapshot_raises_typeerror(self):
        with self.assertRaises(TypeError):
            R.render("<html></html>", "text/html", "html")


# ------------------------------------------------- html: strip tiers + mode
class TestHtmlElementorCase(unittest.TestCase):
    """The vum.bg/coruption/ lesson (vendored spike-B snapshot): an
    Elementor tab widget (ul.nav.nav-tabs) carries the tuition data; bare
    'nav' must not be treated as a chrome class token."""

    @classmethod
    def setUpClass(cls):
        cls.snapshot = (FIXTURES / "vum-coruption.html").read_bytes()
        cls.artifact = R.render(cls.snapshot, "text/html; charset=utf-8",
                                "html")

    def test_returns_provenance_artifact(self):
        self.assertIsInstance(self.artifact, Artifact)

    def test_tab_widget_tuition_survives_aggressive_strip(self):
        self.assertIn("Semester fee: 1100 leva", self.artifact.text)
        self.assertIn("900 leva", self.artifact.text)  # additional semester

    def test_mode_is_aggressive_and_recorded_in_renderer_id(self):
        self.assertEqual(self.artifact.renderer_id,
                         R.RENDERER_HTML + ":" + R.MODE_AGGRESSIVE)

    def test_renderer_version_embeds_bs4(self):
        self.assertTrue(self.artifact.renderer_version.startswith("bs4-"),
                        self.artifact.renderer_version)

    def test_ref_defaults_to_snapshot_sha256(self):
        self.assertEqual(
            self.artifact.ref,
            "sha256:" + hashlib.sha256(self.snapshot).hexdigest())

    def test_explicit_ref_is_honored(self):
        art = R.render(self.snapshot, "text/html", "html",
                       ref="https://vum.bg/coruption/")
        self.assertEqual(art.ref, "https://vum.bg/coruption/")

    def test_rendered_artifact_feeds_the_gate(self):
        verdict = gate("1100 leva", ["Semester fee: 1100 leva"],
                       self.artifact)
        self.assertEqual(verdict.status, Status.PASS, verdict.detail)


class TestHtmlStripTiers(unittest.TestCase):
    def test_aggressive_drops_chrome_keeps_body(self):
        art = _render_html(
            '<nav>NAVTAG-JUNK</nav>'
            '<header>HEADERTAG-JUNK</header>'
            '<div class="main-menu">MENUCLASS-JUNK</div>'
            '<div id="site-footer">IDCHROME-JUNK</div>'
            '<p>{0}</p>'
            '<footer>FOOTERTAG-JUNK</footer>'.format(FILLER))
        self.assertTrue(art.renderer_id.endswith(":" + R.MODE_AGGRESSIVE))
        for junk in ("NAVTAG-JUNK", "HEADERTAG-JUNK", "MENUCLASS-JUNK",
                     "IDCHROME-JUNK", "FOOTERTAG-JUNK"):
            self.assertNotIn(junk, art.text)
        self.assertIn("софтуерно инженерство", art.text)

    def test_chrome_class_match_is_exact_token_only(self):
        # The compound Elementor body class must NOT match "header"/"footer"
        art = _render_html(
            '<div class="page-template-elementor_header_footer">'
            '<p>COMPOUND-KEEP {0}</p></div>'.format(FILLER))
        self.assertTrue(art.renderer_id.endswith(":" + R.MODE_AGGRESSIVE))
        self.assertIn("COMPOUND-KEEP", art.text)

    def test_synthetic_elementor_tab_widget_survives(self):
        art = _render_html(
            '<ul class="nav nav-tabs elementkit-tab-nav">'
            '<li>TabLabel-Tuition</li><li>TabLabel-Admission</li></ul>'
            '<div>Semester fee: 999 leva</div>'
            '<p>{0}</p>'.format(FILLER))
        self.assertTrue(art.renderer_id.endswith(":" + R.MODE_AGGRESSIVE))
        self.assertIn("TabLabel-Tuition", art.text)
        self.assertIn("Semester fee: 999 leva", art.text)

    def test_mega_menu_dropped_in_aggressive(self):
        items = "".join("<li>chrome-item-{0}</li>".format(i)
                        for i in range(25))
        art = _render_html(
            '<ul class="anything">{0}</ul><p>{1}</p>'.format(items, FILLER))
        self.assertTrue(art.renderer_id.endswith(":" + R.MODE_AGGRESSIVE))
        self.assertNotIn("chrome-item-3", art.text)
        self.assertIn("софтуерно инженерство", art.text)

    def test_fallback_when_aggressive_eats_the_page(self):
        # Nearly all content inside <header>: strict < 400 chars => light
        art = _render_html(
            '<header><h1>Заглавие</h1><p>HEADER-BODY {0}</p></header>'
            '<p>tiny</p>'.format(FILLER))
        self.assertEqual(art.renderer_id,
                         R.RENDERER_HTML + ":" + R.MODE_LIGHT_FALLBACK)
        self.assertIn("HEADER-BODY", art.text)
        self.assertIn("tiny", art.text)

    def test_fallback_on_volumetric_ratio(self):
        # strict >= 400 chars but < 15% of light => still fall back
        keep = "Такса за обучение 460 евро на семестър в редовна форма. " * 9
        menu = "пункт от менюто с още и още връзки " * 190   # ~6.6k chars
        self.assertGreater(len(R._norm(keep)), 400)
        art = _render_html(
            '<p>BODY-KEEP {0}</p><div class="menu">MENU-BULK {1}</div>'.format(
                keep, menu))
        self.assertEqual(art.renderer_id,
                         R.RENDERER_HTML + ":" + R.MODE_LIGHT_FALLBACK)
        self.assertIn("BODY-KEEP", art.text)
        self.assertIn("MENU-BULK", art.text)   # light keeps chrome classes

    def test_mega_menu_dropped_even_in_light_fallback(self):
        items = "".join("<li>menu-item-{0}</li>".format(i)
                        for i in range(30))
        art = _render_html(
            '<ul>{0}</ul>'
            '<header><p>HEADER-BODY {1}</p></header><p>tiny</p>'.format(
                items, FILLER))
        self.assertEqual(art.renderer_id,
                         R.RENDERER_HTML + ":" + R.MODE_LIGHT_FALLBACK)
        self.assertNotIn("menu-item-7", art.text)
        self.assertIn("HEADER-BODY", art.text)

    def test_the_two_modes_are_distinct_renderer_identities(self):
        aggressive = _render_html("<p>{0}</p>".format(FILLER))
        fallback = _render_html("<header><p>{0}</p></header>".format(FILLER))
        self.assertNotEqual(aggressive.renderer_id, fallback.renderer_id)
        for art in (aggressive, fallback):
            self.assertTrue(
                art.renderer_id.startswith(R.RENDERER_HTML + ":"),
                art.renderer_id)


# --------------------------------------------- tsv_artifact_text (the port)
class TestTsvArtifactText(unittest.TestCase):
    """The spike-A tsv_artifact_text port, against the vendored spike-A TSV
    (mu-fees.table00.tsv) and the golden artifact text it must reproduce."""

    @classmethod
    def setUpClass(cls):
        cls.tsv = (FIXTURES / "mu-fees.table00.tsv").read_text()
        cls.expected = (
            FIXTURES / "expected-docling-tsv-mu-fees.txt").read_text()

    def test_port_reproduces_spike_a_artifact_text_exactly(self):
        self.assertEqual(R.tsv_artifact_text([self.tsv]), self.expected)

    def test_matches_the_shared_provenance_fixture_when_present(self):
        shared = SHARED_ARTIFACTS / "docling-tsv-mu-fees.txt"
        if not shared.exists():
            self.skipTest("shared provenance fixture not vendored here")
        self.assertEqual(R.tsv_artifact_text([self.tsv]),
                         shared.read_text())

    def test_known_fee_row_renders_as_one_line(self):
        lines = R.tsv_artifact_text([self.tsv]).splitlines()
        med = [ln for ln in lines
               if "Медицина (I, II, III и IV курс)" in ln]
        self.assertTrue(med, "Медицина row missing from TSV artifact text")
        self.assertIn("620", med[0])

    def test_grid_path_equals_tsv_file_path(self):
        # A docling grid (cells with newlines/extra whitespace) must render
        # to the same canonical lines as its .tsv serialization.
        grid = [
            [{"text": "Области на висше\nобразование"}, {"text": "редовно"}],
            [{"text": "Медицина  (I, II,\nIII и IV курс)"}, {"text": "620"}],
            [{"text": ""}, {"text": ""}],
        ]
        json_content = {"tables": [{"data": {"grid": grid}}]}
        tsv_file = "\n".join(
            "\t".join(" ".join((c["text"] or "").split()) for c in row)
            for row in grid)
        self.assertEqual(
            "\n".join(R._tsv_lines_from_docling(json_content)),
            R.tsv_artifact_text([tsv_file]))

    def test_multiple_tsvs_concatenate_in_order(self):
        out = R.tsv_artifact_text(["a\tb", "c\td"])
        self.assertEqual(out, "a b\nc d")


# ------------------------------------------------ docling client (mocked)
class _Resp(object):
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body


def _ok_response():
    return _Resp(200, {
        "status": "success",
        "document": {"json_content": {"tables": [{"data": {"grid": [
            [{"text": "Специалност"}, {"text": "такса"}],
            [{"text": "Медицина"}, {"text": "620"}],
        ]}}]}},
    })


class TestDoclingClientMocked(unittest.TestCase):
    PDF = b"%PDF-1.4 fake-snapshot-bytes"

    def test_success_renders_tsv_artifact(self):
        with mock.patch.object(R.requests, "post",
                               return_value=_ok_response()) as post:
            art = R.render(self.PDF, "application/pdf", "table-pdf",
                           backoff_s=0)
        self.assertIsInstance(art, Artifact)
        self.assertEqual(art.text, "Специалност такса\nМедицина 620")
        self.assertEqual(art.renderer_id, R.RENDERER_TABLE_PDF)
        self.assertEqual(art.renderer_version, R.DOCLING_IMAGE_TAG)
        self.assertEqual(post.call_count, 1)

    def test_payload_is_unified_sources_array(self):
        # v1.28 shape: {"sources":[{"kind":"file",...}]} — NOT the stale
        # upstream-docs http_sources/file_sources shape (422s on v1.28).
        with mock.patch.object(R.requests, "post",
                               return_value=_ok_response()) as post:
            R.render(self.PDF, "application/pdf", "table-pdf", backoff_s=0)
        args, kwargs = post.call_args
        self.assertTrue(args[0].endswith("/v1/convert/source"), args[0])
        payload = kwargs["json"]
        self.assertNotIn("http_sources", payload)
        self.assertNotIn("file_sources", payload)
        (src,) = payload["sources"]
        self.assertEqual(src["kind"], "file")
        import base64 as b64
        self.assertEqual(b64.b64decode(src["base64_string"]), self.PDF)

    def test_504_retries_once_with_backoff_then_succeeds(self):
        with mock.patch.object(R.requests, "post",
                               side_effect=[_Resp(504), _ok_response()]) \
                as post, mock.patch.object(R.time, "sleep") as sleep:
            art = R.render(self.PDF, "application/pdf", "table-pdf",
                           backoff_s=7)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(7)
        self.assertEqual(art.text, "Специалност такса\nМедицина 620")

    def test_504_twice_raises_after_exactly_one_retry(self):
        with mock.patch.object(R.requests, "post",
                               side_effect=[_Resp(504), _Resp(504),
                                            _ok_response()]) as post, \
                mock.patch.object(R.time, "sleep"):
            with self.assertRaises(R.RenderError):
                R.render(self.PDF, "application/pdf", "table-pdf",
                         backoff_s=0)
        self.assertEqual(post.call_count, 2)   # once + one retry, never more

    def test_http_error_raises(self):
        with mock.patch.object(R.requests, "post",
                               return_value=_Resp(500, text="boom")):
            with self.assertRaises(R.RenderError):
                R.render(self.PDF, "application/pdf", "table-pdf",
                         backoff_s=0)

    def test_conversion_failure_status_raises(self):
        body = {"status": "failure", "errors": ["missing enrichment model"]}
        with mock.patch.object(R.requests, "post",
                               return_value=_Resp(200, body)):
            with self.assertRaises(R.RenderError) as ctx:
                R.render(self.PDF, "application/pdf", "table-pdf",
                         backoff_s=0)
        self.assertIn("failure", str(ctx.exception))


# ----------------------------------------------------- live: pdftotext
@unittest.skipUnless(shutil.which("pdftotext"),
                     "pdftotext (poppler) not on PATH")
class TestProsePdfLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = (FIXTURES / "su-compchem.pdf").read_bytes()
        cls.artifact = R.render(cls.pdf, "application/pdf")   # sniffed route

    def test_renderer_identity(self):
        self.assertEqual(self.artifact.renderer_id, R.RENDERER_PROSE_PDF)
        self.assertTrue(
            self.artifact.renderer_version.startswith("poppler-"),
            self.artifact.renderer_version)

    def test_canonical_text_carries_the_program(self):
        self.assertIn("Компютърна химия", self.artifact.text)

    def test_flow_plus_layout_composite(self):
        # Both surfaces present: the flow half and the layout half each
        # contain the document title, so the title occurs at least twice.
        self.assertGreaterEqual(
            self.artifact.text.count("Компютърна химия"), 2)

    def test_matches_vendored_spike_artifact_when_version_matches(self):
        shared = SHARED_ARTIFACTS / "pdftext-su-compchem.txt"
        if not shared.exists():
            self.skipTest("shared provenance fixture not vendored here")
        if self.artifact.renderer_version != "poppler-26.08.0":
            self.skipTest("different poppler version: "
                          + self.artifact.renderer_version)
        self.assertEqual(self.artifact.text, shared.read_text())


# ----------------------------------------------------- live: docling serve
def _docling_up():
    try:
        return requests.get(R.DOCLING_URL + "/health",
                            timeout=2).status_code == 200
    except requests.RequestException:
        return False


@unittest.skipUnless(_docling_up(),
                     "docling-serve not responding at " + R.DOCLING_URL)
class TestTablePdfLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = (FIXTURES / "mu-fees.pdf").read_bytes()
        cls.artifact = R.render(cls.pdf, "application/pdf", "table-pdf")

    def test_renderer_identity(self):
        self.assertEqual(self.artifact.renderer_id, R.RENDERER_TABLE_PDF)
        self.assertEqual(self.artifact.renderer_version, R.DOCLING_IMAGE_TAG)

    def test_reproduces_spike_a_tsv_artifact_text(self):
        expected = (FIXTURES / "expected-docling-tsv-mu-fees.txt").read_text()
        self.assertEqual(self.artifact.text, expected)

    def test_fee_join_provenance_gates_against_it(self):
        lines = [ln for ln in self.artifact.text.splitlines()
                 if "Медицина (I, II, III и IV курс)" in ln]
        self.assertTrue(lines)
        verdict = gate("620 евро", [lines[0],
                                    "обучение на български език (в евро)"],
                       self.artifact)
        # currency 'евро' comes from the section-header segment (spike A's
        # mu_fee_join shape); the row segment carries the number
        self.assertEqual(verdict.status, Status.PASS, verdict.detail)


if __name__ == "__main__":
    unittest.main()
