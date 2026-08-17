"""The deterministic extraction cascade — tiers G, F and B (RFC v2 §2-3, Q3).

Ported from spike A's extract_lib.py + e3_extract.py (STA-78; audited,
re-runnable), with all site knowledge moved into typed config
(crawler/config.py + crawler/configs/*.json). Three tiers, zero LLM calls:

  tier G   the shared label-pattern library (LABEL_PATTERNS, 27 patterns) —
           institutional wording that survives redesigns, reused across
           universities, plus the title-language rule. 58% of the benchmark.
  tier F   family joins, config-parameterized: column-aware fee-table row
           joins reading the ACTUAL per-table Docling TSV files, sectioned
           fee-table joins, ordinance row/clause joins over pdftotext
           -layout text, справочник sentence joins, and shared fees-page
           sections. 29% of the benchmark.
  tier B   bespoke per-page anchors, ported from spike A's ANCHORS table as
           per-site CONFIG (site.anchors + program.field_anchors) — the
           interim bridge until the gated LLM tail (ticket 02, DEC-1/DEC-4)
           replaces it. 12% of the benchmark; per-anchor method names keep
           the maintenance bill countable.

A None from this module means "the deterministic cascade has no verifiable
value" — never "the field has no value"; those fields are the LLM tail's
territory once ticket 02 lands.

The cascade emits, it never gates: every non-None result is an Extraction
carrying the quintuple the provenance gate needs —

    (value, segments, artifact_ref, method, tier)

where segments are VERBATIM whitespace-normalized substrings of the exact
rendering named by artifact_ref, one per joined piece (composing pieces into
one string is the v1 failure class). The RUNNER resolves artifact_ref
through the artifact store and calls crawler.provenance.gate; nothing here
reads the store, performs IO beyond the TSV files handed to TableSource, or
constructs the gate's frozen artifact type (ADR-0002 store-only invariant,
enforced by the grep test).

Inputs are plain value objects the runner builds from store artifacts:

  TextSource   ref + canonical text (+ optional raw pdftotext -layout text,
               the line-anchored working surface ordinance joins need; its
               normalized substrings are contained in the flow+layout
               composite artifact text, so segments still gate)
  TableSource  ref + the parsed cell grid of the ACTUAL TSV artifact files.
               Emitted segments are whitespace-normalized joined row lines —
               by construction lines of crawler.render.tsv_artifact_text
               over the same files, i.e. literal text of the table artifact.

The column-aware resolver (fee-row join) is the resolver-side fix for the
gate's documented blind spot (truthful snippet, wrong row/column — measured
twice, RFC v2 Q4): the alias column and the value column are located BY
HEADER TEXT per table, and the value is read from that one cell only. An
alias row whose value cell is empty yields nothing — it never bleeds into a
neighbouring column the way spike A's positional fallback could. Table
values are never free-read; in ticket 02 the LLM/agent proposes the ROW
only, and the cell is taken deterministically from the TSV column.

Python 3.9 compatible; stdlib only.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from crawler.config import (
    FIELDS,
    AnchorConfig,
    FeeRowJoin,
    SectionedFeeRowJoin,
    OrdinanceJoin,
    SpravochnikJoin,
    FeesPageJoin,
    ProgramConfig,
    SiteConfig,
    ALIAS_PLACEHOLDER,
)

__all__ = [
    "TextSource",
    "TableSource",
    "Extraction",
    "LABEL_PATTERNS",
    "TIER_G",
    "TIER_F",
    "TIER_B",
    "norm",
    "snippet_around",
    "harvest_labels",
    "language_from_name",
    "anchor_probe",
    "fee_row_join",
    "sectioned_fee_join",
    "sectioned_language_join",
    "ordinance_join",
    "spravochnik_join",
    "spravochnik_admission_join",
    "fees_page_section",
    "resolve_field",
    "extract_program",
    "extract_site",
]

TIER_G = "G"
TIER_F = "F"
TIER_B = "B"

_WS_RX = re.compile(r"\s+")


def norm(s):
    # type: (Optional[str]) -> str
    """Spike A's whitespace collapse — case and characters PRESERVED.

    Segments must stay verbatim (the gate applies its own one
    normalization policy to both sides); this only folds runs of
    whitespace so pattern offsets are stable across renderings."""
    return _WS_RX.sub(" ", s or "").strip()


def snippet_around(text, start, end, pad=90):
    # type: (str, int, int, int) -> str
    return norm(text[max(0, start - pad):min(len(text), end + pad)])


# ------------------------------------------------------------------- sources
@dataclass(frozen=True)
class TextSource:
    """One text rendering the cascade may read.

    ref     opaque identifier of the exact artifact this text IS — it
            travels into Extraction.artifact_ref, and the runner resolves
            it back to the store-constructed artifact for the gate
    text    the canonical artifact text (bs4 canonical HTML text, or the
            pdftotext flow+layout composite)
    layout  optional raw `pdftotext -layout` text WITH line structure —
            the working surface of line-anchored ordinance joins. Its
            whitespace-normalized substrings are contained in the
            flow+layout composite, so segments cut here still gate
            against `text`.
    """
    ref: str
    text: str
    layout: Optional[str] = None


@dataclass(frozen=True)
class TableSource:
    """The parsed cell grid of one table-pdf artifact's TSV files.

    tables is a tuple of tables; each table a tuple of rows; each row a
    tuple of whitespace-normalized cell strings — exactly spike A's
    load_tsv_tables shape, but keeping per-table boundaries so the
    column-aware resolver can read each table's own header rows.

    Segments emitted from a row are norm(" ".join(cells)) — by
    construction equal to the corresponding line of
    crawler.render.tsv_artifact_text over the same files, i.e. literal
    text of the joined table artifact named by ref.
    """
    ref: str
    tables: Tuple[Tuple[Tuple[str, ...], ...], ...]

    @classmethod
    def from_tsv_files(cls, ref, paths):
        """Read ACTUAL per-table TSV artifact files (document order)."""
        tables = []
        for path in paths:
            rows = tuple(
                tuple(norm(c) for c in line.split("\t"))
                for line in Path(path).read_text().splitlines())
            tables.append(rows)
        return cls(ref=ref, tables=tuple(tables))

    @classmethod
    def from_tsv_dir(cls, ref, directory):
        """All *.tsv files of a directory, sorted by filename — the same
        order crawler.render.tsv_artifact_text renders them in."""
        return cls.from_tsv_files(ref, sorted(Path(directory).glob("*.tsv")))

    def rows(self):
        """All rows, tables concatenated in document order."""
        for table in self.tables:
            for row in table:
                yield row


# ---------------------------------------------------------------- extraction
@dataclass(frozen=True)
class Extraction:
    """One cascade emission — the provenance quintuple, ungated.

    segments are verbatim (whitespace-normalized) substrings of the
    rendering named by artifact_ref; joined values carry one segment per
    joined piece. The runner gates every Extraction before it exists as
    data; the cascade itself never does (ADR-0002 keeps the gate pure).
    """
    field: str
    value: str
    segments: Tuple[str, ...]
    artifact_ref: str
    method: str
    tier: str
    context: Optional[Mapping] = None


def _emit(field, value, segments, source_ref, method, tier, context=None):
    return Extraction(field=field, value=value,
                      segments=tuple(s for s in segments if s),
                      artifact_ref=source_ref, method=method, tier=tier,
                      context=context)


# --------------------------------------------------- tier G: label patterns
# The shared label-pattern library — 27 (field, pattern_id, regex) entries
# ported verbatim from spike A extract_lib.LABEL_PATTERNS. Group 1 = value.
# Wording is institutional and survives redesigns (RFC v2 Q7); patterns are
# tried in order over each document's normalized text, first hit wins.
LABEL_PATTERNS = {
    "degree": [
        ("bg-oks-label", r'Степен:\s*(ОКС\s*[„"«][А-Яа-я ]+["”»])'),
        ("bg-oks-inline", r'(ОКС\s*[„"«][А-Яа-я][а-я ]*["”»])'),
        ("bg-okstepen", r'([Оо]бразователно\s*-?\s*квалификационна степен\s*[–-]?\s*[„"“]\s*[а-я ]+["”“])'),
        ("bg-pridobilite", r'Придобилите степен\s+([А-Я][А-Я ]+[А-Я])(?=\s+са)'),
        ("en-equal-degree", r'Educational qualification degree\s+([A-Z]+)'),
        ("vum-dual-bachelor", r'((?:accredited )?(?:Bulgarian )?Professional Bachelor degree from VUM and the British [^.]+? awarded by Cardiff Metropolitan University)'),
        ("vum-dual-master", r"(VUM Master[’']s degree in [^.]+? and the [^.]+? awarded by Cardiff Metropolitan University)"),
    ],
    "duration": [
        ("bg-semestri-label", r'(Продължителност на обучението \(брой семестри\):\s*[0-9а-я]+)'),
        ("bg-srok-label", r'(Срок на обучение[^:]{0,25}:\s*\d+\s*семестъра(?:\s*\(\d+\s*учебни години\))?)'),
        ("bg-godishen", r'(\d+-годишен период на обучение(?:\s*\([^)]+\))?)'),
        ("bg-srok-godini", r'(срок на обучение\s+\d+\s+години)'),
        ("en-nmonth-nterm", r'([a-z]+-month \([a-z]+-term\))'),
        ("en-duration-label", r'Duration of training\s+(\d+\s*[–-]\s*\d+\s+semesters)'),
        ("en-years-sem", r'Professional Bachelor\s+(\d(?:\.\d)? years? / \d semesters)'),
        ("en-sem-or-sem", r'(\d semesters or \d semesters)'),
    ],
    "language": [
        ("en-lang-label", r'Language of instruction\s+(English|Bulgarian|ENGLISH|BULGARIAN)'),
        ("en-taught-in", r'taught entirely in (English)'),
    ],
    "tuition": [
        ("en-semfee-label", r'Semester fee:\s*(\d[\d ,.]* (?:leva|лв|евро|EUR|€))'),
        ("bg-godtaksa-label", r'[Гг]одишна такса[^:]{0,20}:\s*(\d[\d ,.]*\s*(?:лв|евро|EUR|€))'),
    ],
    "admission": [
        # MU family: 'по избор' phrasing names the entrance test explicitly
        ("bg-test-po-izbor", r'могат да кандидатстват по избор с оценката от него или с (приемен изпит-тест по [а-я]+)'),
        ("bg-konkurs-label", r'Конкурс\s*:\s*(.+?)(?=\s*(?:Обучение\s*:|Прием\s*:|Реализация\s*:|$))'),
        ("bg-priemat-chrez", r'Приемът на студенти за специалността\s*[„"“]?[^.]*?се извършва чрез\s+(.+?)(?=,\s*както и|\s+както и|\.)'),
        ("en-open-to", r'(?:programme is )?open to (candidates holding a Bachelor[’\']s or Master[’\']s degree from any field of study)'),
        ("en-must-demonstrate", r'Applicants must demonstrate (English language proficiency equivalent to IELTS [\d.]+ or above)'),
        ("en-required-min", r'Applicants are required to have obtained a (minimum of a bachelor[’\']s degree from an accredited institution of higher education)'),
        ("en-dzi-b2", r'(?:may also be admitted based on their result from the )(State Matriculation Examination \(DZI\) in English language, provided that the exam corresponds to CEFR level B2 and the final grade is at least Very Good \(5\.00\))'),
        ("en-min-gpa", r'The (recommended minimum GPA for applicants is .+?scale\))'),
    ],
}


def harvest_labels(field, source, max_span=400):
    # type: (str, TextSource, int) -> Optional[Extraction]
    """Run the shared label library over one document (tier G)."""
    text = norm(source.text)
    for pid, rx in LABEL_PATTERNS[field]:
        m = re.search(rx, text)
        if m and len(m.group(1)) <= max_span:
            return _emit(field, norm(m.group(1)),
                         [snippet_around(text, m.start(), m.end())],
                         source.ref, "label:" + pid, TIER_G)
    return None


_TITLE_LANG_RX = re.compile(r'((?:с частично обучение )?на английски език)')


def language_from_name(name, source):
    # type: (str, TextSource) -> Optional[Extraction]
    """BG family rule (tier G): the language variant is printed in the
    program title itself; the snippet anchors on the title in the page."""
    m = _TITLE_LANG_RX.search(name)
    if not m:
        return None
    text = norm(source.text)
    i = text.find(name.split(" (")[0])
    if i < 0:
        i = 0
    return _emit("language", m.group(1),
                 [snippet_around(text, i, i + len(name), 60)],
                 source.ref, "title-language", TIER_G)


# ------------------------------------------------------- tier B: anchors
def anchor_probe(field, source, anchor):
    # type: (str, TextSource, AnchorConfig) -> Optional[Extraction]
    """One bespoke per-page anchor (tier B) — spike A's anchor() port.

    The anchor regex is site config, never shared code; group 1 is the
    value; the method records the anchor id so the per-anchor maintenance
    bill stays countable. Interim until the ticket-02 LLM tail replaces
    the tier (DEC-1/DEC-4)."""
    if not isinstance(source, TextSource):
        raise TypeError("anchor probe needs a TextSource, got "
                        + type(source).__name__)
    text = norm(source.text)
    m = re.search(anchor.pattern, text)
    if not m:
        return None
    return _emit(field, norm(m.group(1)),
                 [snippet_around(text, m.start(), m.end())],
                 source.ref, "anchor:" + anchor.id, TIER_B)


# ------------------------------------------- tier F: column-aware fee join
_PLACEHOLDER_CELLS = frozenset(["-", "–", "—", "n/a", "na"])

# Longest a cell can be and still read as a column LABEL rather than
# prose. Fee-table headers are short ("СПЕЦИАЛНОСТ", "Редовно обучение");
# a spreadsheet's title block runs to whole sentences.
_HEADER_CELL_MAX = 60


def _is_placeholder(cell):
    # type: (str) -> bool
    """A cell that prints a dash instead of a value. Verbatim-present in
    the artifact, so gate() cannot catch it -- it must be filtered at
    the resolver, like the neighbouring-column rule above it."""
    return cell.strip().casefold() in _PLACEHOLDER_CELLS


def _header_zone(table, join):
    """(start, end) of the header rows -- those mentioning the
    match-column header or a value-column token. Data starts at end.

    start is not assumed to be 0: a Docling-extracted table begins at
    its header row, but a spreadsheet SHEET carries a title/preamble
    block above it (an order number, a legal citation, a blank row), so
    the header is found rather than assumed. For a table whose first row
    IS the header this returns (0, n) exactly as before.
    """
    tokens = [join.match_header.casefold()]
    tokens += [t.casefold() for t in join.value_headers]

    def is_header(row):
        # A header token must appear in a short LABEL cell, not anywhere
        # in the row. A spreadsheet fee schedule opens with a prose
        # title ("...за специалностите, по които...") that mentions the
        # match header inside a sentence; treating that row as header
        # would stack its whole title into column 0's header text and
        # resolve the match column to 0 instead of the real
        # "СПЕЦИАЛНОСТ" column -- the wrong-column failure this join
        # exists to prevent (RFC v2 Q4).
        return any(t in cell.casefold()
                   for cell in row if len(cell) <= _HEADER_CELL_MAX
                   for t in tokens)

    start = None
    for i, row in enumerate(table):
        if is_header(row):
            start = i
            break
    if start is None:
        return 0, 0
    end = start
    while end < len(table) and is_header(table[end]):
        end += 1
    return start, end


def _resolve_columns(table, zone, join):
    """(match_col, value_col) located BY HEADER TEXT — never blind index.

    A column's header is its cells stacked across the header zone (fee
    tables carry a category row and a form row). The match column is the
    first whose header mentions match_header; the value column the first
    whose header mentions ALL value_headers tokens."""
    start, end = zone
    if end <= start:
        return None
    header_rows = table[start:end]
    ncols = max(len(row) for row in header_rows)
    stacks = [[row[i].casefold() for row in header_rows if i < len(row)]
              for i in range(ncols)]
    headers = [" ".join(s) for s in stacks]

    def specificity(col, tokens):
        """How many tokens this column states as a header cell of its OWN
        rather than merely mentioning inside a longer one.

        Ties are broken toward the column that literally says the word.
        A fee table's currency columns sit under one spanning title
        ("Семестриална такса, лв./евро") which, once merged cells are
        expanded, puts BOTH currency words into BOTH columns' stacks —
        so plain containment matches the лв. column for a value_headers
        of "евро" and silently ships the wrong currency. The лв. column
        only ever MENTIONS евро inside that title; the евро column has a
        cell that IS "евро"."""
        return sum(1 for t in tokens
                   if any(cell == t for cell in stacks[col]))

    match_col = None
    for i, header in enumerate(headers):
        if join.match_header.casefold() in header:
            match_col = i
            break

    wanted = [t.casefold() for t in join.value_headers]
    candidates = [i for i, header in enumerate(headers)
                  if all(t in header for t in wanted)]
    if match_col is None or not candidates:
        return None
    best = max(specificity(i, wanted) for i in candidates)
    value_col = next(i for i in candidates
                     if specificity(i, wanted) == best)
    return match_col, value_col


def _marker_row(table, marker):
    # type: (tuple, str) -> Optional[tuple]
    """The row of TABLE carrying MARKER as a WHOLE CELL, or None.

    Whole-cell, never substring: a real fee workbook numbers its tables
    "Приложение 1" ... "Приложение 10", and "Приложение 1" is a substring
    of "Приложение 10" — substring matching silently selects the wrong
    table, and every fee read from it gates green because the number
    really is printed there. Same wrong-row/column class the column
    resolution above exists to prevent (RFC v2 Q4)."""
    want = norm(marker)
    for row in table:
        if any(norm(cell) == want for cell in row):
            return row
    return None


def _select_tables(source, join):
    """(table, marker_row) pairs this join may read, in document order.

    Without a marker every table is a candidate and the first hit wins —
    safe only when one table can possibly match. With a marker, only the
    tables that actually carry it, and the marker's own row travels back
    so the value's provenance can QUOTE which table it came from rather
    than asserting it."""
    if join.table_marker is None:
        return [(t, None) for t in source.tables]
    out = []
    for t in source.tables:
        row = _marker_row(t, join.table_marker)
        if row is not None:
            out.append((t, row))
    return out


def fee_row_join(field, source, join, alias):
    # type: (str, TableSource, FeeRowJoin, str) -> Optional[Extraction]
    """Column-aware fee-table row join over the ACTUAL TSV artifact files.

    The value is the single (alias row x value column) cell. An alias row
    whose value cell is empty yields nothing — no positional fallback into
    neighbouring columns (the resolver-side fix for the gate's
    truthful-snippet-wrong-column blind spot, RFC v2 Q4).

    join.table_marker narrows WHICH table may be read (a workbook prices
    the same program several times, once per funding band); the marker
    row ships as an extra provenance segment, so "this came from
    Приложение 1" is quoted from the artifact, never asserted."""
    if not isinstance(source, TableSource):
        raise TypeError("fee-row join needs a TableSource, got "
                        + type(source).__name__)
    for table, marker_row in _select_tables(source, join):
        zone = _header_zone(table, join)
        cols = _resolve_columns(table, zone, join)
        if cols is None:
            continue
        _header_start, data_start = zone
        match_col, value_col = cols
        for row in table[data_start:]:
            if len(row) <= match_col or alias not in row[match_col]:
                continue
            cell = norm(row[value_col]) if value_col < len(row) else ""
            if not cell or _is_placeholder(cell):
                # No value in MY column: never read a neighbour. A dash
                # placeholder ("-", "–", "—") is how a fee schedule
                # writes "this program is not offered in this form" --
                # shipping it as a fee would be a fabricated value that
                # gate() would happily PASS, since the dash really is
                # printed in that cell.
                continue
            if join.value_pattern:
                m = re.match(join.value_pattern, cell)
                if m:
                    cell = m.group(1)
            context = dict(join.context)
            context["currency"] = "EUR" if "EUR" in cell else None
            segments = [norm(" ".join(row))]
            if marker_row is not None:
                # The marker row is quoted, not asserted: it is literal
                # text of this artifact, so gate() checks it like any
                # other segment and "which table this fee came from"
                # becomes evidence a human can re-read.
                segments.append(norm(" ".join(marker_row)))
                if join.funding:
                    context["funding"] = join.funding
                context["table_marker"] = join.table_marker
            return _emit(field, cell, segments, source.ref,
                         "fee-join:" + join.name, TIER_F, context)
    return None


# --------------------------------------- tier F: sectioned fee-table join
def _sectioned_hits(source, join, alias_pattern):
    """Scan all rows; track the active section; collect alias-row hits.

    Returns (hits, header_rows): hits are (track_or_None, fee, row_line)
    in document order; header_rows maps track label -> its verbatim
    section-header row line."""
    if not isinstance(source, TableSource):
        raise TypeError("sectioned-fee-row join needs a TableSource, got "
                        + type(source).__name__)
    alias_rx = re.compile(alias_pattern)
    fee_rx = re.compile(join.fee_pattern)
    section = None
    header_rows = {}
    hits = []
    for row in source.rows():
        joined = norm(" ".join(row))
        for spec in join.sections:
            if spec.match in joined:
                section = spec
                header_rows[spec.track] = joined
                break
        if alias_rx.search(joined):
            m = fee_rx.search(joined)
            if m:
                hits.append((section, m.group(1), joined))
    return hits, header_rows


def _tracks(hits):
    """Distinct non-foreign track labels, document order."""
    ordered = []
    for section, _, _ in hits:
        if section is not None and not section.foreign \
                and section.track not in ordered:
            ordered.append(section.track)
    return ordered


def sectioned_fee_join(field, source, join, alias_pattern):
    # type: (str, TableSource, SectionedFeeRowJoin, str) -> Optional[Extraction]
    """MU-family fee join: the fee is matched inside the alias row (this
    family's Docling grid shifts columns per row — measured — so the row
    is the deterministic unit); the currency is stated in the section
    header, which is why the header row is a provenance segment."""
    hits, header_rows = _sectioned_hits(source, join, alias_pattern)
    if not hits:
        return None
    section, fee, row_line = hits[0]
    value = fee + (" " + join.currency_suffix if join.currency_suffix else "")
    tracks = _tracks(hits)
    segments = []
    if section is not None:
        segments.append(header_rows.get(section.track, ""))
    segments.append(row_line)
    context = dict(join.context)
    context["tracks"] = tracks
    context["track_headers"] = {t: header_rows.get(t, "") for t in tracks}
    return _emit(field, value, segments, source.ref,
                 "fee-join:" + join.name, TIER_F, context)


def sectioned_language_join(source, join, alias_pattern):
    # type: (TableSource, SectionedFeeRowJoin, str) -> Optional[Extraction]
    """Language from track membership: which section headers the program's
    rows sit under. Provenance = the section-header rows + the program
    row, each a verbatim segment."""
    hits, header_rows = _sectioned_hits(source, join, alias_pattern)
    if not hits:
        return None
    tracks = _tracks(hits)
    if not tracks:
        return None
    segments = [header_rows[t] for t in tracks if header_rows.get(t)]
    segments.append(hits[0][2])
    context = dict(join.context)
    context["tracks"] = tracks
    context["track_headers"] = {t: header_rows.get(t, "") for t in tracks}
    return _emit("language", "; ".join(tracks), segments, source.ref,
                 "fee-join:" + join.name + "-language", TIER_F, context)


# ------------------------------------------- tier F: ordinance row/clause
def _fill_alias(template, alias):
    """Substitute the {alias} placeholder — plain string replacement with
    the re.escape()d alias, so regex braces in the template stay literal."""
    return template.replace(ALIAS_PLACEHOLDER, re.escape(alias))


def ordinance_join(source, join, alias):
    # type: (TextSource, OrdinanceJoin, str) -> Optional[Extraction]
    """SU-family admission ordinance joins over pdftotext -layout text.

    Row form: line-anchored '<program>  Редовна ... 1. изпит по X'.
    Clause fallback: the 'За допускане ... условия:' bullet block — the
    value is ONE bullet verbatim (composing bullets into one string fails
    the containment gate, rightly)."""
    if not isinstance(source, TextSource):
        raise TypeError("ordinance join needs a TextSource, got "
                        + type(source).__name__)
    if source.layout is None:
        raise ValueError(
            "ordinance join needs the raw pdftotext -layout surface, but "
            "TextSource {0!r} has none — the runner must supply "
            "layout".format(source.ref))
    raw = source.layout
    m = re.search(_fill_alias(join.row_pattern, alias), raw, re.M | re.S)
    if m:
        return _emit("admission", norm(m.group(join.row_value_group)),
                     [norm(m.group(0))], source.ref,
                     "ordinance-join:" + join.name + "-row", TIER_F)
    if join.clause_pattern:
        m = re.search(_fill_alias(join.clause_pattern, alias), raw)
        if m:
            bullets = [norm(b) for b in re.split(r'\n?\s*-\s+', m.group(1))
                       if norm(b)]
            pick = None
            if join.clause_pick_token:
                pick = next((b for b in bullets
                             if join.clause_pick_token in b), None)
            if pick is None:
                pick = bullets[0] if bullets else None
            if pick:
                return _emit("admission", pick, [norm(m.group(0))],
                             source.ref,
                             "ordinance-join:" + join.name + "-clause",
                             TIER_F)
    return None


# --------------------------------------- tier F: справочник sentence joins
def spravochnik_join(field, source, join, alias):
    # type: (str, TextSource, SpravochnikJoin, str) -> Optional[Extraction]
    """MU-family Справочник sentence join: degree/duration from the
    'Специалност „X" – ОКС ..., редовна форма, срок на обучение N години'
    sentence (named groups pick the field)."""
    if not isinstance(source, TextSource):
        raise TypeError("spravochnik join needs a TextSource, got "
                        + type(source).__name__)
    if field not in ("degree", "duration"):
        return None
    text = source.text
    m = re.search(_fill_alias(join.sentence_pattern, alias), text)
    if m:
        return _emit(field, norm(m.group(field)),
                     [snippet_around(text, m.start(), m.end(), 40)],
                     source.ref, "spravochnik-join:" + join.name, TIER_F)
    return None


def spravochnik_admission_join(source, join, alias):
    # type: (TextSource, SpravochnikJoin, str) -> Optional[Extraction]
    """Admission from the section-scoped rule (MU: чл. 30) — the alias must
    be named in the section for the rule to bind to the program."""
    if not isinstance(source, TextSource):
        raise TypeError("spravochnik join needs a TextSource, got "
                        + type(source).__name__)
    if not join.admission_pattern:
        return None
    m = re.search(_fill_alias(join.admission_pattern, alias), source.text)
    if not m:
        return None
    return _emit("admission", norm(m.group(1)), [norm(m.group(0)[-400:])],
                 source.ref,
                 "spravochnik-join:{0}-{1}".format(join.name,
                                                   join.admission_label),
                 TIER_F)


# ------------------------------------------- tier F: shared fees-page join
def fees_page_section(source, join, section_pattern):
    # type: (TextSource, FeesPageJoin, str) -> Optional[Extraction]
    """VUM-family shared fees page: the program's section label anchors a
    window in which the (group-free, config-supplied) value pattern must
    appear."""
    if not isinstance(source, TextSource):
        raise TypeError("fees-page join needs a TextSource, got "
                        + type(source).__name__)
    text = norm(source.text)
    rx = "{0}.{{0,{1}}}?(?P<value>{2})".format(
        section_pattern, join.window, join.value_pattern)
    m = re.search(rx, text)
    if not m:
        return None
    return _emit("tuition", norm(m.group("value")),
                 [snippet_around(text, m.start(), m.end())], source.ref,
                 "fees-page-section:" + join.name, TIER_F)


# ------------------------------------------------------------- the cascade
def _join_of(site, ref):
    return site.sources[ref.source].join


def _table(docs, ref):
    return docs.get(ref.source)


def resolve_field(site, program, field, docs):
    # type: (SiteConfig, ProgramConfig, str, Mapping) -> Optional[Extraction]
    """Resolve one Program field through the B/G/F cascade (spike A's e3
    resolution order: configured bespoke anchor first, then shared labels,
    then family joins).

    docs maps source keys to TextSource/TableSource values: program pages
    and extra/lang/adm/tuition pages are keyed by their URL, shared
    sources by their config source id. Keys absent from docs are skipped
    (the runner owns supplying what config names — a partial docs mapping
    extracts from what is there); a wrongly TYPED source raises.
    Returns an ungated Extraction, or None for the LLM tail."""
    # tier B first — a bespoke anchor explicitly configured for this field
    # wins over generic mechanisms (spike A's resolution order)
    anchor_id = program.field_anchors.get(field)
    if anchor_id is not None:
        anchor_cfg = site.anchors[anchor_id]
        source = docs.get(anchor_cfg.source)
        if source is not None:
            r = anchor_probe(field, source, anchor_cfg)
            if r:
                return r

    page = docs.get(program.page)

    text_docs = []
    if page is not None:
        text_docs.append(page)
    for url in program.extra_pages:
        if docs.get(url) is not None:
            text_docs.append(docs[url])
    for sid in program.extra_sources:
        if docs.get(sid) is not None:
            text_docs.append(docs[sid])

    if field == "language" and program.lang_page:
        if docs.get(program.lang_page) is not None:
            text_docs.append(docs[program.lang_page])

    if field == "admission":
        if program.spravochnik is not None:
            source = docs.get(program.spravochnik.source)
            if source is not None:
                alias = program.spravochnik.alias or program.name
                r = spravochnik_admission_join(
                    source, _join_of(site, program.spravochnik), alias)
                if r:
                    return r
        if program.admission_join is not None:
            source = docs.get(program.admission_join.source)
            if source is not None:
                r = ordinance_join(source,
                                   _join_of(site, program.admission_join),
                                   program.admission_join.alias)
                if r:
                    return r
        if program.adm_page and docs.get(program.adm_page) is not None:
            text_docs.append(docs[program.adm_page])

    if field in ("degree", "duration") and program.spravochnik is not None:
        source = docs.get(program.spravochnik.source)
        if source is not None:
            alias = program.spravochnik.alias or program.name
            r = spravochnik_join(field, source,
                                 _join_of(site, program.spravochnik), alias)
            if r:
                return r

    for source in text_docs:
        r = harvest_labels(field, source)
        if r:
            return r

    if field == "language":
        if page is not None:
            r = language_from_name(program.name, page)
            if r:
                return r
        if program.language_tracks is not None:
            source = _table(docs, program.language_tracks)
            if source is not None:
                r = sectioned_language_join(
                    source, _join_of(site, program.language_tracks),
                    program.language_tracks.alias_pattern)
                if r:
                    return r

    if field == "tuition":
        if program.tuition_join is not None:
            source = _table(docs, program.tuition_join)
            if source is not None:
                join = _join_of(site, program.tuition_join)
                if isinstance(join, SectionedFeeRowJoin):
                    r = sectioned_fee_join(
                        field, source, join,
                        program.tuition_join.alias_pattern)
                else:
                    r = fee_row_join(field, source, join,
                                     program.tuition_join.alias)
                if r:
                    return r
        if program.fees_section is not None:
            source = docs.get(program.fees_section.source)
            if source is not None:
                r = fees_page_section(
                    source, _join_of(site, program.fees_section),
                    program.fees_section.section_pattern)
                if r:
                    return r
        if program.tuition_page and docs.get(program.tuition_page) is not None:
            r = harvest_labels("tuition", docs[program.tuition_page])
            if r:
                return r

    return None


def extract_program(site, program, docs):
    # type: (SiteConfig, ProgramConfig, Mapping) -> Dict[str, Optional[Extraction]]
    """All five fields of one Program; None marks LLM-tail fall-through."""
    return {field: resolve_field(site, program, field, docs)
            for field in FIELDS}


def extract_site(site, docs):
    # type: (SiteConfig, Mapping) -> Dict[str, Dict[str, Optional[Extraction]]]
    """Every configured Program of one university, keyed by program id."""
    return {program.id: extract_program(site, program, docs)
            for program in site.programs}
