#!/usr/bin/env python3
"""List every cell that changed between two run-reports — by value OR by
attribution. Feeds the attribution review (docs/agents/attribution-review.md).

    python3 scripts/changed-cells.py BEFORE.json AFTER.json [--snippets]

A cell is (program_id, field). It is reported when any of value, method,
artifact ref, or verbatim snippets differs. Cells whose value is unchanged
are labelled `attribution-only` — that class is invisible to
`crawler.ledger.diff_runs`, which compares values alone, and it is the shape
the rev.5 round's uncovered fabrications took: a right-looking value arriving
from the wrong place.

BEFORE.json is a copy of crawler-out/<UniID>/run-report.json taken before the
change; `crawler run` overwrites that file in place, so make the copy first.
"""

import json
import sys


def cells(path):
    """{(program_id, field): (status, value, method, artifact_ref, snippets)}

    `status` is carried because a REJECT_* cell's value never shipped: without
    it, a REJECT -> PASS transition is indistinguishable from a plain value
    change, and a PASS -> REJECT cell would print a `now:` value the pipeline
    refused to ship.
    """
    out = {}
    with open(path, encoding="utf-8") as f:
        report = json.load(f)
    for program in report["programs"]:
        for field, rec in program["fields"].items():
            prov = rec.get("provenance") or {}
            out[(program["program_id"], field)] = (
                rec.get("status"),
                rec.get("value"),
                rec.get("method"),
                (rec.get("artifact") or {}).get("ref"),
                tuple(prov.get("source_snippets") or ()),
            )
    return out


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    show_snippets = "--snippets" in argv[1:]
    if len(args) != 2:
        sys.stderr.write(__doc__)
        return 2

    before, after = cells(args[0]), cells(args[1])
    changed = 0
    for key in sorted(set(before) | set(after)):
        was, now = before.get(key), after.get(key)
        if was == now:
            continue
        changed += 1
        if was is None:
            kind = "added"
        elif now is None:
            kind = "removed"
        elif was[0] != now[0]:
            kind = "{0}->{1}".format(was[0], now[0])
        elif was[1] != now[1]:
            kind = "value"
        else:
            kind = "attribution-only"
        print("{0}\t{1}\t{2}".format(key[0], key[1], kind))
        for label, cell in (("was", was), ("now", now)):
            if cell is None:
                print("  {0}: -".format(label))
                continue
            print("  {0}: [{1}] {2!r} via {3} in {4}".format(
                label, cell[0], cell[1], cell[2], cell[3]))
            if show_snippets:
                for snippet in cell[4]:
                    print("       | {0}".format(snippet))
    print("\n{0} changed cell(s) of {1}".format(
        changed, len(set(before) | set(after))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
