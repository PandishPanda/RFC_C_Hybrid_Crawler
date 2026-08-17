# Cascade test fixtures (vendored from spike A, STA-78)

Working surfaces the extraction cascade reads that are NOT the gate-checked
artifact text itself (those live in ../artifacts/):

- `docling/su-fees/*.tsv`, `docling/mu-fees/*.tsv` — the ACTUAL per-table
  Docling TSV files (spike A `out/docling/<name>/*.tsv`). The tier-F
  column-aware fee-row resolver reads these; its emitted segments must be
  lines of the joined `docling-tsv-*` artifact text (verified in
  test_cascade.py against crawler.render.tsv_artifact_text).
- `layout-su-adm.txt` — raw `pdftotext -layout` output of the SU admission
  ordinance (spike A `out/pdftext/su-adm.txt`). The ordinance row/clause
  joins are line-anchored and need the layout surface; their segments are
  whitespace-normalized substrings of it, contained in the composite
  `pdftext-su-adm` artifact text the gate checks.

Vendored 2026-08-12 from `.scratch/sta-78/spikes/a/out/`. Do not edit.
