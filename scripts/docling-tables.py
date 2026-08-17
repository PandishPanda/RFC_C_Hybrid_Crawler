#!/usr/bin/env python3
"""Split a docling-serve conversion response into markdown + one TSV per table.

Called by docling-convert.sh; usable standalone on an already-downloaded response:

    python3 scripts/docling-tables.py response.json outdir basename
"""

import json
import sys
from pathlib import Path


def cell_text(cell):
    """Grid cells carry newlines and stray whitespace; TSV tolerates neither."""
    return " ".join((cell.get("text") or "").split())


def main():
    if len(sys.argv) != 4:
        sys.exit("usage: docling-tables.py <response.json> <outdir> <basename>")

    raw, outdir, base = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    resp = json.loads(raw.read_text())

    # A 200 response can still carry status=failure (e.g. a missing enrichment
    # model), in which case `document` is empty and there is nothing to split.
    status = resp.get("status")
    if status not in ("success", "partial_success"):
        print(f"conversion failed: status={status}", file=sys.stderr)
        for err in resp.get("errors") or [resp.get("detail")]:
            if err:
                print(f"  {err}", file=sys.stderr)
        sys.exit(1)

    doc = resp.get("document") or {}
    dl = doc.get("json_content") or {}

    if md := doc.get("md_content"):
        (outdir / f"{base}.md").write_text(md)

    if dl:
        (outdir / f"{base}.docling.json").write_text(
            json.dumps(dl, ensure_ascii=False, indent=1)
        )

    tables = dl.get("tables") or []
    texts = dl.get("texts") or []

    for i, table in enumerate(tables):
        grid = (table.get("data") or {}).get("grid") or []
        path = outdir / f"{base}.table{i:02d}.tsv"
        with path.open("w") as fh:
            for row in grid:
                fh.write("\t".join(cell_text(c) for c in row) + "\n")

    # Summary: enough to see at a glance whether the tables came out plausible.
    print(f"status: {status}  ({resp.get('processing_time') or 0:.1f}s)")
    print(f"pages: {len(dl.get('pages') or {})}  texts: {len(texts)}  tables: {len(tables)}")
    for i, table in enumerate(tables):
        data = table.get("data") or {}
        page = (table.get("prov") or [{}])[0].get("page_no", "?")
        print(
            f"  table{i:02d}  {data.get('num_rows')}x{data.get('num_cols')}"
            f"  page {page}  -> {base}.table{i:02d}.tsv"
        )
    if not tables:
        print("  (no tables found)")

    print(f"\nwrote to {outdir}/")


if __name__ == "__main__":
    main()
