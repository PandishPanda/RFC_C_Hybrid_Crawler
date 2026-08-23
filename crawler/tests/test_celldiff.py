"""Provenance-aware changed-cell diff between two run-reports — the
mechanism behind `crawler diff` and the attribution review
(docs/agents/attribution-review.md).

Four proofs:

1. SCOPE — a cell is changed when ANY of status, value, method, artifact
   ref or verbatim snippets moved; an identical report against itself
   reports nothing.
2. ATTRIBUTION-ONLY — the class this module exists for: the value is
   byte-identical but it now comes from somewhere else. Proven twice —
   that celldiff reports it, and that ledger.diff_runs (values only)
   does NOT, which is why a second diff had to exist.
3. STATUS — a cell that newly ships (REJECT_* -> PASS) or stops shipping
   (PASS -> NULL_OK) is labelled with both sides, never as a plain value
   change, and a non-shipping side never presents a value as current.
4. CLI — `crawler diff` exits 0 when nothing moved and 1 when the
   attribution review has cells to read; --json is machine-readable;
   --after defaults to <out>/<UniID>/run-report.json (the path every
   reviewer uses), and a missing report is named rather than raised.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import celldiff, ledger  # noqa: E402
from crawler.__main__ import main as cli_main  # noqa: E402

ARTIFACT = "html:https://example.test/programs"


def field_record(status="PASS", value="Bachelor", method="anchor:a",
                 artifact=ARTIFACT, snippets=None):
    rec = {"status": status, "value": value, "tier": "B", "method": method,
           "artifact": {"ref": artifact,
                        "renderer_id": "bs4-lxml-canonical:aggressive",
                        "renderer_version": "bs4-4.15.0/lxml-6.1.1"},
           "provenance": {
               "value": value,
               "source_url": "https://example.test/programs",
               "source_snippets": snippets or ["... {0} ...".format(value)],
               "retrieved_at": "2026-08-15T00:00:00Z",
               "method": method}}
    if status != "PASS":
        rec["value"] = None if status == "NULL_OK" else value
    return rec


def make_report(programs, uni_id="TestUni"):
    return {"uni_id": uni_id,
            "programs": [{"program_id": pid, "name": pid, "fields": fields}
                         for pid, fields in programs.items()],
            "gate_failures": [], "summary": {}}


BASE = make_report({"p-one": {"degree": field_record()},
                    "p-two": {"degree": field_record(value="Master")}})


def only_change(before, after):
    changes = celldiff.changed_cells(before, after)
    assert len(changes) == 1, changes
    return changes[0]


class ScopeTest(unittest.TestCase):
    def test_identical_reports_report_nothing(self):
        self.assertEqual(celldiff.changed_cells(BASE, BASE), [])

    def test_value_change_is_reported_as_value(self):
        after = make_report({"p-one": {"degree": field_record(value="Doctor")},
                             "p-two": {"degree": field_record(value="Master")}})
        change = only_change(BASE, after)
        self.assertEqual(change["kind"], "value")
        self.assertEqual(change["program_id"], "p-one")
        self.assertEqual(change["before"]["value"], "Bachelor")
        self.assertEqual(change["after"]["value"], "Doctor")

    def test_added_and_removed_cells(self):
        after = make_report({
            "p-one": {"degree": field_record(), "tuition": field_record(value="€500")},
        })
        kinds = {(c["program_id"], c["field"]): c["kind"]
                 for c in celldiff.changed_cells(BASE, after)}
        self.assertEqual(kinds[("p-one", "tuition")], "added")
        self.assertEqual(kinds[("p-two", "degree")], "removed")

    def test_ordering_is_deterministic(self):
        after = make_report({"p-two": {"degree": field_record(value="X")},
                             "p-one": {"degree": field_record(value="Y")}})
        keys = [(c["program_id"], c["field"])
                for c in celldiff.changed_cells(BASE, after)]
        self.assertEqual(keys, sorted(keys))


class AttributionOnlyTest(unittest.TestCase):
    """Same value, different provenance — right answer, wrong reason."""

    def _after(self, **kw):
        return make_report({"p-one": {"degree": field_record(**kw)},
                            "p-two": {"degree": field_record(value="Master")}})

    def test_method_moved(self):
        change = only_change(BASE, self._after(method="anchor:neighbour"))
        self.assertEqual(change["kind"], "attribution")
        self.assertEqual(change["before"]["value"], change["after"]["value"])

    def test_artifact_moved(self):
        change = only_change(BASE, self._after(artifact="html:https://example.test/other"))
        self.assertEqual(change["kind"], "attribution")

    def test_snippet_moved(self):
        change = only_change(BASE, self._after(snippets=["a different span saying Bachelor"]))
        self.assertEqual(change["kind"], "attribution")

    def test_ledger_diff_runs_cannot_see_it(self):
        """Why this module exists: the append-only ledger diffs values."""
        after = self._after(method="anchor:neighbour")
        with tempfile.TemporaryDirectory() as tmp:
            ledger.append_run(tmp, "TestUni", "run-a", BASE, "2026/2027")
            ledger.append_run(tmp, "TestUni", "run-b", after, "2026/2027")
            self.assertEqual(ledger.diff_runs(tmp, "TestUni", "run-a", "run-b"), [])
        self.assertEqual(len(celldiff.changed_cells(BASE, after)), 1)


class StatusTest(unittest.TestCase):
    def test_newly_shipping_cell_is_labelled_on_both_sides(self):
        before = make_report({"p-one": {"degree": field_record(
            status="REJECT_NOT_VERBATIM")}})
        after = make_report({"p-one": {"degree": field_record()}})
        change = only_change(before, after)
        self.assertEqual(change["kind"], "status")
        self.assertEqual(change["status_change"], "REJECT_NOT_VERBATIM->PASS")

    def test_a_cell_that_stops_shipping_carries_no_current_value(self):
        after = make_report({"p-one": {"degree": field_record(status="NULL_OK")},
                             "p-two": {"degree": field_record(value="Master")}})
        change = only_change(BASE, after)
        self.assertEqual(change["status_change"], "PASS->NULL_OK")
        self.assertIsNone(change["after"]["value"])

    def test_status_outranks_value_so_it_is_never_read_as_a_restatement(self):
        before = make_report({"p-one": {"degree": field_record(
            status="REJECT_NOT_VERBATIM", value="Bachelor")}})
        after = make_report({"p-one": {"degree": field_record(value="Doctor")}})
        self.assertEqual(only_change(before, after)["kind"], "status")


class CliTest(unittest.TestCase):
    def _run(self, before, after, *flags):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, report in (("before", before), ("after", after)):
                p = Path(tmp) / (name + ".json")
                p.write_text(json.dumps(report), encoding="utf-8")
                paths.append(str(p))
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli_main(["diff", "TestUni", "--before", paths[0],
                                 "--after", paths[1]] + list(flags))
            return code, buf.getvalue()

    def test_exit_zero_when_nothing_moved(self):
        code, out = self._run(BASE, BASE)
        self.assertEqual(code, 0)
        self.assertIn("0 changed", out)

    def test_exit_one_when_cells_need_review(self):
        after = make_report({"p-one": {"degree": field_record(method="anchor:other")},
                             "p-two": {"degree": field_record(value="Master")}})
        code, out = self._run(BASE, after)
        self.assertEqual(code, 1)
        self.assertIn("attribution", out)

    def test_snippets_flag_prints_the_verbatim_spans(self):
        after = make_report({"p-one": {"degree": field_record(
            snippets=["a wholly different span"])},
            "p-two": {"degree": field_record(value="Master")}})
        _, quiet = self._run(BASE, after)
        _, loud = self._run(BASE, after, "--snippets")
        self.assertNotIn("a wholly different span", quiet)
        self.assertIn("a wholly different span", loud)

    def test_after_defaults_to_the_run_report_under_out(self):
        """The doc's Step 1 omits --after; that path must actually work."""
        after = make_report({"p-one": {"degree": field_record(value="Doctor")},
                             "p-two": {"degree": field_record(value="Master")}})
        with tempfile.TemporaryDirectory() as tmp:
            before_path = Path(tmp) / "before.json"
            before_path.write_text(json.dumps(BASE), encoding="utf-8")
            out_root = Path(tmp) / "out"
            (out_root / "TestUni").mkdir(parents=True)
            (out_root / "TestUni" / "run-report.json").write_text(
                json.dumps(after), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli_main(["diff", "TestUni", "--before", str(before_path),
                                 "--out", str(out_root)])
        self.assertEqual(code, 1)
        self.assertIn("1 changed cell(s)", buf.getvalue())

    def test_a_missing_report_names_the_path_it_looked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            before_path = Path(tmp) / "before.json"
            before_path.write_text(json.dumps(BASE), encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = cli_main(["diff", "TestUni", "--before", str(before_path),
                                 "--out", str(Path(tmp) / "nope")])
        self.assertEqual(code, 2)
        self.assertIn("nope/TestUni/run-report.json", err.getvalue())
        self.assertIn("--after", err.getvalue())

    def test_json_output_is_machine_readable(self):
        after = make_report({"p-one": {"degree": field_record(value="Doctor")},
                             "p-two": {"degree": field_record(value="Master")}})
        _, out = self._run(BASE, after, "--json")
        payload = json.loads(out)
        self.assertEqual(payload["uni_id"], "TestUni")
        self.assertEqual(len(payload["changes"]), 1)
        self.assertEqual(payload["changes"][0]["kind"], "value")


if __name__ == "__main__":
    unittest.main()
