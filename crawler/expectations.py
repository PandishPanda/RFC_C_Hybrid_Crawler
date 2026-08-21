"""Dataset-level expectation checks that gate the ledger pointer (ticket 03).

A run's values are ALWAYS appended to the ledger (append-only, nothing is
ever lost) — what these checks gate is whether the ``current`` pointer is
allowed to move to the new run. A blocked run stays fully inspectable in
the ledger; it just never becomes "current" until a human clears it.

Checks (ticket 03's exact list):
  1. coverage drop  > COVERAGE_DROP_THRESHOLD (10 percentage points)
  2. null-rate spike on key fields > NULL_RATE_SPIKE_THRESHOLD (15 points)
  3. falling row count (fewer programs than the previous promoted run)
  4. valid_for year-lag after 1 July (any value's academic_year behind
     the upcoming admission cycle)

"coverage" here is SELF-RELATIVE by design (ADR-0006): extracted share
of this run's configured programs. It is a delta brake ("6 of 8 programs
stopped resolving"), not a completeness claim against any external
enumeration — no such denominator exists since the RSVU registry was
dropped.

Euro rules: BGN_EUR_RATE is Bulgaria's currency-board peg (also the euro-
changeover conversion rate) — a value restated from BGN to EUR at exactly
this rate is the SAME value, not a plausibility outlier.
"""
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from crawler.ledger import infer_academic_year

__all__ = [
    "COVERAGE_DROP_THRESHOLD", "NULL_RATE_SPIKE_THRESHOLD", "KEY_FIELDS",
    "BGN_EUR_RATE", "bgn_to_eur", "eur_to_bgn", "currency_equivalent",
    "expected_academic_year", "ExpectationResult", "check", "summarize",
]

COVERAGE_DROP_THRESHOLD = 0.10
NULL_RATE_SPIKE_THRESHOLD = 0.15
KEY_FIELDS = ("tuition", "admission")

BGN_EUR_RATE = 1.95583  # fixed peg; also the euro-changeover rate


def bgn_to_eur(amount):
    return amount / BGN_EUR_RATE


def eur_to_bgn(amount):
    return amount * BGN_EUR_RATE


_AMOUNT_RX = re.compile(
    r"(\d[\d.,]*)\s*(лв\.?|leva|bgn|eur|€|евро)", re.IGNORECASE)


def _parse_amount(text):
    """First (number, currency) pair in text, or None. Currency
    normalized to 'BGN'/'EUR'; number parsed with '.' decimal, ','/space
    thousands stripped."""
    m = _AMOUNT_RX.search(text or "")
    if not m:
        return None
    raw_num, cur = m.groups()
    cur = cur.lower()
    currency = "EUR" if cur in ("eur", "€", "евро") else "BGN"
    num = raw_num.replace(" ", "")
    # decide decimal vs thousands separator: a trailing ,NN or .NN (2
    # digits) is decimal; anything else is a thousands separator
    if re.search(r"[.,]\d{2}$", num):
        num = num[:-3] + "." + num[-2:]
        num = num.replace(",", "").replace(".", "", num.count(".") - 1) \
            if num.count(".") > 1 else num
    num = num.replace(",", "")
    try:
        return float(num), currency
    except ValueError:
        return None


def currency_equivalent(old_value, new_value, tolerance=0.02):
    # type: (str, str, float) -> bool
    """True when old_value and new_value are the same amount, just
    denominated in different currencies at the fixed peg rate (within a
    small rounding tolerance) — e.g. "1200 лв." vs "613.55 EUR"."""
    a = _parse_amount(old_value)
    b = _parse_amount(new_value)
    if not a or not b or a[1] == b[1]:
        return False
    amount_a, cur_a = a
    amount_b, cur_b = b
    a_in_eur = amount_a if cur_a == "EUR" else bgn_to_eur(amount_a)
    b_in_eur = amount_b if cur_b == "EUR" else bgn_to_eur(amount_b)
    if a_in_eur == 0:
        return False
    return abs(a_in_eur - b_in_eur) / a_in_eur <= tolerance


def expected_academic_year(today=None):
    """The admission cycle a fresh value should carry. Before 1 July the
    current cycle is still fresh; from 1 July the UPCOMING cycle is
    expected (RFC v2 Q9)."""
    t = today or time.gmtime()
    year, month = t.tm_year, t.tm_mon
    start = year if month >= 7 else year - 1
    return "{0}/{1}".format(start, start + 1)


def _year_start(academic_year):
    try:
        return int(academic_year.split("/")[0])
    except (ValueError, AttributeError, IndexError):
        return None


# --------------------------------------------------------------- summarize
def summarize(report):
    """Per-run metrics expectation checks compare across runs.

    ``per_program`` carries the same metrics keyed by program_id so a
    later run can compare like with like. Without it, adding programs
    to a university is indistinguishable from the data getting worse:
    every new program starts unconfigured, which lowers coverage and
    raises the null rate exactly as a regression would (measured across
    the 2026-08-21 build-out — nearly every university tripped these
    checks purely by growing).
    """
    programs = report["programs"]
    covered = sum(1 for p in programs
                 if any(f["status"] == "PASS" for f in p["fields"].values()))
    denom = len(programs) or 1
    key_total = key_null = 0
    per_program = {}
    for p in programs:
        p_total = p_null = 0
        for name, f in p["fields"].items():
            if name in KEY_FIELDS:
                key_total += 1
                p_total += 1
                if f["status"] == "NULL_OK":
                    key_null += 1
                    p_null += 1
        per_program[p["program_id"]] = {
            "covered": any(f["status"] == "PASS"
                           for f in p["fields"].values()),
            "key_null": p_null,
            "key_total": p_total,
        }
    return {
        "programs": len(programs),
        "covered_programs": covered,
        "coverage": covered / denom,
        "key_field_null_rate": (key_null / key_total) if key_total else 0.0,
        "per_program": per_program,
    }


def _subset_metrics(summary, program_ids):
    # type: (dict, set) -> Optional[dict]
    """coverage/null-rate recomputed over PROGRAM_IDS only, or None when
    the summary predates per_program (older run summaries stay readable)."""
    per = summary.get("per_program")
    if per is None:
        return None
    rows = [per[pid] for pid in program_ids if pid in per]
    if not rows:
        return None
    key_total = sum(r["key_total"] for r in rows)
    key_null = sum(r["key_null"] for r in rows)
    return {
        "programs": len(rows),
        "coverage": sum(1 for r in rows if r["covered"]) / len(rows),
        "key_field_null_rate": (key_null / key_total) if key_total else 0.0,
    }


# ------------------------------------------------------------------- check
@dataclass(frozen=True)
class ExpectationResult:
    blocked: bool
    reasons: List[str] = field(default_factory=list)
    current: dict = field(default_factory=dict)
    previous: Optional[dict] = None
    # Program-set change since the previous promoted run. added/removed
    # are program ids; compared_on is how many programs the delta checks
    # actually ran over (the stable intersection).
    added: Tuple[str, ...] = ()
    removed: Tuple[str, ...] = ()
    compared_on: Optional[int] = None


def check(report, previous_summary=None, *, today=None,
         academic_year=None):
    # type: (dict, Optional[dict], Optional[object], Optional[str]) -> ExpectationResult
    """Gate the pointer move for one run. previous_summary is the
    summarize() output of the last PROMOTED run (None on a university's
    first-ever run — nothing to regress against, so nothing can block).
    academic_year is the run's declared cycle, the same fallback
    ledger.append_run uses when no value states its own year — pass the
    same value to both so the ledger and this check agree."""
    academic_year = academic_year or expected_academic_year(today)
    current = summarize(report)
    reasons = []

    added = removed = ()
    compared_on = None
    if previous_summary is not None:
        cur_ids = set(current.get("per_program") or {})
        prev_ids = set(previous_summary.get("per_program") or {})
        if cur_ids and prev_ids:
            added = tuple(sorted(cur_ids - prev_ids))
            removed = tuple(sorted(prev_ids - cur_ids))
            stable = cur_ids & prev_ids
        else:
            stable = set()

        # Compare like with like: the delta checks run over the programs
        # BOTH runs contain. A program added since the last promoted run
        # has no history to regress against, and counting it as a
        # regression is what made every expansion in the 2026-08-21
        # build-out look like data loss.
        cur_cmp = _subset_metrics(current, stable) if stable else None
        prev_cmp = _subset_metrics(previous_summary, stable) if stable else None
        if cur_cmp is None or prev_cmp is None:
            # No per_program on one side (a summary written before this
            # existed): fall back to whole-run comparison rather than
            # silently skipping the gate.
            cur_cmp, prev_cmp = current, previous_summary
        else:
            compared_on = cur_cmp["programs"]

        scope = ("" if compared_on is None
                 else " [on {0} program(s) present in both runs".format(
                     compared_on)
                     + ("; {0} added".format(len(added)) if added else "")
                     + ("; {0} removed".format(len(removed)) if removed else "")
                     + "]")

        drop = prev_cmp["coverage"] - cur_cmp["coverage"]
        if drop > COVERAGE_DROP_THRESHOLD:
            reasons.append(
                "coverage dropped {0:.1%} -> {1:.1%} ({2:.1%} drop, "
                "threshold {3:.0%}){4}".format(
                    prev_cmp["coverage"], cur_cmp["coverage"],
                    drop, COVERAGE_DROP_THRESHOLD, scope))

        spike = cur_cmp["key_field_null_rate"] - prev_cmp["key_field_null_rate"]
        if spike > NULL_RATE_SPIKE_THRESHOLD:
            reasons.append(
                "key-field null rate spiked {0:.1%} -> {1:.1%} ({2:.1%} "
                "rise, threshold {3:.0%}){4}".format(
                    prev_cmp["key_field_null_rate"],
                    cur_cmp["key_field_null_rate"], spike,
                    NULL_RATE_SPIKE_THRESHOLD, scope))

        # Programs DISAPPEARING is still a regression worth blocking on --
        # but a falling total is not, when the set was deliberately
        # changed. Fire on what actually vanished.
        if removed:
            reasons.append(
                "{0} program(s) present in the last promoted run are "
                "missing: {1}{2}".format(
                    len(removed), ", ".join(removed[:5]),
                    ", ..." if len(removed) > 5 else ""))
        elif not prev_ids and current["programs"] < previous_summary["programs"]:
            reasons.append(
                "row count fell {0} -> {1}".format(
                    previous_summary["programs"], current["programs"]))

    expected = expected_academic_year(today)
    expected_start = _year_start(expected)
    lagging = []
    for p in report["programs"]:
        for name, f in p["fields"].items():
            if f["status"] != "PASS":
                continue
            prov = f.get("provenance") or {}
            valid_for = infer_academic_year(
                prov.get("source_snippets", []), f.get("value") or "",
                academic_year, field=name)
            start = _year_start(valid_for)
            if start is not None and start < expected_start:
                lagging.append("{0}.{1}={2}".format(p["program_id"], name, valid_for))
    if lagging:
        reasons.append(
            "{0} value(s) lag the expected {1} admission cycle: {2}".format(
                len(lagging), expected, ", ".join(lagging[:5])
                + (", ..." if len(lagging) > 5 else "")))

    return ExpectationResult(blocked=bool(reasons), reasons=reasons,
                             current=current, previous=previous_summary,
                             added=added, removed=removed,
                             compared_on=compared_on)
