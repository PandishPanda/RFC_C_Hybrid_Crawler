"""The refresh orchestrator (ADR-0005, ticket 05): the unattended loop.

``crawler refresh`` walks every configured university — publish (which
runs the deterministic spine; the LLM tail is opt-in per invocation,
keeping ADR-0001's no-LLM-in-the-refresh-loop default), then the
version-pin drift check — and everything needing judgment lands in the
Attention Ledger instead of expecting a human to watch stdout.

One university's crash becomes a ``refresh-error`` item (traceback
snapshotted — it exists nowhere else) and the loop continues. The tick
ends with ``crawler-out/refresh-report.json`` and a non-zero exit iff
anything NEW needs a human — a standing backlog the operator already
knows about must not page every week — so a weekly cron plus exit code
is a complete v1 alerting story. A notification adapter seam exists in
the report shape; no channel is committed until one is chosen.

Grade and validate stay human-driven acts outside the tick: refresh
reads pending CHECK items into the verdict but never lapses them (only
a grade can say a CHECK stopped existing).
"""
import json
import time
import traceback
from pathlib import Path

from crawler import attention, policy, publish as publish_mod, runner, \
    staleness

__all__ = ["refresh", "exit_code", "REPORT_NAME"]

REPORT_NAME = "refresh-report.json"


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_drift_fn(configs_dir):
    """Fetch the curriculum listing once per tick, only if some site
    actually pins versions; a listing failure surfaces as that
    university's refresh-error, never a silent skip."""
    from crawler.config import load_site_config
    cache = {}

    def drift_fn(uni_id):
        config_path = Path(
            configs_dir or runner.DEFAULT_CONFIGS_DIR
        ) / "{0}.json".format(uni_id)
        site = load_site_config(config_path)
        if not staleness.pinned_sources(site):
            return []
        if "listing" not in cache:
            import requests
            cache["listing"] = staleness.listed_versions(
                requests.get(staleness.CURRICULUM_LIST_URL, timeout=30).text)
        return staleness.check_version_drift(site, cache["listing"])

    return drift_fn


def refresh(uni_ids, *, configs_dir=None, out_dir=None, replay_dir=None,
            docling_url=None, tail_fn=None, publish_fn=None, drift_fn=None):
    # type: (...) -> dict
    """One tick over ``uni_ids``. Returns the refresh report (also
    written to ``<out>/refresh-report.json``).

    ``publish_fn``/``drift_fn`` are injectable for tests; the defaults
    are publish_mod.publish and a curriculum-listing drift check.
    """
    out_root = out_dir or runner.DEFAULT_OUT_ROOT
    if publish_fn is None:
        def publish_fn(uni_id, **kw):
            # the tail is built per university: its usage ledger is a
            # per-uni file, and ADR-0001 keeps it opt-in per invocation
            tail = tail_fn(uni_id) if tail_fn is not None else None
            return publish_mod.publish(
                uni_id, configs_dir=configs_dir, out_dir=kw.get("out_dir"),
                replay_dir=replay_dir, docling_url=docling_url, tail=tail)
    if drift_fn is None:
        drift_fn = _default_drift_fn(configs_dir)

    unis = []
    # the attention delta is measured against the ledger, not against
    # refresh's own sync calls: publish() emits its items itself, and
    # those must count toward "something NEW needs a human" too
    before = {iid: (item["status"], item["opened_at"], item["last_seen"])
              for iid, item in attention.load_items(out_root).items()}

    for uni_id in uni_ids:
        error = None
        publish_report = {}
        drift_entries = []
        try:
            publish_report = publish_fn(uni_id, out_dir=out_dir)
            drift_entries = drift_fn(uni_id)
            attention.sync(
                out_root, attention.detect_drift(uni_id, drift_entries),
                kinds=["drift"], unis=[uni_id])
            # a proposal awaiting review stays visible tick over tick
            proposal_path = (Path(out_root) / uni_id
                             / "onboarding-proposal.json")
            proposals = []
            if proposal_path.exists():
                doc = json.loads(proposal_path.read_text(encoding="utf-8"))
                proposals = attention.detect_proposals(doc, out_root)
            attention.sync(out_root, proposals,
                           kinds=["proposal"], unis=[uni_id])
        except Exception:
            error = traceback.format_exc()
        # the error scope syncs every tick: a clean pass lapses the
        # previous tick's refresh-error
        error_items = []
        if error is not None:
            error_items = [{"kind": "refresh-error", "uni_id": uni_id,
                            "evidence": {"error": error}}]
        attention.sync(out_root, error_items,
                       kinds=["refresh-error"], unis=[uni_id])

        pending_checks = sum(
            1 for i in attention.load_items(out_root).values()
            if i["status"] == "open" and i["kind"] == "check-verdict"
            and i["uni_id"] == uni_id)
        pending_proposals = sum(
            1 for i in attention.load_items(out_root).values()
            if i["status"] == "open" and i["kind"] == "proposal"
            and i["uni_id"] == uni_id)

        v = policy.verdict({
            "refresh_error": error is not None,
            "publish_blocked": bool(publish_report) and
                               not publish_report.get("promoted", True),
            "gate_failures": publish_report.get("gate_failures", 0),
            "pending_checks": pending_checks,
            "pending_proposals": pending_proposals,
            "drift": len(drift_entries),
        })
        uni_row = {"uni_id": uni_id, "verdict": v.decision,
                   "needs_human": list(v.needs_human)}
        if error is not None:
            uni_row["error"] = error.strip().splitlines()[-1]
        unis.append(uni_row)

    opened, refreshed, lapsed = [], [], []
    for iid, item in attention.load_items(out_root).items():
        prior = before.get(iid)
        if item["status"] == "open":
            if prior is None or prior[0] != "open" \
                    or prior[1] != item["opened_at"]:
                opened.append(iid)      # new, or reopened with a new clock
            elif prior[2] != item["last_seen"]:
                refreshed.append(iid)
        elif item["status"] == "lapsed" and prior and prior[0] == "open":
            lapsed.append(iid)

    report = {
        "generated_at": _utcnow(),
        "unis": unis,
        "attention": {"opened": sorted(opened),
                      "refreshed": sorted(refreshed),
                      "lapsed": sorted(lapsed)},
    }
    out_path = Path(out_root) / REPORT_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return report


def exit_code(report):
    # type: (dict) -> int
    """Non-zero iff something NEW needs a human: new items this tick, or
    a university that errored. Standing backlog does not re-page."""
    if report["attention"]["opened"]:
        return 1
    if any("error" in u for u in report["unis"]):
        return 1
    return 0
