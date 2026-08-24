"""The Renderer seam — snapshot bytes in, store-constructed Artifact out.

    render(snapshot_bytes, content_type, route_hint) -> Artifact

This module (with the artifact store) is the ONLY place Artifacts are
constructed outside tests (ADR-0002; enforced by the grep test in
crawler/tests/test_adr0002_grep.py). Rendering is deterministic per
(renderer_id, renderer_version): same snapshot bytes + same route + same
pinned renderer ⇒ byte-identical canonical text. Renderer identity travels
on the Artifact because provenance is only ever checked against the exact
rendering the extractor read (the wrong-artifact failure class nulled 15
join values in spike A's first E3 run).

Routes (per-document config decides; nothing here guesses table-pdf):

  html       bs4 canonical text — the spike-B two-tier strip. Aggressive
             tier drops chrome (semantic nav/header/footer/aside/form tags,
             exact-token chrome classes/ids, mega-menu lists); light tier
             drops only script/style-class junk and mega-menus. If the
             aggressive strip eats the page (< 400 chars, or < 15% of the
             light strip's text) we fall back to light — a lost page body
             is unrecoverable, leftover boilerplate is not. THE TIER USED
             IS PART OF RENDERER IDENTITY (renderer_id suffix ":aggressive"
             / ":light-fallback") — the Elementor lesson from vum.bg: two
             strips of the same snapshot are two different artifacts.

  prose-pdf  pdftotext -layout (poppler) subprocess. Canonical text is the
             spike-A "flow+layout" composite: de-hyphenated whitespace-
             collapsed flow text, then a space, then the whitespace-
             collapsed layout text — both provenance surfaces of spike A's
             ordinance/справочник joins in one artifact.

  table-pdf  Docling Serve (pinned image, localhost:5001), unified
             `sources` array with a base64 file source (v1.28 payload
             shape — upstream docs are stale, see /openapi.json). One 504
             retry with backoff (big PDFs: the 2.2 MB AUBG catalog).
             Canonical text is the spike-A tsv_artifact_text port: one
             whitespace-normalized line per table row, tables in document
             order — the ONLY text fee-join provenance is checked against.

Chrome-token lesson (vum.bg/coruption/, Elementor): bare "nav" is NOT a
chrome token — it false-positived on an Elementor tab widget
(ul.nav.nav-tabs) that carried tuition data. Semantic <nav> TAGS are still
stripped in the aggressive tier; class/id matching is exact-token only, so
compounds like "page-template-elementor_header_footer" survive.
"""
import base64
import hashlib
import io
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from xml.etree import ElementTree

import bs4
import requests
from bs4 import BeautifulSoup

from crawler.provenance import Artifact

__all__ = [
    "RenderError",
    "render",
    "resolve_route",
    "prose_pdf_texts",
    "compose_prose_text",
    "tsv_artifact_text",
    "table_grids_from_tsv",
    "grid_artifact_text",
    "docling_grids",
    "spreadsheet_grids",
    "ROUTE_HTML",
    "ROUTE_PROSE_PDF",
    "ROUTE_TABLE_PDF",
    "ROUTE_SPREADSHEET",
    "RENDERER_HTML",
    "RENDERER_PROSE_PDF",
    "RENDERER_TABLE_PDF",
    "RENDERER_SPREADSHEET",
    "DOCLING_URL",
    "DOCLING_IMAGE_TAG",
]

ROUTE_HTML = "html"
ROUTE_PROSE_PDF = "prose-pdf"
ROUTE_TABLE_PDF = "table-pdf"
ROUTE_SPREADSHEET = "spreadsheet"
_ROUTES = (ROUTE_HTML, ROUTE_PROSE_PDF, ROUTE_TABLE_PDF, ROUTE_SPREADSHEET)

RENDERER_HTML = "bs4-lxml-canonical"        # + ":aggressive" / ":light-fallback"
RENDERER_PROSE_PDF = "pdftotext-flow+layout"
RENDERER_TABLE_PDF = "docling-serve-tsv"
RENDERER_SPREADSHEET = "xlsx-sheet-grids"
SPREADSHEET_RENDERER_VERSION = "1"

MODE_AGGRESSIVE = "aggressive"
MODE_LIGHT_FALLBACK = "light-fallback"

DOCLING_URL = "http://localhost:5001"
# Pinned in docker-compose.yml; the payload shape changes between versions
# (<=1.2x used http_sources/file_sources, 1.28 uses a unified `sources` array).
DOCLING_IMAGE_TAG = "ghcr.io/docling-project/docling-serve-cpu:v1.28.0"
DOCLING_TIMEOUT_S = (10, 600)          # connect, read — big PDFs take minutes
DOCLING_RETRY_BACKOFF_S = 30.0

# The default preset ("auto") silently selects an OCR engine with no
# Cyrillic model at all, misreading Cyrillic as Latin lookalikes
# ("OBIIIECTBEHO 3IPABE" for "ОБЩЕСТВЕНО ЗДРАВЕ" — measured live on
# MU-Sofia's scanned fee PDF, 2026-08-24). Every table-pdf source this
# fleet points at is a Bulgarian document, so Bulgarian OCR is the
# renderer's own default, not per-site config (ADR-0001: this is a
# pipeline operating parameter, not site knowledge). Requires the
# EasyOCR Cyrillic recognizer weight in the docling-serve model cache
# (docker-compose.yml's docling-models volume; see docs for how it was
# seeded) — falls back to a text-only result for any PDF whose scanned
# regions are language content the weight doesn't cover, same as
# before. Empirically verified (2026-08-24) to change nothing for the
# 8 real table-pdf sources already configured across 5 universities —
# every one has a genuine text layer, so OCR (bitmap-only) never
# engages for them; only scanned documents like MU-Sofia's are
# affected. No attribution-review trigger: no existing cell can move.
DOCLING_OCR_PRESET = "easyocr"
DOCLING_OCR_LANG = ("bg", "en")

_WS_RX = re.compile(r"\s+")


def _norm(s):
    # type: (str) -> str
    return _WS_RX.sub(" ", s or "").strip()


class RenderError(Exception):
    """A snapshot could not be rendered on the requested route."""


# ------------------------------------------------------------------ routing
_PDF_MAGIC = b"%PDF-"


def resolve_route(snapshot_bytes, content_type, route_hint=None):
    # type: (bytes, str, str) -> str
    """Pick the rendering route. An explicit route_hint (per-document
    config) always wins — table-pdf in particular is NEVER sniffed, it is
    config opt-in (RFC v2 Q2: routing is per-document config). Without a
    hint, content_type and the PDF magic route between html and prose-pdf.
    """
    if route_hint is not None:
        if route_hint not in _ROUTES:
            raise RenderError(
                "unknown route_hint {0!r} (valid: {1})".format(
                    route_hint, ", ".join(_ROUTES)))
        return route_hint
    ct = (content_type or "").lower()
    if "html" in ct or ct.startswith("text/"):
        return ROUTE_HTML
    if "pdf" in ct or snapshot_bytes[:len(_PDF_MAGIC)] == _PDF_MAGIC:
        return ROUTE_PROSE_PDF
    raise RenderError(
        "cannot route snapshot: content_type={0!r} and no route_hint "
        "(bytes start {1!r})".format(content_type, snapshot_bytes[:12]))


# ------------------------------------------------------------ html renderer
def _pick_parser():
    try:
        import lxml
        return "lxml", "lxml-" + lxml.__version__
    except ImportError:                              # pragma: no cover
        import platform
        return "html.parser", "html.parser-py" + platform.python_version()


_BS4_PARSER, _BS4_PARSER_VERSION = _pick_parser()
HTML_RENDERER_VERSION = "bs4-{0}/{1}".format(bs4.__version__,
                                             _BS4_PARSER_VERSION)

# Exact-token chrome match only: "menu", "main-menu", "site-footer" — never a
# word buried inside a compound like "page-template-elementor_header_footer".
# NB: bare "nav" removed from the token list after it false-positived on an
# Elementor tab widget (ul.nav.nav-tabs) that carried tuition data on
# vum.bg/coruption/ — semantic <nav> tags are still stripped tag-level.
_W = r"(menu|navbar|footer|header|breadcrumb|cookie|share|search)"
_CHROME_TOKEN = re.compile(
    r"^({W}|(site|main|top|primary|mega|mobile)[-_]{W}"
    r"|{W}[-_](wrap|wrapper|container|bar|area|inner))$".format(W=_W),
    re.I)

_MEGA_MENU_LI = 20        # any <ul>/<ol> with more <li> is a mega-menu


def _strip(soup, aggressive):
    for tag in soup(["script", "style", "noscript", "svg", "iframe",
                     "button"]):
        tag.decompose()
    for lst in soup.find_all(["ul", "ol"]):
        if lst.parent is not None and len(lst.find_all("li")) > _MEGA_MENU_LI:
            lst.decompose()
    if aggressive:
        for tag in soup(["nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        for tag in list(soup.find_all(True)):
            if tag.name in ("body", "html") or tag.parent is None:
                continue
            idents = [tag.get("id") or ""] + list(tag.get("class") or [])
            if any(_CHROME_TOKEN.match(c) for c in idents if c):
                tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _render_html(snapshot_bytes):
    # type: (bytes) -> tuple
    """Two-tier strip with the light-strip volumetric fallback.

    Returns (canonical_text, mode) where mode is MODE_AGGRESSIVE or
    MODE_LIGHT_FALLBACK — the caller records it in renderer_id, because
    which tier ran is part of artifact identity.
    """
    strict = _strip(BeautifulSoup(snapshot_bytes, _BS4_PARSER),
                    aggressive=True)
    light = _strip(BeautifulSoup(snapshot_bytes, _BS4_PARSER),
                   aggressive=False)
    if len(strict) < 400 or len(strict) < 0.15 * len(light):
        return light, MODE_LIGHT_FALLBACK
    return strict, MODE_AGGRESSIVE


# ------------------------------------------------------- prose-pdf renderer
_PDFTOTEXT_VERSION_RX = re.compile(r"pdftotext version (\S+)")
_pdftotext_version_cache = {}


def _pdftotext_version():
    # type: () -> str
    if "v" not in _pdftotext_version_cache:
        proc = subprocess.run(["pdftotext", "-v"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        blurb = (proc.stderr or proc.stdout).decode("utf-8", "replace")
        m = _PDFTOTEXT_VERSION_RX.search(blurb)
        _pdftotext_version_cache["v"] = (
            "poppler-" + m.group(1) if m else "poppler-unknown")
    return _pdftotext_version_cache["v"]


def compose_prose_text(raw_layout_text):
    # type: (str) -> str
    """Spike A's flow+layout composite over raw `pdftotext -layout` output:
    norm(de-hyphenated raw) + " " + norm(raw). Both spike-A provenance
    surfaces (flow label patterns, layout row joins) are contained in the
    one artifact text, so snippets from either mechanism gate against it.
    The ONE composite rule — the artifact store's replay path (vendored
    spike renderings) and the live pdftotext path both go through here.
    """
    # 'Помощник-\nфармацевт' -> 'Помощник-фармацевт'
    flow = _norm(re.sub(r"-\n\s*", "-", raw_layout_text))
    return (flow + " " + _norm(raw_layout_text)).strip()


def prose_pdf_texts(snapshot_bytes):
    # type: (bytes) -> tuple
    """pdftotext -layout on snapshot bytes.

    Returns (composite_text, raw_layout_text, renderer_version): the
    composite is the canonical artifact text; the raw layout text is the
    line-anchored working surface ordinance joins need (TextSource.layout).
    """
    if shutil.which("pdftotext") is None:
        raise RenderError(
            "pdftotext (poppler) not found on PATH — required for the "
            "prose-pdf route")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(snapshot_bytes)
        tmp.flush()
        proc = subprocess.run(
            ["pdftotext", "-layout", tmp.name, "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RenderError(
            "pdftotext -layout failed (rc={0}): {1}".format(
                proc.returncode,
                proc.stderr.decode("utf-8", "replace")[-400:]))
    raw = proc.stdout.decode("utf-8", "replace")
    return compose_prose_text(raw), raw, _pdftotext_version()


def _render_prose_pdf(snapshot_bytes):
    # type: (bytes) -> tuple
    text, _, version = prose_pdf_texts(snapshot_bytes)
    return text, version


# ------------------------------------------------------- table-pdf renderer
def _cell_text(cell):
    """Grid cells carry newlines and stray whitespace; TSV tolerates
    neither (port of scripts/docling-tables.py cell_text)."""
    return " ".join((cell.get("text") or "").split())


def tsv_artifact_text(tsv_texts):
    # type: (list) -> str
    """Port of spike A extract_lib.tsv_artifact_text — the exact rendering
    the fee joins read: one whitespace-normalized line per table row, cells
    joined by single spaces, tables concatenated in document order.
    Provenance for joined table values is checked against THIS text (the
    first E3 run checked TSV snippets against the pdftotext rendering —
    every one failed the gate, correctly: same PDF, different artifact).

    tsv_texts: ordered iterable of TSV file contents (tab-separated rows).
    """
    lines = []
    for tsv in tsv_texts:
        for line in tsv.splitlines():
            lines.append(_norm(" ".join(line.split("\t"))))
    return "\n".join(lines)


def table_grids_from_tsv(tsv_texts):
    # type: (list) -> tuple
    """Parsed cell grids of TSV artifact files, per-table boundaries kept:
    a tuple of tables, each a tuple of rows of whitespace-normalized cell
    strings. grid_artifact_text over this equals tsv_artifact_text over
    the same files — one rendering, two views (text for the gate, grids
    for the column-aware resolver)."""
    return tuple(
        tuple(tuple(_norm(c) for c in line.split("\t"))
              for line in tsv.splitlines())
        for tsv in tsv_texts)


def grid_artifact_text(grids):
    # type: (tuple) -> str
    """Canonical table-artifact text from parsed grids: one whitespace-
    normalized line per row, tables in document order — the same lines
    tsv_artifact_text renders from the TSV files."""
    return "\n".join(_norm(" ".join(row))
                     for table in grids for row in table)


# ---------------------------------------------------- spreadsheet renderer
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_CELL_REF_RX = re.compile(r"([A-Z]+)(\d+)")


def _col_index(ref):
    # type: (str) -> int
    """0-based column index from a cell ref's letters ("A"->0, "AA"->26).

    Cells are addressed, not positional: a row's XML omits empty cells
    entirely, so reading them in document order would silently shift
    every value left of a gap into the wrong column -- exactly the
    wrong-column failure class the fee-row join's header resolution
    exists to prevent (RFC v2 Q4)."""
    match = _CELL_REF_RX.match(ref)
    if match is None:
        raise RenderError("malformed cell ref {0!r} in workbook".format(ref))
    letters = match.group(1)
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


# Built-in OOXML number formats, by the ids real workbooks actually use.
# Only the decimal-place count matters here: the artifact must show what a
# reader sees, and a fee cell storing 204.5167524784874 under format "0.00"
# is DISPLAYED as 204.52 -- rendering the raw float would put a string in
# the Artifact that appears nowhere on screen, so any human-sourced key
# would disagree with it forever and gate() could never bridge the gap.
_BUILTIN_DECIMALS = {"1": 0, "2": 2, "3": 0, "4": 2, "37": 0, "38": 0,
                     "39": 2, "40": 2}
_DECIMALS_RX = re.compile(r"\.(0+)")


def _format_decimals(fmt_id, custom):
    # type: (Optional[str], dict) -> Optional[int]
    """Decimal places a cell's number format displays, or None to leave the
    stored value alone (General, dates, text)."""
    if fmt_id is None:
        return None
    code = custom.get(fmt_id)
    if code is not None:
        m = _DECIMALS_RX.search(code)
        return len(m.group(1)) if m else 0
    return _BUILTIN_DECIMALS.get(fmt_id)


def _cell_formats(zf):
    # type: (zipfile.ZipFile) -> tuple
    """(style index -> numFmtId, custom numFmtId -> formatCode)."""
    try:
        root = ElementTree.fromstring(zf.read("xl/styles.xml"))
    except KeyError:
        return [], {}
    custom = {f.get("numFmtId"): f.get("formatCode")
              for f in root.iter(_XLSX_NS + "numFmt")}
    xfs_el = root.find(_XLSX_NS + "cellXfs")
    xfs = [x.get("numFmtId") for x in xfs_el] if xfs_el is not None else []
    return xfs, custom


def _display_number(raw, decimals):
    # type: (str, Optional[int]) -> str
    if decimals is None:
        return raw
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return raw
    return "{0:.{1}f}".format(value, decimals)


def _shared_strings(zf):
    # type: (zipfile.ZipFile) -> list
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(raw)
    return ["".join(t.text or "" for t in si.iter(_XLSX_NS + "t"))
            for si in root.findall(_XLSX_NS + "si")]


def _sheet_paths_in_workbook_order(zf):
    # type: (zipfile.ZipFile) -> list
    """Sheet part paths in the order the WORKBOOK declares, not zip
    order. Order is load-bearing: a fee workbook's sheets run
    most-authoritative first (state-subsidized before paid-place), so
    first-table-wins resolution reads as a real precedence rule rather
    than an accident of zip layout."""
    rels_root = ElementTree.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rels = {}
    for rel in rels_root:
        rels[rel.get("Id")] = rel.get("Target")
    wb_root = ElementTree.fromstring(zf.read("xl/workbook.xml"))
    rel_attr = ("{http://schemas.openxmlformats.org/officeDocument/"
                "2006/relationships}id")
    paths = []
    for sheet in wb_root.iter(_XLSX_NS + "sheet"):
        target = rels.get(sheet.get(rel_attr))
        if not target:
            continue
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        paths.append(target)
    return paths


def _merged_spans(root):
    # type: (object) -> list
    """(r0, c0, r1, c1) of every declared merge, 0-based inclusive.

    A merged cell stores its text ONLY in the top-left cell, so without
    expanding it a two-level fee header reads wrong in both directions:
    "Редовно обучение" (merged across the лв. and евро columns) never
    reaches the евро column, making it unaddressable, while the spanning
    title "Семестриална такса, лв./евро" sits in the лв. column alone and
    makes THAT column match a value_headers token of "евро". Measured on
    University of Ruse's workbook: ["редовно","евро"] resolved to the лв.
    column and shipped 400 where the euro figure is 204.52."""
    spans = []
    el = root.find(_XLSX_NS + "mergeCells")
    if el is None:
        return spans
    for mc in el:
        ref = mc.get("ref") or ""
        if ":" not in ref:
            continue
        a, b = ref.split(":", 1)
        ma, mb = _CELL_REF_RX.match(a), _CELL_REF_RX.match(b)
        if not ma or not mb:
            continue
        spans.append((int(ma.group(2)) - 1, _col_index(a),
                      int(mb.group(2)) - 1, _col_index(b)))
    return spans


def _sheet_grid(zf, path, shared, xfs=(), custom=None):
    # type: (zipfile.ZipFile, str, list, list, dict) -> tuple
    custom = custom or {}
    root = ElementTree.fromstring(zf.read(path))
    by_row, widths = {}, []
    for row_el in root.iter(_XLSX_NS + "row"):
        cells = {}
        for c in row_el.findall(_XLSX_NS + "c"):
            ref = c.get("r")
            if not ref:
                continue
            v = c.find(_XLSX_NS + "v")
            text = v.text if v is not None else None
            is_number = text is not None and c.get("t") not in ("s", "str",
                                                                "inlineStr")
            if text is None:
                is_el = c.find(_XLSX_NS + "is")
                if is_el is not None:
                    text = "".join(t.text or ""
                                   for t in is_el.iter(_XLSX_NS + "t"))
            if text is not None and c.get("t") == "s":
                try:
                    text = shared[int(text)]
                except (ValueError, IndexError):
                    text = ""
            elif is_number:
                style = c.get("s")
                fmt_id = None
                if style is not None:
                    try:
                        fmt_id = xfs[int(style)]
                    except (ValueError, IndexError):
                        fmt_id = None
                text = _display_number(
                    text, _format_decimals(fmt_id, custom))
            cells[_col_index(ref)] = _norm(text or "")
        if not cells:
            continue
        width = max(cells) + 1
        by_row[int(row_el.get("r")) - 1] = cells
        widths.append(width)

    spans = _merged_spans(root)
    for r0, c0, r1, c1 in spans:
        source = by_row.get(r0, {}).get(c0, "")
        if not source:
            continue
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                if (r, c) == (r0, c0):
                    continue
                # Only FILL, never overwrite: a merge's covered cells are
                # empty by definition, and clobbering a real value would
                # invent text the document does not show.
                if by_row.setdefault(r, {}).get(c):
                    continue
                by_row[r][c] = source
                widths.append(c + 1)

    if not by_row:
        return ()
    width = max(widths) if widths else 0
    return tuple(
        tuple(by_row[r].get(i, "") for i in range(width))
        for r in sorted(by_row))


def spreadsheet_grids(snapshot_bytes):
    # type: (bytes) -> tuple
    """Parsed cell grids of an .xlsx workbook -- ONE TABLE PER SHEET, in
    workbook order, in the same shape table_grids_from_tsv produces for
    Docling TSVs (a tuple of tables, each a tuple of rows of
    whitespace-normalized cell strings).

    Same shape on purpose: grid_artifact_text over these grids is the
    artifact text the gate checks, and cascade.TableSource carries the
    same grids to the column-aware fee-row resolver -- one rendering,
    two views, exactly like the table-pdf route. No new artifact type,
    no parallel resolver.

    stdlib only (zipfile + ElementTree), per this package's stated
    constraint -- openpyxl is not a dependency here. Only the sheet
    surface a fee schedule actually uses is read: shared strings, inline
    strings, and raw cell values. Formulas are read as their cached
    value (the <v> element), which is what a published fee workbook
    ships; number formatting is deliberately NOT applied, so a cell
    reads as the workbook stores it.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(snapshot_bytes))
    except zipfile.BadZipFile as exc:
        raise RenderError("not a readable .xlsx workbook: {0}".format(exc))
    with zf:
        try:
            shared = _shared_strings(zf)
            xfs, custom = _cell_formats(zf)
            return tuple(_sheet_grid(zf, path, shared, xfs, custom)
                         for path in _sheet_paths_in_workbook_order(zf))
        except (KeyError, ElementTree.ParseError) as exc:
            raise RenderError(
                "malformed .xlsx workbook: {0}".format(exc))


def _tsv_lines_from_docling(json_content):
    # type: (dict) -> list
    """The same canonical lines, straight from a DoclingDocument's table
    grids (what docling-tables.py serializes to .tsv files). Must stay
    equivalent to tsv_artifact_text over those files — the store may
    re-render from either surface."""
    lines = []
    for table in json_content.get("tables") or []:
        grid = (table.get("data") or {}).get("grid") or []
        for row in grid:
            lines.append(_norm(" ".join(_cell_text(c) for c in row)))
    return lines


def _docling_convert(snapshot_bytes, docling_url, backoff_s):
    # type: (bytes, str, float) -> dict
    """POST the snapshot to Docling Serve; return the DoclingDocument
    json_content. v1.28 unified `sources` array (upstream docs show
    http_sources/file_sources and 422 here — check /openapi.json, not
    GitHub). One 504 retry with backoff: big PDFs (the 2.2 MB AUBG
    catalog) time out at the gateway on the first pass while conversion
    continues server-side."""
    payload = {
        "sources": [{
            "kind": "file",
            "base64_string": base64.b64encode(snapshot_bytes).decode("ascii"),
            "filename": "snapshot.pdf",
        }],
        "options": {"to_formats": ["json"],
                   "ocr_preset": DOCLING_OCR_PRESET,
                   "ocr_lang": list(DOCLING_OCR_LANG)},
    }
    url = docling_url.rstrip("/") + "/v1/convert/source"
    resp = requests.post(url, json=payload, timeout=DOCLING_TIMEOUT_S)
    if resp.status_code == 504:
        time.sleep(backoff_s)
        resp = requests.post(url, json=payload, timeout=DOCLING_TIMEOUT_S)
    if resp.status_code == 504:
        raise RenderError(
            "docling-serve 504 twice (after one {0}s-backoff retry) at "
            "{1}".format(backoff_s, url))
    if resp.status_code != 200:
        raise RenderError(
            "docling-serve HTTP {0} at {1}: {2}".format(
                resp.status_code, url, resp.text[:400]))
    body = resp.json()
    status = body.get("status")
    if status not in ("success", "partial_success"):
        errors = body.get("errors") or [body.get("detail")]
        raise RenderError(
            "docling conversion failed: status={0!r} errors={1}".format(
                status, [e for e in errors if e][:3]))
    return (body.get("document") or {}).get("json_content") or {}


def docling_grids(snapshot_bytes, docling_url=DOCLING_URL,
                  backoff_s=DOCLING_RETRY_BACKOFF_S):
    # type: (bytes, str, float) -> tuple
    """Convert via Docling Serve and return the parsed cell grids
    (per-table boundaries kept, cells whitespace-normalized) — what the
    artifact store feeds the column-aware resolver in live table-pdf runs.
    grid_artifact_text over the result is the canonical artifact text."""
    json_content = _docling_convert(snapshot_bytes, docling_url, backoff_s)
    return tuple(
        tuple(tuple(_cell_text(c) for c in row)
              for row in (table.get("data") or {}).get("grid") or [])
        for table in json_content.get("tables") or [])


def _render_table_pdf(snapshot_bytes, docling_url, backoff_s):
    # type: (bytes, str, float) -> str
    json_content = _docling_convert(snapshot_bytes, docling_url, backoff_s)
    return "\n".join(_tsv_lines_from_docling(json_content))


# ------------------------------------------------------------------- render
def render(snapshot_bytes, content_type, route_hint=None, *, ref=None,
           docling_url=DOCLING_URL, backoff_s=DOCLING_RETRY_BACKOFF_S):
    # type: (...) -> Artifact
    """Render a snapshot into the Artifact provenance is checked against.

    Arguments:
      snapshot_bytes  raw fetched bytes of the Snapshot (never a str)
      content_type    the snapshot's Content-Type header (may be empty)
      route_hint      per-document config route: "html" / "prose-pdf" /
                      "table-pdf"; wins over content_type when given
      ref             identifier of the rendered snapshot (URL or store
                      key); defaults to "sha256:<hex>" of snapshot_bytes,
                      matching the content-addressed snapshot store
      docling_url     Docling Serve base URL (table-pdf route only)
      backoff_s       504-retry backoff seconds (table-pdf route only)

    Raises RenderError when the snapshot cannot be routed or the routed
    renderer fails. Never returns partial text: a rendering either exists
    with full renderer identity, or does not exist.
    """
    if not isinstance(snapshot_bytes, (bytes, bytearray)):
        raise TypeError(
            "snapshot_bytes must be bytes (the raw Snapshot), got "
            + type(snapshot_bytes).__name__)
    snapshot_bytes = bytes(snapshot_bytes)
    route = resolve_route(snapshot_bytes, content_type, route_hint)
    if ref is None:
        ref = "sha256:" + hashlib.sha256(snapshot_bytes).hexdigest()

    if route == ROUTE_HTML:
        text, mode = _render_html(snapshot_bytes)
        renderer_id = RENDERER_HTML + ":" + mode
        renderer_version = HTML_RENDERER_VERSION
    elif route == ROUTE_PROSE_PDF:
        text, renderer_version = _render_prose_pdf(snapshot_bytes)
        renderer_id = RENDERER_PROSE_PDF
    elif route == ROUTE_SPREADSHEET:
        text = grid_artifact_text(spreadsheet_grids(snapshot_bytes))
        renderer_id = RENDERER_SPREADSHEET
        renderer_version = SPREADSHEET_RENDERER_VERSION
    else:
        text = _render_table_pdf(snapshot_bytes, docling_url, backoff_s)
        renderer_id = RENDERER_TABLE_PDF
        renderer_version = "{0}/ocr={1}:{2}".format(
            DOCLING_IMAGE_TAG, DOCLING_OCR_PRESET,
            ",".join(DOCLING_OCR_LANG))

    return Artifact(text=text, renderer_id=renderer_id,
                    renderer_version=renderer_version, ref=ref)
