"""Pipeline policy (ADR-0005, ticket 04): one verdict per university per
tick, and the SLA age constants.

The proceed/warn/block rules were scattered — expectations blocked
publishes, gate failures set exit codes, drift printed a warning, CHECKs
held grades PENDING — each surface deciding its own severity inline.
This module is the one table. It CONSUMES those surfaces' outputs
(expectations keeps its thresholds; the runner keeps its gate); it never
recomputes them.

Policy is code constants, not config: these are pipeline policy, not
site knowledge, so ADR-0001's config-is-site-data line stays clean.
Per-kind SLA pairs and per-uni policy config were considered and
rejected for v1 — revisit when two policies genuinely diverge.

Pure functions only: dict in, verdict out.
"""
from typing import NamedTuple, Tuple

__all__ = ["WARN_AGE_DAYS", "ESCALATE_AGE_DAYS", "sla_state",
           "Verdict", "verdict", "RULES"]

# kind + age is the priority signal (ADR-0005)
WARN_AGE_DAYS = 7
ESCALATE_AGE_DAYS = 30


def sla_state(age_days):
    # type: (int) -> str
    """ok < 7 days open <= warn < 30 <= escalate."""
    if age_days >= ESCALATE_AGE_DAYS:
        return "escalate"
    if age_days >= WARN_AGE_DAYS:
        return "warn"
    return "ok"


class Verdict(NamedTuple):
    decision: str                  # proceed | warn | block
    needs_human: Tuple[str, ...]   # attention kinds, table order


# (signal, attention kind, decision when truthy) — table order is
# presentation order for needs_human. block: promoted data is stale
# (blocked-publish) or the tick itself broke (refresh-error). warn:
# values were nulled or judgments wait, but what shipped is gate-clean.
RULES = (
    ("refresh_error", "refresh-error", "block"),
    ("publish_blocked", "blocked-publish", "block"),
    ("gate_failures", "gate-failure", "warn"),
    ("pending_checks", "check-verdict", "warn"),
    ("pending_proposals", "proposal", "warn"),
    ("drift", "drift", "warn"),
)

_SEVERITY = {"proceed": 0, "warn": 1, "block": 2}


def verdict(signals):
    # type: (dict) -> Verdict
    """The tick's one verdict for one university.

    ``signals`` must carry exactly the keys the table names (booleans or
    counts) — an unknown key is a typo that would otherwise silently
    read as no-signal, so it raises."""
    unknown = sorted(set(signals) - {rule[0] for rule in RULES})
    if unknown:
        raise ValueError(
            "unknown policy signal(s) {0} — the table knows: {1}".format(
                ", ".join(unknown), ", ".join(r[0] for r in RULES)))
    decision = "proceed"
    kinds = []
    for signal, kind, rule_decision in RULES:
        if signals.get(signal):
            kinds.append(kind)
            if _SEVERITY[rule_decision] > _SEVERITY[decision]:
                decision = rule_decision
    return Verdict(decision=decision, needs_human=tuple(kinds))
