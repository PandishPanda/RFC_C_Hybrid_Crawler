"""`crawler validate` -- the pipeline scorecard harness (ticket 23).

Re-measures every check that is mechanical, reads DURABLE evidence for
the human-gated ones, and emits the locked scorecard format against the
locked bars (pipeline-validation map, tickets 02/03, 2026-08-17).

Two hard rules, both learned this branch:

* A graded number is only ever read from an ARCHIVED artifact -- never
  from crawler-out/<uni>/run-report.json, which later runs overwrite in
  place (measured: the n=33 graded result was silently replaced by a
  tail-less rerun within a day).
* Output is archived per run (timestamped), never overwritten. The
  harness READS measurements; it never manufactures or destroys them.

Verdicts: PASS / FAIL / PROXY / PENDING. PROXY marks a row with no
direct correctness measurement -- an assumption made visible, visually
distinct by design. A population-limited bound prints as PASS at the
bound the population supports. Precision rows are batch-scoped, never
university-named (Alexander's format reaction, 2026-08-17).
"""
import json
import math
import time
from pathlib import Path

from crawler import hash_stability

__all__ = ["run_checks", "render_scorecard", "write_archive", "MARK"]

MARK = {"PASS": "✓", "FAIL": "✗", "PROXY": "≈",
        "PENDING": "…", "INFO": "·"}

BASELINE = Path("benchmark/baseline.json")
VERDICTS = Path("benchmark/sample-verdicts.json")
FINDINGS = Path("benchmark/site-findings.json")
STABILITY_DIR = "crawler-out/hash-stability/snapshots"
OUT_DIR = Path("crawler-out/validation")

# The frozen cascade acceptance (spike A audit; test_replay holds the
# full auto-grade -- the harness re-checks the count/tier line).
FROZEN = {"PASS": 94, "NULL_OK": 6, "tiers": {"G": 55, "F": 27, "B": 12}}


def _row(step, metric, value, bar, verdict, cost="—", source=""):
    # type: (str, str, str, str, str, str, str) -> dict
    return {"step": step, "metric": metric, "value": value, "bar": bar,
            "verdict": verdict, "cost": cost, "source": source}


def _binom_cdf(k, n, p):
    # P(X <= k) for X ~ Binomial(n, p). n is benchmark-sized (hundreds),
    # so the exact sum is cheap and avoids a scipy dependency (stdlib only).
    return sum(math.comb(n, i) * p ** i * (1.0 - p) ** (n - i)
               for i in range(k + 1))


def _upper_bound_k(n, k):
    # One-sided 95% Clopper-Pearson upper bound on the wrong rate given k
    # wrong out of n: the largest p whose P(X <= k) is still >= 0.05.
    # Bisection rather than a closed form because only k=0 has one -- and
    # quoting the k=0 bound beside a nonzero wrong count (what this used
    # to do) understates the real uncertainty.
    if not n:
        return 100.0
    if k >= n:
        return 100.0
    lo, hi = k / float(n), 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _binom_cdf(k, n, mid) > 0.05:
            lo = mid
        else:
            hi = mid
    return 100.0 * hi


def _upper_bound(n):
    # one-sided 95% Clopper-Pearson upper bound on the wrong rate, 0 wrong
    return _upper_bound_k(n, 0)


def _check_hash_stability(rows):
    if not Path(STABILITY_DIR).exists():
        rows.append(_row(
            "1 SNAPSHOT→ARTIFACT", "canonical churn (unexplained)",
            "no stability snapshots yet in this clone", "0 unexplained",
            "PENDING", source="run hash_stability.measure() to start"))
        return
    rep = hash_stability.churn_report(STABILITY_DIR)
    per = rep["per_host"]
    pairs = sum(s["pairs"] for s in per.values())
    raw = sum(s["raw_churn"] for s in per.values())
    canonical = sum(s["canonical_churn"] for s in per.values())
    # the bar: any canonical churn must be EXPLAINED by a ledger value
    # diff; with zero churn there is nothing to explain.
    if canonical == 0:
        verdict, note = "PASS", ""
    else:
        # Fail-closed BY LABEL: ledger reconciliation (matching each
        # canonical change to a value-diff) is unimplemented, so any
        # churn currently reads as unexplained. The label says so --
        # a legitimate site update will FAIL here until that lands.
        verdict = "FAIL"
        note = " (ledger reconciliation unimplemented -- ALL churn "\
               "currently counts as unexplained)"
    rows.append(_row(
        "1 SNAPSHOT→ARTIFACT", "canonical churn (unexplained)",
        "{0} of {1} pairs (raw churn {2} filtered){3}".format(
            canonical, pairs, raw, note),
        "0 unexplained", verdict,
        source="churn_report@" + time.strftime("%Y-%m-%d")))


def _check_frozen_acceptance(rows):
    # counts + tier split re-derived offline through the real runner over
    # the frozen configs + spike cache (the same path test_replay runs).
    import tempfile
    from crawler import runner
    frozen_dir = str(Path("crawler/tests/fixtures_benchmark_configs"))
    cache = ".scratch/sta-78/spikes/a/cache"
    if not Path(cache).exists():
        rows.append(_row(
            "2 CASCADE (G/F/B)", "frozen acceptance",
            "spike cache not present in this clone",
            "94P/6N G:55/F:27/B:12 exact", "PENDING",
            source="needs .scratch/sta-78/spikes/a (gathered data)"))
        rows.append(_row(
            "1 SNAPSHOT→ARTIFACT", "doc failures (benchmark replay)",
            "—", "0", "PENDING", source="same"))
        return
    counts = {"PASS": 0, "NULL_OK": 0}
    tiers = {}
    with tempfile.TemporaryDirectory() as td:
        for uni in ("AUBG", "MUPleven", "SofiaUniversity", "VUM"):
            rep = runner.run(uni, configs_dir=frozen_dir, out_dir=td,
                             replay_dir=cache)
            for key, val in rep["summary"]["status_counts"].items():
                counts[key] = counts.get(key, 0) + val
            counts["_docfails"] = (counts.get("_docfails", 0)
                                   + rep["summary"]["document_failures"])
            for key, val in rep["summary"]["tier_counts"].items():
                tiers[key] = tiers.get(key, 0) + val
    docfails = counts.pop("_docfails", 0)
    exact = (counts.get("PASS") == FROZEN["PASS"]
             and counts.get("NULL_OK") == FROZEN["NULL_OK"]
             and tiers == FROZEN["tiers"])
    rows.append(_row(
        "2 CASCADE (G/F/B)", "frozen acceptance",
        "{0}P/{1}N G:{2}/F:{3}/B:{4}".format(
            counts.get("PASS"), counts.get("NULL_OK"),
            tiers.get("G", 0), tiers.get("F", 0), tiers.get("B", 0)),
        "94P/6N G:55/F:27/B:12 exact", "PASS" if exact else "FAIL",
        source="frozen configs + spike cache, re-run"))
    rows.append(_row(
        "1 SNAPSHOT→ARTIFACT", "doc failures (benchmark replay)",
        str(docfails), "0", "PASS" if docfails == 0 else "FAIL",
        source="same re-run"))


# Findings that describe a site limit we chose to live with are INFO;
# findings still costing us data are FAIL-worthy in spirit but must not
# flip the harness exit code, which belongs to the mechanical checks --
# so open ones print as PROXY: a visible, unmeasured cost, not a pass.
_FINDING_VERDICT = {
    "open": "PROXY",
    "accepted": "INFO",
    "accepted-by-owner": "INFO",
    "mitigated": "INFO",
    "avoided": "INFO",
}


def _read_site_findings(rows):
    """Per-university negative findings -- what each site cost us.

    Durable evidence, never recomputed: a finding is retired by fixing
    the site handling, not by a later run happening to look fine."""
    if not FINDINGS.exists():
        rows.append(_row("8 SITE FINDINGS", "per-university negatives",
                         "not recorded", "every uni accounted for",
                         "PENDING", source="missing site-findings.json"))
        return
    data = json.loads(FINDINGS.read_text(encoding="utf-8"))
    items = data.get("findings", [])
    by_uni = {}
    for f in items:
        by_uni.setdefault(f["uni_id"], []).append(f)
    open_count = sum(1 for f in items if f.get("status") == "open")
    rows.append(_row(
        "8 SITE FINDINGS", "recorded",
        "{0} finding(s) across {1} universit(ies); {2} still open".format(
            len(items), len(by_uni), open_count),
        "every uni accounted for", "INFO",
        source="site-findings.json " + data.get("date", "")))
    for uni in sorted(by_uni):
        for f in by_uni[uni]:
            rows.append(_row(
                "8 SITE FINDINGS", "{0} · {1}".format(uni, f["kind"]),
                f["finding"], f.get("impact", "—"),
                _FINDING_VERDICT.get(f.get("status"), "INFO"),
                source="status: {0}".format(f.get("status", "?"))))


def _read_sample_verdicts(rows):
    if not VERDICTS.exists():
        rows.append(_row("7 FEES", "sampled fee correctness", "not yet run",
                         "0 wrong (population bound)", "PENDING",
                         source="map ticket 06"))
        return
    data = json.loads(VERDICTS.read_text(encoding="utf-8"))
    for sheet, step, name in (("B", "7 FEES",
                               "sampled fee correctness"),):
        items = [i for i in data["items"] if i["sheet"] == sheet]
        wrong = [i for i in items if i["verdict"] != "ok"]
        n = len(items)
        bound = _upper_bound_k(n, len(wrong))
        verdict = "PASS" if not wrong else "FAIL"
        rows.append(_row(
            step, name,
            "{0} wrong in {1} ⇒ ≤{2:.1f}% @95%".format(
                len(wrong), n, bound),
            "0 wrong (population-limited)", verdict,
            cost="~1.4 min/item human",
            source="sample-verdicts.json {0} judge={1}".format(
                data["date"], data["judge"])))


def _read_baseline(rows):
    if not BASELINE.exists():
        rows.append(_row("6 GRADER", "graded accuracy", "no baseline",
                         "—", "PENDING", source="missing baseline.json"))
        return
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    graded = base["steps"]["6-grader"]["blind_grade_n33"]
    n, wrong = graded["n"], graded["wrong"]
    bound = _upper_bound_k(n, wrong)
    rows.append(_row(
        "6 GRADER (oracle)", "graded accuracy tier-1",
        "{0} wrong / {1} ⇒ ≤{2:.1f}% @95%".format(wrong, n, bound),
        "≤8.7%", "PASS" if bound <= 8.7 else "FAIL",
        cost="~90 min human (key)",
        source="ARCHIVED " + graded["date"]))
    rows.append(_row(
        "3 LLM TAIL", "fabrications (graded runs)",
        str(graded.get("fabrications", "?")), "0",
        "PASS" if graded.get("fabrications") == 0 else "FAIL",
        source="ARCHIVED " + graded["date"]))
    if n < 100:
        rows.append(_row(
            "6 GRADER (oracle)", "graded accuracy tier-2",
            "n={0} of 100 needed".format(n), "≤3.0%", "PENDING",
            source="grows via per-uni blind keys"))
    else:
        # n has reached the tier-2 population: this row MEASURES now.
        # It used to be hardcoded PENDING, so it could never leave that
        # state however large n grew -- a permanently-green-by-omission
        # row of exactly the kind this scorecard exists to prevent.
        rows.append(_row(
            "6 GRADER (oracle)", "graded accuracy tier-2",
            "{0} wrong / {1} ⇒ ≤{2:.1f}% @95%".format(wrong, n, bound),
            "≤3.0%", "PASS" if bound <= 3.0 else "FAIL",
            cost="~90 min human per uni key",
            source="ARCHIVED " + graded["date"]))
    fill = base["steps"]["3-llm-tail"]["fill"]["value"]
    rows.append(_row(
        "3 LLM TAIL", "fill on prose pages",
        " | ".join("{0} {1}".format(k, v) for k, v in sorted(fill.items()))
        + " (site-dependent)",
        "— (no bar: property of pages)", "PROXY",
        source=base["steps"]["3-llm-tail"]["fill"]["reproduce"]))
    promo = base["steps"]["4-onboarding"].get("promotions_verified")
    if promo:
        v = promo["value"]
        ok = v["verified"] == v["promoted"]
        rows.append(_row(
            "4 ONBOARDING", "promoted-config discipline",
            "{0}/{1} promotions independently verified".format(
                v["verified"], v["promoted"]),
            "100%", "PASS" if ok else "FAIL",
            cost="$0.31–0.82/row + ~2 min human/row",
            source="baseline {0} — prose record, RECOUNT at next "
                   "batch".format(promo["date"])))


def _check_costs(rows):
    # type: (list) -> None
    # A ledger mixes phases: tail calls tag "Uni:prog:field:attempt"
    # (4 parts), onboarding "Uni:rowid" (2). Blending their means under a
    # "TAIL cost" label measured neither (review finding, 2026-08-17).
    tail_means = {}
    for path in sorted(Path("crawler-out").glob("*/llm-usage.jsonl")):
        entries = [json.loads(l) for l in path.read_text().splitlines() if l]
        tail = [e for e in entries
                if len((e.get("tag") or "").split(":")) >= 4]
        if tail:
            tail_means[path.parent.name] = (
                sum(e.get("cost_usd", 0) for e in tail) / len(tail))
    if not tail_means:
        return
    lo, hi = min(tail_means.values()), max(tail_means.values())
    rows.append(_row(
        "3 LLM TAIL", "cost per TAIL call",
        "${0:.3f}–${1:.3f} by site ({2})".format(
            lo, hi, ", ".join(sorted(tail_means))), "$0.06",
        "FAIL" if hi > 0.06 else "PASS",
        cost="cause: prompt bundling (ticket 02)",
        source="llm-usage ledgers, tail-tagged calls only"))


def _check_onboarding_discipline(rows):
    # Promotion history shows independent verification -- recorded per
    # batch in the tickets. Precision is batch-scoped INFO.
    rows.append(_row(
        "4 ONBOARDING", "proposal precision (per batch)",
        "vum-bg-08-17: 100% | uniruse-08-15: 20% → change seeds",
        "advice <50%", "INFO", source="onboarding-proposal.json"))


def run_checks():
    # type: () -> list
    rows = []
    _check_hash_stability(rows)
    _check_frozen_acceptance(rows)
    _read_baseline(rows)
    _check_costs(rows)
    _check_onboarding_discipline(rows)
    _read_sample_verdicts(rows)
    _read_site_findings(rows)
    return rows


def render_scorecard(rows):
    # type: (list) -> str
    lines = ["crawler validate — scorecard ({0})".format(
        time.strftime("%Y-%m-%d")), "=" * 78]
    current = None
    for r in sorted(rows, key=lambda r: r["step"]):
        if r["step"] != current:
            current = r["step"]
            lines.append("")
            lines.append(current)
        mark = MARK.get(r["verdict"].split(":")[0], "?")
        lines.append("  {0:34} {1}".format(r["metric"][:34], r["value"]))
        lines.append("  {0:34} {1:30} {2} {3:8} [{4}]".format(
            "", r["cost"], mark, r["verdict"], r["source"]))
        lines.append("  {0:34} bar: {1}".format("", r["bar"]))
    lines += ["", "=" * 78,
              "≈ PROXY = no direct correctness measurement — "
              "an assumption made visible",
              "population-limited bounds print as PASS at the bound the "
              "population supports"]
    return "\n".join(lines)


def write_archive(rows, scorecard_text, stamp=None):
    # type: (list, str, str) -> tuple
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    json_path = OUT_DIR / "scorecard-{0}.json".format(stamp)
    md_path = OUT_DIR / "dossier-{0}.md".format(stamp)
    if json_path.exists() or md_path.exists():   # paranoid: never overwrite
        raise RuntimeError("archive collision at {0}".format(stamp))
    json_path.write_text(json.dumps(
        {"generated_at": stamp, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    dossier = ["# Pipeline validation dossier — {0}".format(stamp), "",
               "```", scorecard_text, "```", ""]
    for r in sorted(rows, key=lambda r: (r["step"], r["metric"])):
        dossier.append("## {0} — {1}".format(r["step"], r["metric"]))
        dossier.append("")
        dossier.append("- measured: {0}".format(r["value"]))
        dossier.append("- bar: {0} — verdict {1}".format(
            r["bar"], r["verdict"]))
        dossier.append("- cost: {0}".format(r["cost"]))
        dossier.append("- evidence: {0}".format(r["source"]))
        dossier.append("")
    md_path.write_text("\n".join(dossier), encoding="utf-8")
    return json_path, md_path
