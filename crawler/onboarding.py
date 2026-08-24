"""Onboarding agent-proposer flow (ticket 06, DEC-2; reshaped by ADR-0006).

With the RSVU registry dropped (ADR-0006), onboarding's unit of work is no
longer "find the page for THIS registry row" — it is "find the degree-
program pages on THIS university's site." The worklist is the candidate
links discovered from the seed page(s); the proposer judges which of them
are degree-program pages and names each one.

Two DIFFERENT kinds of claim come out of this module, and they must never
be presented as the same kind of thing:

  1. gate_verified_fields -- an actual field VALUE (degree/duration/...)
     found on the proposed page, checked by the SAME
     crawler.provenance.gate() every other tier feeds (ADR-0002). This is
     real signal: "this page has extractable program data."

  2. proposed_url + proposed_name -- the judgment that a page IS a
     degree-program page, and what the program is called. That is a
     semantic judgment with no verbatim source text gate() could check.
     ``ProposedProgram.assignment_verified`` is ALWAYS False; this is the
     thing a human confirms before promoting anything into
     crawler/configs/. Nothing here writes to that directory -- only to
     <out_dir>/<UniID>/onboarding-proposal.json (ADR-0003).

Verification only runs tier G (crawler.cascade.harvest_labels) against
the proposed page -- a fresh candidate has no site config yet, so tiers
F/B (which need bespoke joins/anchors) cannot run. This under-counts what
a fully-configured page could extract; it is real signal about THIS page,
not a ceiling on the eventual Program once a human writes real config for
it.

Agent-proposed renderer routing and join/alias recipes stay deliberately
NOT built (ADR-0003): correspondence and routing are human-authored,
after a human confirms proposed_url -- the same moment a human is already
reading the real page.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Tuple
from urllib.parse import urljoin, urlparse

import bs4

from crawler import cascade
from crawler.config import ConfigError, load_site_config, parse_site_config
from crawler.llm_tail import HAIKU
from crawler.provenance import Status, gate
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
    "You are surveying a Bulgarian university's website to find its "
    "DEGREE-PROGRAM pages, from a list of candidate pages (URL + the "
    "link text that pointed to it). Select ONLY urls that most likely "
    "document one specific degree program (bachelor's/master's/PhD), and "
    "give each its program name as the site states it. Do not guess -- "
    "a wrong page in the list is worse than a missing one. News, staff, "
    "faculty-overview, admission-procedure and generic pages are not "
    "program pages.")


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


def fetch_links(seed_urls, fetcher, *, cookies=None, same_domain=True,
                failures=None):
    # type: (List[str], object, Optional[Mapping], bool, Optional[list]) -> List[Tuple[str, str]]
    """Fetch every seed page and union their discovered links, deduped.

    A seed that fails to fetch is appended to ``failures`` (when given) as
    {seed, status, error}. Silently skipping it made a dead seed URL
    indistinguishable from "the model found nothing" -- both surfaced as
    zero proposals at zero cost (measured live on ANIS, 2026-08-21: three
    404 seeds read as a clean decline). A seed that 404s is an operator
    error to fix, not a finding about the university.
    """
    found = {}
    for seed in seed_urls:
        snap = fetcher.fetch(seed, {"cookies": cookies or {},
                                    "label": "onboarding-discovery"})
        if not snap.ok:
            if failures is not None:
                failures.append({
                    "seed": seed,
                    "status": getattr(snap, "status", None),
                    "error": getattr(snap, "error", None)})
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
            "programs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string",
                               "enum": list(candidate_urls)},
                        "name": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["url", "name", "reasoning"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["programs"],
        "additionalProperties": False,
    }


def build_prompt(uni_id, candidates):
    # type: (str, List[Tuple[str, str]]) -> str
    lines = [
        "University: {0}".format(uni_id),
        "",
        "Candidate pages on the university's website:",
    ]
    for url, text in candidates:
        lines.append("- {0}  (link text: {1!r})".format(url, text or ""))
    return "\n".join(lines)


@dataclass(frozen=True)
class ProposedProgram:
    """assignment_verified is a computed property, not a field -- there is
    no constructor argument that could ever set it True. "This page IS a
    degree program named X" is a semantic judgment gate() structurally
    cannot check (ADR-0002); this makes "always unverified" hold by
    construction, not by every call site remembering to pass False.

    adapter_error is set ONLY when the survey call itself failed
    (timeout, transport error, malformed response) -- distinct from a
    normal decline, where the model looked at the candidates and
    affirmatively selected nothing. proposed_url is always None when
    this is set; match_reasoning still carries a human-readable copy of
    the error for context, but adapter_error is the field to check
    programmatically -- "worth a retry" vs "the model declined for a
    real reason" are different questions."""
    proposed_name: str
    proposed_url: Optional[str]
    match_reasoning: str
    gate_verified_fields: Mapping[str, Mapping]
    field_pass_count: int
    adapter_error: Optional[str] = None

    @property
    def assignment_verified(self):
        return False


def verify_page(store, url, program_name, *, cookies=None):
    # type: (object, str, str, Optional[Mapping]) -> Tuple[object, Mapping]
    """Resolve URL as a real Artifact and run tier G against it -- the
    only tier that needs no bespoke site config. Tier G is the label
    library (harvest_labels) PLUS the title-language rule
    (language_from_name, cascade.py's own definition) -- PROGRAM_NAME is
    the proposer's name for the page, the same input language_from_name
    reads on a configured Program. Returns (doc, fields) where fields is
    {field: {value, segments, source_url, retrieved_at, artifact_ref,
    method}} for every gate-PASSed extraction (never anything gate
    rejected) -- so a human can independently re-check it without
    knowing artifact_store.py's ref convention."""
    doc = store.resolve(url, ROUTE_HTML, cookies=cookies or {},
                        label="onboarding-verify")
    source = cascade.TextSource(ref=doc.ref, text=doc.artifact.text)
    artifact = store.artifact(doc.ref)
    verified = {}
    for field in cascade.LABEL_PATTERNS:
        extraction = cascade.harvest_labels(field, source)
        if extraction is None and field == "language":
            extraction = cascade.language_from_name(program_name, source)
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


def propose_onboarding(uni_id, candidate_links, adapter, store, *,
                       cookies=None, tag_prefix="", max_pages=None,
                       survey_rounds=3):
    # type: (str, List[Tuple[str, str]], object, object, Optional[Mapping], str, Optional[int], int) -> Tuple[List[ProposedProgram], float]
    """Returns (proposals, total_cost_usd) -- cost is summed from the
    adapter.call()'s usage dict, 0.0 for adapters that don't report cost
    (FakeAdapter). max_pages caps how many selected pages are verified
    (each verify is a real fetch).

    survey_rounds: the survey is the only non-deterministic module in
    onboarding, and one call proved wasteful live -- the same MUSofia
    seed gave 9 proposals then 0, and SWU lost two paid runs to the
    variance (2026-08-24). k rounds union their URL selections (dedup by
    URL, first round's naming wins); the deterministic verify step stays
    the ranker via its gate-verified-field counts. A single failed round
    is tolerated; only ALL rounds failing reports an adapter error.
    Cost of the extra rounds is ~$0.02 -- the survey is the cheap half,
    verification the expensive one, and only the flaky half is
    ensembled. ADR-0003 untouched: still propose-only."""
    urls = [url for url, _ in candidate_links]
    if not urls:
        return [], 0.0
    url_set = set(urls)
    schema = build_schema(urls)
    prompt = build_prompt(uni_id, candidate_links)

    total_cost = 0.0
    selected = []
    seen_keys = set()
    errors = []
    for round_no in range(1, max(1, survey_rounds) + 1):
        tag = "{0}survey:{1}".format(tag_prefix, round_no) \
            if survey_rounds > 1 else "{0}survey".format(tag_prefix)
        try:
            structured, usage = adapter.call(prompt, schema, HAIKU, tag)
            total_cost += usage.get("cost_usd") or 0.0
        except Exception as exc:  # noqa: BLE001 -- adapter/transport failure
            errors.append(str(exc))
            continue
        for item in structured.get("programs") or []:
            key = item.get("url") or ("name:" + (item.get("name") or ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            selected.append(item)
    if errors and not selected and len(errors) == max(1, survey_rounds):
        return [ProposedProgram(
            "(survey failed)", None,
            "adapter error: {0}".format(errors[-1]), {}, 0,
            adapter_error=errors[-1])], total_cost

    proposals = []
    if max_pages is not None:
        selected = selected[:max_pages]
    for item in selected:
        url = item.get("url")
        name = item.get("name") or "(unnamed)"
        reasoning = item.get("reasoning") or ""
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
                _doc, verified_fields = verify_page(store, url, name,
                                                    cookies=cookies)
            except Exception as exc:  # noqa: BLE001 -- fetch/render failure
                reasoning += " (page verify failed: {0})".format(exc)
                url = None
        proposals.append(ProposedProgram(
            proposed_name=name, proposed_url=url,
            match_reasoning=reasoning,
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
    seed_failures: Tuple[dict, ...] = ()   # seeds that never fetched


def write_proposal(out_dir, uni_id, proposals):
    # type: (str, str, List[ProposedProgram]) -> str
    path = Path(out_dir) / uni_id / PROPOSAL_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "uni_id": uni_id,
        "note": ("proposed_name/proposed_url is an UNVERIFIED "
                 "page-is-a-program judgment (assignment_verified is "
                 "always false) -- a human must confirm it before "
                 "anything here is promoted into crawler/configs/. "
                 "gate_verified_fields are real gate-PASSed extractions."),
        "proposals": [
            dict(proposed_name=p.proposed_name,
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
    # type: (str, List[ProposedProgram]) -> Tuple[Optional[bool], Optional[str]]
    """Free structural smoke-check: would the shape a human eventually
    hand-writes actually load? Never written to crawler/configs/;
    (True, None) on a clean parse, (False, message) on a ConfigError,
    (None, None) if there is nothing to validate yet."""
    programs = [
        {"id": "proposal-{0}".format(i), "name": p.proposed_name,
         "page": p.proposed_url}
        for i, p in enumerate(proposals) if p.proposed_url is not None
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
                   cookies=None, max_pages=None):
    # type: (...) -> OnboardingReport
    """Discover candidate pages from SEED_URLS, survey them for degree-
    program pages (one LLM call), tier-G-verify each selected page, write
    the proposal, and smoke-check the shape a human would eventually
    write by hand.

    max_pages caps how many selected pages get the verify fetch -- the
    survey call itself is one call, but each verify is a real fetch and
    render. Pages already configured for this uni are excluded from the
    candidate list so re-running onboarding proposes only NEW pages."""
    out_root = out_dir or DEFAULT_OUT_ROOT

    config_path = Path(configs_dir or DEFAULT_CONFIGS_DIR) / "{0}.json".format(uni_id)
    # Load ONLY this uni's own config file directly -- load_configs_dir()
    # loads every *.json in the directory and would raise (and get
    # swallowed here) on an unrelated SIBLING config's parse error,
    # silently treating THIS uni as unconfigured and re-proposing pages
    # it already covers. A missing file is legitimately "no config yet";
    # a malformed file for THIS uni must still raise loudly (config.py's
    # own stated philosophy).
    site = load_site_config(config_path) if config_path.exists() else None
    configured_pages = ({p.page for p in site.programs}
                        if site is not None else set())

    fetcher, store = build_fetcher_and_store(
        uni_id, out_dir=out_root, replay_dir=replay_dir,
        docling_url=docling_url)

    seed_failures = []
    candidate_links = [
        (url, text)
        for url, text in fetch_links(seed_urls, fetcher, cookies=cookies,
                                     failures=seed_failures)
        if url not in configured_pages
    ]
    proposals, total_cost = propose_onboarding(
        uni_id, candidate_links, adapter, store, cookies=cookies,
        tag_prefix=uni_id + ":", max_pages=max_pages)
    write_proposal(out_root, uni_id, proposals)
    valid, error = validate_as_draft_config(uni_id, proposals)
    return OnboardingReport(uni_id=uni_id, proposals=tuple(proposals),
                            total_cost_usd=total_cost, draft_config_valid=valid,
                            draft_config_error=error,
                            seed_failures=tuple(seed_failures))
