# Pipeline workflow — current version (2026-08-17)

The full DEC-1 workflow as it runs today: the extraction spine, the
publish gates, and the blind-benchmark loop that measures it. Updated for
the VUM benchmark round: shared BG label patterns, per-program
`suppress_labels` (human-adjudicated stale-green verdicts as config
data), shared fees-page joins on the BG site, and the manual-verdict
grade overlay. See README.md for the component-level spine diagram,
CONTEXT.md for the ubiquitous language, docs/adr/ for the decisions.

```mermaid
flowchart TB
    subgraph onboardG["Onboard (proposal-only, ADR-0003)"]
        onboard["crawler onboard --seed URL"] --> proposal["row↔page proposals"]
        proposal --> promote{"human promotes"}
    end
    promote --> config["Site config (DATA, strictly validated)<br/>sources · fees-page joins · anchors<br/>field_anchors · suppress_labels"]
    registry["RSVU registry export<br/>rows + Offerings (ADR-0004)"]

    subgraph spine["Extraction spine — crawler run [--tail]"]
        fetch["Snapshot store<br/>polite fetch · content-addressed · append-only"]
        render["Artifact store (ADR-0002)<br/>bs4 canonical · pdftotext · Docling TSV<br/>renderer identity pinned"]
        cascade["Deterministic cascade (zero LLM)<br/>tier B anchors → tier G shared labels<br/>(minus suppress_labels) → tier F family joins<br/>incl. shared fees-page sections (EN + BG)"]
        tail["Gated LLM tail (--tail only)<br/>cascade-nulled fields · Haiku via claude CLI<br/>escalation to Sonnet"]
        gate{"Provenance gate (pure fn)<br/>verbatim snippet ⊆ Artifact<br/>value tokens ⊆ snippet"}
        fetch --> render --> cascade
        cascade -- "field nulled" --> tail
        cascade -- "value+snippet" --> gate
        tail -- "value+snippet" --> gate
    end
    config --> fetch
    registry --> cascade

    report["run-report.json<br/>PASS · NULL_OK · REJECT_*"]
    gate -- pass --> report
    gate -- reject --> nulled["value nulled, never shipped<br/>repair-queue.json"]

    subgraph publishG["Publish — crawler publish"]
        ledger["append-only ledger<br/>Program × field × year × run"]
        expect{"dataset expectations<br/>coverage drop · null-spike · row count"}
        pointer["current pointer moves (promoted)"]
        blockedP["pointer stays — blocked + why"]
        ledger --> expect
        expect -- pass --> pointer
        expect -- fail --> blockedP
    end
    report --> ledger

    adjudicate["crawler adjudicate<br/>registry rows ↔ configured Programs<br/>coverage % + repair queue"]
    registry --> adjudicate

    subgraph benchG["Blind benchmark — the accuracy loop"]
        labelkit["crawler labelkit<br/>blank Phase-0 worksheet"]
        keyH{"human labels pages<br/>(never sees run report)"}
        key["frozen key (KeyEntry JSON)<br/>frozen: never reopened after grading"]
        grade["crawler grade<br/>token-support oracle"]
        checks{"CHECK rows"}
        manual["manual verdicts (human)<br/>ok / wrong per program-field"]
        verdictG{"gate: fabrications = 0<br/>wrong-but-gate-green ≤ 3%"}
        labelkit --> keyH --> key --> grade
        report --> grade
        grade --> checks -- "human judges" --> manual --> verdictG
        grade -- "graded rows" --> verdictG
    end
    verdictG -- "wrong claims traced" --> config

    subgraph validateG["Scorecard — crawler validate"]
        stability["hash-stability passes<br/>canonical churn over benchmark unis"]
        scorecard["scorecard + dossier<br/>PASS/FAIL/PENDING/PROXY per claim"]
        baseline["baseline.json (graded accuracy)"]
        stability --> scorecard
        baseline --> scorecard
    end
    verdictG -.-> baseline
    pointer -.-> scorecard

    staleness["Freshness: evidence inside documents only<br/>check-pins · year-patterned probes<br/>never HTTP metadata"]
    staleness -.-> fetch
```

## What changed in this version (2026-08-17 VUM round)

| Change | Kind | Why |
| -- | -- | -- |
| `bg-lang-label` in the shared label library | shared code (generic) | «Език на преподаване» declarations outrank EN boilerplate prose; captures dual declarations whole («БЪЛГАРСКИ ИЛИ АНГЛИЙСКИ») |
| `suppress_labels` per program-field | config schema + cascade | human-adjudicated stale-green verdicts as config data — a verbatim-present but wrong-claim value falls through to the next tier or an honest null (vum-corr's stale «1100 leva») |
| `vum-fees-bg` fees-page joins | config data | tuition for BG-site programs lives only on «Стипендии и такси»; 8 programs joined, deterministic tuition coverage 3 → 11 |
| manual-verdict overlay exercised | process | CHECK rows (incl. process-page admission cells keyed by sentinel) settled by human ok/wrong; verdicts carried over only when shipped values are byte-identical |
| `labelkit` CLI un-dead-coded | bug fix | handler body sat unreachable inside the `check-pins` block |

## Known open edges

- **JS-injected content is invisible to the fetch route** — vum-tour-phd's
  fee block is Elementor-loaded; the fetched HTML contains no fee at all.
  Honest null today; the JS-heavy benchmark uni (AUBG) is where this gets
  addressed or formally bounded.
- **LLM tail cost** — $0.10/call vs the $0.06 bar (scorecard FAIL); the
  named cause is prompt bundling (one call per field today; bundling a
  program's nulled fields into one call is the designed remedy).
- **Spike-A replay cache absent on this machine** — CASCADE frozen
  acceptance stays PENDING by design; the cache would need to come from
  the machine that ran the original spike.
