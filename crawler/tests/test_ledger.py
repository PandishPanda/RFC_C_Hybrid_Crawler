"""The value ledger, expectation checks, and publish orchestration
(ticket 03). All offline, synthetic reports shaped like runner.run()'s
output — no network, no LLM.

Four proofs:

1. LEDGER — append-only, keyed by (program, field, academic_year, run);
   a value carrying its own "YYYY/YYYY" token is keyed by THAT year, not
   the run's declared fallback.
2. POINTER / ROLLBACK — write_current moves the pointer; "rollback" is
   verified to be exactly write_current(previous_run_id) — no ledger
   data is touched.
3. EXPECTATION CHECKS — each of the four block conditions triggers
   independently (coverage drop, null-rate spike, falling row count,
   valid_for lag after 1 July); a clean run does not block; a first-ever
   run (no previous_summary) never blocks on regression checks.
4. PUBLISH — a blocked run still lands in the ledger and writes a
   publish-report, but does NOT move the pointer; a passing run does;
   a currency-only restatement is annotated, not counted as a real
   value change.
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import expectations, ledger, publish  # noqa: E402


def field_record(status, value=None, snippets=None, method="tier-g",
                 tier="G"):
    rec = {"status": status, "value": value}
    if status == "PASS":
        rec["tier"] = tier
        rec["method"] = method
        rec["provenance"] = {
            "value": value,
            "source_url": "https://example.test/page",
            "source_snippets": snippets or [value],
            "retrieved_at": "2026-08-15T00:00:00Z",
            "method": method,
        }
    return rec


def make_report(programs):
    """programs: {program_id: {field: field_record(...)}}"""
    return {
        "uni_id": "TestUni",
        "programs": [{"program_id": pid, "name": pid, "fields": fields}
                    for pid, fields in programs.items()],
        "gate_failures": [],
        "summary": {"tail_calls": 0, "tail_escalations": 0},
    }


FULL_COVERAGE = make_report({
    "p1": {"degree": field_record("PASS", "бакалавър"),
           "duration": field_record("PASS", "8"),
           "tuition": field_record("PASS", "460 EUR"),
           "admission": field_record("PASS", "изпит по математика"),
           "language": field_record("NULL_OK")},
    "p2": {"degree": field_record("PASS", "бакалавър"),
           "duration": field_record("PASS", "8"),
           "tuition": field_record("PASS", "460 EUR"),
           "admission": field_record("PASS", "изпит по математика"),
           "language": field_record("NULL_OK")},
})


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_append_and_load(self):
        ledger.append_run(self.dir, "TestUni", "run-1", FULL_COVERAGE, "2026/2027")
        values = ledger.load_run_values(self.dir, "TestUni", "run-1")
        self.assertEqual(values[("p1", "degree", "2026/2027", None)]["value"], "бакалавър")
        # NULL_OK fields never enter the value ledger (nothing to key on)
        self.assertNotIn(("p1", "language", "2026/2027", None), values)

    def test_value_states_its_own_year(self):
        report = make_report({
            "p1": {"tuition": field_record(
                "PASS", "460 EUR (уч. 2025/2026)",
                snippets=["460 EUR за учебната 2025/2026 г."])},
        })
        ledger.append_run(self.dir, "TestUni", "run-1", report, "2026/2027")
        values = ledger.load_run_values(self.dir, "TestUni", "run-1")
        self.assertIn(("p1", "tuition", "2025/2026", None), values)
        self.assertNotIn(("p1", "tuition", "2026/2027", None), values)

    def test_unrelated_year_token_ignored_for_non_year_varying_fields(self):
        # Regression: a curriculum-revision cohort label ("Випуск
        # 2021/2022") sitting in a DURATION field's segment was misread as
        # the value's own academic year and falsely blocked a first-ever
        # SofiaUniversity publish (2026-08-15 acceptance run). duration
        # doesn't vary by admission cycle — the segment's incidental year
        # token must never override the run's declared academic_year.
        report = make_report({
            "p1": {"duration": field_record(
                "PASS", "Продължителност на обучението (брой семестри): осем",
                snippets=["Форма на обучение: редовно Продължителност на "
                         "обучението (брой семестри): осем УЧЕБЕН ПЛАН "
                         "Випуск 2021/2022 и следващи"])},
        })
        ledger.append_run(self.dir, "TestUni", "run-1", report, "2026/2027")
        values = ledger.load_run_values(self.dir, "TestUni", "run-1")
        self.assertIn(("p1", "duration", "2026/2027", None), values)
        self.assertNotIn(("p1", "duration", "2021/2022", None), values)

    def test_unrelated_year_token_does_not_falsely_block_publish(self):
        stale_looking = make_report({
            "p1": {"duration": field_record(
                "PASS", "осем семестъра",
                snippets=["Випуск 2021/2022 и следващи, осем семестъра"])},
        })
        result = expectations.check(stale_looking, None,
                                    today=time.strptime("2026-08-15", "%Y-%m-%d"))
        self.assertFalse(result.blocked)

    def test_diff_added_changed_removed(self):
        ledger.append_run(self.dir, "TestUni", "run-1", FULL_COVERAGE, "2026/2027")
        changed = make_report({
            "p1": {"tuition": field_record("PASS", "500 EUR")},  # changed
            "p3": {"tuition": field_record("PASS", "300 EUR")},  # added
        })
        ledger.append_run(self.dir, "TestUni", "run-2", changed, "2026/2027")
        diff = ledger.diff_runs(self.dir, "TestUni", "run-1", "run-2")
        by_key = {d["key"]: d for d in diff}
        self.assertEqual(by_key[("p1", "tuition", "2026/2027", None)]["change"], "changed")
        self.assertEqual(by_key[("p3", "tuition", "2026/2027", None)]["change"], "added")
        self.assertEqual(by_key[("p1", "degree", "2026/2027", None)]["change"], "removed")

    def test_diff_against_no_previous_run_is_all_additions(self):
        ledger.append_run(self.dir, "TestUni", "run-1", FULL_COVERAGE, "2026/2027")
        diff = ledger.diff_runs(self.dir, "TestUni", None, "run-1")
        self.assertTrue(diff)
        self.assertTrue(all(d["change"] == "added" for d in diff))


class PointerRollbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_no_pointer_yet(self):
        self.assertIsNone(ledger.read_current(self.dir, "TestUni"))

    def test_write_and_read(self):
        ledger.write_current(self.dir, "TestUni", "run-1")
        self.assertEqual(ledger.read_current(self.dir, "TestUni"), "run-1")

    def test_rollback_is_exactly_a_repoint(self):
        ledger.append_run(self.dir, "TestUni", "run-1", FULL_COVERAGE, "2026/2027")
        ledger.write_current(self.dir, "TestUni", "run-1")
        bad = make_report({"p1": {"tuition": field_record("PASS", "999 EUR")}})
        ledger.append_run(self.dir, "TestUni", "run-2", bad, "2026/2027")
        ledger.write_current(self.dir, "TestUni", "run-2")
        self.assertEqual(ledger.read_current(self.dir, "TestUni"), "run-2")

        # rollback: repoint to run-1. Both runs' data must still be intact.
        ledger.write_current(self.dir, "TestUni", "run-1")
        self.assertEqual(ledger.read_current(self.dir, "TestUni"), "run-1")
        v1 = ledger.load_run_values(self.dir, "TestUni", "run-1")
        v2 = ledger.load_run_values(self.dir, "TestUni", "run-2")
        self.assertEqual(v1[("p1", "tuition", "2026/2027", None)]["value"], "460 EUR")
        self.assertEqual(v2[("p1", "tuition", "2026/2027", None)]["value"], "999 EUR")


class ExpectationChecksTest(unittest.TestCase):
    def test_first_run_never_blocks(self):
        result = expectations.check(FULL_COVERAGE, previous_summary=None)
        self.assertFalse(result.blocked)

    def test_clean_repeat_run_does_not_block(self):
        prev = expectations.summarize(FULL_COVERAGE)
        result = expectations.check(FULL_COVERAGE, prev)
        self.assertFalse(result.blocked)

    def test_coverage_drop_blocks(self):
        prev = expectations.summarize(FULL_COVERAGE)  # 2/2 covered
        dropped = make_report({
            "p1": {"degree": field_record("PASS", "бакалавър")},
            "p2": {"degree": field_record("NULL_OK")},  # p2 now uncovered
        })
        result = expectations.check(dropped, prev)
        self.assertTrue(result.blocked)
        self.assertTrue(any("coverage dropped" in r for r in result.reasons))

    def test_simulated_15_percent_coverage_drop_blocks(self):
        # 20 programs, all covered -> previous coverage 1.0
        base = make_report({
            "p{0}".format(i): {"degree": field_record("PASS", "бакалавър")}
            for i in range(20)
        })
        prev = expectations.summarize(base)
        # 15% drop: only 17/20 covered this run
        dropped_programs = {
            "p{0}".format(i): {"degree": field_record(
                "PASS" if i < 17 else "NULL_OK",
                "бакалавър" if i < 17 else None)}
            for i in range(20)
        }
        dropped = make_report(dropped_programs)
        result = expectations.check(dropped, prev)
        self.assertTrue(result.blocked)
        self.assertTrue(any("coverage dropped" in r for r in result.reasons))

    def test_null_rate_spike_on_key_fields_blocks(self):
        prev = expectations.summarize(FULL_COVERAGE)  # 0% null on tuition/admission
        spiked = make_report({
            "p1": {"tuition": field_record("NULL_OK"),
                  "admission": field_record("NULL_OK")},
            "p2": {"tuition": field_record("NULL_OK"),
                  "admission": field_record("NULL_OK")},
        })
        result = expectations.check(spiked, prev)
        self.assertTrue(result.blocked)
        self.assertTrue(any("null rate spiked" in r for r in result.reasons))

    def test_falling_row_count_blocks(self):
        # Now reported by WHICH programs vanished rather than by a bare
        # count -- a count can also fall because the set was deliberately
        # changed, and only a disappearance is a regression.
        prev = expectations.summarize(FULL_COVERAGE)  # 2 programs
        fewer = make_report({"p1": FULL_COVERAGE["programs"][0]["fields"]})
        result = expectations.check(fewer, prev)
        self.assertTrue(result.blocked)
        self.assertTrue(any("missing" in r for r in result.reasons))
        self.assertEqual(result.removed, ("p2",))

    def test_valid_for_lag_after_july_blocks(self):
        stale = make_report({
            "p1": {"tuition": field_record(
                "PASS", "460 EUR (2022/2023)",
                snippets=["460 EUR за учебната 2022/2023 г."])},
        })
        result = expectations.check(stale, None, today=time.strptime(
            "2026-08-01", "%Y-%m-%d"))
        self.assertTrue(result.blocked)
        self.assertTrue(any("lag the expected" in r for r in result.reasons))

    def test_valid_for_lag_not_checked_before_july(self):
        # same stale value, but "today" is in June -> current cycle is
        # still fresh, nothing to flag yet
        stale = make_report({
            "p1": {"tuition": field_record(
                "PASS", "460 EUR (2025/2026)",
                snippets=["460 EUR за учебната 2025/2026 г."])},
        })
        result = expectations.check(stale, None, today=time.strptime(
            "2026-06-01", "%Y-%m-%d"), academic_year="2025/2026")
        self.assertFalse(result.blocked)


class HistoricalYearIsNotTheValuesYearTest(unittest.TestCase):
    """«до уч. 2020/2021 г.» dates a subject's OLD NAME, not the value.

    Measured 2026-08-23: SU's 2026/2027 balloobrazuvane ordinance
    explains that «философия» was called «Философски цикъл» until
    2020/2021. Two admission values inherited that year and the year-lag
    gate blocked SU's publish on a document that is current."""

    def test_a_do_uch_year_is_skipped(self):
        seg = ("Публична администрация Редовна С коефициент 3: 1. ДЗИ по "
               "математика 12. ДЗИ по философия (до уч. 2020/2021 г. "
               "„Философски цикъл“) 13. ДЗИ по биология")
        self.assertEqual(
            ledger.infer_academic_year([seg], "изпит по математика",
                                       "2026/2027", field="admission"),
            "2026/2027")

    def test_the_long_form_do_uchebnata_is_skipped_too(self):
        seg = "нещо (до учебната 2020/2021 г. се казваше друго)"
        self.assertEqual(
            ledger.infer_academic_year([seg], "изпит", "2026/2027",
                                       field="admission"),
            "2026/2027")

    def test_a_real_stated_year_still_wins(self):
        # The whole point of the check: a value that states its own,
        # older cycle must still be caught as lagging.
        seg = "Такси за учебната 2025/2026 година: 500 лв."
        self.assertEqual(
            ledger.infer_academic_year([seg], "500 лв.", "2026/2027",
                                       field="tuition"),
            "2025/2026")

    def test_a_real_year_after_a_historical_one_still_wins(self):
        seg = ("до уч. 2020/2021 г. беше друго; таксите за 2025/2026 са "
               "определени с заповед")
        self.assertEqual(
            ledger.infer_academic_year([seg], "500 лв.", "2026/2027",
                                       field="tuition"),
            "2025/2026")


class CurrencyEquivalenceTest(unittest.TestCase):
    def test_bgn_eur_peg_restatement_is_equivalent(self):
        # 1200 BGN / 1.95583 = 613.55 EUR
        self.assertTrue(expectations.currency_equivalent(
            "1200 лв.", "613.55 EUR"))

    def test_genuinely_different_amount_is_not_equivalent(self):
        self.assertFalse(expectations.currency_equivalent(
            "1200 лв.", "900 EUR"))

    def test_same_currency_is_never_flagged_equivalent(self):
        self.assertFalse(expectations.currency_equivalent(
            "1200 лв.", "1300 лв."))


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_first_publish_promotes(self):
        result = publish.publish("TestUni", out_dir=self.dir,
                                 ledger_dir=self.dir, report=FULL_COVERAGE,
                                 academic_year="2026/2027")
        self.assertTrue(result["promoted"])
        self.assertEqual(ledger.read_current(self.dir, "TestUni"), result["run_id"])
        self.assertTrue((Path(self.dir) / "TestUni" / publish.PUBLISH_REPORT_NAME).exists())

    def test_blocked_publish_does_not_move_pointer_but_still_ledgers(self):
        publish.publish("TestUni", out_dir=self.dir, ledger_dir=self.dir,
                        report=FULL_COVERAGE, academic_year="2026/2027")
        first_pointer = ledger.read_current(self.dir, "TestUni")

        dropped = make_report({
            "p1": {"degree": field_record("PASS", "бакалавър")},
            "p2": {"degree": field_record("NULL_OK")},
        })
        result = publish.publish("TestUni", out_dir=self.dir, ledger_dir=self.dir,
                                 report=dropped, academic_year="2026/2027")
        self.assertFalse(result["promoted"])
        self.assertTrue(result["blocked_reasons"])
        # pointer unchanged
        self.assertEqual(ledger.read_current(self.dir, "TestUni"), first_pointer)
        # but the blocked run's values ARE in the ledger, inspectable
        blocked_values = ledger.load_run_values(self.dir, "TestUni", result["run_id"])
        self.assertTrue(blocked_values)

    def test_currency_only_restatement_not_counted_as_a_real_change(self):
        base = make_report({"p1": {"tuition": field_record("PASS", "1200 лв.")}})
        publish.publish("TestUni", out_dir=self.dir, ledger_dir=self.dir,
                        report=base, academic_year="2026/2027")
        restated = make_report({"p1": {"tuition": field_record("PASS", "613.55 EUR")}})
        result = publish.publish("TestUni", out_dir=self.dir, ledger_dir=self.dir,
                                 report=restated, academic_year="2026/2027")
        self.assertEqual(result["value_diff_summary"]["changed"], 0)
        self.assertEqual(result["value_diff_summary"]["currency_only"], 1)


if __name__ == "__main__":
    unittest.main()


class ProgramSetChangeTest(unittest.TestCase):
    """Growth must not read as regression. The delta checks compare the
    programs BOTH runs contain; a program with no history has nothing to
    regress against (2026-08-21 build-out: nearly every university
    tripped these checks purely by adding programs)."""

    def _covered(self, pid):
        return {pid: {"degree": field_record("PASS", "бакалавър"),
                      "tuition": field_record("PASS", "460 EUR"),
                      "admission": field_record("PASS", "изпит")}}

    def _bare(self, pid):
        return {pid: {"degree": field_record("NULL_OK"),
                      "tuition": field_record("NULL_OK"),
                      "admission": field_record("NULL_OK")}}

    def test_adding_unconfigured_programs_does_not_block(self):
        prev_report = make_report({**self._covered("p1"), **self._covered("p2")})
        prev = expectations.summarize(prev_report)
        grown = dict(self._covered("p1"))
        grown.update(self._covered("p2"))
        for i in range(3, 14):          # 11 new, entirely unconfigured
            grown.update(self._bare("p{0}".format(i)))
        result = expectations.check(make_report(grown), prev)
        self.assertFalse(result.blocked, result.reasons)
        self.assertEqual(result.compared_on, 2)
        self.assertEqual(len(result.added), 11)

    def test_a_real_regression_on_stable_programs_still_blocks(self):
        prev_report = make_report({**self._covered("p1"), **self._covered("p2")})
        prev = expectations.summarize(prev_report)
        # p1 and p2 both stop resolving, while new programs are added --
        # growth must not mask the regression on the programs we had.
        worse = dict(self._bare("p1"))
        worse.update(self._bare("p2"))
        for i in range(3, 10):
            worse.update(self._covered("p{0}".format(i)))
        result = expectations.check(make_report(worse), prev)
        self.assertTrue(result.blocked)
        self.assertTrue(any("coverage dropped" in r for r in result.reasons))
        self.assertTrue(any("present in both runs" in r for r in result.reasons))

    def test_a_disappearing_program_blocks(self):
        prev_report = make_report({**self._covered("p1"), **self._covered("p2")})
        prev = expectations.summarize(prev_report)
        result = expectations.check(make_report(self._covered("p1")), prev)
        self.assertTrue(result.blocked)
        self.assertTrue(any("missing" in r for r in result.reasons))
        self.assertEqual(result.removed, ("p2",))

    def test_summary_without_per_program_falls_back_not_skips(self):
        # A run summary written before per_program existed must still be
        # gated, not silently waved through.
        prev = expectations.summarize(
            make_report({**self._covered("p1"), **self._covered("p2")}))
        prev.pop("per_program")
        worse = dict(self._bare("p1"))
        worse.update(self._bare("p2"))
        result = expectations.check(make_report(worse), prev)
        self.assertTrue(result.blocked)
