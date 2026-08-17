# Crawler v2: deterministic extraction core with a gated LLM tail

Status: accepted (2026-08-11)

We need trustworthy structured data (tuition, duration, language, degree,
admission) for ~6,400 RSVU registry Programs spread across 51 heterogeneous
Bulgarian university sites and PDFs, where the previous attempt shipped 36%
unverifiable "quoted" fields because trust lived in prompts. We decided to
build the crawler as a deterministic pipeline — plain-HTTP fetch into a
content-addressed append-only snapshot store, per-document renderer routing
(bs4 / pdftotext / Docling table TSVs), a shared-pattern + family-join
extraction cascade with **table values always resolved column-aware from table
structure** — with a **cheap gated LLM tail (Haiku, forced structured output,
tier escalation) only for fields the deterministic cascade nulls**, and a
**mechanical provenance gate** (verbatim snippet ⊆ exact named artifact; value
tokens ⊆ snippet) that every non-null value must pass before entering an
append-only ledger keyed by Program × field × academic_year × run. Site
knowledge is config data proposed by onboarding agents and promoted by a
human; no LLM and no per-site code runs in the production refresh loop for
the deterministic share. Freshness trusts only evidence inside documents
(`valid_for` year + year-patterned URL probes), never HTTP metadata.

Why: three independently-written, posture-seeded RFCs (deterministic-max /
LLM-max / agentic; STA-78) converged unanimously on the skeleton (snapshots,
Docling routing, mechanical gate, append-only ledger, canonical-text hashing,
`valid_for` drift detection) — convergence across opposed priors. An
adversarial audit then established that the deterministic core was the only
fully-proven engine (99/100 on the frozen benchmark key, reproduced twice
offline, 0 fabrications; weighted score 75.5 vs 59.5/52.0 under
accuracy-dominant weights agreed before writing), while the measured failure
modes point in both directions: pure determinism's cost concentrates in a
prose-shaped tail (5 of 6 bespoke anchors served one marketing-prose site),
and both LLM-flavored spikes measured the gate's blind spot — a truthful
snippet from the wrong table row/column passes — which is why table values
are deterministic even when an LLM proposes the row.

Considered and rejected: pure deterministic extraction (rejected for the
authoring bill and prose-tail risk its own RFC flagged as the killer);
LLM-primary extraction (completed measurement: 66/100 raw on the benchmark
key, 85% on parseable output, 0 fabrications, but 25% of programs lost to
raw-JSON parse failures single-shot at Haiku tier, and cost ~2–3× the
original claim — strong as a gated tail, unsafe as the primary engine); autonomous agent passes in
the production loop (the only approach with shipped precedent, but discovery
cost/quality at scale had zero experiments behind it — demoted to
onboarding-proposer, its measured strength).

Consequences: refresh for the deterministic share (~87% measured on the
benchmark) costs €0 and 0 tokens; every value in the product is mechanically
traceable to bytes in a stored snapshot; accuracy claims are upper bounds
until the blind fresh-university benchmark (v0.2 gate: wrong-but-gate-green
≤2–3%, fabrications = 0) because all round evidence was measured against a
key present during development. Revisit if the LLM-tail share exceeds ~35%
on the next 10 universities (rebalance toward LLM-primary; same gate and
ledger survive) or if the blind benchmark breaches its thresholds (demote the
LLM tail to proposal-only).
