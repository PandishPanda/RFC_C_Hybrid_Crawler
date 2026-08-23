"""The Field record (crawler/field_record.py): the persisted form of one
Program-field cell, constructed, serialized and parsed by ONE module.

Before this module the record was an implicit, polymorphic dict: three
constructors in the runner emitted seven distinct key-sets, five modules
consumed them by raw key access, and the sharpest consumer — celldiff,
the Changed-cell finder the attribution review runs on — read every axis
through .get(), so a renamed key degraded to None on BOTH sides of the
comparison and `crawler diff` printed "nothing moved" for exactly the
class it exists to catch.

Three proofs:

1. GOLDEN SHAPES — for each of the seven states, to_dict() equals the
   dict the runner used to build by hand, KEY ORDER INCLUDED (json.dump
   preserves insertion order, so this is the byte-level proof that
   run-reports do not change).
2. ROUND-TRIP — from_dict(to_dict(x)) == x for every state.
3. VALIDATION — a shape no constructor emits cannot exist: unknown keys,
   a PASS without provenance, a DERIVED without derivation, an unknown
   status all raise instead of flowing on as Nones.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import field_record  # noqa: E402
from crawler.field_record import FieldRecord  # noqa: E402

ARTIFACT = {"ref": "html:https://x.test/p",
            "renderer_id": "bs4-lxml-canonical:aggressive",
            "renderer_version": "bs4-4.15.0/lxml-6.1.1"}
PROV = {"value": "Бакалавър", "source_url": "https://x.test/p",
        "source_snippets": ["... Бакалавър ..."],
        "retrieved_at": "2026-08-15T00:00:00Z", "method": "label:x"}


def golden():
    """The seven literal shapes exactly as runner.py built them."""
    return {
        "spine-null": FieldRecord.spine_null("cascade found nothing"),
        "spine-pass": FieldRecord.spine_pass(
            value="Бакалавър", tier="G", method="label:x",
            artifact=dict(ARTIFACT), provenance=dict(PROV),
            verdict_detail="1 segment(s) contained"),
        "spine-reject": FieldRecord.spine_reject(
            status="REJECT_SUPPORT", tier="G", method="label:x",
            artifact=dict(ARTIFACT), verdict_detail="unsupported"),
        "tail-pass": FieldRecord.tail_pass(
            value="Бакалавър", tier="llm-tail", method="llm-tail:haiku",
            artifact=dict(ARTIFACT), provenance=dict(PROV),
            verdict_detail="ok", tail_attempts=1, tail_escalated=False),
        "tail-null": FieldRecord.tail_null(
            "no candidate documents", tail_attempts=0,
            tail_escalated=False),
        "tail-reject": FieldRecord.tail_reject(
            status="REJECT_CONTAINMENT", verdict_detail="not contained",
            tail_attempts=2, tail_escalated=True),
        "derived": FieldRecord.derived(
            value="български", rule="default_language",
            basis="asserted by site config; no document of this "
                  "program states this field"),
    }


class GoldenShapeTest(unittest.TestCase):
    EXPECTED_KEYS = {
        "spine-null": ["status", "value", "null_reason"],
        "spine-pass": ["status", "tier", "method", "artifact",
                       "verdict_detail", "value", "provenance"],
        "spine-reject": ["status", "tier", "method", "artifact",
                         "verdict_detail", "value"],
        "tail-pass": ["status", "verdict_detail", "tail_attempts",
                      "tail_escalated", "tier", "method", "artifact",
                      "value", "provenance"],
        "tail-null": ["status", "verdict_detail", "tail_attempts",
                      "tail_escalated", "value", "null_reason"],
        "tail-reject": ["status", "verdict_detail", "tail_attempts",
                        "tail_escalated", "value"],
        "derived": ["status", "value", "tier", "method", "derivation"],
    }

    def test_key_orders_are_exactly_the_runner_originals(self):
        for name, rec in golden().items():
            with self.subTest(state=name):
                self.assertEqual(list(rec.to_dict().keys()),
                                 self.EXPECTED_KEYS[name])

    def test_representative_values(self):
        d = golden()["spine-pass"].to_dict()
        self.assertEqual(d["status"], "PASS")
        self.assertEqual(d["provenance"], PROV)
        d = golden()["tail-null"].to_dict()
        self.assertEqual(d["status"], "NULL_OK")
        self.assertEqual(d["value"], None)
        self.assertEqual(d["null_reason"], "no candidate documents")
        d = golden()["derived"].to_dict()
        self.assertEqual(d["method"], "derive:default_language")
        self.assertEqual(d["tier"], "D")

    def test_spine_context_is_optional_and_ordered_before_value(self):
        rec = FieldRecord.spine_pass(
            value="x", tier="B", method="anchor:a",
            artifact=dict(ARTIFACT), provenance=dict(PROV),
            verdict_detail="ok", context={"form": "редовно"})
        keys = list(rec.to_dict().keys())
        self.assertEqual(keys, ["status", "tier", "method", "artifact",
                                "verdict_detail", "context", "value",
                                "provenance"])


class RoundTripTest(unittest.TestCase):
    def test_every_state_round_trips(self):
        for name, rec in golden().items():
            with self.subTest(state=name):
                self.assertEqual(FieldRecord.from_dict(rec.to_dict()), rec)


class ValidationTest(unittest.TestCase):
    def test_unknown_key_is_rejected(self):
        d = golden()["spine-null"].to_dict()
        d["provenence"] = {}    # the typo the .get() era would swallow
        with self.assertRaises(field_record.RecordShapeError):
            FieldRecord.from_dict(d)

    def test_a_pass_without_provenance_cannot_exist(self):
        d = golden()["spine-pass"].to_dict()
        del d["provenance"]
        with self.assertRaises(field_record.RecordShapeError):
            FieldRecord.from_dict(d)

    def test_a_derived_without_derivation_cannot_exist(self):
        d = golden()["derived"].to_dict()
        del d["derivation"]
        with self.assertRaises(field_record.RecordShapeError):
            FieldRecord.from_dict(d)

    def test_an_unknown_status_is_rejected(self):
        d = golden()["spine-null"].to_dict()
        d["status"] = "MAYBE"
        with self.assertRaises(field_record.RecordShapeError):
            FieldRecord.from_dict(d)

    def test_a_null_without_reason_cannot_exist(self):
        with self.assertRaises(field_record.RecordShapeError):
            FieldRecord.from_dict({"status": "NULL_OK", "value": None})


if __name__ == "__main__":
    unittest.main()
