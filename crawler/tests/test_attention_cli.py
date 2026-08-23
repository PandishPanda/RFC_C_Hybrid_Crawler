"""CLI verbs `crawler attention` and `crawler resolve` (ADR-0005,
ticket 03). Offline: synthetic ledgers in temp dirs.

Four proofs:

1. LISTING — open items print with age and SLA state; --uni/--kind/--age
   filter; --json is machine-readable; resolved/lapsed items stay out
   unless --all.
2. RESOLVE EXECUTES — blocked-publish moves the ledger pointer (and
   refuses when the run is not in the ledger); check-verdict writes a
   real manual verdict through grader.write_manual_verdict. Neither
   merely records.
3. REASONS — promote-type actions refuse to run without --reason.
4. LAPSE-ONLY KINDS — gate-failure, drift and refresh-error have no
   manual resolve: the fix is config/world repair and the item lapses
   when no longer detected. The CLI says so instead of recording a
   judgment nothing performed.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import attention, grader, ledger  # noqa: E402
from crawler.__main__ import main as cli_main  # noqa: E402

T_OLD = "2026-07-01T00:00:00Z"
T_NEW = "2026-08-20T00:00:00Z"


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


class ListingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = self._tmp.name
        attention.sync(self.out, [
            {"kind": "blocked-publish", "uni_id": "VUM",
             "evidence": {"run_id": "r1", "blocked_reasons": ["drop"]}},
        ], kinds=["blocked-publish"], unis=["VUM"], now=T_OLD)
        attention.sync(self.out, [
            {"kind": "check-verdict", "uni_id": "SHU",
             "subject": "shu-1.tuition", "ref": "benchmark/verdicts/SHU.json"},
        ], kinds=["check-verdict"], unis=["SHU"], now=T_NEW)

    def tearDown(self):
        self._tmp.cleanup()

    def test_open_items_print_with_age_and_sla(self):
        code, out, _ = run_cli(["attention", "--out", self.out,
                                "--now", T_NEW])
        self.assertEqual(code, 0)
        self.assertIn("blocked-publish:VUM", out)
        self.assertIn("ESCALATE", out)          # 50 days old
        self.assertIn("check-verdict:SHU:shu-1.tuition", out)

    def test_filters(self):
        code, out, _ = run_cli(["attention", "--out", self.out,
                                "--uni", "SHU", "--now", T_NEW])
        self.assertNotIn("VUM", out)
        code, out, _ = run_cli(["attention", "--out", self.out,
                                "--kind", "blocked-publish", "--now", T_NEW])
        self.assertNotIn("SHU", out)
        code, out, _ = run_cli(["attention", "--out", self.out,
                                "--age", "10", "--now", T_NEW])
        self.assertIn("VUM", out)
        self.assertNotIn("SHU", out)

    def test_json_lists_items_with_sla_state(self):
        _, out, _ = run_cli(["attention", "--out", self.out, "--json",
                             "--now", T_NEW])
        payload = json.loads(out)
        by_id = {i["id"]: i for i in payload["items"]}
        self.assertEqual(by_id["blocked-publish:VUM"]["sla"], "escalate")
        self.assertEqual(by_id["check-verdict:SHU:shu-1.tuition"]["sla"],
                         "ok")

    def test_resolved_items_hidden_unless_all(self):
        attention.mark_resolved(
            self.out, "check-verdict:SHU:shu-1.tuition", action="ok",
            reason="judged", resolved_by="t",
            resolutions_path=str(Path(self.out) / "r.jsonl"), now=T_NEW)
        _, out, _ = run_cli(["attention", "--out", self.out, "--now", T_NEW])
        self.assertNotIn("shu-1.tuition", out)
        _, out, _ = run_cli(["attention", "--out", self.out, "--all",
                             "--now", T_NEW])
        self.assertIn("shu-1.tuition", out)


class ResolveBlockedPublishTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = self._tmp.name
        self.res = str(Path(self.out) / "resolutions.jsonl")
        # a blocked run whose values and summary ARE in the ledger
        report = {"uni_id": "TestUni", "programs": [
            {"program_id": "p1", "name": "p1", "fields": {
                "degree": {"status": "PASS", "value": "Бакалавър",
                           "tier": "G", "method": "label:x",
                           "provenance": {"value": "Бакалавър",
                                          "source_url": "https://x",
                                          "source_snippets": ["Бакалавър"],
                                          "retrieved_at": T_OLD,
                                          "method": "label:x"}}}}],
            "gate_failures": [], "summary": {}}
        ledger.append_run(self.out, "TestUni", "run-blocked", report,
                          "2026/2027")
        ledger.write_run_summary(self.out, "TestUni", "run-blocked",
                                 {"fields": 1})
        attention.sync(self.out, [
            {"kind": "blocked-publish", "uni_id": "TestUni",
             "evidence": {"run_id": "run-blocked",
                          "blocked_reasons": ["coverage drop"]}},
        ], kinds=["blocked-publish"], unis=["TestUni"], now=T_OLD)

    def tearDown(self):
        self._tmp.cleanup()

    def test_resolve_moves_the_pointer(self):
        code, out, _ = run_cli([
            "resolve", "blocked-publish:TestUni", "--out", self.out,
            "--reason", "drop is real: programme retired",
            "--resolutions-path", self.res, "--resolved-by", "tester"])
        self.assertEqual(code, 0)
        self.assertEqual(ledger.read_current(self.out, "TestUni"),
                         "run-blocked")
        item = attention.load_items(self.out)["blocked-publish:TestUni"]
        self.assertEqual(item["status"], "resolved")
        lines = Path(self.res).read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["action"], "promoted")

    def test_reason_is_required(self):
        code, out, err = run_cli([
            "resolve", "blocked-publish:TestUni", "--out", self.out,
            "--resolutions-path", self.res, "--resolved-by", "tester"])
        self.assertEqual(code, 2)
        self.assertIn("--reason", out + err)
        self.assertIsNone(ledger.read_current(self.out, "TestUni"))

    def test_a_run_missing_from_the_ledger_refuses(self):
        """Resolve executes a pointer write; pointing at a run the ledger
        does not hold would promote nothing and record success."""
        items = attention.load_items(self.out)
        items["blocked-publish:TestUni"]["evidence"]["run_id"] = "ghost"
        attention._write_items(self.out, items)
        code, out, err = run_cli([
            "resolve", "blocked-publish:TestUni", "--out", self.out,
            "--reason", "x", "--resolutions-path", self.res,
            "--resolved-by", "tester"])
        self.assertEqual(code, 2)
        self.assertIn("ghost", out + err)
        self.assertIsNone(ledger.read_current(self.out, "TestUni"))
        self.assertEqual(
            attention.load_items(self.out)["blocked-publish:TestUni"]
            ["status"], "open")


class ResolveCheckVerdictTest(unittest.TestCase):
    def test_resolve_writes_a_real_manual_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = str(Path(tmp) / "r.jsonl")
            vdir = str(Path(tmp) / "verdicts")
            attention.sync(tmp, [
                {"kind": "check-verdict", "uni_id": "TestUni",
                 "subject": "p1.tuition", "ref": "x"},
            ], kinds=["check-verdict"], unis=["TestUni"], now=T_OLD)
            code, out, _ = run_cli([
                "resolve", "check-verdict:TestUni:p1.tuition",
                "--out", tmp, "--verdict", "ok",
                "--note", "fee restated in EUR",
                "--shipped-value", "310 EUR",
                "--verdicts-dir", vdir,
                "--resolutions-path", res, "--resolved-by", "tester"])
            self.assertEqual(code, 0)
            verdicts = grader.read_manual_verdicts(vdir, "TestUni")
            v = verdicts[("p1", "tuition")]
            self.assertEqual(v["verdict"], "ok")
            self.assertEqual(v["shipped_value"], "310 EUR")
            self.assertEqual(
                attention.load_items(tmp)
                ["check-verdict:TestUni:p1.tuition"]["status"], "resolved")

    def test_verdict_flag_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            attention.sync(tmp, [
                {"kind": "check-verdict", "uni_id": "TestUni",
                 "subject": "p1.tuition", "ref": "x"},
            ], kinds=["check-verdict"], unis=["TestUni"], now=T_OLD)
            code, out, err = run_cli([
                "resolve", "check-verdict:TestUni:p1.tuition", "--out", tmp,
                "--resolutions-path", str(Path(tmp) / "r.jsonl"),
                "--resolved-by", "tester"])
            self.assertEqual(code, 2)
            self.assertIn("--verdict", out + err)


class LapseOnlyKindsTest(unittest.TestCase):
    def test_gate_failure_has_no_manual_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            attention.sync(tmp, [
                {"kind": "gate-failure", "uni_id": "TestUni",
                 "subject": "p1.degree", "evidence": {"status": "REJECT"}},
            ], kinds=["gate-failure"], unis=["TestUni"], now=T_OLD)
            code, out, err = run_cli([
                "resolve", "gate-failure:TestUni:p1.degree", "--out", tmp,
                "--reason", "fixed the anchor",
                "--resolutions-path", str(Path(tmp) / "r.jsonl"),
                "--resolved-by", "tester"])
            self.assertEqual(code, 2)
            self.assertIn("lapse", (out + err).lower())
            self.assertEqual(
                attention.load_items(tmp)["gate-failure:TestUni:p1.degree"]
                ["status"], "open")

    def test_unknown_item_names_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli([
                "resolve", "blocked-publish:Nowhere", "--out", tmp,
                "--reason", "x",
                "--resolutions-path", str(Path(tmp) / "r.jsonl"),
                "--resolved-by", "tester"])
            self.assertEqual(code, 2)
            self.assertIn("blocked-publish:Nowhere", out + err)


if __name__ == "__main__":
    unittest.main()
