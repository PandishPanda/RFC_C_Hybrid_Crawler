"""Program status adjudication + repair queue (ticket 05, CONTEXT.md
Coverage, ADR-0002).

Coverage's true denominator is RSVU registry rows (crawler.registry), not
a university's own advertised program list. Most registry rows already
map to a configured Program (ProgramConfig.rsvu_code) and are Covered by
the ordinary extraction run. For the rest, this proposes a status --
today only the affirmative "enrolling" case: the row's name found,
verbatim, inside an already-resolved Artifact from this run -- through the
SAME crawler.provenance.gate() every other tier feeds (ADR-0002: no local
containment checks invented here). A row whose name appears nowhere in
the fetched pages has no "we offer this" text to quote, so it is left
UNRESOLVED in the repair queue rather than guessed at -- that is the
correct outcome for a row nobody has onboarded yet (ticket 06's job), not
a false Covered and not a false not-enrolling.

Repair-queue entries also accept a human resolution
(resolve_repair_entry()) for the judgment calls the gate can't make on its
own -- explicitly not-enrolling, page-gone, variant-of an already-covered
row. A human resolution still must name a real source_url + verbatim
segment from a real, store-resolved Artifact and pass the same gate() --
there is no exemption for humans (ADR-0002). A variant-of resolution
additionally names variant_of_row_id, checked against a real row in the
loaded registry export -- an unchecked int naming a nonexistent row would
be the same class of unverifiable claim the gate already forbids for
VALUE/SEGMENT.

RepairEntry is per ROW, not per FIELD -- this queue resolves a row's
enrollment status, which has no field to name (unlike the extraction
cascade's own field-level repair queue, RFC v2 §2's general description).
Where a row's name IS found on a fetched page but the gate rejects it,
the entry's `candidate` carries that evidence (segment + artifact_ref +
the rejecting verdict) so a human starts from something instead of
re-searching from scratch; `candidate` is None only when the name
appears on no fetched page at all -- a distinct, and worse, case that a
flat reason string alone used to leave indistinguishable from "found but
ungate-able".

Resolutions (automatic or human) are persisted to
<out_dir>/<UniID>/resolutions.json so a later run doesn't silently reopen
a human's not-enrolling/page-gone/variant-of call -- adjudicate() treats
any row with a durable resolution as settled without re-deriving it.

Known, documented blind spot (mirrors provenance.py's row/column one):
propose_enrolling() matches on NAME ONLY, with no degree-level
disambiguation. Two registry rows can share an identical name at
different degree levels (VUM's own data has three "Бизнес администрация"
rows: professional-bachelor, bachelor, master) -- a single page mentioning
that name will gate-PASS and mark ALL same-named rows "enrolling"
regardless of which level(s) the page actually documents. The fix is a
degree-level-aware resolver, never a local relaxation of the gate.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

from crawler import cascade
from crawler.config import load_configs_dir
from crawler.provenance import Status, gate
from crawler.registry import RegistryExport, RegistryRow, load_captured_export
from crawler.runner import (
    DEFAULT_CONFIGS_DIR,
    DEFAULT_OUT_ROOT,
    build_docs,
    build_fetcher_and_store,
)

__all__ = [
    "AdjudicationStatus", "Resolution", "RepairEntry", "AdjudicationReport",
    "covered_codes", "propose_enrolling", "adjudicate",
    "write_repair_queue", "read_repair_queue", "resolve_repair_entry",
    "unresolve",
    "read_resolutions", "run_adjudication", "QUEUE_NAME", "RESOLUTIONS_NAME",
]

QUEUE_NAME = "repair-queue.json"
RESOLUTIONS_NAME = "resolutions.json"


class AdjudicationStatus(Enum):
    ENROLLING = "enrolling"
    NOT_ENROLLING = "not-enrolling"
    VARIANT_OF = "variant-of"
    PAGE_GONE = "page-gone"


@dataclass(frozen=True)
class Resolution:
    """A gate-PASSed status claim for one registry row.

    variant_of_row_id is only meaningful when status is VARIANT_OF -- the
    id of the registry row this one is a variant of. resolve_repair_entry
    validates it against a real loaded registry export before a
    Resolution is ever constructed with it set (ADR-0002: no exemption
    for humans -- a variant-of claim naming a nonexistent row is exactly
    as invalid as a fabricated segment)."""
    row_id: int
    status: AdjudicationStatus
    value: str
    segments: Tuple[str, ...]
    source_url: str
    retrieved_at: str
    method: str
    variant_of_row_id: Optional[int] = None


@dataclass(frozen=True)
class RepairEntry:
    """One open repair-queue item -- a registry row nobody has resolved.

    This queue is per ROW (a row's enrollment status), not per FIELD --
    unlike the extraction cascade's field-level repair queue (RFC v2 §2's
    general description), status adjudication has no field to name, only
    a row.

    candidate, when not None, is the closest thing to evidence this row
    has: the row's name WAS found on a fetched page but the gate rejected
    it (segment + artifact_ref + the rejecting verdict), so a human can
    re-run gate() themselves against the same artifact rather than
    starting from nothing. None means the name appears on no fetched page
    at all -- a genuinely different, and worse, case that a flat "reason"
    string alone doesn't distinguish."""
    row_id: int
    row_code: str
    row_name: str
    reason: str
    opened_at: str
    candidate: Optional[Mapping] = None


@dataclass(frozen=True)
class AdjudicationReport:
    uni_id: str
    total_rows: int
    covered_by_config: Tuple[int, ...]   # row ids matched via rsvu_code
    resolved: Tuple[Resolution, ...]      # row ids affirmatively resolved
    queue: Tuple[RepairEntry, ...]        # unresolved -> repair queue

    @property
    def covered_count(self):
        return len(self.covered_by_config) + len(self.resolved)

    @property
    def coverage(self):
        return self.covered_count / self.total_rows if self.total_rows else 0.0


def covered_codes(site):
    # type: (object) -> set
    """RSVU codes already matched to a configured Program."""
    return {p.rsvu_code for p in site.programs if p.rsvu_code}


def _iter_text_sources(docs):
    seen = set()
    for source in docs.values():
        if not isinstance(source, cascade.TextSource):
            continue
        if source.ref in seen:
            continue
        seen.add(source.ref)
        yield source


def _name_pattern(name):
    # type: (str) -> re.Pattern
    """Word-bounded, case-insensitive pattern for NAME.

    \\b anchors on both ends so a short/common name (e.g. "Маркетинг")
    cannot match as a bare substring inside a longer unrelated word (e.g.
    "маркетингови"). This does NOT stop a match inside an unrelated but
    word-bounded mention of the same name elsewhere on the page (a
    faculty's own name, an admission-exam subject list, a list of
    constituent subjects taught) -- that is a distinct, structural limit:
    gate() can confirm the name's tokens are verbatim-quoted from the
    artifact, never that this particular occurrence asserts "this program
    is offered here" rather than something else. Same class of limit as
    ADR-0003's join/alias-correspondence gap, one layer up.
    """
    return re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)


# Words a page uses to announce "what follows is a program we offer".
# Bulgarian first (most of the corpus), then the English forms AUBG and
# VUM actually use -- a Bulgarian-only marker set would mean those two
# universities could never auto-resolve at all. This default is a
# STARTING POINT, not site knowledge: a site whose vocabulary differs
# overrides it via SiteConfig.program_markers, per ADR-0001 ("site
# knowledge is config data ... no per-site code in the refresh loop").
_DEFAULT_DECLARATION_MARKERS = (
    "специалност", "специалността", "магистърска програма",
    "programme", "program", "major", "degree in",
)
_DECLARATION_MARKER_WINDOW = 80


def _marker_pattern(markers):
    # type: (Tuple[str, ...]) -> re.Pattern
    return re.compile(
        "|".join(r"\b" + re.escape(m) for m in markers), re.IGNORECASE)


_DECLARATION_MARKER_RX = _marker_pattern(_DEFAULT_DECLARATION_MARKERS)


def _near_declaration_marker(text, start, *, window=_DECLARATION_MARKER_WINDOW,
                             marker_rx=None):
    # type: (str, int, int, Optional[re.Pattern]) -> bool
    """True if a program-declaration marker appears shortly before START
    in TEXT.

    Found live on UniRuse 2026-08-15: a row's name matching SOMEWHERE on
    an already-fetched page is not evidence the page is declaring that
    program offered there -- "Електроника" matched inside a different
    faculty's own name ("Факултет Електротехника, електроника и
    автоматика"); "Право"/"Маркетинг" matched inside a list of subjects
    a DIFFERENT program teaches ("икономика, право, маркетинг, финанси и
    счетоводство"); exam-subject names matched inside an admission-exam
    list. Every genuine match found the same day was immediately preceded
    by "Специалност" -- a heading marker announcing an actual program
    declaration. This narrows the CANDIDATE set propose_enrolling
    gate-checks; it never relaxes gate() itself (adjudication.py's own
    docstring: "the fix is a degree-level-aware resolver, never a local
    relaxation of the gate").
    """
    before = text[max(0, start - window):start]
    return bool((marker_rx or _DECLARATION_MARKER_RX).search(before))


def propose_enrolling(row, docs, store, *, pad=60, markers=None):
    # type: (RegistryRow, Mapping, object, int, Optional[Tuple[str, ...]]) -> Optional[Resolution]
    """Search every already-resolved text Artifact for ROW's name.

    Searches cascade.norm(source.text) -- the same whitespace-collapsed
    (case/characters preserved) text every other tier searches, so a name
    split across inline markup (a <span>/<strong> inside a heading) still
    matches, exactly like harvest_labels/anchor_probe. Returns a
    Resolution on the first gate-PASS occurrence that also sits near a
    declaration marker (see _near_declaration_marker); None if no such
    occurrence exists in the fetched pages. Absence is not evidence of
    non-enrollment -- it just means nobody has onboarded this row's page
    yet, or the name only appears incidentally (see below).

    Word-bounded name matching (_name_pattern) plus a declaration-marker
    proximity check (_near_declaration_marker) -- narrows WHICH occurrence
    of the name is treated as a candidate, before gate() ever runs on it.
    Every occurrence in a source's text is checked in order (finditer, not
    search) so a name that appears once incidentally and once as a real
    declaration on the SAME page still resolves correctly. A PASS here
    still only means the name's tokens are verbatim in the matched
    segment, gate-checked exactly as before -- a human should treat every
    "adjudication:name-match" resolution as a proposal, not settled fact,
    until the segment is read.

    markers overrides the default declaration vocabulary for a site whose
    pages announce programs differently (SiteConfig.program_markers).
    """
    needle_rx = _name_pattern(row.name)
    marker_rx = _marker_pattern(markers) if markers else None
    for source in _iter_text_sources(docs):
        text = cascade.norm(source.text)
        for m in needle_rx.finditer(text):
            if not _near_declaration_marker(text, m.start(),
                                            marker_rx=marker_rx):
                continue
            segment = cascade.snippet_around(text, m.start(), m.end(), pad=pad)
            artifact = store.artifact(source.ref)
            verdict = gate(row.name, [segment], artifact)
            if verdict.status is not Status.PASS:
                continue
            doc = store.doc(source.ref)
            return Resolution(
                row_id=row.id, status=AdjudicationStatus.ENROLLING,
                value=row.name, segments=(segment,),
                source_url=doc.source_url, retrieved_at=doc.retrieved_at,
                method="adjudication:name-match")
    return None


def _find_near_miss(row, docs, store, *, pad=60):
    # type: (RegistryRow, Mapping, object, int) -> Optional[Mapping]
    """The first occurrence of ROW's name that propose_enrolling did NOT
    turn into a resolution -- surfaced on the row's repair-queue entry so
    "the name is on a page but wasn't accepted" stays distinguishable
    from "the name appears nowhere".

    Two distinct near-miss shapes, both reported (the queue entry's
    gate_status says which):

      * the gate rejected the segment -- the name is there but the
        quoted evidence doesn't hold up;
      * the gate PASSED but no declaration marker sits near the match --
        the name is genuinely, verbatim on the page, just not in a
        position that asserts the program is offered there (a faculty
        name, a subject list). Before the marker check existed, a PASS
        here was impossible by construction; now it is the COMMON case,
        and reporting candidate=None for it would tell a human "no
        fetched page mentions this row's name" when a page plainly
        does.
    """
    needle_rx = _name_pattern(row.name)
    for source in _iter_text_sources(docs):
        text = cascade.norm(source.text)
        for m in needle_rx.finditer(text):
            segment = cascade.snippet_around(text, m.start(), m.end(), pad=pad)
            artifact = store.artifact(source.ref)
            verdict = gate(row.name, [segment], artifact)
            doc = store.doc(source.ref)
            marker_near = _near_declaration_marker(text, m.start())
            if verdict.status is Status.PASS and marker_near:
                # propose_enrolling would already have resolved this;
                # nothing to report as a near miss.
                continue
            return {
                "segment": segment,
                "artifact_ref": source.ref,
                "source_url": doc.source_url,
                "retrieved_at": doc.retrieved_at,
                "gate_status": verdict.status.value,
                "gate_detail": verdict.detail,
                "declaration_marker_near": marker_near,
            }
    return None


def adjudicate(uni_id, site, registry, docs, store, *, resolved_by_id=None,
               prior_opened_at=None, now=None):
    # type: (str, object, RegistryExport, Mapping, object, Optional[Mapping], Optional[Mapping], Optional[str]) -> AdjudicationReport
    """Classify every registry row: config-covered, durably/freshly
    resolved, or queued for repair. Never mutates ledger/pointer state --
    this only produces a report; the caller (run_adjudication) decides
    what to persist.

    resolved_by_id   {row_id: Resolution} from a prior run's
                     resolutions.json -- these are taken as settled
                     without re-deriving them (a human's not-enrolling
                     call must survive a later re-run).
    prior_opened_at  {row_id: opened_at} from a prior run's open queue --
                     preserves "how long has this been open" instead of
                     resetting it on every run.

    Raises ValueError if site.rsvu_id disagrees with the loaded registry
    export, or if a configured rsvu_code matches no row in it -- both are
    data-integrity bugs that must fail loudly, never silently under-count.
    """
    resolved_by_id = resolved_by_id or {}
    prior_opened_at = prior_opened_at or {}

    if site.rsvu_id is not None and site.rsvu_id != registry.rsvu_uni_id:
        raise ValueError(
            "{0}: configured rsvu_id={1} does not match the loaded "
            "registry export's rsvu_uni_id={2} -- wrong export file, or "
            "the config's rsvu_id is stale".format(
                uni_id, site.rsvu_id, registry.rsvu_uni_id))

    registry_codes = {row.code for row in registry.rows}
    codes = covered_codes(site)
    orphan_codes = codes - registry_codes
    if orphan_codes:
        raise ValueError(
            "{0}: configured rsvu_code(s) {1} match no row in the loaded "
            "registry export -- stale or typo'd code(s)".format(
                uni_id, sorted(orphan_codes)))

    opened_at = now or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    markers = getattr(site, "program_markers", None) or None
    covered_ids = []
    resolved = []
    queue = []
    for row in registry.rows:
        if row.code in codes:
            covered_ids.append(row.id)
            continue
        if row.id in resolved_by_id:
            resolved.append(resolved_by_id[row.id])
            continue
        resolution = propose_enrolling(row, docs, store, markers=markers)
        if resolution is not None:
            resolved.append(resolution)
            continue
        candidate = _find_near_miss(row, docs, store)
        if candidate is None:
            reason = (
                "no configured Program and no fetched page mentions this "
                "row's name -- needs onboarding (ticket 06) or a human "
                "status call (resolve_repair_entry)")
        elif candidate["declaration_marker_near"]:
            reason = (
                "this row's name was found on a fetched page but the gate "
                "rejected it -- see candidate; a human can resolve from "
                "real evidence (resolve_repair_entry) without re-searching")
        else:
            reason = (
                "this row's name IS on a fetched page and gate-checkable, "
                "but not next to any program-declaration marker -- it "
                "reads as an incidental mention (a faculty name, a "
                "subject list), not an offer. See candidate and judge the "
                "segment (resolve_repair_entry)")
        queue.append(RepairEntry(
            row_id=row.id, row_code=row.code, row_name=row.name,
            reason=reason, opened_at=prior_opened_at.get(row.id, opened_at),
            candidate=candidate))
    return AdjudicationReport(
        uni_id=uni_id, total_rows=len(registry.rows),
        covered_by_config=tuple(covered_ids), resolved=tuple(resolved),
        queue=tuple(queue))


# ------------------------------------------------------------ repair queue
def _queue_path(out_dir, uni_id):
    return Path(out_dir) / uni_id / QUEUE_NAME


def _resolutions_path(out_dir, uni_id):
    return Path(out_dir) / uni_id / RESOLUTIONS_NAME


def write_repair_queue(out_dir, uni_id, entries):
    # type: (str, str, Tuple[RepairEntry, ...]) -> str
    path = _queue_path(out_dir, uni_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [dict(row_id=e.row_id, row_code=e.row_code,
                    row_name=e.row_name, reason=e.reason,
                    opened_at=e.opened_at,
                    candidate=dict(e.candidate) if e.candidate else None)
              for e in entries]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return str(path)


def read_repair_queue(out_dir, uni_id):
    # type: (str, str) -> List[RepairEntry]
    path = _queue_path(out_dir, uni_id)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [RepairEntry(**entry) for entry in data]


def _resolution_from_dict(r):
    return Resolution(
        row_id=r["row_id"], status=AdjudicationStatus(r["status"]),
        value=r["value"], segments=tuple(r["segments"]),
        source_url=r["source_url"], retrieved_at=r["retrieved_at"],
        method=r["method"],
        variant_of_row_id=r.get("variant_of_row_id"))


def read_resolutions(out_dir, uni_id):
    # type: (str, str) -> List[Resolution]
    path = _resolutions_path(out_dir, uni_id)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [_resolution_from_dict(r) for r in data]


def _write_resolutions(out_dir, uni_id, resolutions):
    # type: (str, str, List[Resolution]) -> str
    path = _resolutions_path(out_dir, uni_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [dict(row_id=r.row_id, status=r.status.value, value=r.value,
                    segments=list(r.segments), source_url=r.source_url,
                    retrieved_at=r.retrieved_at, method=r.method,
                    variant_of_row_id=r.variant_of_row_id)
              for r in resolutions]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return str(path)


def write_resolution(out_dir, uni_id, resolution):
    # type: (str, str, Resolution) -> str
    """Append/replace ONE resolution durably (keyed by row_id) -- the
    fix for resolutions not surviving a later adjudicate() re-run."""
    existing = [r for r in read_resolutions(out_dir, uni_id)
               if r.row_id != resolution.row_id]
    existing.append(resolution)
    return _write_resolutions(out_dir, uni_id, existing)


def unresolve(out_dir, uni_id, row_id):
    # type: (str, str, int) -> Resolution
    """Durably remove ROW_ID's resolution from resolutions.json so a
    subsequent adjudicate()/run_adjudication() call treats the row as
    open again -- re-attempts propose_enrolling, falling through to the
    repair queue if it still doesn't resolve. Validates the row actually
    has a resolution BEFORE touching disk (same discipline as
    resolve_repair_entry): raises KeyError for a row_id with no
    resolution, never silently no-ops. Returns the removed Resolution so
    a caller can log/display what was undone.

    Nothing else in this module can reverse a resolution once made -- a
    bad auto-resolution (or a human's own mistake) was otherwise
    permanent."""
    existing = read_resolutions(out_dir, uni_id)
    match = next((r for r in existing if r.row_id == row_id), None)
    if match is None:
        raise KeyError(
            "no resolution for row {0} -- nothing to unresolve".format(
                row_id))
    remaining = [r for r in existing if r.row_id != row_id]
    _write_resolutions(out_dir, uni_id, remaining)
    return match


def resolve_repair_entry(out_dir, uni_id, row_id, status, *, value, segment,
                         artifact, source_url, retrieved_at,
                         method="human-review", variant_of_row_id=None,
                         registry=None):
    # type: (...) -> Resolution
    """One-keystroke resolve: a human supplies a real source_url + verbatim
    segment from a real Artifact; it is gated exactly like an automatic
    proposal (ADR-0002 -- no exemption for humans).

    Validates STATUS, VARIANT_OF_ROW_ID (when status is variant-of), and
    checks the row is actually open BEFORE touching disk, and gates
    BEFORE mutating anything -- an invalid status, an unresolvable
    variant target, or a rejected gate call must never remove an entry
    from the queue without a durable record replacing it (the entry
    would otherwise vanish: neither open nor resolved). On success the
    resolution is written to resolutions.json (durable across future
    adjudicate() runs) and the entry is removed from the open queue.

    variant_of_row_id names the row THIS one is a variant of -- required
    when status="variant-of", meaningless otherwise. registry (the same
    RegistryExport adjudicate()/run_adjudication() load) is required
    alongside it, so the target can be checked against a REAL row rather
    than trusted as a bare int -- the same "no exemption for humans"
    discipline the gate() call below already applies to VALUE/SEGMENT."""
    status_enum = AdjudicationStatus(status)  # raises ValueError first
    if status_enum is AdjudicationStatus.VARIANT_OF:
        if variant_of_row_id is None:
            raise ValueError(
                "status=variant-of requires variant_of_row_id naming the "
                "row this one is a variant of")
        if variant_of_row_id == row_id:
            raise ValueError("a row cannot be a variant of itself")
        if registry is None:
            raise ValueError(
                "status=variant-of requires registry= to validate "
                "variant_of_row_id against a real registry row")
        if not any(r.id == variant_of_row_id for r in registry.rows):
            raise ValueError(
                "variant_of_row_id={0} matches no row in the loaded "
                "registry export".format(variant_of_row_id))
    elif variant_of_row_id is not None:
        raise ValueError(
            "variant_of_row_id is only meaningful when "
            "status=variant-of")

    queue = read_repair_queue(out_dir, uni_id)
    if not any(e.row_id == row_id for e in queue):
        raise KeyError(
            "no open repair-queue entry for row {0}".format(row_id))
    verdict = gate(value, [segment], artifact)
    if verdict.status is not Status.PASS:
        raise ValueError(
            "resolution for row {0} rejected by the gate: {1} ({2})".format(
                row_id, verdict.status.value, verdict.detail))

    resolution = Resolution(
        row_id=row_id, status=status_enum, value=value,
        segments=(segment,), source_url=source_url,
        retrieved_at=retrieved_at, method=method,
        variant_of_row_id=variant_of_row_id)
    write_resolution(out_dir, uni_id, resolution)
    remaining = [e for e in queue if e.row_id != row_id]
    write_repair_queue(out_dir, uni_id, remaining)
    return resolution


# ------------------------------------------------------------ orchestration
def _load_site_and_store(uni_id, configs_dir=None, out_dir=None,
                         replay_dir=None, docling_url=None):
    """Loads this uni's config and builds its docs mapping, reusing
    runner's own build_fetcher_and_store/build_docs -- duplicated here
    only for the config-lookup + docs-mapping glue, never the fetcher/
    store construction itself (shared with crawler.onboarding via
    crawler.runner.build_fetcher_and_store)."""
    configs = load_configs_dir(configs_dir or DEFAULT_CONFIGS_DIR)
    if uni_id not in configs:
        raise KeyError(
            "no config for {0!r} -- configured universities: {1}".format(
                uni_id, ", ".join(sorted(configs))))
    site = configs[uni_id]

    out_root = Path(out_dir or DEFAULT_OUT_ROOT)
    run_dir = out_root / uni_id
    run_dir.mkdir(parents=True, exist_ok=True)

    fetcher, store = build_fetcher_and_store(
        uni_id, out_dir=out_dir, replay_dir=replay_dir,
        docling_url=docling_url)

    scratch_report = {"documents": [], "document_failures": []}
    docs = build_docs(site, store, replay_dir is not None, scratch_report)
    return site, store, docs


def run_adjudication(uni_id, *, configs_dir=None, out_dir=None,
                     replay_dir=None, docling_url=None,
                     registry_exports_dir=None, now=None):
    # type: (...) -> AdjudicationReport
    """Fetch this uni's configured pages, load its registry export, and
    adjudicate every row against the durable resolutions.json and the
    previously open repair-queue.json (so a human's earlier call survives
    and a still-open row keeps its original opened_at). Any FRESH
    automatic resolution this run finds is persisted to resolutions.json
    too, so it doesn't need re-deriving (and can't be silently lost)
    next time either."""
    out_root = out_dir or DEFAULT_OUT_ROOT
    site, store, docs = _load_site_and_store(
        uni_id, configs_dir, out_dir, replay_dir, docling_url)
    registry = load_captured_export(uni_id, exports_dir=registry_exports_dir)
    resolved_by_id = {r.row_id: r for r in read_resolutions(out_root, uni_id)}
    prior_opened_at = {e.row_id: e.opened_at
                       for e in read_repair_queue(out_root, uni_id)}
    report = adjudicate(uni_id, site, registry, docs, store,
                        resolved_by_id=resolved_by_id,
                        prior_opened_at=prior_opened_at, now=now)
    for resolution in report.resolved:
        if resolution.row_id not in resolved_by_id:
            write_resolution(out_root, uni_id, resolution)
    write_repair_queue(out_root, uni_id, report.queue)
    return report
