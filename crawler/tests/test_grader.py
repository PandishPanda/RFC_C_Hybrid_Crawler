"""crawler.grader suite (ticket 07) -- zero network.

The oracle only: matching + classification against crawler.provenance's
own Status vocabulary. Never grades content -- that needs a real frozen
key nobody in this session can honestly author (see crawler/grader.py's
module docstring and .scratch/crawler-v2/issues/07-blind-benchmark-gate.md).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.grader import (  # noqa: E402
    GradeCategory,
    KeyEntry,
    grade_field,
    grade_report,
    load_frozen_key,
    read_manual_verdicts,
    tier_for,
    write_frozen_key,
    write_manual_verdict,
)


def record(status, value=None):
    return {"status": status, "value": value}


def key_entry(program_id, field, expected_value, **kw):
    return KeyEntry(program_id=program_id, field=field,
                    expected_value=expected_value, **kw)


class GradeFieldMatrixTest(unittest.TestCase):
    """Every (key expects null/value) x (shipped status) combination --
    the full state table grade_field must implement."""

    def test_key_null_status_null_ok_is_ok_null(self):
        entry = key_entry("p1", "language", None)
        self.assertEqual(grade_field(entry, record("NULL_OK")),
                         GradeCategory.OK_NULL)

    def test_key_null_status_pass_is_fabrication(self):
        entry = key_entry("p1", "language", None)
        self.assertEqual(
            grade_field(entry, record("PASS", "английски")),
            GradeCategory.FABRICATION)

    def test_key_null_status_reject_containment_is_rejected_vs_null_key(self):
        entry = key_entry("p1", "language", None)
        self.assertEqual(
            grade_field(entry, record("REJECT_CONTAINMENT")),
            GradeCategory.REJECTED_VS_NULL_KEY)

    def test_key_null_status_reject_support_is_rejected_vs_null_key(self):
        entry = key_entry("p1", "language", None)
        self.assertEqual(
            grade_field(entry, record("REJECT_SUPPORT")),
            GradeCategory.REJECTED_VS_NULL_KEY)

    def test_key_value_status_pass_matching_is_ok_value(self):
        entry = key_entry("p1", "degree", "бакалавър")
        self.assertEqual(
            grade_field(entry, record("PASS", "ОКС \"бакалавър\"")),
            GradeCategory.OK_VALUE)

    def test_key_value_status_pass_not_matching_is_check(self):
        entry = key_entry("p1", "duration", "8")
        self.assertEqual(
            grade_field(entry, record("PASS", "6 semesters")),
            GradeCategory.CHECK)

    def test_key_value_status_null_ok_is_miss(self):
        entry = key_entry("p1", "tuition", "460 EUR")
        self.assertEqual(grade_field(entry, record("NULL_OK")),
                         GradeCategory.MISS)

    def test_key_value_status_reject_containment_is_miss_gate(self):
        entry = key_entry("p1", "tuition", "460 EUR")
        self.assertEqual(
            grade_field(entry, record("REJECT_CONTAINMENT")),
            GradeCategory.MISS_GATE)

    def test_key_value_status_reject_support_is_miss_gate(self):
        entry = key_entry("p1", "tuition", "460 EUR")
        self.assertEqual(
            grade_field(entry, record("REJECT_SUPPORT")),
            GradeCategory.MISS_GATE)


class ValuesMatchIsNeverMoreLenientThanGateTest(unittest.TestCase):
    """Regression: _values_match used to short-circuit on whole-string
    containment (`expected in shipped`) BEFORE the number/word checks --
    an oracle more lenient than the crawler.provenance.gate() it grades
    can manufacture a passing wrong-but-gate-green rate the product
    itself would reject. Every one of these is a truthful-looking but
    WRONG value that must stay CHECK, never auto-grade OK_VALUE."""

    def test_wrong_number_that_is_a_substring_of_the_right_one_stays_check(self):
        entry = key_entry("p1", "tuition", "900 EUR")
        self.assertEqual(
            grade_field(entry, record("PASS", "1900 EUR")),
            GradeCategory.CHECK)

    def test_another_substring_number_case_stays_check(self):
        entry = key_entry("p1", "tuition", "60 EUR")
        self.assertEqual(
            grade_field(entry, record("PASS", "160 EUR")),
            GradeCategory.CHECK)

    def test_wrong_duration_number_stays_check(self):
        entry = key_entry("p1", "duration", "8 semesters")
        self.assertEqual(
            grade_field(entry, record("PASS", "18 semesters")),
            GradeCategory.CHECK)

    def test_short_word_matching_inside_an_unrelated_word_stays_check(self):
        # "an" (< 5 chars) must require an EXACT token match, never a
        # substring hit inside "urban" -- same policy gate() applies to
        # short words (no prefix leniency below 5 characters).
        entry = key_entry("p1", "degree", "an degree program")
        self.assertEqual(
            grade_field(entry, record(
                "PASS", "program for urban students degree here")),
            GradeCategory.CHECK)

    def test_genuinely_matching_value_still_passes(self):
        entry = key_entry("p1", "tuition", "1900 EUR")
        self.assertEqual(
            grade_field(entry, record("PASS", "1900 EUR annually")),
            GradeCategory.OK_VALUE)

    def test_tokenless_expected_value_never_vacuously_matches(self):
        # A placeholder key entry ("-", "?") has no numbers/currency/
        # words to check -- gate() treats a tokenless VALUE as
        # PARSE_FAILURE, never a vacuous PASS; _values_match must not
        # let a tokenless EXPECTED auto-match whatever shipped.
        entry = key_entry("p1", "admission", "-")
        self.assertEqual(
            grade_field(entry, record("PASS", "1900 EUR")),
            GradeCategory.CHECK)

    def test_every_currency_mention_must_independently_be_found(self):
        # gate() requires EVERY distinct currency token in the value to
        # be found, not just one of several -- a value naming both
        # "евро" and "€" must not pass when the shipped text only has one.
        entry = key_entry("p1", "tuition", "100 евро / 100 €")
        self.assertEqual(
            grade_field(entry, record("PASS", "100 евро available")),
            GradeCategory.CHECK)


class ParseFailureNeverGradesAsNullTest(unittest.TestCase):
    """Named defect #2 (ticket 07 / ADR-0002 consequences): the spike
    graders collapsed every non-PASS verdict into a single `not passed`
    boolean, so a PARSE_FAILURE record (malformed, uncheckable) graded as
    an "ok_null" whenever the key happened to expect null -- exactly the
    outcome ADR-0002 says must never happen again. PARSE_FAILURE must be
    its own category regardless of what the key expects."""

    def test_parse_failure_vs_null_key_is_not_ok_null(self):
        entry = key_entry("p1", "language", None)
        category = grade_field(entry, record("PARSE_FAILURE"))
        self.assertEqual(category, GradeCategory.PARSE_FAILURE)
        self.assertNotEqual(category, GradeCategory.OK_NULL)

    def test_parse_failure_vs_value_key_is_not_miss(self):
        entry = key_entry("p1", "tuition", "460 EUR")
        category = grade_field(entry, record("PARSE_FAILURE"))
        self.assertEqual(category, GradeCategory.PARSE_FAILURE)
        self.assertNotEqual(category, GradeCategory.MISS)


class LoadFrozenKeyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "key.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trips(self):
        entries = [
            key_entry("p1", "degree", "бакалавър", snippet="s",
                      source_url="https://x.example/p1"),
            key_entry("p1", "language", None, null_reason="not stated"),
        ]
        write_frozen_key(self.path, entries)
        loaded = load_frozen_key(self.path)
        self.assertEqual(loaded[("p1", "degree")].expected_value, "бакалавър")
        self.assertIsNone(loaded[("p1", "language")].expected_value)
        self.assertEqual(loaded[("p1", "language")].null_reason, "not stated")

    def test_duplicate_program_field_pair_raises(self):
        entries = [key_entry("p1", "degree", "a"),
                  key_entry("p1", "degree", "b")]
        write_frozen_key_raw = json.dumps({"entries": [
            dict(program_id=e.program_id, field=e.field,
                expected_value=e.expected_value) for e in entries]})
        self.path.write_text(write_frozen_key_raw, encoding="utf-8")
        with self.assertRaises(ValueError):
            load_frozen_key(self.path)


class GradeReportMatchesByProgramIdNotPositionTest(unittest.TestCase):
    """Named defect #1 (ticket 07): spike C's grader paired the frozen
    key to extraction output by zip(key_order, extraction_order) -- two
    independently-maintained parallel lists. If they ever drift (a
    program reordered, inserted, or removed on either side) every
    following program grades against the wrong one, silently. The real
    grader must match by the explicit program_id both the key and the
    run-report already carry -- order must be provably irrelevant."""

    def test_shuffled_report_order_still_grades_correctly(self):
        key = {
            ("p1", "degree"): key_entry("p1", "degree", "bachelor"),
            ("p2", "degree"): key_entry("p2", "degree", "master"),
        }
        # p2 listed BEFORE p1 in the report -- a position/zip-based
        # grader would pair key-order p1 against report-order p2 and
        # vice versa, cross-grading both programs wrong.
        run_report = {"programs": [
            {"program_id": "p2", "fields": {"degree": record("PASS", "master")}},
            {"program_id": "p1", "fields": {"degree": record("PASS", "bachelor")}},
        ]}
        report = grade_report(key, run_report)
        by_id = {(r.program_id, r.field): r.category for r in report.rows}
        self.assertEqual(by_id[("p1", "degree")], GradeCategory.OK_VALUE)
        self.assertEqual(by_id[("p2", "degree")], GradeCategory.OK_VALUE)

    def test_a_reordered_key_dict_still_grades_correctly(self):
        # Same content, key built in the OPPOSITE insertion order --
        # dict order must never be load-bearing either.
        key = {
            ("p2", "degree"): key_entry("p2", "degree", "master"),
            ("p1", "degree"): key_entry("p1", "degree", "bachelor"),
        }
        run_report = {"programs": [
            {"program_id": "p1", "fields": {"degree": record("PASS", "bachelor")}},
            {"program_id": "p2", "fields": {"degree": record("PASS", "master")}},
        ]}
        report = grade_report(key, run_report)
        by_id = {(r.program_id, r.field): r.category for r in report.rows}
        self.assertEqual(by_id[("p1", "degree")], GradeCategory.OK_VALUE)
        self.assertEqual(by_id[("p2", "degree")], GradeCategory.OK_VALUE)


class GradeReportCompletenessTest(unittest.TestCase):
    def test_report_field_with_no_key_entry_raises(self):
        key = {("p1", "degree"): key_entry("p1", "degree", "bachelor")}
        run_report = {"programs": [
            {"program_id": "p1", "fields": {
                "degree": record("PASS", "bachelor"),
                "language": record("NULL_OK")}},
        ]}
        with self.assertRaises(KeyError):
            grade_report(key, run_report)

    def test_key_entry_with_no_matching_report_field_raises(self):
        key = {("p1", "degree"): key_entry("p1", "degree", "bachelor"),
              ("p1", "language"): key_entry("p1", "language", None)}
        run_report = {"programs": [
            {"program_id": "p1", "fields": {"degree": record("PASS", "bachelor")}},
        ]}
        with self.assertRaises(KeyError):
            grade_report(key, run_report)


class ManualVerdictOverlayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_unresolved_check_stays_check(self):
        key = {("p1", "duration"): key_entry("p1", "duration", "8")}
        run_report = {"programs": [
            {"program_id": "p1", "fields": {
                "duration": record("PASS", "6 semesters")}},
        ]}
        report = grade_report(key, run_report)
        self.assertEqual(report.rows[0].category, GradeCategory.CHECK)
        self.assertIsNone(report.wrong_rate,
                          "wrong_rate must be undetermined with an "
                          "unresolved CHECK outstanding")

    def test_manual_ok_resolves_to_ok_manual(self):
        write_manual_verdict(self.out, "X", "p1", "duration", "ok",
                             note="8 = 2 semesters/year x 4 years")
        key = {("p1", "duration"): key_entry("p1", "duration", "8")}
        run_report = {"programs": [
            {"program_id": "p1", "fields": {
                "duration": record("PASS", "4 years")}},
        ]}
        manual = read_manual_verdicts(self.out, "X")
        report = grade_report(key, run_report, manual_verdicts=manual)
        self.assertEqual(report.rows[0].category, GradeCategory.OK_MANUAL)

    def test_manual_wrong_resolves_to_wrong_and_counts_toward_wrong_rate(self):
        write_manual_verdict(self.out, "X", "p1", "duration", "wrong",
                             note="different program's duration")
        key = {("p1", "duration"): key_entry("p1", "duration", "8")}
        run_report = {"programs": [
            {"program_id": "p1", "fields": {
                "duration": record("PASS", "6 semesters")}},
        ]}
        manual = read_manual_verdicts(self.out, "X")
        report = grade_report(key, run_report, manual_verdicts=manual)
        self.assertEqual(report.rows[0].category, GradeCategory.WRONG)
        self.assertEqual(report.wrong_rate, 1.0)

    def test_verdict_bound_to_a_shipped_value_ignores_a_changed_value(self):
        # The labeller's verdict adjudicates ONE shipped value. If a later
        # run ships something else, reusing the verdict would grade a value
        # no human ever looked at (measured: mu-nurse tuition was ruled
        # "wrong" against «безплатно», then the fee-order join shipped
        # «410 евро» and inherited the stale "wrong").
        write_manual_verdict(self.out, "X", "p1", "tuition", "wrong",
                             note="fee order governs",
                             shipped_value="безплатно")
        key = {("p1", "tuition"): key_entry("p1", "tuition",
                                            "410 € / семестър")}
        run_report = {"programs": [
            {"program_id": "p1", "fields": {
                "tuition": record("PASS", "410 евро")}},
        ]}
        manual = read_manual_verdicts(self.out, "X")
        report = grade_report(key, run_report, manual_verdicts=manual)
        self.assertEqual(report.rows[0].category, GradeCategory.CHECK,
                         "a changed shipped value must return to CHECK, "
                         "not inherit a verdict on the old value")

    def test_verdict_bound_to_the_current_shipped_value_still_applies(self):
        write_manual_verdict(self.out, "X", "p1", "tuition", "ok",
                             shipped_value="410 евро")
        key = {("p1", "tuition"): key_entry("p1", "tuition",
                                            "410 € / семестър")}
        run_report = {"programs": [
            {"program_id": "p1", "fields": {
                "tuition": record("PASS", "410 евро")}},
        ]}
        manual = read_manual_verdicts(self.out, "X")
        report = grade_report(key, run_report, manual_verdicts=manual)
        self.assertEqual(report.rows[0].category, GradeCategory.OK_MANUAL)

    def test_legacy_verdict_without_shipped_value_applies_unconditionally(self):
        # Pre-existing verdict files carry no shipped_value; they keep
        # working exactly as before (byte-identical carry-over).
        write_manual_verdict(self.out, "X", "p1", "duration", "ok")
        key = {("p1", "duration"): key_entry("p1", "duration", "8")}
        run_report = {"programs": [
            {"program_id": "p1", "fields": {
                "duration": record("PASS", "4 years")}},
        ]}
        manual = read_manual_verdicts(self.out, "X")
        report = grade_report(key, run_report, manual_verdicts=manual)
        self.assertEqual(report.rows[0].category, GradeCategory.OK_MANUAL)

    def test_invalid_verdict_string_is_rejected(self):
        with self.assertRaises(ValueError):
            write_manual_verdict(self.out, "X", "p1", "duration", "maybe")


class GradeReportMetricsTest(unittest.TestCase):
    def test_fabrication_rate_and_gate_thresholds(self):
        key = {
            ("p1", "language"): key_entry("p1", "language", None),
            ("p1", "degree"): key_entry("p1", "degree", "bachelor"),
        }
        run_report = {"programs": [
            {"program_id": "p1", "fields": {
                "language": record("PASS", "english"),  # FABRICATION
                "degree": record("PASS", "bachelor")}},  # OK_VALUE
        ]}
        report = grade_report(key, run_report)
        self.assertEqual(report.fabrication_count, 1)
        self.assertEqual(report.fabrication_rate, 0.5)
        self.assertFalse(report.gate_pass,
                         "fabrications must never gate-pass, regardless "
                         "of the wrong-rate threshold")

    def test_gate_pass_true_under_thresholds_with_no_fabrication(self):
        key = {("p1", "degree"): key_entry("p1", "degree", "bachelor")}
        run_report = {"programs": [
            {"program_id": "p1", "fields": {
                "degree": record("PASS", "bachelor")}},
        ]}
        report = grade_report(key, run_report)
        self.assertEqual(report.fabrication_count, 0)
        self.assertEqual(report.wrong_rate, 0.0)
        self.assertTrue(report.gate_pass)

    def test_gate_pass_is_none_pending_unresolved_checks(self):
        key = {("p1", "degree"): key_entry("p1", "degree", "bachelor")}
        run_report = {"programs": [
            {"program_id": "p1", "fields": {
                "degree": record("PASS", "master")}},  # CHECK, unresolved
        ]}
        report = grade_report(key, run_report)
        self.assertIsNone(report.gate_pass)


class KeyDraftTierTest(unittest.TestCase):
    """tier_for ports .scratch/sta-78/phase0/check-key.py's HARD/SOFT/OK
    mechanical pre-check so a future Phase-0 labeling pass doesn't need
    to hand-copy the spike script."""

    def test_verbatim_containment_is_ok(self):
        tier, _ = tier_for("8", "Продължителност: 8 семестъра")
        self.assertEqual(tier, "OK")

    def test_missing_number_is_hard(self):
        tier, why = tier_for("460 EUR", "Таксата е освободени")
        self.assertEqual(tier, "HARD")
        self.assertIn("460", why)

    def test_composed_value_with_all_tokens_present_is_soft(self):
        # Value's tokens (460, EUR, годишна, такса) are all present in the
        # snippet, but not as one contiguous substring -- reordered by
        # the snippet's own sentence structure.
        tier, _ = tier_for(
            "460 EUR годишна такса",
            "Годишна такса за обучение 460 EUR, дължима до 30 октомври")
        self.assertEqual(tier, "SOFT")

    def test_empty_snippet_is_hard(self):
        tier, why = tier_for("8", "")
        self.assertEqual(tier, "HARD")


if __name__ == "__main__":
    unittest.main()


class VerdictsNeverWriteIntoTheRepoTest(unittest.TestCase):
    """A hardcoded verdicts path once put a unit test's fixture
    university (uni_id 'X') into a tracked commit. Writes must honour the
    caller's root."""

    def test_write_honours_the_given_root(self):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as td:
            write_manual_verdict(td, "X", "p1", "duration", "ok")
            self.assertTrue((P(td) / "X.json").exists())
            self.assertFalse(P("benchmark/verdicts/X.json").exists())

    def test_default_root_is_the_tracked_tree(self):
        from crawler import grader as g
        self.assertEqual(str(g.verdicts_dir()), "benchmark/verdicts")
