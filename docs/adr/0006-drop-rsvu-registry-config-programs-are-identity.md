# Drop the RSVU registry entirely: config program entries are the unit of identity; Coverage, adjudication, and Offerings enumeration are removed

Status: accepted (2026-08-17) — supersedes ADR-0004; amends ADR-0003's
row↔page framing; departs from STA-78's fixed-input boundary

## Decision

The owner decided (2026-08-17, during the AUBG benchmark round) that the
RSVU registry (`rsvu.mon.bg`) will no longer be used anywhere in the
pipeline. Everything registry-derived is removed, not made optional:

- **Identity.** A Program is now a hand-authored entry in
  `crawler/configs/<Uni>.json`, identified by its config program id
  (`aubg-cs`). The `rsvu_code`/`rsvu_id` fields are deleted. CONTEXT.md's
  Program definition changes accordingly.
- **Coverage is dropped.** Without an external enumeration there is no
  honest denominator; a Coverage computed against configured programs is
  always ~100% and an honesty check in name only. Completeness claims
  are now explicitly scoped: "the configured programs were extracted."
  `adjudication.py`, its repair queue, and the registry exports go with
  it.
- **Offerings enumeration is dropped** (supersedes ADR-0004). Fee and
  form data attach to the Program directly as extracted, gate-checked
  fields; no (attendance form × duration) enumeration exists.
- **Onboarding** no longer matches registry rows to pages; it proposes
  candidate program pages from a seed. ADR-0003's core stands unchanged:
  proposals are never auto-promoted; a human authors and promotes config.

## What this knowingly gives up

Recorded so the trade is visible, not rediscovered:

1. **The honesty denominator.** "23.8% of registry rows covered" (MUPleven,
   measured hours before this decision) is the kind of claim the pipeline
   can no longer make or be held to. Nothing now detects that a
   university teaches programs we simply never configured.
2. **Enumerated Offerings and per-form fees** — the registry's `edu_forms`
   was the only source that listed every accredited (form, duration)
   pair; extraction can only find what pages state.
3. **STA-78 comparability.** The RFC exercise fixed the registry as an
   input shared by all three RFCs. RFC-C's evidence remains valid for
   extraction/trust claims, but its Coverage-related material and the
   benchmark's "37 programs" style denominators no longer apply to this
   pipeline. The review round should read this ADR alongside RFC-C.

## Why (as given)

The registry dependency was judged not worth its cost by the owner:
stale hand-captured exports, per-row reconciliation burden (48-row repair
queues at a 4-program uni), and identity friction (unlinked or
ambiguously-linked rows) against a pipeline whose real deliverable is
extracted page data.

## Revisit if

A completeness claim with an external denominator becomes a product
requirement again (then a curated target list — human-owned, per ADR-0003
discipline — is the replacement, not RSVU); or per-form fee structure
returns as a requirement (then forms become extracted, gate-checked
fields, not an enumeration).

## Consequence for ADR-0005 (Attention Ledger)

The `repair-row` attention kind dies with adjudication, and onboarding
proposals change shape (page proposals, not row matches). ADR-0005's v1
kind list is therefore eight, not nine: blocked-publish, gate-failure,
proposal, check-verdict, drift, export-age → dropped as well (no exports
to age), unreviewed-auto-resolution → dropped (no auto-resolutions
exist). Net: **six kinds** — blocked-publish, gate-failure, proposal,
check-verdict, drift, refresh-error. The ledger design itself is
unchanged.
