# Pipeline architecture — 2026-08-17

One-screen view, left → right: inputs → extraction spine → trust gates → publish → quality loop.
Detail lives in [workflow.md](workflow.md); decisions in [adr/](adr/).

```mermaid
flowchart LR
    subgraph IN["INPUTS (data, never code)"]
        direction TB
        CFG["Site config<br/>joins · anchors · suppress_labels"]
        ONB["onboard → proposals<br/>human promotes (ADR-0003)"] -.-> CFG
    end

    subgraph SPINE["EXTRACTION SPINE — crawler run [--tail]"]
        direction TB
        FETCH["Snapshot store<br/>append-only"] --> REND["Artifact store<br/>renderer pinned (ADR-0002)"]
        REND --> CASC["Deterministic cascade<br/>B anchors · G labels · F joins<br/>€0 · zero LLM"]
        CASC -- nulls --> TAIL["Gated LLM tail<br/>Haiku → Sonnet"]
    end

    subgraph TRUST["TRUST"]
        direction TB
        GATE{"Provenance gate<br/>value ⊆ snippet ⊆ Artifact"}
        GATE -- reject --> NUL["nulled, never ships"]
    end

    subgraph PUB["PUBLISH"]
        direction TB
        LED["append-only ledger"] --> EXP{"dataset expectations"}
        EXP -- pass --> PTR["pointer moves"]
        EXP -- fail --> BLK["blocked + why"]
    end

    subgraph QA["QUALITY LOOP"]
        direction TB
        GRD["blind grade vs frozen key<br/>+ human verdicts<br/>fab=0 · wrong ≤3%"]
        VAL["validate scorecard<br/>stability · baseline"]
    end

    CFG --> FETCH
    CASC -- values --> GATE
    TAIL -- values --> GATE
    GATE -- pass --> LED
    LED --> GRD
    GRD -- "wrong claims → config repairs" --> CFG
    PTR -.-> VAL
    GRD -.-> VAL
```

## Invariants (one line each)

| # | Invariant | Where |
| -- | -- | -- |
| 1 | No per-site code; site knowledge is strictly-validated config data | ADR-0001, `config.py` |
| 2 | Every EXTRACTED value carries verbatim, gate-checked Provenance — no human exemption | ADR-0002, `provenance.py` |
| 2b | A value true but stated nowhere ships as DERIVED, never as PASS — the gate cannot emit DERIVED | ADR-0007, `runner.derive_fields` |
| 3 | Onboarding proposes; only humans promote configs | ADR-0003, `onboarding.py` |
| 4 | No external registry: config program entries are the unit of identity | ADR-0006 |
| 5 | "Can't determine" ships as null — never a plausible value | gate + `suppress_labels` |
| 6 | Snapshots append-only; re-extraction from snapshots is cheap (shadow runs) | `store.py`, `publish(report=)` |
| 7 | The gate proves presence, not truth — semantic wrongs are caught by the blind key + human verdicts | `grader.py` |
| 8 | The blind key is a sample; a right value from the wrong place is caught by reading every changed cell + an independent refuter | `celldiff.py`, `docs/agents/attribution-review.md` |

## Current benchmark state (VUM, 2026-08-17)

94.7% correct (71/75) · 0 fabrications · gate PASS (1.4% ≤ 3%) · tail 42 calls/run ·
open design gap: adjudicated-stale values must also bind the LLM tail (`.scratch/pipeline-e2e/issues/11`).
