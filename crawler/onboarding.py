"""Onboarding agent-proposer flow (ticket 06, DEC-2).

Ticket 05 proved the real bottleneck: a Program only counts as Covered
via ``ProgramConfig.rsvu_code`` (an exact registry-row match) or a
gate-PASSed field extraction. `propose_enrolling`'s name-match found 0/14
of VUM's uncovered rows because those rows have no configured page at
all. So onboarding's unit of work is "find the page for THIS registry
row" -- the worklist is registry rows (crawler.registry.RegistryRow),
not a from-scratch homepage crawl into an LLM-guessed program list.

Two DIFFERENT kinds of claim come out of this module, and they must never
be presented as the same kind of thing:

  1. gate_verified_fields -- an actual field VALUE (degree/duration/...)
     found on the proposed page, checked by the SAME
     crawler.provenance.gate() every other tier feeds (ADR-0002). This is
     real signal: "this page has extractable program data."

  2. proposed_url / the row<->page ASSIGNMENT itself -- a cross-language
     semantic judgment ("Софтуерни системи и технологии" IS "Software
     Systems and Technologies"). There is no verbatim source text to
     quote for "this page documents THIS registry row" -- gate()
     structurally cannot check it. ``ProposedProgram.assignment_verified``
     is ALWAYS False; this is the thing a human confirms before promoting
     anything into crawler/configs/. Nothing here writes to that
     directory -- only to <out_dir>/<UniID>/onboarding-proposal.json.

Verification only runs tier G (crawler.cascade.harvest_labels) against
the proposed page -- a fresh candidate has no site config yet, so tiers
F/B (which need bespoke joins/anchors) cannot run. This under-counts what
a fully-configured page could extract; it is real signal about THIS page,
not a ceiling on the eventual Program once a human writes real config for
it.

Ticket 06's original scope also asked for agent-proposed renderer routing
and join/alias recipes (tier F config) -- deliberately not built here.
ADR-0003 records why: join/alias CORRESPONDENCE (which table row belongs
to which program) is a hard, measured gate() limit (RFC v2 SS3 Q4's
wrong-row/column blind spot), not a lesser priority, and table-pdf
ROUTING is structurally circular for gate() to check (it would need an
Artifact that doesn't exist until the routing question is already
answered). Both stay human-authored, after a human confirms proposed_url
-- the same moment a human is already reading the real page.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Tuple
from urllib.parse import urljoin, urlparse

import bs4

from crawler import cascade
from crawler.adjudication import covered_codes
from crawler.config import ConfigError, load_site_config, parse_site_config
from crawler.llm_tail import HAIKU
from crawler.provenance import Status, gate
from crawler.registry import RegistryRow, load_captured_export
from crawler.render import ROUTE_HTML
from crawler.runner import (
    DEFAULT_CONFIGS_DIR,
    DEFAULT_OUT_ROOT,
    build_fetcher_and_store,
)

__all__ = [
    "ProposedProgram", "OnboardingReport", "discover_links", "fetch_links",
    "build_schema", "build_prompt", "verify_page", "propose_onboarding",
    "write_proposal", "validate_as_draft_config", "run_onboarding",
    "PROPOSAL_NAME", "SYSTEM_PROMPT",
]

PROPOSAL_NAME = "onboarding-proposal.json"

SYSTEM_PROMPT = (
    "You are matching a Bulgarian university registry program to the "
    "correct page on that university's own website, from a list of "
    "candidate pages (URL + the link text that pointed to it). Pick "
    "EXACTLY ONE url that most likely documents this exact program, or "
    "null if none of the candidates look like a confident match. Do not "
    "guess -- a wrong match is worse than no match.")


# --------------------------------------------------------------- discovery
def discover_links(html_bytes, base_url, *, same_domain=True):
    # type: (bytes, str, bool) -> List[Tuple[str, str]]
    """Every same-domain <a href> on one page: (absolute_url, link_text).

    Not a provenance claim -- this never feeds a field value, only a
    candidate-URL list for a human/LLM to judge, so it deliberately does
    NOT go through the artifact store (no Artifact is constructed here).
    """
    soup = bs4.BeautifulSoup(html_bytes, "lxml")
    base_host = urlparse(base_url).netloc
    seen = set()
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href).split("#", 1)[0]
        if same_domain and urlparse(url).netloc != base_host:
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append((url, cascade.norm(a.get_text(" "))))
    return links


def fetch_links(seed_urls, fetcher, *, cookies=None, same_domain=True):
    # type: (List[str], object, Optional[Mapping], bool) -> List[Tuple[str, str]]
    """Fetch every seed page and union their discovered links, deduped."""
    found = {}
    for seed in seed_urls:
        snap = fetcher.fetch(seed, {"cookies": cookies or {},
                                    "label": "onboarding-discovery"})
        if not snap.ok:
            continue
        for url, text in discover_links(snap.read_bytes(), seed,
                                        same_domain=same_domain):
            found.setdefault(url, text)
    return list(found.items())


# ---------------------------------------------------------------- proposer
def build_schema(candidate_urls):
    return {
        "type": "object",
        "properties": {
            "url": {"type": ["string", "null"],
                   "enum": [None] + list(candidate_urls)},
            "reasoning": {"type": "string"},
        },
        "required": ["url", "reasoning"],
        "additionalProperties": False,
    }


def build_prompt(row, candidates):
    # type: (RegistryRow, List[Tuple[str, str]]) -> str
    lines = [
        "Registry program: {0!r}".format(row.name),
        "Professional field: {0!r}".format(row.major_name),
        "Degree level: {0!r}".format(row.degree_name),
        "",
        "Candidate pages on the university's website:",
    ]
    for url, text in candidates:
        lines.append("- {0}  (link text: {1!r})".format(url, text or ""))
    return "\n".join(lines)


@dataclass(frozen=True)
class ProposedProgram:
    """assignment_verified is a computed property, not a field -- there is
    no constructor argument that could ever set it True. The row<->page
    assignment is a cross-language semantic judgment gate() structurally
    cannot check (ADR-0002); this makes "always unverified" hold by
    construction, not by every call site remembering to pass False.

    adapter_error is set ONLY when the ranking call itself failed
    (timeout, transport error, malformed response) -- distinct from a
    normal decline, where the model looked at the candidates and
    affirmatively returned no url. Found live 2026-08-15: a 180s
    adapter timeout recorded identically to every genuine "no confident
    match" reasoning string, indistinguishable to a human reviewing
    onboarding-proposal.json. proposed_url is always None when this is
    set; match_reasoning still carries a human-readable copy of the
    error for context, but adapter_error is the field to check
    programmatically -- "worth a retry" vs "the model declined for a
    real reason" are different questions."""
    row_id: int
    row_code: str
    row_name: str
    proposed_url: Optional[str]
    match_reasoning: str
    gate_verified_fields: Mapping[str, Mapping]
    field_pass_count: int
    adapter_error: Optional[str] = None

    @property
    def assignment_verified(self):
        return False


def verify_page(store, url, row_name, *, cookies=None):
    # type: (ArtifactStore, str, str, Optional[Mapping]) -> Tuple[object, Mapping]
    """Resolve URL as a real Artifact and run tier G against it -- the
    only tier that needs no bespoke site config. Tier G is the label
    library (harvest_labels) PLUS the title-language rule
    (language_from_name, cascade.py's own definition) -- ROW_NAME is the
    registry's program title, the same input language_from_name reads on
    a configured Program. Returns (doc, fields) where fields is
    {field: {value, segments, source_url, retrieved_at, artifact_ref,
    method}} for every gate-PASSed extraction (never anything gate
    rejected) -- shaped like adjudication.Resolution so a human can
    independently re-check it without knowing artifact_store.py's ref
    convention."""
    doc = store.resolve(url, ROUTE_HTML, cookies=cookies or {},
                        label="onboarding-verify")
    source = cascade.TextSource(ref=doc.ref, text=doc.artifact.text)
    artifact = store.artifact(doc.ref)
    verified = {}
    for field in cascade.LABEL_PATTERNS:
        extraction = cascade.harvest_labels(field, source)
        if extraction is None and field == "language":
            extraction = cascade.language_from_name(row_name, source)
        if extraction is None:
            continue
        verdict = gate(extraction.value, list(extraction.segments), artifact)
        if verdict.status is Status.PASS:
            verified[field] = {
                "value": extraction.value,
                "segments": list(extraction.segments),
                "source_url": doc.source_url,
                "retrieved_at": doc.retrieved_at,
                "artifact_ref": doc.ref,
                "method": extraction.method,
            }
    return doc, verified


def propose_onboarding(uni_id, rows, candidate_links, adapter, store, *,
                       cookies=None, tag_prefix=""):
    # type: (str, List[RegistryRow], List[Tuple[str, str]], object, object, Optional[Mapping], str) -> Tuple[List[ProposedProgram], float]
    """Returns (proposals, total_cost_usd) -- cost is summed from every
    adapter.call()'s usage dict (ticket 06: "measure... proposer token
    cost per uni"), 0.0 for adapters that don't report cost (FakeAdapter)."""
    urls = [url for url, _ in candidate_links]
    url_set = set(urls)
    schema = build_schema(urls)
    proposals = []
    total_cost = 0.0
    for row in rows:
        if not urls:
            proposals.append(ProposedProgram(
                row.id, row.code, row.name, None,
                "no candidate pages discovered", {}, 0))
            continue
        prompt = build_prompt(row, candidate_links)
        tag = "{0}{1}".format(tag_prefix, row.id)
        try:
            structured, usage = adapter.call(prompt, schema, HAIKU, tag)
            total_cost += usage.get("cost_usd") or 0.0
        except Exception as exc:  # noqa: BLE001 -- adapter/transport failure
            proposals.append(ProposedProgram(
                row.id, row.code, row.name, None,
                "adapter error: {0}".format(exc), {}, 0,
                adapter_error=str(exc)))
            continue
        url = structured.get("url")
        reasoning = structured.get("reasoning") or ""
        if url is not None and url not in url_set:
            # Schema enforcement should make this impossible, but a fresh
            # network fetch is real I/O -- never trust the enum alone to
            # gate it (same defense-in-depth as llm_tail's store.artifact
            # dict-lookup backstop on source_ref).
            reasoning += " (model named a URL outside the candidate list; ignored)"
            url = None
        verified_fields = {}
        if url is not None:
            try:
                _doc, verified_fields = verify_page(store, url, row.name,
                                                    cookies=cookies)
            except Exception as exc:  # noqa: BLE001 -- fetch/render failure
                reasoning += " (page verify failed: {0})".format(exc)
                url = None
        proposals.append(ProposedProgram(
            row_id=row.id, row_code=row.code, row_name=row.name,
            proposed_url=url, match_reasoning=reasoning,
            gate_verified_fields=verified_fields,
            field_pass_count=len(verified_fields)))
    return proposals, total_cost


# ----------------------------------------------------------------- output
@dataclass(frozen=True)
class OnboardingReport:
    uni_id: str
    proposals: Tuple[ProposedProgram, ...]
    total_cost_usd: float
    draft_config_valid: Optional[bool]
    draft_config_error: Optional[str]


def write_proposal(out_dir, uni_id, proposals):
    # type: (str, str, List[ProposedProgram]) -> str
    path = Path(out_dir) / uni_id / PROPOSAL_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "uni_id": uni_id,
        "note": ("row_name is the registry's Bulgarian program title. "
                 "proposed_url is an UNVERIFIED row<->page assignment "
                 "(assignment_verified is always false) -- a human must "
                 "confirm it before anything here is promoted into "
                 "crawler/configs/. gate_verified_fields are real "
                 "gate-PASSed extractions."),
        "proposals": [
            dict(row_id=p.row_id, row_code=p.row_code, row_name=p.row_name,
                proposed_url=p.proposed_url,
                assignment_verified=p.assignment_verified,
                match_reasoning=p.match_reasoning,
                gate_verified_fields=dict(p.gate_verified_fields),
                field_pass_count=p.field_pass_count,
                adapter_error=p.adapter_error)
            for p in proposals
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return str(path)


def validate_as_draft_config(uni_id, proposals):
    # type: (str, List[ProposedProgram]) -> Tuple[bool, Optional[str]]
    """Free structural smoke-check: would the shape a human eventually
    hand-writes actually load? Deliberately omits rsvu_code -- the
    row<->page assignment is unverified and must not look like config
    data. Never written to crawler/configs/; (True, None) on a clean
    parse, (False, message) on a ConfigError, (None, None) if there is
    nothing to validate yet."""
    programs = [
        {"id": "row-{0}".format(p.row_id), "name": p.row_name,
         "page": p.proposed_url}
        for p in proposals if p.proposed_url is not None
    ]
    if not programs:
        return None, None
    draft = {"uni_id": uni_id, "sources": {}, "programs": programs}
    try:
        parse_site_config(draft, origin="<onboarding draft>")
    except ConfigError as exc:
        return False, str(exc)
    return True, None


# ------------------------------------------------------------ orchestration
def run_onboarding(uni_id, seed_urls, adapter, *, configs_dir=None,
                   out_dir=None, replay_dir=None, docling_url=None,
                   registry_exports_dir=None, cookies=None, max_rows=None):
    # type: (...) -> OnboardingReport
    """Discover candidate pages from SEED_URLS, propose+verify a page for
    every registry row not already config-covered (all of them, for a
    university with no config yet), write the proposal, and smoke-check
    the shape a human would eventually write by hand.

    max_rows caps how many uncovered rows are proposed for -- each one is
    a real, non-trivial-cost LLM call (measured ~$0.15/call mean on the
    2026-08-15 live smoke test), so a fresh university's full registry
    (dozens of rows) is a real-money command, not a free one. None means
    no cap; the CLI defaults this to a small number for exactly that
    reason."""
    out_root = out_dir or DEFAULT_OUT_ROOT
    registry = load_captured_export(uni_id, exports_dir=registry_exports_dir)

    config_path = Path(configs_dir or DEFAULT_CONFIGS_DIR) / "{0}.json".format(uni_id)
    # Load ONLY this uni's own config file directly -- load_configs_dir()
    # loads every *.json in the directory and would raise (and get
    # swallowed here) on an unrelated SIBLING config's parse error,
    # silently treating THIS uni as unconfigured and re-proposing rows
    # it already covers. A missing file is legitimately "no config yet";
    # a malformed file for THIS uni must still raise loudly (config.py's
    # own stated philosophy).
    site = load_site_config(config_path) if config_path.exists() else None
    codes = covered_codes(site) if site is not None else set()
    rows = [r for r in registry.rows if r.code not in codes]
    if max_rows is not None:
        rows = rows[:max_rows]

    fetcher, store = build_fetcher_and_store(
        uni_id, out_dir=out_root, replay_dir=replay_dir,
        docling_url=docling_url)

    candidate_links = fetch_links(seed_urls, fetcher, cookies=cookies)
    proposals, total_cost = propose_onboarding(
        uni_id, rows, candidate_links, adapter, store, cookies=cookies,
        tag_prefix=uni_id + ":")
    write_proposal(out_root, uni_id, proposals)
    valid, error = validate_as_draft_config(uni_id, proposals)
    return OnboardingReport(uni_id=uni_id, proposals=tuple(proposals),
                            total_cost_usd=total_cost, draft_config_valid=valid,
                            draft_config_error=error)
