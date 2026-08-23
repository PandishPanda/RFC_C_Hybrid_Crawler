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


class QueryTest(unittest.TestCase):
    """attention.query — the backlog as a deep function (ADR-0005 review:
    the whole query used to live inside an argparse branch, and its SLA
    behaviour was asserted by scraping stdout)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = self._tmp.name
        attention.sync(self.out, [
            {"kind": "blocked-publish", "uni_id": "VUM",
             "evidence": {"run_id": "r1", "blocked_reasons": ["drop"]}},
        ], kinds=["blocked-publish"], unis=["VUM"], now=T0)
        attention.sync(self.out, [
            {"kind": "check-verdict", "uni_id": "SHU",
             "subject": "shu-1.tuition", "ref": "x"},
        ], kinds=["check-verdict"], unis=["SHU"], now=T2)

    def tearDown(self):
        self._tmp.cleanup()

    def test_items_come_back_aged_annotated_and_oldest_first(self):
        items = attention.query(self.out, now="2026-08-31T00:00:00Z")
        self.assertEqual([i["id"] for i in items],
                         ["blocked-publish:VUM",
                          "check-verdict:SHU:shu-1.tuition"])
        self.assertEqual(items[0]["age_days"], 30)
        self.assertEqual(items[0]["sla"], "escalate")
        self.assertEqual(items[1]["sla"], "warn")

    def test_filters(self):
        q = attention.query
        self.assertEqual([i["uni_id"] for i in
                          q(self.out, uni="SHU", now=T2)], ["SHU"])
        self.assertEqual([i["kind"] for i in
                          q(self.out, kind="blocked-publish", now=T2)],
                         ["blocked-publish"])
        self.assertEqual([i["id"] for i in
                          q(self.out, min_age=10, now=T2)],
                         ["blocked-publish:VUM"])

    def test_resolved_items_hidden_unless_show_all(self):
        attention.mark_resolved(
            self.out, "check-verdict:SHU:shu-1.tuition", action="ok",
            reason="judged", resolved_by="t",
            resolutions_path=str(Path(self.out) / "r.jsonl"), now=T2)
        self.assertNotIn("check-verdict:SHU:shu-1.tuition",
                         [i["id"] for i in attention.query(self.out,
                                                           now=T2)])
        self.assertIn("check-verdict:SHU:shu-1.tuition",
                      [i["id"] for i in attention.query(self.out,
                                                        show_all=True,
                                                        now=T2)])


class ResolveSeamTest(unittest.TestCase):
    """attention.resolve — ADR-0005's central rule ("resolve executes,
    never merely records") as a module invariant instead of an argparse
    branch. Every refusal is a typed ResolveRefused."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = self._tmp.name
        self.res = str(Path(self.out) / "resolutions.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def _blocked_item(self, run_id="run-1", in_ledger=True):
        from crawler import ledger
        if in_ledger:
            report = {"uni_id": "U", "programs": [
                {"program_id": "p1", "name": "p1", "fields": {
                    "degree": {"status": "PASS", "value": "x", "tier": "G",
                               "method": "label:x", "provenance": {
                                   "value": "x", "source_url": "u",
                                   "source_snippets": ["x"],
                                   "retrieved_at": T0,
                                   "method": "label:x"}}}}],
                "gate_failures": [], "summary": {}}
            ledger.append_run(self.out, "U", run_id, report, "2026/2027")
            ledger.write_run_summary(self.out, "U", run_id, {"fields": 1})
        attention.sync(self.out, [
            {"kind": "blocked-publish", "uni_id": "U",
             "evidence": {"run_id": run_id, "blocked_reasons": ["d"]}},
        ], kinds=["blocked-publish"], unis=["U"], now=T0)

    def test_promote_executes_the_pointer_write(self):
        from crawler import ledger
        self._blocked_item()
        r = attention.resolve(self.out, "blocked-publish:U",
                              reason="drop is real",
                              resolved_by="tester",
                              resolutions_path=self.res)
        self.assertEqual(r["action"], "promoted")
        self.assertEqual(ledger.read_current(self.out, "U"), "run-1")

    def test_promote_without_reason_is_refused(self):
        self._blocked_item()
        with self.assertRaises(attention.ResolveRefused):
            attention.resolve(self.out, "blocked-publish:U",
                              resolved_by="t", resolutions_path=self.res)

    def test_a_run_missing_from_the_ledger_is_refused(self):
        """The execute-check: pointing the pointer at a run the ledger
        does not hold would promote nothing and record success."""
        self._blocked_item(run_id="ghost", in_ledger=False)
        with self.assertRaises(attention.ResolveRefused) as ctx:
            attention.resolve(self.out, "blocked-publish:U", reason="x",
                              resolved_by="t", resolutions_path=self.res)
        self.assertIn("ghost", str(ctx.exception))
        self.assertEqual(
            attention.load_items(self.out)["blocked-publish:U"]["status"],
            "open")

    def test_check_verdict_executes_write_manual_verdict(self):
        from crawler import grader
        vdir = str(Path(self.out) / "verdicts")
        attention.sync(self.out, [
            {"kind": "check-verdict", "uni_id": "U",
             "subject": "p1.tuition", "ref": "x"},
        ], kinds=["check-verdict"], unis=["U"], now=T0)
        attention.resolve(self.out, "check-verdict:U:p1.tuition",
                          verdict="ok", note="n", shipped_value="v",
                          verdicts_dir=vdir, resolved_by="t",
                          resolutions_path=self.res)
        v = grader.read_manual_verdicts(vdir, "U")[("p1", "tuition")]
        self.assertEqual(v["verdict"], "ok")
        self.assertEqual(v["shipped_value"], "v")

    def test_check_verdict_without_verdict_is_refused(self):
        attention.sync(self.out, [
            {"kind": "check-verdict", "uni_id": "U",
             "subject": "p1.tuition", "ref": "x"},
        ], kinds=["check-verdict"], unis=["U"], now=T0)
        with self.assertRaises(attention.ResolveRefused):
            attention.resolve(self.out, "check-verdict:U:p1.tuition",
                              resolved_by="t", resolutions_path=self.res)

    def test_lapse_only_kinds_are_refused(self):
        for kind, subject in (("gate-failure", "p.f"), ("drift", "894"),
                              ("refresh-error", None)):
            attention.sync(self.out, [
                {"kind": kind, "uni_id": "U", "subject": subject,
                 "evidence": {"x": 1}},
            ], kinds=[kind], unis=["U"], now=T0)
            iid = attention.item_id(kind, "U", subject)
            with self.assertRaises(attention.ResolveRefused) as ctx:
                attention.resolve(self.out, iid, reason="fixed it",
                                  resolved_by="t",
                                  resolutions_path=self.res)
            self.assertIn("lapse", str(ctx.exception).lower())
            self.assertEqual(attention.load_items(self.out)[iid]["status"],
                             "open")

    def test_unknown_item_is_refused_by_name(self):
        with self.assertRaises(attention.ResolveRefused) as ctx:
            attention.resolve(self.out, "blocked-publish:Nowhere",
                              reason="x", resolved_by="t",
                              resolutions_path=self.res)
        self.assertIn("blocked-publish:Nowhere", str(ctx.exception))


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
