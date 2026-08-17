# The provenance gate is a pure function over store-constructed Artifacts

Status: accepted (2026-08-11)

The STA-78 spikes grew four divergent provenance gates whose real difference was
not normalization but *which document they checked against* — two of the four
could validate a snippet against the wrong artifact without erroring, and the
one correct resolver (renderer-identity routing) lived in a spike script. We
decided the v2 gate is a **pure function** — `gate(value, segments, artifact)
→ Verdict` in `crawler/provenance.py` — where `Artifact` is a frozen value
object (canonical text + renderer id + version) that only the artifact-store
module constructs (enforced by a grep test on `Artifact(` call sites), and
`Verdict` is the one status vocabulary (`PASS · REJECT_CONTAINMENT ·
REJECT_SUPPORT · NULL_OK · PARSE_FAILURE`) spoken by the extraction cascade,
the LLM tail, status adjudication, the onboarding proposer, and the graders.

Why: purity makes the gate testable against the frozen benchmark key with zero
fixtures beyond vendored artifacts, and the store-only construction rule kills
the wrong-artifact failure class in the type system rather than in review
vigilance. The considered alternative — a gate that owns artifact resolution —
couples the trust check to storage IO and reproduces the path-guessing that
caused the measured 15-null incident. Consequences: resolution risk moves to
the artifact store (where rendering knowledge lives); `PARSE_FAILURE` travels
in the type from tail to grader, so a parse failure can never again grade as a
correct null; and a future contributor who makes the gate read files or accept
raw text is contradicting this ADR, not improving ergonomics.
