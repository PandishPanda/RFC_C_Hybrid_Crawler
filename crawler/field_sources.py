"""The Readable set (CONTEXT.md): the documents one Program-field may
draw values from — the Program's own page narrowed to its own regions,
its routed pages, and its extra sources, each participating once under
its Artifact ref.

This module exists because the selection policy lived TWICE: braided
into cascade.resolve_field's tier-order control flow, and restated in
llm_tail.candidate_docs (whose docstring admitted it "mirrors" the
first). The copies drifted twice, measured — the tail read shared pages
whole until 2026-08-23 (VUM: durations swapped between two Programs
sharing a page; admission complete for one, a fragment for the other —
all verbatim, so the Provenance gate structurally cannot catch it), and
the don't-re-add-the-page guard was spelled four times in cascade and
zero times in the tail. Both extractors now consume ONE return value,
which is what makes their agreement a construction rather than a
promise.

Two invariants, each with a dedicated test:

1. NO-SPANS — a shared page that never names the Program yields no view
   of that page at all, for either consumer (the honest-null path).
2. REF-IDENTITY, SCOPED VIEW WINS — a document participates once, under
   its Artifact ref. If any config attribute (extra_pages,
   extra_sources, a routed page) routes in a document whose ref equals
   the shared own page's, the scoped view is what participates: the
   whole-page text cannot re-enter under an alias. (The live-shaped
   hazard: ANIS's 18-program shared page is also the configured source
   anis-master-catalog — one config line would have defeated region
   scoping in BOTH former copies, and no downstream check can see that
   class.)

Join sources (spravochnik, fee orders, ordinances) are deliberately NOT
routed through this module: they are human-attributed config executed
as tier-F mechanisms, their listing has never drifted, and nothing has
ever varied across that seam (one adapter = hypothetical seam). Widen
only if a drift appears there.

This module also owns program_region and the source types it scopes —
the scoping engine and the selection belong together; cascade re-exports
every moved name so existing imports and tests stay valid.

Python 3.9, stdlib only.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

__all__ = [
    "norm", "TextSource", "TableSource", "program_region",
    "REGION_WINDOW", "ScopedDoc", "FIELD_PAGES", "readable_sources",
]

_WS_RX = re.compile(r"\s+")


def norm(s):
    # type: (Optional[str]) -> str
    """Spike A's whitespace collapse — case and characters PRESERVED.

    Segments must stay verbatim (the gate applies its own one
    normalization policy to both sides); this only folds runs of
    whitespace so pattern offsets are stable across renderings."""
    return _WS_RX.sub(" ", s or "").strip()



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



def _find_all(haystack, needle):
    # type: (str, str) -> list
    """Word-bounded occurrences: a name embedded in a longer word is not
    an occurrence (measured on ANIS: «Финанси» matched inside
    «финансиране», opening a span beside another program's block)."""
    out = []
    i = haystack.find(needle)
    while i >= 0:
        before = haystack[i - 1] if i > 0 else " "
        after_i = i + len(needle)
        after = haystack[after_i] if after_i < len(haystack) else " "
        if not before.isalpha() and not after.isalpha():
            out.append(i)
        i = haystack.find(needle, i + 1)
    return out



# The farthest a claim may sit from the program's name and still be
# attributed to it, in normalized chars. Every measured TRUE section in
# the 8-university benchmark fits in ~1,600; every measured CONTAMINATION
# sat >=3,602 past the nearest anchor. Unbounded spans encode the false
# assumption that configured programs tile the page -- the UniRuse
# philology block leaked into the LAST configured program's span exactly
# that way. Too small fails to a null; too large fails to a wrong value;
# the constant is chosen so measured sections fit with headroom.
REGION_WINDOW = 2500


# a trailing parenthetical on a programme name is an entry-route or
# cohort qualifier, not part of what pages call the programme
_BASE_NAME_RX = re.compile(r"\s*\([^)]*\)\s*$")


def _is_caps_heading(text, pos, ln):
    # type: (str, int, int) -> bool
    """True when the occurrence at POS is fully uppercase and its line
    holds nothing else (whitespace and zero-width characters aside) --
    the shape section headings take in rendered page text."""
    occ = text[pos:pos + ln]
    if occ != occ.upper() or not any(c.isalpha() for c in occ):
        return False
    lo = text.rfind("\n", 0, pos) + 1
    hi = text.find("\n", pos + ln)
    if hi < 0:
        hi = len(text)
    line = text[lo:hi]
    for ch in ("\u200b", "\ufeff"):
        line = line.replace(ch, "")
    # Deliberately EXACT: UniRuse writes «СОФТУЕРНО ИНЖЕНЕРСТВО -», and
    # accepting a punctuation tail would activate heading-anchoring on
    # its faculty pages. Measured 2026-08-22: doing so removed no bad
    # value and cost two correct ones, because those pages need the
    # matching BOUNDARY rule too (a section must end at the next
    # HEADING, not at a prose mention of a sibling — «икономика»
    # occurring in Социални дейности's prose truncated its section
    # before its own admission formula). Loosen this only together with
    # that change, and re-measure.
    return line.strip() == occ


def program_region(text, name, sibling_names):
    # type: (str, str, list) -> list
    """Ordered [(start, end)] spans of a SHARED page that belong to the
    named program -- the only part of the page its tier-G harvest may
    read (2026-08-22 attribution work; measured contamination: MUVarna
    shipped one program's degree to two others, UniRuse gave two
    bachelor programs a master's degree lifted from a third section).

    Every case-insensitive occurrence of NAME anchors a span; each span
    runs to the next occurrence of a DISTINCT sibling name (same-named
    variants -- VUM's pb/b twins -- never bound each other). Multiple
    occurrences all anchor because real pages repeat names in nav lists
    before the real heading. Overlapping spans merge. No occurrence of
    NAME at all means NO spans: a shared page that never names the
    program must not feed it values.

    EXCEPT: when the page marks the program's section with a CAPS
    HEADING -- the name fully uppercase, alone on its line (zero-width
    spaces and whitespace aside) -- only heading occurrences anchor.
    A prose MENTION of the name inside a foreign section otherwise
    opens a window into that section's claims (measured 2026-08-22:
    MUVarna's «„Акушерка“» in a neighbour's closing sentence reached
    the unconfigured «ФАРМАЦЕВТИЧЕН МЕНИДЖМЪНТ» block and shipped its
    «магистър» for the bachelor programme). Pages with no caps heading
    (UniRuse, ANIS) keep mention anchoring unchanged; the rule only
    SHRINKS regions, so its failure mode is a null, never a wrong
    value.

    Plain substring matching, deliberately: names are config data, not
    regexes, and a false boundary from a name collision only SHRINKS a
    span -- the conservative failure is a null, never a wrong value.
    """
    low = text.lower()
    if len(low) != len(text):
        # str.lower() expanded some character (e.g. 'İ' -> 2 chars), so
        # lowered indexes would mis-slice the original. Degrade to
        # case-sensitive matching -- the conservative failure is a
        # smaller region and a null, never a mis-attributed value.
        low = text
    own = name.lower()
    distinct = []
    seen = {own}
    for sib in sibling_names:
        s = sib.lower()
        if s not in seen:
            seen.add(s)
            distinct.append(s)
    # Longest-name-wins where names nest: «Финанси» occurring as a whole
    # word inside «Международни финанси» belongs to the LONGER name, both
    # as an anchor and as a boundary (review finding, 2026-08-22).
    occ = {n: _find_all(low, n) for n in [own] + distinct}

    def _suppressed(pos, ln, myname):
        for other, positions in occ.items():
            if len(other) <= ln or other == myname:
                continue
            for p in positions:
                if p <= pos and pos + ln <= p + len(other):
                    return True
        return False

    anchors = [a for a in occ[own] if not _suppressed(a, len(own), own)]
    headings = [a for a in anchors if _is_caps_heading(text, a, len(own))]
    if headings:
        anchors = headings
    if not anchors:
        return []
    boundaries = sorted(
        b for n in distinct for b in occ[n]
        if not _suppressed(b, len(n), n))
    spans = []
    for a in anchors:
        end = next((b for b in boundaries if b > a), len(text))
        spans.append((a, min(end, a + REGION_WINDOW)))
    spans.sort()
    merged = [spans[0]]
    for s0, e0 in spans[1:]:
        ps, pe = merged[-1]
        if s0 <= pe:
            merged[-1] = (ps, max(pe, e0))
        else:
            merged.append((s0, e0))
    return merged




# ------------------------------------------------------------ the Readable set
class ScopedDoc(NamedTuple):
    """One document of a Readable set, narrowed to what the Program may
    read of it.

    ref     the Artifact ref — the document's sole identity (the store
            indexes by it; lookup keys — URL, source id — never leave
            this module, which is the lesson of the 2026-08-15 UniRuse
            live bug where a model quoted a lookup key as its ref and
            every test fixture had masked it by setting ref == key)
    spans   raw text regions in document order; the whole text when the
            document is unscoped. Raw, not normalized: program_region's
            caps-heading rule needs line structure, and each consumer
            applies its own normalization (measured 2026-08-22: norming
            first made the rule inert in production while its unit
            tests stayed green)
    source  the ORIGINAL TextSource/TableSource when unscoped — passed
            through untouched so table grids and layout text survive;
            None when region-scoped
    late    tuition_page only: harvested AFTER the tier-F joins, so a
            fee-table row beats a tuition_page label, exactly as the
            cascade has always ordered them
    """
    ref: str
    spans: Tuple[str, ...]
    source: Optional[object] = None
    late: bool = False

    def harvest_views(self):
        # type: () -> list
        """What tier-G harvest reads: the original source when unscoped,
        else one whitespace-normalized TextSource per region span —
        spans stay separate because harvest is first-hit-wins in
        document order, and a correct value in the Program's own
        section must beat later-span poison regardless of pattern
        order."""
        if self.source is not None:
            return [self.source]
        return [TextSource(ref=self.ref, text=norm(s)) for s in self.spans]

    def model_view(self):
        # type: () -> object
        """What the LLM tail shows the model: the original source when
        unscoped, else the raw spans joined — one document, narrowed,
        under the same ref (the gate still checks quoted snippets
        against the FULL artifact, so scoping can only narrow what the
        model may read, never change what counts as proof)."""
        if self.source is not None:
            return self.source
        return TextSource(ref=self.ref, text="\n".join(self.spans))


# field -> the config attributes that may route extra documents to it,
# in participation order. Data, like LABEL_PATTERNS: wiring a new field
# or page kind is a row here, not control flow (ADR-0001's line — site
# knowledge is config data — applied to the code's own knowledge of the
# config). The (attr, late) pairs join the always-readable base set
# (own page, extra_pages, extra_sources).
FIELD_PAGES = {
    "degree": (),
    "duration": (),
    "language": (("lang_page", False),),
    "admission": (("adm_page", False),),
    # tuition_page is LATE: the cascade has always tried the fee-table
    # joins first and harvested tuition_page only when they missed —
    # reordering would let a page label beat a fee row
    "tuition": (("tuition_page", True),),
}


def readable_sources(site, program, field, docs):
    # type: (object, object, str, dict) -> List[ScopedDoc]
    """The Readable set for one Program-field, in participation order.

    docs maps lookup keys (page URLs, source ids) to TextSource/
    TableSource values, exactly as runner.build_docs supplies it; keys
    absent from docs are skipped (the runner owns supplying what config
    names). The return value is consumed by BOTH extractors:
    cascade.resolve_field harvests each entry's harvest_views() and the
    LLM tail shows each entry's model_view() — one list, two adapters,
    agreement by construction.
    """
    out = []
    claimed_refs = set()
    # the own page's URL is claimed even when its fetch failed: a routed
    # page or extra entry equal to the shared page must never re-enter
    # whole, whether or not the page itself made it into docs
    shared = any(p.page == program.page and p.id != program.id
                 for p in site.programs)
    claimed_keys = {program.page} if shared else set()

    page = docs.get(program.page)
    if page is not None:
        claimed_refs.add(page.ref)
        siblings = [p.name for p in site.programs
                    if p.page == program.page and p.id != program.id]
        if siblings and isinstance(page, TextSource):
            raw = page.text or ""
            spans = tuple(raw[s0:e0] for s0, e0
                          in program_region(raw, program.name, siblings))
            if spans:
                out.append(ScopedDoc(ref=page.ref, spans=spans))
            # no spans: the page never names the Program — it yields
            # nothing, but its ref stays claimed (invariant 1 + 2)
        else:
            out.append(_whole(page))

    def add(key, late=False):
        if not key:
            return
        source = docs.get(key)
        if source is None:
            return
        if key in claimed_keys or source.ref in claimed_refs:
            return
        claimed_refs.add(source.ref)
        out.append(_whole(source, late))

    for url in program.extra_pages:
        add(url)
    for sid in program.extra_sources:
        add(sid)
    for attr, late in FIELD_PAGES[field]:
        add(getattr(program, attr), late=late)
    return out


def _whole(source, late=False):
    # type: (object, bool) -> ScopedDoc
    text = source.text if isinstance(source, TextSource) else ""
    return ScopedDoc(ref=source.ref, spans=(text,), source=source,
                     late=late)
