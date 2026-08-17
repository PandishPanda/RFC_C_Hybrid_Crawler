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
    RFC v2 §2;
  - a Program that declares `offerings` also carries programs[].offerings[]
    — a SIBLING of its fields, one entry per (attendance form, duration)
    the REGISTRY row enumerates, each with its own gate-checked tuition
    (ADR-0004). Readers of programs[].fields see no difference. Alongside
    it: offering_config_unused / offering_unparsed / offering_duplicate_key
    / offering_row_missing (loud, non-fatal diagnostics) and
    summary["offering_completeness"], a per-form FLOOR that gates nothing.

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
from crawler.artifact_store import (
    ArtifactStore,
    ResolveError,
    SpikeCacheFetcher,
)
from crawler.config import FIELDS, ConfigError, load_configs_dir
from crawler.provenance import Status, gate
from crawler.registry import load_captured_export, parse_edu_forms
from crawler.render import DOCLING_URL, ROUTE_HTML
from crawler.store import LiveFetcher, SnapshotStore

__all__ = ["run", "document_plan", "build_docs", "build_fetcher_and_store",
          "init_offering_report_keys", "offering_completeness",
          "DEFAULT_OUT_ROOT", "CASCADE_NULL_REASON"]

DEFAULT_OUT_ROOT = "crawler-out"
DEFAULT_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"


def build_fetcher_and_store(uni_id, *, out_dir=None, replay_dir=None,
                            docling_url=None):
    # type: (str, Optional[str], Optional[str], Optional[str]) -> tuple
    """The replay-vs-live (fetcher, store) construction every entry point
    (run, crawler.adjudication, crawler.onboarding) needs identically:
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
        for recipe in program.offerings.values():
            if recipe.curriculum is not None:
                add_page(recipe.curriculum.url)
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
OFFERING_REPORT_KEYS = ("offering_config_unused", "offering_unparsed",
                        "offering_duplicate_key", "offering_row_missing",
                        "curriculum_unbound")


def init_offering_report_keys(report):
    """Give REPORT the diagnostic lists _offering_records appends to.

    Owned here rather than by each caller so a rename cannot leave a test
    fixture silently re-guessing the shape it is meant to check."""
    for key in OFFERING_REPORT_KEYS:
        report.setdefault(key, [])
    return report


def offering_completeness(report):
    """Offerings with a Stated Fee / Offerings enumerated, PER FORM.

    Per form, never blended: самостоятелна offerings can never have a fee
    column in a workbook, so a single number reads as permanent failure
    for a reason that has nothing to do with extraction quality.

    This is a FLOOR, not a rate. A dash cell is quotable text, but gate()
    has no positional check, so "not offered in this form" cannot be
    affirmatively evidenced today (ADR-0004) -- an unpriced Offering may
    be genuinely unpriced or merely unconfigured, and this figure cannot
    tell them apart. It gates NOTHING; it is reported so the gap stays
    visible.
    """
    by_form = {}
    for program in report["programs"]:
        for offering in program.get("offerings") or ():
            stated, total = by_form.get(offering["form"], (0, 0))
            tuition = offering["fields"].get("tuition") or {}
            has_fee = (tuition.get("status") == Status.PASS.value
                       and tuition.get("value") is not None)
            by_form[offering["form"]] = (stated + (1 if has_fee else 0),
                                         total + 1)
    return {form: {"stated": s, "enumerated": n,
                   "floor": round(s / n, 4) if n else 0.0,
                   "caveat": "FLOOR, not a rate: an unpriced Offering may "
                             "be genuinely unpriced or merely unconfigured, "
                             "and this figure cannot tell them apart"}
            for form, (s, n) in sorted(by_form.items())}


def _curriculum_binding(recipe, offering_id, docs, store, report):
    """A gate-checked attestation that the fetched plan is THIS
    offering's -- or None plus a loud report entry when it is not.

    The plan page's breadcrumb states its own identity verbatim
    ("Факултет ... > Бизнес мениджмънт > ОКС "Бакалавър" > Редовно"), so
    binding is checkable the same way every value is: the configured
    form_phrase (REQUIRED) plus program_name/degree_phrase (optional
    extras) must each be verbatim-quoted from the fetched Artifact,
    through the one gate() everything else uses.

    This is an ATTESTATION, not a field: it lives beside the offering's
    fields, never inside them -- `curriculum` is not in FIELDS and no
    value is extracted FROM the plan here. Per this ticket's own rules,
    duration is never derived from counting "Година:" sections (a year
    label gates PASS while meaning something else) and language is never
    inferred from the absence of an English marker (a claim grounded in
    absence cannot be quoted).

    KNOWN, UNMITIGATED BY THIS CHECK: the plan URL is version-pinned, so
    a superseded plan keeps binding successfully forever -- the
    breadcrumb it states does not change. That exposure is ticket 22's
    check-pins, not this one; binding proves WHICH plan was fetched,
    not that it is current.
    """
    if recipe is None or recipe.curriculum is None:
        return None
    cur = recipe.curriculum
    source = docs.get(cur.url)
    if source is None:
        report["curriculum_unbound"].append(
            {"offering_id": offering_id, "url": cur.url,
             "reason": "plan document not fetched (not in the document "
                       "plan, or its fetch failed)"})
        return None
    artifact = store.artifact(source.ref)
    # Every configured claim must be verbatim in the plan. form_phrase
    # alone degenerates into a one-word substring search over the whole
    # page (measured: a page saying "изпитите се провеждат редовно" would
    # bind "Редовно" -- ticket 10's wrong-occurrence blind spot, one
    # level down), so when the breadcrumb extras are configured they
    # must gate too, AND the form_phrase must sit near one of them.
    claims = [("form_phrase", cur.form_phrase)]
    if cur.program_name:
        claims.append(("program_name", cur.program_name))
    if cur.degree_phrase:
        claims.append(("degree_phrase", cur.degree_phrase))
    for label, claim in claims:
        verdict = gate(claim, [claim], artifact)
        if verdict.status is not Status.PASS:
            report["curriculum_unbound"].append(
                {"offering_id": offering_id, "url": cur.url,
                 "gate_status": verdict.status.value,
                 "reason": "{0} {1!r} is not verbatim in the fetched "
                           "plan -- this plan is NOT attested to be this "
                           "offering's".format(label, claim)})
            return None
    anchors = [c for label, c in claims if label != "form_phrase"]
    if anchors:
        text = cascade.norm(artifact.text)
        form = cascade.norm(cur.form_phrase)
        window = 120
        anchored = any(
            form in text[i:i + len(cascade.norm(a)) + window]
            for a in (cascade.norm(x) for x in anchors)
            for i in [text.find(a)] if i >= 0)
        if not anchored:
            report["curriculum_unbound"].append(
                {"offering_id": offering_id, "url": cur.url,
                 "gate_status": "REJECT_CONTAINMENT",
                 "reason": "form_phrase {0!r} appears in the plan but not "
                           "within {1} chars of the configured breadcrumb "
                           "anchors -- an incidental occurrence elsewhere "
                           "on the page must not bind".format(
                               cur.form_phrase, window)})
            return None
    binding = {"url": cur.url, "form_phrase": cur.form_phrase,
               "segments": [c for _, c in claims],
               "artifact_ref": source.ref,
               # This attests the plan's (program, degree, FORM) -- never
               # its duration. Registry row 30083 states дистанционна at
               # 4.5, 4 and 5 years while the university publishes ONE
               # дистанционно plan, so several offerings legitimately
               # share one binding; a duration-specific plan would need
               # its own duration-keyed recipe.
               "attests": "form-level plan identity, not duration"}
    if cur.code:
        binding["code"] = cur.code
    if cur.version:
        binding["version"] = cur.version
    return binding


def _offering_null_record(reason):
    """A NULL_OK offering field with a resolver-authored reason.

    An Offering with no matching recipe, or a recipe whose cell is empty,
    ships an explicit null naming WHAT WAS CHECKED -- it never inherits
    the Program-level value. A Program's tuition stands for a set of
    offerings priced differently (ADR-0004); silently reusing it here
    would attribute one member's price to all of them.
    """
    verdict = gate(None, [], None, null_reason=reason)
    return {"status": verdict.status.value, "value": None,
            "null_reason": verdict.detail}


def _resolve_offering_tuition(site, offering_id, recipe, docs, store):
    """(record, gate_failure) for ONE offering's tuition."""
    if recipe is None or recipe.tuition_join is None:
        return _offering_null_record(
            "no offering recipe configured for this attendance form"), None
    ref = recipe.tuition_join
    join = cascade._join_of(site, ref)
    source = cascade._table(docs, ref)
    if source is None:
        return _offering_null_record(
            "configured fee source {0!r} resolved to no document".format(
                ref.source)), None
    extraction = cascade.fee_row_join("tuition", source, join, ref.alias)
    if extraction is None:
        return _offering_null_record(
            "no fee for alias {0!r} in table {1!r}, column {2}".format(
                ref.alias, join.table_marker or "(first match)",
                "/".join(join.value_headers))), None
    return _field_record(offering_id, "tuition", extraction, store)


def _offering_records(site, program, row, docs, store, report):
    """Every Offering the REGISTRY states for PROGRAM, with its tuition.

    Config never enumerates offerings -- it supplies a recipe map keyed by
    form, and the registry row is the enumerating authority (ADR-0004).
    Lookup is most-specific-wins: a "задочна - 4.5" recipe beats a bare
    "задочна" one for that duration only.
    """
    forms, unparsed = parse_edu_forms(row.edu_forms)
    for item in unparsed:
        report["offering_unparsed"].append(
            {"program_id": program.id, "row_id": row.id, "item": item})

    used = set()
    records = []
    failures = []
    seen_ids = {}
    for edu in forms:
        key = edu.key if edu.key in program.offerings else (
            edu.form if edu.form in program.offerings else None)
        if key is not None:
            used.add(key)
        offering_id = "{0}#{1}".format(program.id, edu.key)
        # A row CAN state the same (form, duration) twice and
        # parse_edu_forms preserves duplicates deliberately, but the
        # Offering key is identity (ADR-0004) -- two records sharing one
        # id would silently merge downstream. Disambiguate and report.
        seen_ids[offering_id] = seen_ids.get(offering_id, 0) + 1
        if seen_ids[offering_id] > 1:
            report["offering_duplicate_key"].append(
                {"program_id": program.id, "row_id": row.id,
                 "offering_id": offering_id,
                 "occurrence": seen_ids[offering_id]})
            offering_id = "{0}~{1}".format(offering_id, seen_ids[offering_id])
        record, failure = _resolve_offering_tuition(
            site, offering_id, program.offerings.get(key), docs, store)
        if failure is not None:
            failures.append(failure)
        recipe = program.offerings.get(key)
        curriculum = _curriculum_binding(recipe, offering_id, docs, store,
                                         report)
        records.append({
            "offering_id": offering_id,
            "offering_key": edu.key,
            "program_id": program.id,
            "form": edu.form,
            "duration_years": edu.duration_years,
            "registry_row_id": row.id,
            "edu_forms_item": edu.item,
            "recipe_key": key,
            "curriculum": curriculum,
            "fields": {"tuition": record},
        })

    for unused in sorted(set(program.offerings) - used):
        report["offering_config_unused"].append(
            {"program_id": program.id, "recipe_key": unused,
             "reason": "no offering the registry states for row {0} matches "
                       "this recipe".format(row.id)})
    return records, failures


def _field_record(program_id, field, extraction, store):
    """Gate one cascade emission; return (record, gate_failure or None).

    The runner is the ONLY caller of the gate on this path: the cascade
    emits, the gate decides, and a rejected value is nulled — nothing
    unverified is ever loaded (RFC v2 Q4).
    """
    if extraction is None:
        verdict = gate(None, [], None, null_reason=CASCADE_NULL_REASON)
        return {
            "status": verdict.status.value,
            "value": None,
            "null_reason": verdict.detail,
        }, None

    artifact = store.artifact(extraction.artifact_ref)
    doc = store.doc(extraction.artifact_ref)
    verdict = gate(extraction.value, list(extraction.segments), artifact)

    record = {
        "status": verdict.status.value,
        "tier": extraction.tier,
        "method": extraction.method,
        "artifact": {
            "ref": artifact.ref,
            "renderer_id": artifact.renderer_id,
            "renderer_version": artifact.renderer_version,
        },
        "verdict_detail": verdict.detail,
    }
    if extraction.context:
        record["context"] = dict(extraction.context)

    if verdict.status is Status.PASS:
        record["value"] = extraction.value
        record["provenance"] = {
            "value": extraction.value,
            "source_url": doc.source_url,
            "source_snippets": list(extraction.segments),
            "retrieved_at": doc.retrieved_at,
            "method": extraction.method,
        }
        return record, None

    # gate failure: the value is nulled and queued, never shipped
    record["value"] = None
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
    record = {
        "status": verdict.status.value,
        "verdict_detail": verdict.detail,
        "tail_attempts": tail_result.attempts,
        "tail_escalated": tail_result.escalated,
    }

    if verdict.status is Status.PASS:
        ext = tail_result.extraction
        artifact = store.artifact(ext.artifact_ref)
        doc = store.doc(ext.artifact_ref)
        record["tier"] = ext.tier
        record["method"] = ext.method
        record["artifact"] = {
            "ref": artifact.ref,
            "renderer_id": artifact.renderer_id,
            "renderer_version": artifact.renderer_version,
        }
        record["value"] = ext.value
        record["provenance"] = {
            "value": ext.value,
            "source_url": doc.source_url,
            "source_snippets": list(ext.segments),
            "retrieved_at": doc.retrieved_at,
            "method": ext.method,
        }
        return record, None

    record["value"] = None
    if verdict.status is Status.NULL_OK:
        record["null_reason"] = verdict.detail
        return record, None

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
        docling_url=None, tail=None, registry_exports_dir=None):
    # type: (str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[object], Optional[str]) -> dict
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
    init_offering_report_keys(report)

    docs = build_docs(site, store, replay, report)

    # Offerings are OPT-IN per Program: only a Program that declares
    # `offerings` enumerates them, so every other Program and university
    # is byte-identical by construction rather than by luck. The export is
    # loaded once, and loudly -- a Program asking to be enumerated against
    # a registry row we do not have is a config bug, not a null.
    offering_programs = [p for p in site.programs if p.offerings]
    rows_by_code = {}
    if offering_programs:
        export = load_captured_export(uni_id,
                                      exports_dir=registry_exports_dir)
        rows_by_code = {row.code: row for row in export.rows}
        for program in offering_programs:
            if program.rsvu_code not in rows_by_code:
                # Loud, but NOT fatal: the Program's own fields are still
                # valid, and aborting the university's whole run over one
                # stale code would discard work that is fine.
                report["offering_row_missing"].append(
                    {"program_id": program.id,
                     "rsvu_code": program.rsvu_code,
                     "reason": "declares offerings but this rsvu_code "
                               "matches no row in the registry export"})

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
            program_record = {"program_id": program.id,
                              "name": program.name, "fields": fields}
            if program.offerings and program.rsvu_code in rows_by_code:
                # SIBLING of "fields", never nested inside it:
                # expectations.summarize and grader.grade_report read only
                # program["fields"], and must keep seeing exactly what
                # they see today.
                offerings, offering_failures = _offering_records(
                    site, program, rows_by_code[program.rsvu_code],
                    docs, store, report)
                program_record["offerings"] = offerings
                report["gate_failures"].extend(offering_failures)
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
        "offerings": sum(len(p.get("offerings") or ())
                         for p in report["programs"]),
        "offering_completeness": offering_completeness(report),
    }

    report_path = run_dir / REPORT_NAME
    tmp_path = report_path.with_name(
        "{0}.tmp{1}".format(REPORT_NAME, os.getpid()))
    tmp_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp_path.replace(report_path)
    partial_path.unlink()
    return report
