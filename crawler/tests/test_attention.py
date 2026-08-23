"""The Attention Ledger core (ADR-0005, ticket 01) and its producers
(ticket 02). All offline, synthetic reports — no network, no LLM.

Five proofs:

1. IDENTITY — an item's id is its natural key kind:uni[:subject];
   re-detection on a later tick updates last_seen and PRESERVES
   opened_at (append-per-detection would reset the age clock).
2. LAPSE — an open item that stops being detected inside the synced
   scope closes as lapsed; items OUTSIDE the scope are untouched (a tick
   for one university must not lapse another's backlog).
3. EVIDENCE CARVE-OUT — volatile kinds (their source is overwritten or
   printed and gone) snapshot evidence into the item at open time;
   durable kinds carry a ref and no copy.
4. RESOLUTIONS — mark_resolved appends {id, resolved_at, resolved_by,
   action, reason} to the tracked resolutions file and flips the item;
   the open ledger stays derived state, the resolution is original data.
5. PRODUCERS — each detector maps its real surface (publish-report,
   run-report gate_failures, onboarding proposal, GradeReport CHECK
   rows, drift entries) to exactly the items ADR-0005 names.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import attention  # noqa: E402

T0 = "2026-08-01T00:00:00Z"
T1 = "2026-08-05T00:00:00Z"
T2 = "2026-08-20T00:00:00Z"


def blocked(uni="VUM"):
    return {"kind": "blocked-publish", "uni_id": uni,
            "evidence": {"run_id": "r1", "blocked_reasons": ["coverage drop"]}}


def drifted(uni="SHU", code="894"):
    return {"kind": "drift", "uni_id": uni, "subject": code,
            "evidence": {"pinned_version": "3", "current_version": "4"}}


def proposal(uni="AUBG"):
    return {"kind": "proposal", "uni_id": uni,
            "ref": "crawler-out/AUBG/onboarding-proposal.json"}


class IdentityTest(unittest.TestCase):
    def test_id_is_the_natural_key(self):
        self.assertEqual(attention.item_id("blocked-publish", "VUM"),
                         "blocked-publish:VUM")
        self.assertEqual(
            attention.item_id("check-verdict", "VUM", "vum-corr.tuition"),
            "check-verdict:VUM:vum-corr.tuition")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            attention.item_id("todo", "VUM")

    def test_redetection_preserves_opened_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            attention.sync(tmp, [blocked()], kinds=["blocked-publish"],
                           unis=["VUM"], now=T0)
            r = attention.sync(tmp, [blocked()], kinds=["blocked-publish"],
                               unis=["VUM"], now=T1)
            self.assertEqual(r["opened"], [])
            self.assertEqual(r["refreshed"], ["blocked-publish:VUM"])
            item = attention.load_items(tmp)["blocked-publish:VUM"]
            self.assertEqual(item["opened_at"], T0)
            self.assertEqual(item["last_seen"], T1)
            self.assertEqual(item["status"], "open")

    def test_age_runs_from_opened_at(self):
        item = {"opened_at": T0}
        self.assertEqual(attention.age_days(item, now=T2), 19)


class LapseTest(unittest.TestCase):
    def test_undetected_item_in_scope_lapses(self):
        with tempfile.TemporaryDirectory() as tmp:
            attention.sync(tmp, [blocked()], kinds=["blocked-publish"],
                           unis=["VUM"], now=T0)
            r = attention.sync(tmp, [], kinds=["blocked-publish"],
                               unis=["VUM"], now=T1)
            self.assertEqual(r["lapsed"], ["blocked-publish:VUM"])
            item = attention.load_items(tmp)["blocked-publish:VUM"]
            self.assertEqual(item["status"], "lapsed")

    def test_items_outside_the_synced_scope_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            attention.sync(tmp, [blocked("VUM")], kinds=["blocked-publish"],
                           unis=["VUM"], now=T0)
            attention.sync(tmp, [drifted("SHU")], kinds=["drift"],
                           unis=["SHU"], now=T0)
            # a later VUM-only tick detects nothing: VUM lapses, SHU stays
            r = attention.sync(tmp, [], kinds=["blocked-publish", "drift"],
                               unis=["VUM"], now=T1)
            self.assertEqual(r["lapsed"], ["blocked-publish:VUM"])
            self.assertEqual(
                attention.load_items(tmp)["drift:SHU:894"]["status"], "open")

    def test_a_lapsed_item_redetected_reopens_with_a_fresh_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            attention.sync(tmp, [blocked()], kinds=["blocked-publish"],
                           unis=["VUM"], now=T0)
            attention.sync(tmp, [], kinds=["blocked-publish"],
                           unis=["VUM"], now=T1)
            r = attention.sync(tmp, [blocked()], kinds=["blocked-publish"],
                               unis=["VUM"], now=T2)
            self.assertEqual(r["opened"], ["blocked-publish:VUM"])
            item = attention.load_items(tmp)["blocked-publish:VUM"]
            self.assertEqual(item["opened_at"], T2)
            self.assertEqual(item["status"], "open")


class EvidenceTest(unittest.TestCase):
    def test_volatile_kind_snapshots_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            attention.sync(tmp, [blocked()], kinds=["blocked-publish"],
                           unis=["VUM"], now=T0)
            item = attention.load_items(tmp)["blocked-publish:VUM"]
            self.assertEqual(item["evidence"]["blocked_reasons"],
                             ["coverage drop"])

    def test_volatile_kind_without_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                attention.sync(
                    tmp, [{"kind": "blocked-publish", "uni_id": "VUM"}],
                    kinds=["blocked-publish"], unis=["VUM"], now=T0)

    def test_durable_kind_carries_a_ref_not_a_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            attention.sync(tmp, [proposal()], kinds=["proposal"],
                           unis=["AUBG"], now=T0)
            item = attention.load_items(tmp)["proposal:AUBG"]
            self.assertIn("ref", item)
            self.assertNotIn("evidence", item)

    def test_redetection_refreshes_volatile_evidence(self):
        """The snapshot exists because the source is overwritten — so a
        re-detection must carry the CURRENT source, not the stale copy."""
        with tempfile.TemporaryDirectory() as tmp:
            attention.sync(tmp, [blocked()], kinds=["blocked-publish"],
                           unis=["VUM"], now=T0)
            newer = {"kind": "blocked-publish", "uni_id": "VUM",
                     "evidence": {"run_id": "r2",
                                  "blocked_reasons": ["null spike"]}}
            attention.sync(tmp, [newer], kinds=["blocked-publish"],
                           unis=["VUM"], now=T1)
            item = attention.load_items(tmp)["blocked-publish:VUM"]
            self.assertEqual(item["evidence"]["run_id"], "r2")
            self.assertEqual(item["opened_at"], T0)


class ResolutionTest(unittest.TestCase):
    def test_resolution_appends_and_flips_the_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "resolutions.jsonl"
            attention.sync(tmp, [blocked()], kinds=["blocked-publish"],
                           unis=["VUM"], now=T0)
            attention.mark_resolved(
                tmp, "blocked-publish:VUM", action="promoted",
                reason="fee order restated; drop is real but correct",
                resolved_by="tester", resolutions_path=str(res), now=T1)
            item = attention.load_items(tmp)["blocked-publish:VUM"]
            self.assertEqual(item["status"], "resolved")
            lines = [json.loads(l) for l in
                     res.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["id"], "blocked-publish:VUM")
            self.assertEqual(lines[0]["action"], "promoted")
            self.assertEqual(lines[0]["resolved_by"], "tester")
            self.assertEqual(lines[0]["resolved_at"], T1)
            self.assertTrue(lines[0]["reason"])

    def test_resolving_an_unknown_item_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                attention.mark_resolved(
                    tmp, "blocked-publish:VUM", action="promoted",
                    reason="x", resolved_by="tester",
                    resolutions_path=str(Path(tmp) / "r.jsonl"), now=T1)

    def test_a_resolved_item_redetected_reopens(self):
        """The world re-broke after the human cleared it — that is new
        work with a new clock, not the old item aging on."""
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp) / "resolutions.jsonl"
            attention.sync(tmp, [blocked()], kinds=["blocked-publish"],
                           unis=["VUM"], now=T0)
            attention.mark_resolved(
                tmp, "blocked-publish:VUM", action="promoted", reason="x",
                resolved_by="tester", resolutions_path=str(res), now=T1)
            r = attention.sync(tmp, [blocked()], kinds=["blocked-publish"],
                               unis=["VUM"], now=T2)
            self.assertEqual(r["opened"], ["blocked-publish:VUM"])
            self.assertEqual(
                attention.load_items(tmp)["blocked-publish:VUM"]["opened_at"],
                T2)


class ProducerTest(unittest.TestCase):
    def test_blocked_publish_snapshots_the_report_evidence(self):
        pr = {"uni_id": "VUM", "run_id": "r9", "promoted": False,
              "blocked_reasons": ["coverage drop 12%"],
              "summary": {"fields": 75}}
        items = attention.detect_blocked_publish(pr)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "blocked-publish")
        self.assertEqual(items[0]["evidence"]["run_id"], "r9")
        self.assertEqual(items[0]["evidence"]["blocked_reasons"],
                         ["coverage drop 12%"])

    def test_a_promoted_publish_detects_nothing(self):
        pr = {"uni_id": "VUM", "run_id": "r9", "promoted": True,
              "blocked_reasons": []}
        self.assertEqual(attention.detect_blocked_publish(pr), [])

    def test_gate_failures_open_one_item_per_cell(self):
        rr = {"uni_id": "SHU", "gate_failures": [
            {"program_id": "shu-1", "field": "tuition",
             "status": "REJECT_SUPPORT"},
            {"program_id": "shu-2", "field": "degree",
             "status": "REJECT_CONTAINMENT"},
        ]}
        items = attention.detect_gate_failures(rr)
        self.assertEqual([i["subject"] for i in items],
                         ["shu-1.tuition", "shu-2.degree"])
        self.assertEqual(items[0]["evidence"]["status"], "REJECT_SUPPORT")

    def test_proposals_reference_their_durable_file(self):
        doc = {"uni_id": "TUG", "proposals": [{"proposed_url": "https://x"}]}
        items = attention.detect_proposals(doc, "crawler-out")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ref"],
                         "crawler-out/TUG/onboarding-proposal.json")
        self.assertNotIn("evidence", items[0])

    def test_an_empty_proposal_file_detects_nothing(self):
        doc = {"uni_id": "TUG", "proposals": []}
        self.assertEqual(attention.detect_proposals(doc, "crawler-out"), [])

    def test_check_rows_become_verdict_items(self):
        rows = [{"program_id": "vum-corr", "field": "tuition",
                 "category": "check"},
                {"program_id": "vum-mkt", "field": "degree",
                 "category": "ok_value"}]
        items = attention.detect_check_verdicts("VUM", rows)
        self.assertEqual([i["subject"] for i in items], ["vum-corr.tuition"])
        self.assertEqual(items[0]["ref"],
                         "benchmark/verdicts/VUM.json")

    def test_drift_entries_snapshot_what_the_listing_said(self):
        entries = [{"code": "894", "pinned_version": "3",
                    "current_version": "4", "status": "superseded",
                    "where": "programs[0].spravochnik", "url": "u",
                    "detail": "d"}]
        items = attention.detect_drift("SHU", entries)
        self.assertEqual(items[0]["subject"], "894")
        self.assertEqual(items[0]["evidence"]["current_version"], "4")


if __name__ == "__main__":
    unittest.main()
