"""The refresh orchestrator (ADR-0005, ticket 05): the unattended loop.
All offline — publish and drift are injected callables.

Four proofs:

1. FAILURE ISOLATION — one university's crash becomes a refresh-error
   item (traceback snapshotted) and the loop continues to the next; a
   later clean tick lapses the error.
2. EXIT CONTRACT — non-zero iff the tick opened new items or errored;
   a quiet tick over a clean fleet exits 0, so weekly cron + exit code
   is a complete v1 alerting story.
3. REPORT — crawler-out/refresh-report.json carries one policy verdict
   per university and the attention delta.
4. SIGNALS — publish blocks and gate failures surface through publish's
   own producers; drift entries open drift items; open check-verdict
   items (grade is a human act outside the tick) still warn the verdict.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import attention, refresh  # noqa: E402

T0 = "2026-08-01T00:00:00Z"
def tick(unis, tmp, publish_fn, **kw):
    kw.setdefault("drift_fn", lambda uni_id: [])
    return refresh.refresh(unis, out_dir=tmp, publish_fn=publish_fn, **kw)


def ok_publish(uni_id, **kw):
    return {"uni_id": uni_id, "run_id": "r1", "promoted": True,
            "blocked_reasons": [], "gate_failures": 0}


def blocked_publish(uni_id, **kw):
    # publish() itself emits the attention items; the injected stand-in
    # must do the same or the test exercises a publish that never existed
    out = kw["out_dir"]
    pr = {"uni_id": uni_id, "run_id": "r1", "promoted": False,
          "blocked_reasons": ["coverage drop"], "gate_failures": 0}
    attention.sync(out, attention.detect_blocked_publish(pr),
                   kinds=["blocked-publish", "gate-failure"], unis=[uni_id])
    return pr


def crash_publish(uni_id, **kw):
    raise RuntimeError("boom at " + uni_id)


class FailureIsolationTest(unittest.TestCase):
    def test_one_crash_does_not_stop_the_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def publish_fn(uni_id, **kw):
                calls.append(uni_id)
                if uni_id == "BadUni":
                    raise RuntimeError("boom")
                return ok_publish(uni_id, **kw)

            report = tick(["BadUni", "GoodUni"], tmp, publish_fn)
            self.assertEqual(calls, ["BadUni", "GoodUni"])
            by_uni = {u["uni_id"]: u for u in report["unis"]}
            self.assertEqual(by_uni["BadUni"]["verdict"], "block")
            self.assertEqual(by_uni["GoodUni"]["verdict"], "proceed")
            item = attention.load_items(tmp)["refresh-error:BadUni"]
            self.assertEqual(item["status"], "open")
            self.assertIn("boom", item["evidence"]["error"])

    def test_a_clean_tick_lapses_the_previous_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tick(["U"], tmp, crash_publish)
            report = tick(["U"], tmp, ok_publish)
            self.assertEqual(
                attention.load_items(tmp)["refresh-error:U"]["status"],
                "lapsed")
            self.assertEqual(report["attention"]["lapsed"],
                             ["refresh-error:U"])


class ExitContractTest(unittest.TestCase):
    def test_quiet_clean_tick_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = tick(["U"], tmp, ok_publish)
            self.assertEqual(refresh.exit_code(report), 0)

    def test_new_items_exit_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = tick(["U"], tmp, blocked_publish)
            self.assertEqual(refresh.exit_code(report), 1)

    def test_an_untouched_out_of_scope_item_is_not_reported(self):
        """A tick over one university must not report another's standing
        item as refreshed -- the delta is what THIS tick did."""
        with tempfile.TemporaryDirectory() as tmp:
            tick(["A"], tmp, blocked_publish)      # A's item opens
            tick(["A"], tmp, blocked_publish)      # A refreshed (older last_seen)
            report = tick(["B"], tmp, ok_publish)  # B-only tick
            self.assertEqual(report["attention"],
                             {"opened": [], "refreshed": [], "lapsed": []})

    def test_a_standing_old_item_does_not_retrigger(self):
        """Alerting is on NEW work: a backlog the operator already knows
        about must not page every week."""
        with tempfile.TemporaryDirectory() as tmp:
            tick(["U"], tmp, blocked_publish)
            report = tick(["U"], tmp, blocked_publish)
            self.assertEqual(report["attention"]["opened"], [])
            self.assertEqual(refresh.exit_code(report), 0)


class ReportTest(unittest.TestCase):
    def test_report_is_written_with_verdicts_and_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            tick(["A", "B"], tmp, blocked_publish)
            on_disk = json.loads(
                (Path(tmp) / "refresh-report.json").read_text(
                    encoding="utf-8"))
            self.assertEqual(len(on_disk["unis"]), 2)
            for u in on_disk["unis"]:
                self.assertEqual(u["verdict"], "block")
                self.assertIn("blocked-publish", u["needs_human"])
            self.assertEqual(sorted(on_disk["attention"]["opened"]),
                             ["blocked-publish:A", "blocked-publish:B"])
            self.assertIn("generated_at", on_disk)


class SignalTest(unittest.TestCase):
    def test_drift_entries_open_items_and_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            def drift_fn(uni_id):
                return [{"code": "894", "pinned_version": "3",
                         "current_version": "4", "status": "superseded",
                         "where": "w", "url": "u", "detail": "d"}]
            report = tick(["U"], tmp, ok_publish, drift_fn=drift_fn)
            self.assertEqual(report["unis"][0]["verdict"], "warn")
            self.assertIn("drift:U:894", attention.load_items(tmp))

    def test_open_check_verdicts_warn_but_are_not_lapsed_by_the_tick(self):
        """grade is a human-driven act outside the tick: refresh reads
        the pending CHECKs but must never lapse them (only a grade can
        say a CHECK stopped existing)."""
        with tempfile.TemporaryDirectory() as tmp:
            attention.sync(tmp, [
                {"kind": "check-verdict", "uni_id": "U",
                 "subject": "p.f", "ref": "x"},
            ], kinds=["check-verdict"], unis=["U"], now=T0)
            report = tick(["U"], tmp, ok_publish)
            self.assertEqual(report["unis"][0]["verdict"], "warn")
            self.assertIn("check-verdict", report["unis"][0]["needs_human"])
            self.assertEqual(
                attention.load_items(tmp)["check-verdict:U:p.f"]["status"],
                "open")

    def test_pending_proposals_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "U"
            pdir.mkdir(parents=True)
            (pdir / "onboarding-proposal.json").write_text(json.dumps(
                {"uni_id": "U", "proposals": [{"proposed_url": "https://x"}]}
            ), encoding="utf-8")
            report = tick(["U"], tmp, ok_publish)
            self.assertEqual(report["unis"][0]["verdict"], "warn")
            self.assertIn("proposal:U", attention.load_items(tmp))


if __name__ == "__main__":
    unittest.main()
