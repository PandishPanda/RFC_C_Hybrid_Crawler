"""The grading oracle for the blind fresh-university benchmark (ticket 07,
RFC v2 SS4 DEC-5, SS8; ADR-0002).

Grades a run-report.json's shipped field records against a FROZEN answer
key -- speaking the SAME Status vocabulary crawler.provenance.gate()
emits (PASS / REJECT_CONTAINMENT / REJECT_SUPPORT / NULL_OK /
PARSE_FAILURE), never a collapsed "did it pass y/n" boolean. This module
is pure and mechanical: no IO beyond reading/writing its own JSON files,
no network, no LLM calls, no opinions about what any real page actually
says. It does not, and cannot, tell you whether a specific extraction is
semantically correct -- that is exactly the judgment the Phase-0 key and
the CHECK/manual-verdict overlay exist to carry, from a human, never from
this module or from the extraction pipeline it grades.

Two defects measured in the STA-78 spike graders (.scratch/sta-78/spikes/
{b,c,stackc}/grade*.py), both excluded here BY CONSTRUCTION rather than
by care:

  1. Position matching. spikes/c/grade_e3.py paired the frozen key to
     extraction output via ``zip(key_order, extraction_order)`` -- two
     independently-maintained parallel lists. If they ever drift (a
     program reordered, inserted, or removed on either side), every
     later program silently grades against the wrong one. grade_report()
     here matches every key entry and every report field by its explicit
     program_id + field name (a dict lookup), never by list position or
     iteration order -- GradeReportMatchesByProgramIdNotPositionTest
     proves a shuffled report and a reordered key still grade correctly.

  2. PARSE_FAILURE grading as a correct null. spikes/{b,stackc}/grade.py
     computed ``passed = (verdict == "PASS")`` and then, when the key
     expected null, graded ANYTHING not-PASS as "ok_null" -- collapsing
     NULL_OK, PARSE_FAILURE, REJECT_CONTAINMENT and REJECT_SUPPORT into
     one bucket. A malformed, uncheckable record (PARSE_FAILURE) then
     graded identically to a clean affirmative null. ADR-0002's own
     consequences line is explicit that this must never happen again:
     "PARSE_FAILURE travels in the type from tail to grader, so a parse
     failure can never again grade as a correct null." grade_field()
     here gives PARSE_FAILURE its own GradeCategory in EVERY branch of
     the key-null/key-value split, never reachable via OK_NULL or MISS.

Also gives REJECT_CONTAINMENT/REJECT_SUPPORT their own categories rather
than folding them into OK_NULL or MISS: a rejected value means the
cascade proposed something and the gate caught it, which is real
diagnostic signal (something on this page is confusing the cascade) that
a flat "ok" would hide, even though the SHIPPED behavior (a null) still
happens to match a null-expecting key.

What this module deliberately does NOT do: author a frozen key. Building
the pipeline that produces run-report.json and building the "ground
truth" it is graded against in the same session, by the same author, is
the exact contamination the blind-benchmark ticket exists to prevent --
see .scratch/crawler-v2/issues/07-blind-benchmark-gate.md for the current
state of that gap. tier_for()/draft helpers below assist a SEPARATE
Phase-0 labeling pass (.scratch/sta-78/phase0/check-key.py's precedent,
ported here) in producing candidate key entries for a human to verify;
they never decide truth themselves.
"""
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from crawler.provenance import (
    normalize,
    _CURRENCY_TOKENS,
    _CURRENCY_WORDS,
    _NUM_RX,
    _PREFIX_LEN,
    _WORD_RX,
    _segment_index,
    _strip_separators,
)

__all__ = [
    "GradeCategory", "KeyEntry", "GradeRow", "GradeReport",
    "load_frozen_key", "write_frozen_key",
    "grade_field", "grade_report",
    "read_manual_verdicts", "write_manual_verdict",
    "tier_for",
    "MANUAL_VERDICTS_NAME",
]

MANUAL_VERDICTS_NAME = "manual-verdicts.json"

_STATUS_PASS = "PASS"
_STATUS_NULL_OK = "NULL_OK"
_STATUS_PARSE_FAILURE = "PARSE_FAILURE"
_STATUS_REJECT_CONTAINMENT = "REJECT_CONTAINMENT"
_STATUS_REJECT_SUPPORT = "REJECT_SUPPORT"
_REJECT_STATUSES = (_STATUS_REJECT_CONTAINMENT, _STATUS_REJECT_SUPPORT)


class GradeCategory(Enum):
    OK_NULL = "ok_null"
    FABRICATION = "fabrication"
    REJECTED_VS_NULL_KEY = "rejected_vs_null_key"
    OK_VALUE = "ok_value"
    CHECK = "check"
    OK_MANUAL = "ok_manual"
    WRONG = "wrong"
    MISS = "miss"
    MISS_GATE = "miss_gate"
    PARSE_FAILURE = "parse_failure"


# categories a human has affirmed or that need no human affirmation --
# everything else (CHECK, REJECTED_VS_NULL_KEY, MISS*, PARSE_FAILURE,
# FABRICATION, WRONG) is either a known problem or awaiting one.
_CORRECT_CATEGORIES = frozenset({
    GradeCategory.OK_NULL, GradeCategory.OK_VALUE, GradeCategory.OK_MANUAL,
})


@dataclass(frozen=True)
class KeyEntry:
    """One human-verified ground-truth cell (Phase-0 protocol).

    expected_value is None when the field should be null; null_reason is
    only meaningful then. snippet/source_url are the verbatim evidence a
    human confirmed on the real page -- carried for audit, not read by
    grade_field itself."""
    program_id: str
    field: str
    expected_value: Optional[str]
    null_reason: Optional[str] = None
    snippet: Optional[str] = None
    source_url: Optional[str] = None


@dataclass(frozen=True)
class GradeRow:
    program_id: str
    field: str
    category: GradeCategory
    expected_value: Optional[str]
    shipped_value: Optional[str]
    shipped_status: str


@dataclass(frozen=True)
class GradeReport:
    rows: Tuple[GradeRow, ...]

    @property
    def tallies(self):
        # type: () -> Dict[GradeCategory, int]
        out = {}
        for row in self.rows:
            out[row.category] = out.get(row.category, 0) + 1
        return out

    @property
    def fabrication_count(self):
        return sum(1 for r in self.rows if r.category is GradeCategory.FABRICATION)

    @property
    def fabrication_rate(self):
        return self.fabrication_count / len(self.rows) if self.rows else 0.0

    @property
    def pass_count(self):
        """Fields that actually shipped a value (status PASS) -- the
        denominator for wrong_rate, since a wrong-but-gate-green rate is
        about the reliability of what ships, not diluted by nulls/misses
        that never shipped a value to begin with."""
        return sum(1 for r in self.rows if r.shipped_status == _STATUS_PASS)

    @property
    def unresolved_check_count(self):
        return sum(1 for r in self.rows if r.category is GradeCategory.CHECK)

    @property
    def wrong_count(self):
        return sum(1 for r in self.rows if r.category is GradeCategory.WRONG)

    @property
    def wrong_rate(self):
        """None (undetermined) while any CHECK entry lacks a manual
        verdict -- reporting a rate that silently treats unresolved
        entries as correct (or as wrong) would misstate the actual gate
        result either optimistically or pessimistically."""
        if self.unresolved_check_count:
            return None
        return self.wrong_count / self.pass_count if self.pass_count else 0.0

    @property
    def gate_pass(self):
        # type: () -> Optional[bool]
        """RFC v2 SS8/ADR thresholds: fabrications = 0, semantically-wrong-
        but-gate-green <= 2-3%. None while wrong_rate is undetermined
        (unresolved CHECK entries outstanding) -- a gate result can't be
        claimed PASS or FAIL until every ambiguous cell has a human
        verdict."""
        if self.wrong_rate is None:
            return None
        return self.fabrication_count == 0 and self.wrong_rate <= 0.03


# ------------------------------------------------------------- the oracle
def _values_match(expected, shipped):
    # type: (str, str) -> bool
    """Does SHIPPED support EXPECTED -- calling the SAME private
    token-support primitives crawler.provenance.gate() itself uses
    (_NUM_RX/_WORD_RX/_CURRENCY_TOKENS/_CURRENCY_WORDS/_PREFIX_LEN/
    _segment_index/_strip_separators), imported directly rather than
    hand-copied, so the two modules' notions of "supported" cannot drift
    apart independently. Earlier hand-copied versions of this function
    diverged from gate() in three ways, all now impossible by
    construction: (1) a whole-string containment fast path let "900 EUR"
    match "1900 EUR" and a short word ("an") match inside an unrelated
    word ("urban") -- gone, this mirrors gate()'s per-token checks
    exactly; (2) a tokenless EXPECTED value (a placeholder like "-")
    vacuously matched anything -- gate() treats a tokenless value as
    PARSE_FAILURE, never PASS, and this does the same, returning False;
    (3) multiple currency mentions in EXPECTED (e.g. "100 евро / 100 €")
    only required ONE to be found in SHIPPED -- gate() requires EVERY
    currency token independently found, and so does this.
    """
    nexp, nship = normalize(expected), normalize(shipped)
    if not nship:
        return False

    number_tokens = _NUM_RX.findall(nexp)
    currency_tokens = [c for c in _CURRENCY_TOKENS if c in nexp]
    word_tokens = [w for w in _WORD_RX.findall(nexp) if w not in _CURRENCY_WORDS]
    if not (number_tokens or currency_tokens or word_tokens):
        return False

    numbers, words, prefixes = _segment_index(nship)
    for tok in number_tokens:
        stripped = _strip_separators(tok).strip()
        if stripped and stripped not in numbers:
            return False
    for cur in currency_tokens:
        if cur not in nship:
            return False
    for w in word_tokens:
        if w in words:
            continue
        if len(w) >= _PREFIX_LEN and w[:_PREFIX_LEN] in prefixes:
            continue
        return False
    return True


def grade_field(entry, record):
    # type: (KeyEntry, Mapping) -> GradeCategory
    """The oracle: one key entry x one shipped field record -> a
    GradeCategory. Pure; no IO. See module docstring for the two named
    defects this shape excludes by construction."""
    status = record["status"]
    shipped_value = record.get("value")

    if entry.expected_value is None:
        if status == _STATUS_PASS:
            return GradeCategory.FABRICATION
        if status == _STATUS_NULL_OK:
            return GradeCategory.OK_NULL
        if status == _STATUS_PARSE_FAILURE:
            return GradeCategory.PARSE_FAILURE
        if status in _REJECT_STATUSES:
            return GradeCategory.REJECTED_VS_NULL_KEY
        raise ValueError("unknown status {0!r}".format(status))

    if status == _STATUS_PASS:
        if shipped_value and _values_match(entry.expected_value, shipped_value):
            return GradeCategory.OK_VALUE
        return GradeCategory.CHECK
    if status == _STATUS_NULL_OK:
        return GradeCategory.MISS
    if status == _STATUS_PARSE_FAILURE:
        return GradeCategory.PARSE_FAILURE
    if status in _REJECT_STATUSES:
        return GradeCategory.MISS_GATE
    raise ValueError("unknown status {0!r}".format(status))


def grade_report(key, run_report, *, manual_verdicts=None):
    # type: (Mapping[Tuple[str, str], KeyEntry], Mapping, Optional[Mapping]) -> GradeReport
    """Grade every (program_id, field) the run report ships against the
    frozen KEY, matched by explicit id -- never by list position or dict
    iteration order (named defect #1). Raises KeyError, loudly, on any
    mismatch between what the key covers and what the report covers in
    either direction: a key entry with no matching report field, or a
    report field with no matching key entry, is a benchmark-integrity bug
    (an incomplete key or a report from the wrong university/run), never
    something to silently skip."""
    manual_verdicts = manual_verdicts or {}
    rows = []
    report_pairs = set()
    for program in run_report["programs"]:
        pid = program["program_id"]
        for field, field_record in program["fields"].items():
            report_pairs.add((pid, field))
            entry = key.get((pid, field))
            if entry is None:
                raise KeyError(
                    "no frozen-key entry for program_id={0!r} "
                    "field={1!r} -- the key is incomplete for this "
                    "run report".format(pid, field))
            category = grade_field(entry, field_record)
            if category is GradeCategory.CHECK:
                manual = manual_verdicts.get((pid, field))
                if manual is not None:
                    category = (GradeCategory.OK_MANUAL if manual == "ok"
                               else GradeCategory.WRONG)
            rows.append(GradeRow(
                program_id=pid, field=field, category=category,
                expected_value=entry.expected_value,
                shipped_value=field_record.get("value"),
                shipped_status=field_record["status"]))

    missing_from_report = set(key) - report_pairs
    if missing_from_report:
        raise KeyError(
            "frozen key has entries with no matching program/field in "
            "the run report: {0}".format(sorted(missing_from_report)))
    return GradeReport(rows=tuple(rows))


# ------------------------------------------------------------- key storage
def load_frozen_key(path):
    # type: (str) -> Dict[Tuple[str, str], KeyEntry]
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = {}
    for raw in data["entries"]:
        entry = KeyEntry(
            program_id=raw["program_id"], field=raw["field"],
            expected_value=raw.get("expected_value"),
            null_reason=raw.get("null_reason"),
            snippet=raw.get("snippet"), source_url=raw.get("source_url"))
        dup = (entry.program_id, entry.field)
        if dup in entries:
            raise ValueError(
                "duplicate key entry for program_id={0!r} "
                "field={1!r}".format(*dup))
        entries[dup] = entry
    return entries


def write_frozen_key(path, entries):
    # type: (str, List[KeyEntry]) -> str
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [
        dict(program_id=e.program_id, field=e.field,
            expected_value=e.expected_value, null_reason=e.null_reason,
            snippet=e.snippet, source_url=e.source_url)
        for e in entries]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return str(path)


# --------------------------------------------------------- manual verdicts
# Manual verdicts are ORIGINAL HUMAN JUDGMENT, not gathered output: a
# human decided a CHECK entry was ok or wrong, and by the Phase-0
# protocol that decision can never be regenerated (re-judging after
# seeing results is the contamination the blind key exists to prevent).
# They therefore live in the TRACKED benchmark/ tree, not under the
# gitignored, wipe-at-will crawler-out/.
VERDICTS_DIR = "benchmark/verdicts"


def _manual_path(out_dir, uni_id):
    return Path(VERDICTS_DIR) / "{0}.json".format(uni_id)


def _legacy_manual_path(out_dir, uni_id):
    """Where verdicts lived before they were tracked (crawler-out).
    Read-only: still honoured so an un-migrated clone keeps its work."""
    return Path(out_dir) / uni_id / MANUAL_VERDICTS_NAME


def read_manual_verdicts(out_dir, uni_id):
    # type: (str, str) -> Dict[Tuple[str, str], str]
    path = _manual_path(out_dir, uni_id)
    if not path.exists():
        path = _legacy_manual_path(out_dir, uni_id)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(e["program_id"], e["field"]): e["verdict"] for e in data["verdicts"]}


def write_manual_verdict(out_dir, uni_id, program_id, field, verdict, *, note=""):
    # type: (str, str, str, str, str, str) -> str
    """verdict is "ok" or "wrong" -- a human's resolution of a CHECK
    entry grade_field couldn't auto-classify. Appends/replaces (keyed by
    program_id+field), durable across re-runs, mirroring
    the durable-resolution file pattern."""
    if verdict not in ("ok", "wrong"):
        raise ValueError(
            "verdict must be 'ok' or 'wrong', got {0!r}".format(verdict))
    path = _manual_path(out_dir, uni_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))["verdicts"]
    existing = [e for e in existing
               if (e["program_id"], e["field"]) != (program_id, field)]
    existing.append(dict(program_id=program_id, field=field,
                        verdict=verdict, note=note))
    path.write_text(
        json.dumps({"verdicts": existing}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return str(path)


# --------------------------------------------------- Phase-0 draft tiering
def tier_for(value, snippet):
    # type: (str, str) -> Tuple[str, str]
    """Mechanical pre-check for a Phase-0 labeling draft, ported from
    .scratch/sta-78/phase0/check-key.py's policy (HARD/SOFT/OK) so a
    future labeling pass doesn't hand-copy the spike script. Never
    decides truth -- a human still verifies every entry before the key
    freezes; this only sorts which entries are worth checking first.

    HARD: the snippet cannot mechanically support the value (a number,
    currency, or most of the wording is missing) -- check these first.
    SOFT: value is composed/annotated but every load-bearing token is
    present in the snippet.
    OK: the snippet literally contains the (normalized) value.
    """
    v, s = normalize(value), normalize(snippet)
    if not s:
        return "HARD", "no snippet"
    if v in s:
        return "OK", ""
    missing_nums = [n for n in re.findall(r"\d[\d.,]*", v) if n.rstrip(".,") not in s]
    if missing_nums:
        return "HARD", "numbers missing from snippet: {0}".format(missing_nums)
    currency_tokens = ("€", "евро", "лева", "leva", "eur", "bgn", "лв")
    cur_in_v = [c for c in currency_tokens if c in v]
    if cur_in_v and not any(c in s for c in cur_in_v):
        return "HARD", "currency {0} missing from snippet".format(cur_in_v)
    stop = frozenset(
        "per for the and или and are of in on at to with a an от на за и по или с в у".split())
    toks = [t for t in re.findall(r"[\wЀ-ӿ]{4,}", v) if t not in stop]
    hit = sum(1 for t in toks if t in s)
    if toks and hit / len(toks) < 0.5:
        return "HARD", "only {0}/{1} significant tokens found in snippet".format(hit, len(toks))
    return "SOFT", "composed value -- verbatim containment fails but all key tokens present"
