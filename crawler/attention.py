"""The Attention Ledger (ADR-0005): everything awaiting a human, in one
place, aged.

An **Attention item** (CONTEXT.md) is one unit of work only a human can
advance. Before this module the backlog lived on six differently-shaped
surfaces, two of which the pipeline's next run destroys — "fails safe"
had degraded into "fails silent". The ledger concentrates it:

- **Reference, not mirror** — items carry a pointer to their source
  store, except the kinds whose source the next run overwrites or whose
  report is printed and gone (SNAPSHOT_KINDS): those copy their evidence
  into the item at open time. Reference where the source is durable,
  snapshot where it is not.
- **Identity is a natural key; age is the point** — id is
  ``kind:uni[:subject]``. Re-detection updates last_seen and preserves
  opened_at; an open item that stops being detected lapses ("the world
  fixed itself", not phantom work). Lapse is scoped to the kinds and
  universities actually synced — a one-university tick must not lapse
  the rest of the fleet's backlog.
- **Storage splits by regenerability** — open items are derived state in
  gitignored ``crawler-out/attention.jsonl``; human resolutions are
  original data (like the answer key) and append to tracked
  ``attention/resolutions.jsonl``.

Resolution EXECUTION lives in the CLI (`crawler resolve` dispatches to
the existing gate-disciplined deep functions and only then calls
mark_resolved here) — a judgment recorded but not performed is a new
silent-rot channel, the exact class ADR-0005 exists to close.

Pure stdlib; no fetching, no rendering.
"""
import calendar
import json
import subprocess
import time
from pathlib import Path

__all__ = [
    "KINDS", "SNAPSHOT_KINDS", "LEDGER_NAME", "RESOLUTIONS_PATH",
    "item_id", "load_items", "sync", "age_days", "mark_resolved",
    "resolver_identity", "query", "resolve", "ResolveRefused",
    "detect_blocked_publish", "detect_gate_failures", "detect_proposals",
    "detect_check_verdicts", "detect_drift",
]

# The six kinds (ADR-0005 as amended by ADR-0006 — repair-row,
# export-age and unreviewed-auto-resolution died with the registry).
KINDS = ("blocked-publish", "gate-failure", "proposal", "check-verdict",
         "drift", "refresh-error")

# Kinds whose evidence the next run destroys: blocked-publish reasons and
# gate-failure lists live inside per-run reports overwritten in place
# (a measured incident erased a graded n=33 result within a day); drift
# is computed from a live listing nothing stores; a refresh-error's
# traceback exists only in the tick that caught it. proposal and
# check-verdict point at durable files and stay references.
SNAPSHOT_KINDS = ("blocked-publish", "gate-failure", "drift",
                  "refresh-error")

LEDGER_NAME = "attention.jsonl"
RESOLUTIONS_PATH = "attention/resolutions.jsonl"


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def item_id(kind, uni_id, subject=None):
    # type: (str, str, str) -> str
    """The natural key: ``kind:uni[:subject]``."""
    if kind not in KINDS:
        raise ValueError("unknown attention kind {0!r} — one of: {1}".format(
            kind, ", ".join(KINDS)))
    if subject:
        return "{0}:{1}:{2}".format(kind, uni_id, subject)
    return "{0}:{1}".format(kind, uni_id)


def _ledger_path(out_dir):
    return Path(out_dir) / LEDGER_NAME


def load_items(out_dir):
    # type: (str) -> dict
    """{id: item}, every status. Absent file = empty ledger."""
    path = _ledger_path(out_dir)
    items = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                items[item["id"]] = item
    return items


def _write_items(out_dir, items):
    path = _ledger_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items.values():
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def sync(out_dir, detected, *, kinds, unis, now=None):
    # type: (str, list, tuple, tuple, str) -> dict
    """Reconcile the ledger with what this tick detected.

    ``detected``: dicts of {kind, uni_id, subject?, evidence?|ref?}.
    ``kinds``/``unis`` bound the scope: an open item inside the scope
    that was not re-detected lapses; everything outside is untouched.
    Returns {"opened": [ids], "refreshed": [ids], "lapsed": [ids]}.
    """
    now = now or _utcnow()
    kinds = set(kinds)
    unis = set(unis)
    items = load_items(out_dir)
    opened, refreshed, lapsed = [], [], []

    seen = set()
    for d in detected:
        iid = item_id(d["kind"], d["uni_id"], d.get("subject"))
        if d["kind"] in SNAPSHOT_KINDS:
            if "evidence" not in d:
                raise ValueError(
                    "{0}: kind {1!r} is volatile — its source is gone by "
                    "the next run, so the item must snapshot evidence at "
                    "open time (ADR-0005 carve-out)".format(iid, d["kind"]))
        seen.add(iid)
        existing = items.get(iid)
        if existing is not None and existing["status"] == "open":
            existing["last_seen"] = now
            # refresh the snapshot: it exists because the source is
            # overwritten, so a stale copy defeats its purpose
            if "evidence" in d:
                existing["evidence"] = d["evidence"]
            if "ref" in d:
                existing["ref"] = d["ref"]
            refreshed.append(iid)
            continue
        # new — or resolved/lapsed and detected again: the world
        # (re-)broke, which is new work with a new age clock
        item = {"id": iid, "kind": d["kind"], "uni_id": d["uni_id"],
                "subject": d.get("subject"), "opened_at": now,
                "last_seen": now, "status": "open"}
        if "evidence" in d:
            item["evidence"] = d["evidence"]
        if "ref" in d:
            item["ref"] = d["ref"]
        items[iid] = item
        opened.append(iid)

    for iid, item in items.items():
        if (item["status"] == "open" and iid not in seen
                and item["kind"] in kinds and item["uni_id"] in unis):
            item["status"] = "lapsed"
            item["lapsed_at"] = now
            lapsed.append(iid)

    _write_items(out_dir, items)
    return {"opened": opened, "refreshed": refreshed, "lapsed": lapsed}


def age_days(item, now=None):
    # type: (dict, str) -> int
    """Whole days since opened_at — the priority signal (kind + age)."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    now_t = time.strptime(now or _utcnow(), fmt)
    opened_t = time.strptime(item["opened_at"], fmt)
    delta = calendar.timegm(now_t) - calendar.timegm(opened_t)
    return max(0, int(delta // 86400))


def resolver_identity():
    # type: () -> str
    """git user.name — the single-operator default (ADR-0005 revisit
    clause: a second operator makes this a real field)."""
    try:
        out = subprocess.run(["git", "config", "user.name"],
                             capture_output=True, text=True, timeout=10)
        name = out.stdout.strip()
        if name:
            return name
    except OSError:
        pass
    import getpass
    return getpass.getuser()


def mark_resolved(out_dir, iid, *, action, reason=None, resolved_by=None,
                  resolutions_path=None, now=None):
    # type: (str, str, str, str, str, str, str) -> dict
    """Record a human resolution: append to the TRACKED resolutions file
    and flip the ledger item. This is the recording half only — the
    caller must have already executed the action through the deep
    functions (pointer write, write_manual_verdict, config edit)."""
    now = now or _utcnow()
    items = load_items(out_dir)
    if iid not in items:
        raise KeyError(
            "{0}: no such attention item — `crawler attention` lists the "
            "open ones".format(iid))
    resolution = {"id": iid, "resolved_at": now,
                  "resolved_by": resolved_by or resolver_identity(),
                  "action": action, "reason": reason}
    path = Path(resolutions_path or RESOLUTIONS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(resolution, ensure_ascii=False) + "\n")
    items[iid]["status"] = "resolved"
    items[iid]["resolved_at"] = now
    _write_items(out_dir, items)
    return resolution


# ------------------------------------------------------------ query/resolve
class ResolveRefused(Exception):
    """A resolution the module refuses to record, with the reason a human
    needs. Every refusal is this one type: the caller's whole error
    contract is "catch it, show its message" (the CLI maps it to exit 2).
    """


def query(out_dir, *, uni=None, kind=None, min_age=None, show_all=False,
          now=None):
    # type: (str, str, str, int, bool, str) -> list
    """The backlog, aged and SLA-annotated, oldest first.

    Age is the priority signal (ADR-0005: kind + age), so the list is
    sorted by it descending; each item gains ``age_days`` and ``sla``
    (crawler.policy's 7d warn / 30d escalate). Open items only, unless
    show_all. Summary tallies are the caller's one-liner — this returns
    the items themselves.
    """
    from crawler import policy
    items = list(load_items(out_dir).values())
    if not show_all:
        items = [i for i in items if i["status"] == "open"]
    if uni:
        items = [i for i in items if i["uni_id"] == uni]
    if kind:
        items = [i for i in items if i["kind"] == kind]
    for item in items:
        item["age_days"] = age_days(item, now=now)
        item["sla"] = policy.sla_state(item["age_days"])
    if min_age is not None:
        items = [i for i in items if i["age_days"] >= min_age]
    items.sort(key=lambda i: (-i["age_days"], i["id"]))
    return items


def resolve(out_dir, iid, *, reason=None, verdict=None, note="",
            shipped_value=None, verdicts_dir=None, resolutions_path=None,
            resolved_by=None):
    # type: (...) -> dict
    """Close one attention item by EXECUTING its resolution through the
    existing gate-disciplined deep functions — never by merely recording
    a judgment (ADR-0005: a judgment recorded but not performed is a new
    silent-rot channel, the exact class the ledger exists to close).

    By kind: blocked-publish promotes via the ledger pointer write, and
    only after checking the run is actually in the ledger — else
    "resolved" would promote nothing and record success; check-verdict
    goes through grader.write_manual_verdict, bound to the exact shipped
    value judged; proposal records the human's review decision (the
    config edit IS the execution, ADR-0003). gate-failure, drift and
    refresh-error have no manual resolve: their only honest fix is a
    config or world repair, after which the next tick lapses them.

    Raises ResolveRefused for every refusal, with the message a human
    needs; on success appends to the tracked resolutions file and
    returns the resolution.
    """
    from crawler import grader, ledger
    items = load_items(out_dir)
    item = items.get(iid)
    if item is None:
        raise ResolveRefused(
            "{0}: no such attention item -- `crawler attention` lists "
            "the open ones".format(iid))
    kind = item["kind"]

    if kind == "blocked-publish":
        if not reason:
            raise ResolveRefused(
                "promoting a blocked run overrides the expectation "
                "checks -- --reason is required and is recorded")
        run_id = (item.get("evidence") or {}).get("run_id")
        # execute-check: the pointer write must promote a run the
        # ledger actually holds
        if not run_id or ledger.read_run_summary(
                out_dir, item["uni_id"], run_id) is None:
            raise ResolveRefused(
                "run {0!r} is not in {1}'s ledger -- refusing to move "
                "the pointer at nothing".format(run_id, item["uni_id"]))
        ledger.write_current(out_dir, item["uni_id"], run_id)
        action = "promoted"
    elif kind == "check-verdict":
        if not verdict:
            raise ResolveRefused(
                "a check-verdict item needs the judgment: "
                "--verdict ok|wrong (with optional --note / "
                "--shipped-value)")
        program_id, _, field = item["subject"].rpartition(".")
        grader.write_manual_verdict(
            verdicts_dir, item["uni_id"], program_id, field, verdict,
            note=note, shipped_value=shipped_value)
        action = "verdict:" + verdict
    elif kind == "proposal":
        if not reason:
            raise ResolveRefused(
                "closing a proposal review records a promotion "
                "decision -- --reason is required")
        action = "reviewed"
    else:
        raise ResolveRefused(
            "{0} items have no manual resolve: fix the cause and the "
            "next tick will lapse the item when it stops being "
            "detected (ADR-0005: resolve executes, never merely "
            "records)".format(kind))

    return mark_resolved(out_dir, iid, action=action, reason=reason,
                         resolved_by=resolved_by,
                         resolutions_path=resolutions_path)


# ---------------------------------------------------------------- producers
# Each detector maps one existing surface to items. They read the
# surface's own report dict — no re-computation, no second truth.

def detect_blocked_publish(publish_report):
    # type: (dict) -> list
    """A blocked publish: old data stays live until a human clears it.
    Evidence is snapshotted — the publish-report is overwritten in
    place by the next run."""
    if publish_report.get("promoted"):
        return []
    return [{
        "kind": "blocked-publish",
        "uni_id": publish_report["uni_id"],
        "evidence": {
            "run_id": publish_report.get("run_id"),
            "blocked_reasons": publish_report.get("blocked_reasons", []),
            "summary": publish_report.get("summary"),
        },
    }]


def detect_gate_failures(run_report):
    # type: (dict) -> list
    """One item per rejected cell — the durable field-level repair queue
    (the run-report's gate_failures list is overwritten every run)."""
    items = []
    for failure in run_report.get("gate_failures", []):
        items.append({
            "kind": "gate-failure",
            "uni_id": run_report["uni_id"],
            "subject": "{0}.{1}".format(failure["program_id"],
                                        failure["field"]),
            "evidence": failure,
        })
    return items


def detect_proposals(proposal_doc, out_dir):
    # type: (dict, str) -> list
    """Pending onboarding proposals: one item per university, referencing
    the durable proposal file (humans promote, ADR-0003)."""
    if not proposal_doc.get("proposals"):
        return []
    uni_id = proposal_doc["uni_id"]
    return [{
        "kind": "proposal",
        "uni_id": uni_id,
        "ref": "{0}/{1}/onboarding-proposal.json".format(out_dir, uni_id),
    }]


def detect_check_verdicts(uni_id, grade_rows):
    # type: (str, list) -> list
    """Unresolved CHECK rows from a grade: the human verdict overlay is
    the durable store, so the item references it. grade_rows are dicts
    of {program_id, field, category} (grader.GradeReport rows, or their
    JSON form)."""
    items = []
    for row in grade_rows:
        if row["category"] != "check":
            continue
        items.append({
            "kind": "check-verdict",
            "uni_id": uni_id,
            "subject": "{0}.{1}".format(row["program_id"], row["field"]),
            "ref": "benchmark/verdicts/{0}.json".format(uni_id),
        })
    return items


def detect_drift(uni_id, drift_entries):
    # type: (str, list) -> list
    """Version-pin drift (staleness.check_version_drift): computed from a
    live listing nothing stores, so the entry is snapshotted."""
    items = []
    for entry in drift_entries:
        items.append({
            "kind": "drift",
            "uni_id": uni_id,
            "subject": str(entry.get("code")),
            "evidence": entry,
        })
    return items
