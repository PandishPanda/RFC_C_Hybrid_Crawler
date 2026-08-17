"""Offering entries in the ledger + completeness (ticket 20) -- no network.

Offering values must be recordable ALONGSIDE Program values without
disturbing the ledger's existing key, because that key is what every
expectation check and value diff is built on.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import ledger, runner  # noqa: E402


def field(value, snippets=("s",)):
    return {"status": "PASS", "value": value, "tier": "F",
            "method": "fee-join:x",
            "provenance": {"value": value, "source_url": "https://x",
                           "source_snippets": list(snippets),
                           "retrieved_at": "2026-08-16T00:00:00Z",
                           "method": "fee-join:x"}}


def report(offerings=()):
    return {"programs": [{
        "program_id": "biz", "name": "Б",
        "fields": {"tuition": field("204.52")},
        "offerings": list(offerings),
    }]}


def offering(key, value):
    """Built by the REAL producer, not hand-rolled.

    Review of ticket 20 found this helper originally constructed
    "biz#" + key itself and split it back apart, so the test asserted its
    own construction round-tripped and would have stayed green if the
    producer's shape diverged. runner._offering_records is the only thing
    that mints an offering record; ask it."""
    from crawler.cascade import TableSource
    from crawler.config import parse_site_config
    from crawler.registry import RegistryRow
    form, _, duration = key.partition(" - ")
    site = parse_site_config({
        "uni_id": "X", "sources": {},
        "programs": [{"id": "biz", "name": "Б", "page": "https://x/b",
                      "rsvu_code": "C"}]}, origin="<t>")
    row = RegistryRow(id=1, code="C", name="Б", major_id=1, major_name="m",
                      degree_code=3, degree_name="Бакалавър",
                      edu_forms="{0} - {1}".format(form, duration))
    records, _ = runner._offering_records(
        site, site.programs[0], row, {}, None,
        runner.init_offering_report_keys({}))
    record = records[0]
    record["fields"]["tuition"] = (
        field(value) if value else
        {"status": "NULL_OK", "value": None, "null_reason": "no recipe"})
    return record


class LedgerKeyIsUnchangedTest(unittest.TestCase):
    """ADR-0004 recorded, as UNVERIFIED design intent, that widening
    load_run_values' 3-tuple key would break test_ledger.py. Measured
    2026-08-16: it breaks 5 assertions. So Offering entries reuse
    program_id as a COMPOSITE id instead, needing zero ledger change."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_program_and_offering_entries_coexist_under_one_key_shape(self):
        ledger.append_run(self.dir, "X", "run-1",
                          report([offering("редовна - 4", "204.52")]),
                          "2026/2027")
        values = ledger.load_run_values(self.dir, "X", "run-1")
        self.assertIn(("biz", "tuition", "2026/2027", None), values)
        self.assertIn(("biz", "tuition", "2026/2027", "редовна - 4"), values)
        # program_id means ONE thing; the Offering is distinguished by the
        # key, not by two entities encoded into one string.
        self.assertEqual({k[0] for k in values}, {"biz"})

    def test_an_offering_entry_carries_its_scope_and_key(self):
        ledger.append_run(self.dir, "X", "run-1",
                          report([offering("задочна - 5", "300.00")]),
                          "2026/2027")
        entry = ledger.load_run_values(self.dir, "X", "run-1")[
            ("biz", "tuition", "2026/2027", "задочна - 5")]
        self.assertEqual(entry["scope"], "offering")
        self.assertEqual(entry["offering_key"], "задочна - 5")

    def test_a_program_entry_is_marked_program_scope(self):
        ledger.append_run(self.dir, "X", "run-1", report(), "2026/2027")
        entry = ledger.load_run_values(self.dir, "X", "run-1")[
            ("biz", "tuition", "2026/2027", None)]
        self.assertEqual(entry["scope"], "program")
        self.assertNotIn("offering_key", entry)

    def test_a_rejected_value_is_not_counted_as_a_stated_fee(self):
        # A record can carry a value AND a non-PASS status; counting it
        # would inflate completeness with something the gate refused.
        rec = offering("редовна - 4", "400")
        rec["fields"]["tuition"]["status"] = "REJECT_CONTAINMENT"
        got = runner.offering_completeness(report([rec]))
        self.assertEqual(got["редовна"]["stated"], 0)

    def test_an_offering_without_a_tuition_field_does_not_crash(self):
        # ADR-0004: "adding a field later is purely additive."
        rec = offering("редовна - 4", "400")
        rec["fields"] = {}
        got = runner.offering_completeness(report([rec]))
        self.assertEqual((got["редовна"]["stated"],
                          got["редовна"]["enumerated"]), (0, 1))

    def test_a_null_offering_is_not_recorded(self):
        # Only PASS values enter the ledger -- an unpriced Offering has
        # no value to diff.
        ledger.append_run(self.dir, "X", "run-1",
                          report([offering("задочна - 5", None)]),
                          "2026/2027")
        self.assertEqual(
            sorted(ledger.load_run_values(self.dir, "X", "run-1")),
            [("biz", "tuition", "2026/2027", None)])

    def test_a_report_without_offerings_writes_what_it_always_did(self):
        ledger.append_run(self.dir, "X", "run-1", report(), "2026/2027")
        entries = list(ledger.load_run_values(self.dir, "X", "run-1"))
        self.assertEqual(entries, [("biz", "tuition", "2026/2027", None)])


class DiffAcrossRunsTest(unittest.TestCase):
    """Ticket checkbox 3's cross-run half, which shipped unverified."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_offering_value_change_is_diffed_without_touching_the_program(self):
        ledger.append_run(self.dir, "X", "r1",
                          report([offering("редовна - 4", "400")]), "2026/2027")
        ledger.append_run(self.dir, "X", "r2",
                          report([offering("редовна - 4", "450")]), "2026/2027")
        changes = ledger.diff_runs(self.dir, "X", "r1", "r2")
        self.assertEqual(
            [(c["key"], c["change"], c.get("old_value"), c.get("new_value"))
             for c in changes],
            [(("biz", "tuition", "2026/2027", "редовна - 4"), "changed",
              "400", "450")])


class CompletenessTest(unittest.TestCase):
    def test_reported_per_form_never_blended(self):
        # самостоятелна can never have a fee column; blending it into one
        # number reads as permanent failure for a non-extraction reason.
        got = runner.offering_completeness(report([
            offering("редовна - 4", "204.52"),
            offering("задочна - 5", None),
            offering("самостоятелна - 4", None),
        ]))
        self.assertEqual(set(got), {"редовна", "задочна", "самостоятелна"})
        self.assertEqual((got["редовна"]["stated"], got["редовна"]["floor"]),
                         (1, 1.0))
        self.assertIn("FLOOR", got["редовна"]["caveat"])
        self.assertEqual(got["задочна"]["floor"], 0.0)

    def test_several_offerings_of_one_form_aggregate(self):
        got = runner.offering_completeness(report([
            offering("дистанционна - 4", "1"),
            offering("дистанционна - 5", None),
        ]))
        self.assertEqual((got["дистанционна"]["stated"],
                          got["дистанционна"]["enumerated"],
                          got["дистанционна"]["floor"]), (1, 2, 0.5))

    def test_a_report_without_offerings_yields_nothing(self):
        self.assertEqual(runner.offering_completeness(report()), {})


if __name__ == "__main__":
    unittest.main()
