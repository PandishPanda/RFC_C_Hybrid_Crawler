"""Provenance-aware changed-cell diff between two run-reports — the
mechanism the attribution review runs on (docs/agents/attribution-review.md).

A *cell* is (program_id, field). It is reported as changed when ANY of
status, value, method, artifact ref or verbatim snippets moved. That scope
is the whole point, and it is deliberately wider than
``ledger.diff_runs``, which compares values alone:

- a value that changed is a value the reviewer must re-read;
- a value that did NOT change but now arrives from a different anchor,
  artifact or span is the harder class — right answer, wrong reason. It is
  the shape the rev.5 round's uncovered fabrications took (a programme
  reading its neighbour's degree, a master's billed at the bachelor's fee),
  it is invisible to value diffing, and no answer key covered it.

Status is carried because a non-PASS cell's value never shipped: without
it a REJECT_* -> PASS transition is indistinguishable from an ordinary
value change, and a cell that stopped shipping would present a value the
gate refused as if it were current.

Pure functions over already-parsed reports — no I/O, no config, stdlib
only. The CLI wrapper is ``python3 -m crawler diff``.
"""
import json

__all__ = ["cells", "changed_cells", "format_changes", "KINDS"]

# in precedence order: the first that applies is the cell's kind
KINDS = ("added", "removed", "status", "value", "attribution")


def cells(report):
    # type: (dict) -> dict
    """{(program_id, field): {status, value, method, artifact_ref, snippets}}"""
    out = {}
    for program in report.get("programs", []):
        for field, rec in program.get("fields", {}).items():
            prov = rec.get("provenance") or {}
            out[(program.get("program_id"), field)] = {
                "status": rec.get("status"),
                "value": rec.get("value"),
                "method": rec.get("method"),
                "artifact_ref": (rec.get("artifact") or {}).get("ref"),
                "snippets": list(prov.get("source_snippets") or ()),
            }
    return out


def _kind(before, after):
    if before is None:
        return "added"
    if after is None:
        return "removed"
    # status first: whether a value shipped at all outranks what it said
    if before["status"] != after["status"]:
        return "status"
    if before["value"] != after["value"]:
        return "value"
    return "attribution"


def changed_cells(before_report, after_report):
    # type: (dict, dict) -> list
    """One entry per changed cell, ordered by (program_id, field).

    Each entry: {program_id, field, kind, before, after} — plus
    ``status_change`` ("REJECT_NOT_VERBATIM->PASS") when kind is "status".
    """
    before, after = cells(before_report), cells(after_report)
    changes = []
    for key in sorted(set(before) | set(after)):
        was, now = before.get(key), after.get(key)
        if was == now:
            continue
        change = {"program_id": key[0], "field": key[1],
                  "kind": _kind(was, now), "before": was, "after": now}
        if change["kind"] == "status":
            change["status_change"] = "{0}->{1}".format(
                was["status"], now["status"])
        changes.append(change)
    return changes


def _side(label, cell, show_snippets):
    if cell is None:
        return ["  {0}: -".format(label)]
    lines = ["  {0}: [{1}] {2!r} via {3} in {4}".format(
        label, cell["status"], cell["value"], cell["method"],
        cell["artifact_ref"])]
    if show_snippets:
        lines.extend("       | {0}".format(s) for s in cell["snippets"])
    return lines


def format_changes(changes, total_cells, show_snippets=False):
    # type: (list, int, bool) -> str
    """The human-readable report `crawler diff` prints."""
    lines = []
    for change in changes:
        lines.append("{0}\t{1}\t{2}".format(
            change["program_id"], change["field"],
            change.get("status_change") or change["kind"]))
        lines.extend(_side("was", change["before"], show_snippets))
        lines.extend(_side("now", change["after"], show_snippets))
    by_kind = {}
    for change in changes:
        by_kind[change["kind"]] = by_kind.get(change["kind"], 0) + 1
    tally = ", ".join("{0} {1}".format(by_kind[k], k)
                      for k in KINDS if k in by_kind)
    lines.append("\n{0} changed cell(s) of {1}{2}".format(
        len(changes), total_cells, " — " + tally if tally else ""))
    return "\n".join(lines)


def as_json(uni_id, changes, total_cells):
    # type: (str, list, int) -> str
    return json.dumps({"uni_id": uni_id, "total_cells": total_cells,
                       "changed": len(changes), "changes": changes},
                      ensure_ascii=False, indent=2)
