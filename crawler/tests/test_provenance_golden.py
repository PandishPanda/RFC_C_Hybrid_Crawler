"""GOLDEN suite — spike A's 94 final E3 records must gate PASS.

Every non-null value spike A shipped (out/e3-results.json, 94 values across
20 answer-key programs x 5 fields) is replayed through the v2 gate against
the exact artifact rendering the spike checked it against (vendored under
fixtures/artifacts/). The spike's own gates passed all 94 with zero
failures; the v2 gate's single normalization policy must not regress any.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.provenance import Status, gate  # noqa: E402
from crawler.tests import provenance_fixtures as fx  # noqa: E402


class TestGoldenRecords(unittest.TestCase):
    def test_fixture_integrity(self):
        """94 records, 7 multi-segment (gate2 joins), artifacts untampered."""
        records = fx.golden_records()
        self.assertEqual(len(records), 94)
        multi = [r for r in records if len(r["segments"]) > 1]
        self.assertEqual(len(multi), 7)
        for ref, m in fx.manifest().items():
            self.assertEqual(fx.artifact_sha256(ref), m["sha256"],
                             f"artifact fixture {ref} does not match its "
                             f"vendored sha256 — fixture tampered or corrupt")

    def test_every_golden_record_passes(self):
        failures = []
        for r in fx.golden_records():
            artifact = fx.load_artifact(r["artifact"])
            v = gate(r["value"], r["segments"], artifact)
            with self.subTest(program=r["program_id"], field=r["field"]):
                if v.status is not Status.PASS:
                    failures.append((r["program_id"], r["field"],
                                     v.status.name, v.detail))
                self.assertIs(v.status, Status.PASS,
                              f"{r['program_id']}/{r['field']} "
                              f"({r['method']}): {v.status.name} — {v.detail}")
        self.assertEqual(failures, [])

    def test_golden_covers_all_renderer_kinds(self):
        """The suite exercises all three artifact renderings."""
        kinds = {fx.manifest()[r["artifact"]]["renderer_id"]
                 for r in fx.golden_records()}
        self.assertEqual(kinds, {"bs4-lxml-canonical",
                                 "pdftotext-flow+layout",
                                 "docling-serve-tsv"})


if __name__ == "__main__":
    unittest.main()
