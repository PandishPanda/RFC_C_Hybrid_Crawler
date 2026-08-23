"""The Field record: the persisted form of one Program-field cell —
status, value, and the proof appropriate to the status (Provenance for
extracted values, a `derivation` for Derived values, a verdict detail
for rejects). ONE module constructs, serializes and parses it; a shape
no constructor emits cannot exist.

Until 2026-08-23 this schema was implicit: three constructors in the
runner emitted seven distinct key-sets and five modules consumed them by
raw dict access, split between loud ``rec["status"]`` reads and
defensive ``.get()`` reads. The defensive half sat on the
safety-critical consumer: celldiff — the Changed-cell finder the
attribution review runs on — read every axis through ``.get()``, so a
renamed or dropped key degraded to None on BOTH sides of a comparison,
the cells compared equal, and `crawler diff` printed "nothing moved"
for exactly the misattribution class it exists to catch. The ``.get()``s
were not carelessness; they existed BECAUSE the record was polymorphic
— which is what made the silent path structural. from_dict() replaces
that with validation: malformed raises, it never flows on as Nones.

The seven states, with the exact key orders the runner has always
written (json.dump preserves insertion order, so to_dict() reproducing
them is the proof that run-reports do not change byte-for-byte):

  spine-null    status, value, null_reason
  spine-pass    status, tier, method, artifact, verdict_detail,
                [context], value, provenance
  spine-reject  status, tier, method, artifact, verdict_detail,
                [context], value
  tail-pass     status, verdict_detail, tail_attempts, tail_escalated,
                tier, method, artifact, value, provenance
  tail-null     status, verdict_detail, tail_attempts, tail_escalated,
                value, null_reason
  tail-reject   status, verdict_detail, tail_attempts, tail_escalated,
                value
  derived       status, value, tier, method, derivation   (ADR-0007)

The tail-null state genuinely lacks tier/method — a divergence the old
duplicate report-shapers introduced by accident and this module now
records on purpose, because changing it would change shipped bytes.

Objects are short-lived by design: the runner constructs and calls
to_dict() immediately, so the in-memory report stays JSON-dict-shaped
for every in-process consumer; disk consumers (grader, celldiff,
ledger) parse with from_dict() and read attributes.

Python 3.9, stdlib only.
"""
from typing import NamedTuple, Optional, Tuple

__all__ = ["FieldRecord", "RecordShapeError", "STATUSES"]

STATUSES = ("PASS", "REJECT_CONTAINMENT", "REJECT_SUPPORT", "NULL_OK",
            "PARSE_FAILURE", "DERIVED")
_REJECTS = ("REJECT_CONTAINMENT", "REJECT_SUPPORT", "PARSE_FAILURE")

_KEYS = ("status", "value", "tier", "method", "artifact", "provenance",
         "derivation", "null_reason", "verdict_detail", "tail_attempts",
         "tail_escalated", "context")


class RecordShapeError(ValueError):
    """A record dict no constructor could have emitted."""


class FieldRecord(NamedTuple):
    status: str
    value: Optional[str] = None
    tier: Optional[str] = None
    method: Optional[str] = None
    artifact: Optional[dict] = None
    provenance: Optional[dict] = None
    derivation: Optional[dict] = None
    null_reason: Optional[str] = None
    verdict_detail: Optional[str] = None
    tail_attempts: Optional[int] = None
    tail_escalated: Optional[bool] = None
    context: Optional[dict] = None

    # ------------------------------------------------------- constructors
    @classmethod
    def spine_null(cls, null_reason):
        return cls(status="NULL_OK", null_reason=null_reason)

    @classmethod
    def spine_pass(cls, *, value, tier, method, artifact, provenance,
                   verdict_detail, context=None):
        return cls(status="PASS", value=value, tier=tier, method=method,
                   artifact=artifact, provenance=provenance,
                   verdict_detail=verdict_detail, context=context)

    @classmethod
    def spine_reject(cls, *, status, tier, method, artifact,
                     verdict_detail, context=None):
        return cls(status=status, tier=tier, method=method,
                   artifact=artifact, verdict_detail=verdict_detail,
                   context=context)

    @classmethod
    def tail_pass(cls, *, value, tier, method, artifact, provenance,
                  verdict_detail, tail_attempts, tail_escalated):
        return cls(status="PASS", value=value, tier=tier, method=method,
                   artifact=artifact, provenance=provenance,
                   verdict_detail=verdict_detail,
                   tail_attempts=tail_attempts,
                   tail_escalated=tail_escalated)

    @classmethod
    def tail_null(cls, null_reason, *, tail_attempts, tail_escalated):
        return cls(status="NULL_OK", null_reason=null_reason,
                   verdict_detail=null_reason,
                   tail_attempts=tail_attempts,
                   tail_escalated=tail_escalated)

    @classmethod
    def tail_reject(cls, *, status, verdict_detail, tail_attempts,
                    tail_escalated):
        return cls(status=status, verdict_detail=verdict_detail,
                   tail_attempts=tail_attempts,
                   tail_escalated=tail_escalated)

    @classmethod
    def derived(cls, *, value, rule, basis):
        # ADR-0007: no provenance, no artifact — an empty or invented
        # snippet is the fabrication the DERIVED status exists to avoid
        return cls(status="DERIVED", value=value, tier="D",
                   method="derive:" + rule,
                   derivation={"rule": rule, "input": value,
                               "basis": basis})

    # ------------------------------------------------------ serialization
    def _is_tail(self):
        return self.tail_attempts is not None

    def to_dict(self):
        # type: () -> dict
        """The exact dict the runner has always written, key order
        included — the JSON shape is this method's contract."""
        if self.status == "DERIVED":
            return {"status": self.status, "value": self.value,
                    "tier": self.tier, "method": self.method,
                    "derivation": dict(self.derivation)}
        if self._is_tail():
            d = {"status": self.status,
                 "verdict_detail": self.verdict_detail,
                 "tail_attempts": self.tail_attempts,
                 "tail_escalated": self.tail_escalated}
            if self.status == "PASS":
                d.update(tier=self.tier, method=self.method,
                         artifact=dict(self.artifact), value=self.value,
                         provenance=dict(self.provenance))
            else:
                d["value"] = None
                if self.status == "NULL_OK":
                    d["null_reason"] = self.null_reason
            return d
        if self.status == "NULL_OK":
            return {"status": self.status, "value": None,
                    "null_reason": self.null_reason}
        d = {"status": self.status, "tier": self.tier,
             "method": self.method, "artifact": dict(self.artifact),
             "verdict_detail": self.verdict_detail}
        if self.context:
            d["context"] = dict(self.context)
        d["value"] = self.value if self.status == "PASS" else None
        if self.status == "PASS":
            d["provenance"] = dict(self.provenance)
        return d

    @classmethod
    def from_dict(cls, data):
        # type: (dict) -> "FieldRecord"
        """Parse and VALIDATE one record dict. A shape no constructor
        emits raises RecordShapeError instead of degrading to Nones —
        the silent-equal path this module exists to close."""
        unknown = sorted(set(data) - set(_KEYS))
        if unknown:
            raise RecordShapeError(
                "unknown record key(s) {0} — a typo'd key must fail "
                "loudly, not read as absent".format(", ".join(unknown)))
        status = data.get("status")
        if status not in STATUSES:
            raise RecordShapeError(
                "unknown status {0!r} — one of: {1}".format(
                    status, ", ".join(STATUSES)))
        if status == "DERIVED" and not data.get("derivation"):
            raise RecordShapeError(
                "a DERIVED record must carry its derivation (ADR-0007: "
                "the rule applied is its whole audit surface)")
        if status == "PASS" and not (data.get("provenance")
                                     and data.get("artifact")):
            raise RecordShapeError(
                "a PASS record must carry provenance and artifact — a "
                "shipped value without proof is the fabrication class "
                "the gate exists to stop")
        if status == "NULL_OK" and not data.get("null_reason"):
            raise RecordShapeError(
                "a NULL_OK record must say why (null_reason)")
        if status in _REJECTS and "tail_attempts" not in data \
                and not data.get("artifact"):
            raise RecordShapeError(
                "a spine reject must carry the artifact it rejected "
                "against")
        return cls(**{k: data.get(k) for k in _KEYS})

    # ---------------------------------------------------------- accessors
    def artifact_ref(self):
        # type: () -> Optional[str]
        return self.artifact["ref"] if self.artifact else None

    def snippets(self):
        # type: () -> Tuple[str, ...]
        if not self.provenance:
            return ()
        return tuple(self.provenance.get("source_snippets") or ())
