"""Publish orchestration (ticket 03): run -> ledger -> expectation checks
-> pointer move -> report.

Every run's values ALWAYS land in the append-only ledger, whatever the
expectation checks decide — publishing only gates whether ``current``
moves to point at them. A blocked run is fully inspectable (same ledger,
same run-report.json) and fully re-promotable later by a human clearing
the block; nothing is lost, nothing is silently retried.
"""
import json
import time
from pathlib import Path
from typing import Optional

from crawler import expectations, ledger, runner

__all__ = ["publish", "PUBLISH_REPORT_NAME"]

PUBLISH_REPORT_NAME = "publish-report.json"


def publish(uni_id, *, configs_dir=None, out_dir=None, replay_dir=None,
           docling_url=None, tail=None, ledger_dir=None,
           academic_year=None, today=None, report=None):
    # type: (...) -> dict
    """Run the extraction spine (unless ``report`` is already supplied —
    tests can skip re-running), append its values to the ledger, check
    dataset-level expectations against the last PROMOTED run, and move
    the pointer only if they pass.

    Returns a publish-report dict: the extraction summary, the
    expectation-check verdict, the value-level diff vs the previous
    promoted run, and whether the pointer moved. Written to
    ``<out_dir>/<uni_id>/publish-report.json``.
    """
    out_root = out_dir or runner.DEFAULT_OUT_ROOT
    ledger_root = ledger_dir or out_root

    if report is None:
        report = runner.run(uni_id, configs_dir=configs_dir, out_dir=out_dir,
                            replay_dir=replay_dir, docling_url=docling_url,
                            tail=tail)

    run_id = ledger.run_id_for(uni_id)
    academic_year = academic_year or expectations.expected_academic_year(today)

    previous_run_id = ledger.read_current(ledger_root, uni_id)
    previous_summary = ledger.read_run_summary(ledger_root, uni_id, previous_run_id)

    result = expectations.check(report, previous_summary, today=today,
                                academic_year=academic_year)

    ledger.append_run(ledger_root, uni_id, run_id, report, academic_year)
    ledger.write_run_summary(ledger_root, uni_id, run_id, result.current)

    diff = ledger.diff_runs(ledger_root, uni_id, previous_run_id, run_id)
    # currency-only restatements (BGN<->EUR at the fixed peg) are not real
    # changes — annotate rather than drop, so the report still shows its work
    for change in diff:
        if change["change"] == "changed":
            old, new = change.get("old_value"), change.get("new_value")
            if old and new and expectations.currency_equivalent(old, new):
                change["change"] = "currency-only"

    promoted = not result.blocked
    if promoted:
        ledger.write_current(ledger_root, uni_id, run_id)

    publish_report = {
        "uni_id": uni_id,
        "run_id": run_id,
        "previous_run_id": previous_run_id,
        "academic_year": academic_year,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "promoted": promoted,
        "blocked_reasons": result.reasons,
        "summary": result.current,
        "previous_summary": result.previous,
        "cost": {
            "tail_calls": report["summary"].get("tail_calls", 0),
            "tail_escalations": report["summary"].get("tail_escalations", 0),
        },
        "gate_failures": len(report.get("gate_failures", [])),
        "value_diff": diff,
        "value_diff_summary": {
            "added": sum(1 for d in diff if d["change"] == "added"),
            "removed": sum(1 for d in diff if d["change"] == "removed"),
            "changed": sum(1 for d in diff if d["change"] == "changed"),
            "currency_only": sum(1 for d in diff if d["change"] == "currency-only"),
        },
    }

    run_dir = Path(out_root) / uni_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / PUBLISH_REPORT_NAME).write_text(
        json.dumps(publish_report, ensure_ascii=False, indent=1), encoding="utf-8")
    return publish_report
