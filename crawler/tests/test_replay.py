"""Acceptance replay — the benchmark four, entirely offline (issue 01).

Runs ``crawler.runner.run`` for AUBG / MUPleven / SofiaUniversity / VUM
against spike A's cache (``--replay`` path: snapshots from
.scratch/sta-78/spikes/a/cache, PDF renderings from the sibling out/
pdftext + docling dirs — Docling and poppler are never invoked) and grades
the run reports with spike A's own auto-grader semantics.

GRADING DATA: the frozen answer key and the auto_grade comparison below
are ported verbatim from .scratch/sta-78/spikes/a/keys.py (itself encoding
the frozen answer-key.md). Graders may hold the key; extractors may not —
nothing in crawler/ outside this test file knows any expected value, and
the runner under test only ever sees config + cache.

Acceptance (spike A's audited numbers, RFC v2 §5):
  - 99/100 auto-graded correct (87 strict + 12 lenient); the single
    non-correct is mu-med language — auto_grade's known WRONG artifact,
    adjudicated semantically correct in the spike's e3-adjudication.md
  - 0 fabrications (every key-null field ships null)
  - 0 misses, 0 gate failures
  - tier split of shipped values G:55 / F:27 / B:12 (anchor tier from
    bespoke-anchor config; the LLM tail is ticket 02 and absent)
  - every non-null value carries the full Provenance quintuple; every
    key-null field is an explicit NULL_OK in the report

The replay must be ZERO network: requests.Session.request is stubbed to
fail the run if anything on the path so much as tries.
"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests  # noqa: E402

from crawler import runner  # noqa: E402
from crawler.provenance import Status  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SPIKE_DIR = REPO_ROOT / ".scratch" / "sta-78" / "spikes" / "a"
SPIKE_CACHE = SPIKE_DIR / "cache"

BENCHMARK_UNIS = ("AUBG", "MUPleven", "SofiaUniversity", "VUM")

# ---------------------------------------------------------------- frozen key
# Ported verbatim from spikes/a/keys.py (frozen answer-key.md encoded).
# None = the key says the field is not published; extracting any value for
# it is a FABRICATION.
KEY = {
    "su-kn":   dict(degree='ОКС "бакалавър"', duration='Продължителност на обучението (брой семестри): 8',
                    language=None, tuition='460 EUR', admission='изпит по математика'),
    "su-si":   dict(degree='ОКС "бакалавър"', duration='Продължителност на обучението (брой семестри): 8',
                    language=None, tuition='460 EUR', admission='изпит по математика'),
    "su-is":   dict(degree='ОКС "бакалавър"', duration='Продължителност на обучението (брой семестри): 8',
                    language=None, tuition='460 EUR', admission='изпит по математика'),
    "su-stat": dict(degree='ОКС "бакалавър"', duration='Продължителност на обучението (брой семестри): 8',
                    language=None, tuition='освободени', admission='изпит по математика'),
    "su-ch":   dict(degree='ОКС „бакалавър”', duration='Продължителност на обучението (брой семестри): осем',
                    language=None, tuition='освободени',
                    admission='конкурсен изпит по химия, математика, физика или биология'),
    "su-ih":   dict(degree='БАКАЛАВЪР ПО ИНЖЕНЕРНА ХИМИЯ И СЪВРЕМЕННИ МАТЕРИАЛИ',
                    duration='Продължителност на обучението (брой семестри): осем',
                    language='с частично обучение на английски език', tuition='освободени',
                    admission='успешно преминал приемен изпит или с оценка от държавен зрелостен изпит по Химия и опазване на околната среда, Физика или Математика'),
    "su-psy":  dict(degree='Образователно-квалификационна степен "бакалавър след средно образование"',
                    duration='Срок на обучение: 8 семестъра (4 учебни години)', language=None,
                    tuition='310 EUR', admission='изпит по английски език'),
    "su-phil": dict(degree='Образователно-квалификационна степен "бакалавър след средно образование"',
                    duration='Срок на обучение в редовна форма: 8 семестъра (4 учебни години)',
                    language='на английски език', tuition='310 EUR',
                    admission='успешно издържан кандидатстудентски изпит по английски език'),
    "mu-med":  dict(degree='образователно-квалификационна степен “магистър”, професионална квалификация “лекар”',
                    duration='6-годишен период на обучение (10 семестъра и 1 година преддипломен стаж)',
                    language='обучение на български език; also offered as Обучение на английски език (English-language track)',
                    tuition='620 евро', admission='изпит по биология и по химия'),
    "mu-pharm": dict(degree='Образователно - квалификационна степен – „магистър“ (магистър-фармацевт)',
                    duration='5-годишен период на обучение (9 семестъра и 1 семестър преддипломен стаж)',
                    language='обучение на български език', tuition='620 евро',
                    admission='изпит по биология и по химия'),
    "mu-nurse": dict(degree='ОКС „Бакалавър”', duration='4-годишен период на обучение',
                    language='обучение на български език', tuition='безплатно',
                    admission="приемен изпит-тест по биология (or DZI biology grade, at the candidate's choice)"),
    "mu-ap":   dict(degree='ОКС „професионален бакалавър”, професионална квалификация „Помощник-фармацевт“',
                    duration='срок на обучение 3 години', language='обучение на български език',
                    tuition='410 евро', admission='изпит по биология или оценка от втори ДЗИ'),
    "aubg-cs": dict(degree='Bachelor of Arts (dual-diploma major: American and Bulgarian diploma)',
                    duration='eight semesters', language='English', tuition='€6,900/semester',
                    admission='Recommended minimum high-school GPA of 3.0 on a 4.0 scale (4.5 out of 6 on the Bulgarian scale)'),
    "aubg-ba": dict(degree='Bachelor of Arts (dual-diploma major: American and Bulgarian diploma)',
                    duration='eight semesters', language='English', tuition='€6,900/semester',
                    admission='Recommended minimum high-school GPA of 3.0 on a 4.0 scale (4.5 out of 6 on the Bulgarian scale)'),
    "aubg-econ": dict(degree='Bachelor of Arts (dual-diploma major: American and Bulgarian diploma)',
                    duration='eight semesters', language='English', tuition='€6,900/semester',
                    admission='Recommended minimum high-school GPA of 3.0 on a 4.0 scale (4.5 out of 6 on the Bulgarian scale)'),
    "aubg-emba": dict(degree='Executive MBA (accredited in the United States by NECHE and in Bulgaria by NEAA)',
                    duration='sixteen-month (four-term)', language='English', tuition='€19,100',
                    admission="A minimum of a bachelor's degree from an accredited institution of higher education"),
    "vum-sst": dict(degree='Professional Bachelor degree from VUM and the British BSc (Hons) Software Engineering awarded by Cardiff Metropolitan University',
                    duration='3 years / 6 semesters', language='English',
                    tuition='1500 € per semester for EU and EEA citizens',
                    admission='State Matriculation Examination (DZI) in English language, provided that the exam corresponds to CEFR level B2 and the final grade is at least Very Good (5.00)'),
    "vum-gca": dict(degree='Professional Bachelor degree from VUM and the British BA (Hons) Culinary Arts Management awarded by Cardiff Metropolitan University',
                    duration='3 years / 6 semesters', language='English',
                    tuition='1500 € per semester for EU and EEA citizens',
                    admission='State Matriculation Examination (DZI) in English language, provided that the exam corresponds to CEFR level B2 and the final grade is at least Very Good (5.00)'),
    "vum-mba": dict(degree="VUM Master's degree in Business Administration and the Master of Business Administration awarded by Cardiff Metropolitan University",
                    duration='3 semesters or 4 semesters', language='English',
                    tuition='1500 € per semester for EU and EEA citizens',
                    admission='English language proficiency equivalent to IELTS 6.5 or above'),
    "vum-corr": dict(degree='MASTER', duration='3 – 4 semesters', language='BULGARIAN',
                    tuition='1100 leva',
                    admission="candidates holding a Bachelor's or Master's degree from any field of study"),
}

FIELDS = ["degree", "duration", "language", "tuition", "admission"]


def _n(s):
    s = re.sub(r"[„“”«»\"']", '"', s or "")
    s = re.sub(r"[’‘]", "'", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _nums(s):
    return [t.replace(",", "").replace(" ", "")
            for t in re.findall(r"\d[\d ,.]*\d|\d", s or "")]


def auto_grade(key_val, got_val):
    """CORRECT / CORRECT-lenient / WRONG / FABRICATION / MISS, per the
    key's rule: semantic equality, numbers and currency exact. Ported
    verbatim from spikes/a/keys.py."""
    if key_val is None:
        return "CORRECT" if got_val is None else "FABRICATION"
    if got_val is None:
        return "MISS"
    k, g = _n(key_val), _n(got_val)
    if _nums(key_val) and sorted(_nums(key_val)) != sorted(
            set(_nums(got_val)) & set(_nums(key_val)) or _nums(got_val)):
        # every number in the key must appear in the extraction
        if not set(_nums(key_val)) <= set(_nums(got_val)):
            return "WRONG"
    for cur in ["eur", "€", "евро", "лв", "leva", "bgn"]:
        if cur in k and not any(
                c in g for c in (["eur", "€", "евро"]
                                 if cur in ("eur", "€", "евро") else [cur])):
            return "WRONG"
    if k == g or k in g or g in k:
        return "CORRECT" if k == g or g in k else "CORRECT-lenient"
    kt = set(re.findall(r"[\wЀ-ӿ]{3,}", k))
    gt = set(re.findall(r"[\wЀ-ӿ]{3,}", g))
    if kt and len(kt & gt) / len(kt) >= 0.6:
        return "CORRECT-lenient"
    return "WRONG"


def _deny_network(*args, **kwargs):
    raise AssertionError(
        "network request attempted during the offline benchmark replay")


@unittest.skipUnless(SPIKE_CACHE.is_dir(),
                     "spike A cache not present at " + str(SPIKE_CACHE))
@unittest.skipUnless(
    SPIKE_DIR.exists(),
    "spike-A cache not present (gathered data, not shipped in a fresh "
    "clone) -- the frozen acceptance replay needs .scratch/sta-78/spikes/a")
class TestBenchmarkReplay(unittest.TestCase):
    """python3 -m crawler run <UniID> --replay <spike cache>, all four."""

    @classmethod
    def setUpClass(cls):
        cls.out_dir = tempfile.mkdtemp(prefix="crawler-replay-")
        original = requests.Session.request
        requests.Session.request = _deny_network
        try:
            cls.reports = {
                uni: runner.run(uni, out_dir=cls.out_dir,
                                configs_dir=str(Path(__file__).parent /
                                                "fixtures_benchmark_configs"),
                                replay_dir=str(SPIKE_CACHE))
                for uni in BENCHMARK_UNIS
            }
        finally:
            requests.Session.request = original

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out_dir, ignore_errors=True)

    def field_records(self):
        """(program_id, field, record) across all four run reports."""
        for uni in BENCHMARK_UNIS:
            for program in self.reports[uni]["programs"]:
                for field in FIELDS:
                    yield program["program_id"], field, \
                        program["fields"][field]

    def grades(self):
        counts = {}
        detail = {}
        for pid, field, record in self.field_records():
            grade = auto_grade(KEY[pid][field], record.get("value"))
            counts[grade] = counts.get(grade, 0) + 1
            detail[(pid, field)] = grade
        return counts, detail

    # ------------------------------------------------------------ the score
    def test_replay_scores_99_of_100_auto(self):
        counts, detail = self.grades()
        correct = counts.get("CORRECT", 0) + counts.get("CORRECT-lenient", 0)
        non_correct = sorted(k for k, g in detail.items()
                             if g not in ("CORRECT", "CORRECT-lenient"))
        self.assertEqual(
            correct, 99,
            "expected spike A's 99/100; non-correct: {0} ({1})".format(
                non_correct, counts))
        # the same split the audit confirmed: 87 strict + 12 lenient
        self.assertEqual(counts.get("CORRECT", 0), 87, counts)
        self.assertEqual(counts.get("CORRECT-lenient", 0), 12, counts)
        # the single non-correct is auto_grade's known artifact on mu-med
        # language ('; also offered as' phrasing), adjudicated correct in
        # spike A's e3-adjudication.md — pinned so a DIFFERENT miss fails
        self.assertEqual(non_correct, [("mu-med", "language")])

    def test_zero_fabrications(self):
        counts, detail = self.grades()
        fabrications = [k for k, g in detail.items() if g == "FABRICATION"]
        self.assertEqual(fabrications, [])

    def test_zero_misses(self):
        counts, detail = self.grades()
        misses = [k for k, g in detail.items() if g == "MISS"]
        self.assertEqual(misses, [])

    def test_zero_gate_failures(self):
        for uni in BENCHMARK_UNIS:
            with self.subTest(uni=uni):
                self.assertEqual(self.reports[uni]["gate_failures"], [])
                self.assertEqual(
                    self.reports[uni]["summary"]["gate_failures"], 0)

    def test_tier_split_g55_f27_b12(self):
        tiers = {}
        for uni in BENCHMARK_UNIS:
            for tier, n in self.reports[uni]["summary"][
                    "tier_counts"].items():
                tiers[tier] = tiers.get(tier, 0) + n
        self.assertEqual(tiers, {"G": 55, "F": 27, "B": 12})

    # ------------------------------------------------------ report contract
    def test_every_non_null_value_carries_the_quintuple(self):
        shipped = 0
        for pid, field, record in self.field_records():
            if record.get("value") is None:
                continue
            with self.subTest(program=pid, field=field):
                self.assertEqual(record["status"], Status.PASS.value)
                provenance = record["provenance"]
                self.assertEqual(provenance["value"], record["value"])
                self.assertTrue(provenance["source_url"].startswith("http"))
                self.assertTrue(provenance["source_snippets"])
                self.assertTrue(all(isinstance(s, str) and s
                                    for s in provenance["source_snippets"]))
                self.assertRegex(provenance["retrieved_at"],
                                 r"^\d{4}-\d{2}-\d{2}T")
                self.assertTrue(provenance["method"])
                artifact = record["artifact"]
                self.assertTrue(artifact["ref"])
                self.assertTrue(artifact["renderer_id"])
                self.assertTrue(artifact["renderer_version"])
                shipped += 1
        self.assertEqual(shipped, 94)

    def test_explicit_nulls_are_null_ok_with_reason(self):
        key_nulls = {(pid, field)
                     for pid, values in KEY.items()
                     for field, value in values.items() if value is None}
        report_nulls = set()
        for pid, field, record in self.field_records():
            if record.get("value") is None:
                with self.subTest(program=pid, field=field):
                    self.assertEqual(record["status"], Status.NULL_OK.value)
                    self.assertIn("cascade-null", record["null_reason"])
                report_nulls.add((pid, field))
        self.assertEqual(report_nulls, key_nulls)

    def test_run_reports_written_and_partials_cleaned(self):
        for uni in BENCHMARK_UNIS:
            with self.subTest(uni=uni):
                run_dir = Path(self.out_dir) / uni
                report_path = run_dir / "run-report.json"
                self.assertTrue(report_path.is_file())
                on_disk = json.loads(report_path.read_text())
                self.assertEqual(on_disk["summary"],
                                 self.reports[uni]["summary"])
                self.assertFalse(
                    (run_dir / "run-report.partial.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
