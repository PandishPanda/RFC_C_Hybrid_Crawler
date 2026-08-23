"""The extraction-spine runner: store -> render -> cascade -> gate -> report.

``run(uni_id)`` wires the whole v0.1 spine for one university (issue 01):

  1. load the typed site config (crawler/configs/<UniID>.json);
  2. fetch every document the config names — live through the polite
     snapshot store, or fully offline through the spike-A cache replay
     adapter (--replay DIR, zero network by construction);
  3. resolve each snapshot into its Artifact via the artifact store (the
     one resolver, ADR-0002) and build the cascade's TextSource/TableSource
     mapping — pages keyed by URL, shared sources by source id and URL;
  4. run the deterministic cascade (tiers G/F + the interim anchor tier B);
  5. gate EVERY emission with the pure provenance gate against the exact
     Artifact its artifact_ref names — a rejected value is nulled into the
     report's gate_failures queue, never shipped;
  6. write crawler-out/<UniID>/run-report.json.

Report contract:
  - every non-null value carries the Provenance quintuple
    (value, source_url, source_snippets, retrieved_at, method) plus tier
    and the artifact's renderer identity;
  - every field has a Verdict status: PASS for gated values, NULL_OK for
    the explicit nulls this run ships (the deterministic cascade emitted
    nothing — the gated LLM tail is ticket 02 and absent), REJECT_* /
    PARSE_FAILURE for gate failures (value nulled, queued);
  - gate failures land in report["gate_failures"] — the repair queue of
    RFC v2 §2.

Checkpoint discipline: fetches checkpoint in the snapshot store (live) and
each program's record is appended to run-report.partial.jsonl as it is
graded, so a killed run leaves resumable work; the final report is written
atomically (tmp + rename) and the partial file is then removed.

Zero LLM calls anywhere on this path.
"""
import json
import os
import time
from pathlib import Path
from typing import Optional

from crawler import cascade, llm_tail
from crawler.field_record import FieldRecord
from crawler.artifact_store import (
    ArtifactStore,
    ResolveError,
    SpikeCacheFetcher,
)
from crawler.config import FIELDS, ConfigError, load_configs_dir
from crawler.provenance import Status, gate
from crawler.render import DOCLING_URL, ROUTE_HTML
from crawler.store import LiveFetcher, SnapshotStore

__all__ = ["run", "document_plan", "build_docs", "build_fetcher_and_store",
          "DEFAULT_OUT_ROOT", "CASCADE_NULL_REASON"]

DEFAULT_OUT_ROOT = "crawler-out"
DEFAULT_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"


def build_fetcher_and_store(uni_id, *, out_dir=None, replay_dir=None,
                            docling_url=None):
    # type: (str, Optional[str], Optional[str], Optional[str]) -> tuple
    """The replay-vs-live (fetcher, store) construction every entry point
    (run, crawler.onboarding) needs identically:
    replay reads a spike-A cache/ directory (fully offline, zero network
    by construction); live fetches politely through a snapshot store
    under <out_dir>/<uni_id>/snapshots. Shared here so the three callers
    can never drift out of sync on this construction."""
    out_root = Path(out_dir or DEFAULT_OUT_ROOT)
    if replay_dir is not None:
        cache_dir = Path(replay_dir)
        fetcher = SpikeCacheFetcher(cache_dir)
        store = ArtifactStore(fetcher, replay_out=cache_dir.parent / "out")
    else:
        snapshots = SnapshotStore(out_root / uni_id / "snapshots")
        fetcher = LiveFetcher(snapshots)
        store = ArtifactStore(fetcher, docling_url=docling_url or DOCLING_URL)
    return fetcher, store

CASCADE_NULL_REASON = (
    "cascade-null: the deterministic cascade emitted no verifiable value; "
    "this run ships an explicit null (no --tail adapter configured for "
    "this run)")

REPORT_NAME = "run-report.json"
PARTIAL_NAME = "run-report.partial.jsonl"


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# -------------------------------------------------------------- document plan
def document_plan(site):
    # type: (...) -> "dict[str, tuple]"
    """{key: (url, route, source_id or None)} — every document the config
    names, keyed by SOURCE ID where one exists and by URL otherwise.

    Shared sources keep their configured route; program pages, extra
    pages, lang/adm/tuition pages and URL-sourced anchors are html. A URL
    that is both a shared source and a page keeps the source's route (the
    source entry is the more specific config statement).

    Keyed by source id, not by url, because several sources legitimately
    share ONE url: a fee workbook is a single document whose per-program
    column differs, and value_headers lives on the source, so a site
    needs one source per attendance form over the same .xlsx. Keying by
    url silently collapsed those to whichever one iterated last, and the
    programs wired to the others resolved nothing.
    """
    plan = {}
    urls_with_a_source = set()
    for sid, src in site.sources.items():
        plan[sid] = (src.url, src.route, sid)
        urls_with_a_source.add(src.url)

    def add_page(url):
        if url and url not in plan and url not in urls_with_a_source:
            plan[url] = (url, ROUTE_HTML, None)

    for program in site.programs:
        add_page(program.page)
        for url in program.extra_pages:
            add_page(url)
        add_page(program.lang_page)
        add_page(program.adm_page)
        add_page(program.tuition_page)
    for anchor in site.anchors.values():
        if "://" in anchor.source:
            add_page(anchor.source)
    return plan


# ------------------------------------------------------------------ documents
def build_docs(site, store, replay, report):
    """Resolve the document plan into the cascade's docs mapping.

    Pages are keyed by URL; shared sources by source id AND URL (the
    cascade addresses joins by id, extra_pages by URL). In replay mode a
    missing snapshot or vendored rendering raises — the benchmark replay
    must fail loudly, never silently degrade. In live mode a failed
    fetch/render is recorded in report["document_failures"] and the
    cascade extracts from what is there (a null is repairable; a crashed
    run is not).
    """
    docs = {}
    for _key, (url, route, sid) in sorted(document_plan(site).items()):
        try:
            resolved = store.resolve(url, route, cookies=dict(site.cookies),
                                     source_id=sid, label=site.uni_id)
        except Exception as exc:  # noqa: BLE001 — recorded or re-raised
            if replay:
                raise
            if not isinstance(exc, (ResolveError, OSError)):
                raise
            report["document_failures"].append({
                "url": url, "route": route, "source_id": sid,
                "error": "{0}: {1}".format(type(exc).__name__, exc),
            })
            continue
        if resolved.tables is not None:
            # Every grid-producing route (table-pdf, spreadsheet) feeds
            # the column-aware resolvers a TableSource. Keyed off the
            # resolved doc actually carrying grids rather than a literal
            # route name, so adding a grid route can't leave this branch
            # behind -- which is exactly what happened when the
            # spreadsheet route landed and this still read "table-pdf".
            source = cascade.TableSource(ref=resolved.ref,
                                         tables=resolved.tables)
        else:
            source = cascade.TextSource(ref=resolved.ref,
                                        text=resolved.artifact.text,
                                        layout=resolved.layout)
        docs[url] = source
        if sid is not None:
            docs[sid] = source
        report["documents"].append({
            "ref": resolved.ref,
            "source_url": resolved.source_url,
            "route": route,
            "source_id": sid,
            "renderer_id": resolved.artifact.renderer_id,
            "renderer_version": resolved.artifact.renderer_version,
            "retrieved_at": resolved.retrieved_at,
            "sha256": resolved.sha256,
        })
    return docs


# --------------------------------------------------------------------- gating
# ------------------------------------------------------- derived values
# ADR-0007. Scoped to `language` on purpose: a university's language of
# instruction is uniform enough for a human to assert one default, while
# tuition and admission vary program by program, where a default would
# be a guess dressed as a value.
DERIVABLE_FIELDS = ("language",)


def derived_record(field, value, rule):
    # type: (str, str, str) -> dict
    """A value that is true but stated in no document (ADR-0007).

    Deliberately carries NO `provenance` and NO `artifact`: there is no
    verbatim support, and shipping an empty or invented snippet is the
    exact fabrication this status exists to avoid. What it carries
    instead is the rule that produced it, so a reader can audit the
    assumption rather than the evidence."""
    return FieldRecord.derived(
        value=value, rule=rule,
        basis=("asserted by site config; no document of this "
               "program states this field")).to_dict()


def derive_fields(fields, default_language=None):
    # type: (dict, Optional[str]) -> dict
    """Fill derivable fields that came out of the spine with nothing.

    LAST RESORT by construction: only a NULL_OK field is derived. A PASS
    keeps its extracted value (including a language that is not the
    default), and a REJECT_* keeps its rejection — quietly covering a
    gate refusal with a site default would hide the refusal."""
    if not default_language:
        return fields
    for field in DERIVABLE_FIELDS:
        record = fields.get(field)
        if record is None or record.get("status") != Status.NULL_OK.value:
            continue
        fields[field] = derived_record(field, default_language,
                                       "default_language")
    return fields


def _field_record(program_id, field, extraction, store):
    """Gate one cascade emission; return (record, gate_failure or None).

    The runner is the ONLY caller of the gate on this path: the cascade
    emits, the gate decides, and a rejected value is nulled — nothing
    unverified is ever loaded (RFC v2 Q4).
    """
    if extraction is None:
        verdict = gate(None, [], None, null_reason=CASCADE_NULL_REASON)
        return FieldRecord.spine_null(verdict.detail).to_dict(), None

    artifact = store.artifact(extraction.artifact_ref)
    doc = store.doc(extraction.artifact_ref)
    verdict = gate(extraction.value, list(extraction.segments), artifact)

    artifact_block = {
        "ref": artifact.ref,
        "renderer_id": artifact.renderer_id,
        "renderer_version": artifact.renderer_version,
    }
    context = dict(extraction.context) if extraction.context else None

    if verdict.status is Status.PASS:
        return FieldRecord.spine_pass(
            value=extraction.value, tier=extraction.tier,
            method=extraction.method, artifact=artifact_block,
            provenance={
                "value": extraction.value,
                "source_url": doc.source_url,
                "source_snippets": list(extraction.segments),
                "retrieved_at": doc.retrieved_at,
                "method": extraction.method,
            },
            verdict_detail=verdict.detail, context=context).to_dict(), None

    # gate failure: the value is nulled and queued, never shipped
    record = FieldRecord.spine_reject(
        status=verdict.status.value, tier=extraction.tier,
        method=extraction.method, artifact=artifact_block,
        verdict_detail=verdict.detail, context=context).to_dict()
    failure = {
        "program_id": program_id,
        "field": field,
        "status": verdict.status.value,
        "detail": verdict.detail,
        "rejected_value": extraction.value,
        "segments": list(extraction.segments),
        "artifact_ref": extraction.artifact_ref,
        "method": extraction.method,
        "tier": extraction.tier,
    }
    return record, failure


def _tail_field_record(program_id, field, tail_result, store):
    """Shape a llm_tail.TailResult into the same (record, gate_failure or
    None) contract _field_record produces for cascade emissions — the
    tail gates internally (crawler.llm_tail.resolve_via_tail calls the
    same crawler.provenance.gate), so this function only reports, never
    re-decides.
    """
    verdict = tail_result.verdict

    if verdict.status is Status.PASS:
        ext = tail_result.extraction
        artifact = store.artifact(ext.artifact_ref)
        doc = store.doc(ext.artifact_ref)
        return FieldRecord.tail_pass(
            value=ext.value, tier=ext.tier, method=ext.method,
            artifact={
                "ref": artifact.ref,
                "renderer_id": artifact.renderer_id,
                "renderer_version": artifact.renderer_version,
            },
            provenance={
                "value": ext.value,
                "source_url": doc.source_url,
                "source_snippets": list(ext.segments),
                "retrieved_at": doc.retrieved_at,
                "method": ext.method,
            },
            verdict_detail=verdict.detail,
            tail_attempts=tail_result.attempts,
            tail_escalated=tail_result.escalated).to_dict(), None

    if verdict.status is Status.NULL_OK:
        return FieldRecord.tail_null(
            verdict.detail, tail_attempts=tail_result.attempts,
            tail_escalated=tail_result.escalated).to_dict(), None

    record = FieldRecord.tail_reject(
        status=verdict.status.value, verdict_detail=verdict.detail,
        tail_attempts=tail_result.attempts,
        tail_escalated=tail_result.escalated).to_dict()

    # rejection after retry + escalation: nulled and queued, never shipped
    last = tail_result.last_attempt
    failure = {
        "program_id": program_id,
        "field": field,
        "status": verdict.status.value,
        "detail": verdict.detail,
        "rejected_value": last.value if last else None,
        "segments": list(last.segments) if last else [],
        "artifact_ref": last.artifact_ref if last else None,
        "method": last.method if last else "llm-tail",
        "tier": "llm-tail",
        "tail_attempts": tail_result.attempts,
    }
    return record, failure


# ------------------------------------------------------------------------ run
def run(uni_id, configs_dir=None, out_dir=None, replay_dir=None,
        docling_url=None, tail=None):
    # type: (str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[object]) -> dict
    """Run the extraction spine for one university; returns the report.

    replay_dir: a spike-A ``cache/`` directory — the run is then entirely
    offline: snapshots come from the cache, PDF renderings from the
    sibling ``out/`` dir (out/pdftext, out/docling), and only the pinned
    bs4 renderer executes locally.

    tail: an optional llm_tail adapter (CLIAdapter/APIAdapter/FakeAdapter).
    Defaults to None, which preserves ticket 01's zero-LLM-calls property
    exactly — a cascade-null field ships an explicit null, same as before
    ticket 02 existed. Pass an adapter to route cascade-nulled fields
    through the gated LLM tail instead.
    """
    configs = load_configs_dir(configs_dir or DEFAULT_CONFIGS_DIR)
    if uni_id not in configs:
        raise KeyError(
            "no config for {0!r} — configured universities: {1}".format(
                uni_id, ", ".join(sorted(configs))))
    site = configs[uni_id]

    out_root = Path(out_dir or DEFAULT_OUT_ROOT)
    run_dir = out_root / uni_id
    run_dir.mkdir(parents=True, exist_ok=True)

    replay = replay_dir is not None
    fetcher, store = build_fetcher_and_store(
        uni_id, out_dir=out_dir, replay_dir=replay_dir, docling_url=docling_url)

    report = {
        "uni_id": uni_id,
        "mode": "replay" if replay else "live",
        "generated_at": _utcnow(),
        "replay_dir": str(replay_dir) if replay else None,
        "documents": [],
        "document_failures": [],
        "programs": [],
        "gate_failures": [],
        "summary": {},
    }
    docs = build_docs(site, store, replay, report)

    partial_path = run_dir / PARTIAL_NAME
    status_counts = {status.value: 0 for status in Status}
    tier_counts = {}
    tail_calls = 0
    tail_escalations = 0
    with open(partial_path, "w", encoding="utf-8") as partial:
        for program in site.programs:
            fields = {}
            for field in FIELDS:
                extraction = cascade.resolve_field(site, program, field,
                                                   docs)
                if extraction is None and tail is not None:
                    tail_result = llm_tail.resolve_via_tail(
                        tail, store, site, program, field, docs,
                        tag_prefix=uni_id + ":")
                    record, failure = _tail_field_record(
                        program.id, field, tail_result, store)
                else:
                    record, failure = _field_record(program.id, field,
                                                    extraction, store)
                fields[field] = record
                status_counts[record["status"]] += 1
                if record["status"] == Status.PASS.value:
                    tier = record["tier"]
                    tier_counts[tier] = tier_counts.get(tier, 0) + 1
                if "tail_attempts" in record:
                    tail_calls += record["tail_attempts"]
                    if record["tail_escalated"]:
                        tail_escalations += 1
                if failure is not None:
                    report["gate_failures"].append(failure)
            fields = derive_fields(
                fields, default_language=site.default_language)
            for name, rec in fields.items():
                if rec["status"] == Status.DERIVED.value:
                    status_counts[Status.NULL_OK.value] -= 1
                    status_counts[Status.DERIVED.value] += 1
                    tier_counts["D"] = tier_counts.get("D", 0) + 1
            program_record = {"program_id": program.id,
                              "name": program.name, "fields": fields}
            report["programs"].append(program_record)
            # checkpoint: one line per graded program, flushed immediately
            partial.write(json.dumps(program_record, ensure_ascii=False)
                          + "\n")
            partial.flush()

    report["summary"] = {
        "programs": len(site.programs),
        "fields": len(site.programs) * len(FIELDS),
        "status_counts": status_counts,
        "tier_counts": tier_counts,
        "documents": len(report["documents"]),
        "document_failures": len(report["document_failures"]),
        "gate_failures": len(report["gate_failures"]),
        "tail_calls": tail_calls,
        "tail_escalations": tail_escalations,
    }

    report_path = run_dir / REPORT_NAME
    tmp_path = report_path.with_name(
        "{0}.tmp{1}".format(REPORT_NAME, os.getpid()))
    tmp_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp_path.replace(report_path)
    partial_path.unlink()
    return report
