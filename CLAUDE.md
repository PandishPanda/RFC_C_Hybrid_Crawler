# CLAUDE.md

RFC-C Hybrid crawler — the DEC-1 extraction pipeline for Bulgarian
university degree-program data. Extracted from the StudyStream monorepo
2026-08-17; this repo is the crawler ONLY.

## Commands

```bash
python3 -m unittest discover -s crawler/tests -p "test_*.py"   # full suite
python3 -m crawler run <UniID> [--tail]     # extraction spine for one uni
python3 -m crawler publish <UniID>          # run + ledger + pointer gates
python3 -m crawler onboard <UniID> --seed URL  # propose program pages
python3 -m crawler grade <UniID> --run-report P --key P  # blind benchmark
python3 -m crawler diff <UniID> --before P   # changed cells, for attribution review
python3 -m crawler labelkit <UniID>         # blank Phase-0 worksheet
python3 -m crawler check-pins <UniID>       # version-pin stale-green check
python3 -m crawler validate                 # the pipeline scorecard
python3 -m crawler refresh [--tail]         # unattended tick -> attention items
python3 -m crawler attention                # the human backlog, aged
python3 -m crawler resolve <item-id>        # execute one resolution
docker compose up -d                        # docling (table-pdf route only)
```

## Architecture

Python 3.9, STDLIB ONLY inside `crawler/` (plus bs4/lxml/requests for
rendering/fetching). Read CONTEXT.md (ubiquitous language — Program,
Snapshot, Artifact, Provenance, Attention) and docs/adr/ before
changing anything:

- ADR-0001 — deterministic core + gated LLM tail; site knowledge is
  CONFIG DATA, no per-site code, no LLM in the refresh loop
- ADR-0002 — Artifacts constructed only by render/artifact-store; every
  value gate-checked against verbatim provenance
- ADR-0003 — onboarding proposes, humans promote
- ADR-0004 — superseded by ADR-0006
- ADR-0006 — RSVU registry dropped entirely; config program entries are
  the unit of identity; Coverage/adjudication/Offerings removed

`crawler/configs/` is DATA the loader
validates strictly (unknown keys are errors). `crawler-out/` is gathered
output — gitignored, regenerable, never committed.

Some tests skip by design when gathered data is absent (spike-A replay
cache, docling not running, stability snapshots) — a skip names its
reason; an unexplained failure is a bug.

## Agent workflow

Issue tracker: local markdown under `.scratch/<feature>/` — see
docs/agents/issue-tracker.md. Every ticket gets a two-axis review
(standards + spec) before it is called done. A ticket that can change a
shipped value gets a THIRD axis — attribution: read the provenance of
every changed cell and run an independent refuter over it. See
docs/agents/attribution-review.md; it is not optional and not a
sampled check.

## Agent skills

### Issue tracker

Local markdown under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Attribution review

Third review axis for any ticket that can change a shipped value. See
`docs/agents/attribution-review.md`; run `crawler diff` to find the cells.

### Operations

The unattended loop (ADR-0005): weekly `crawler refresh` cron, the
attention backlog, resolve workflow, seasonal cadence. See
`docs/operations.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
