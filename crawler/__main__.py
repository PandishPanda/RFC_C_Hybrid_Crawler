"""CLI for the StudyStream crawler v2 extraction spine.

    python3 -m crawler run <UniID> [--replay DIR] [--configs DIR]
                                   [--out DIR] [--docling-url URL] [--tail]
    python3 -m crawler publish <UniID> [same flags]
                                       [--academic-year YYYY/YYYY]
    python3 -m crawler onboard <UniID> --seed URL [--seed URL ...]
                                       [--replay DIR] [--configs DIR]
                                       [--out DIR] [--max-pages N]
    python3 -m crawler grade <UniID> --run-report PATH --key PATH [--out DIR]
    python3 -m crawler labelkit <UniID> [--configs DIR] [--out-file PATH]
    python3 -m crawler check-pins <UniID> [--configs DIR] [--list-html PATH]

``run`` executes store -> render -> cascade -> gate for one configured
university and writes crawler-out/<UniID>/run-report.json. With --replay
pointing at a spike-A cache/ directory the run is entirely offline
(snapshots from the cache, PDF renderings from the sibling out/ dir).

``publish`` (ticket 03) does everything ``run`` does, then appends the
run's values to the append-only ledger and runs the dataset-level
expectation checks (coverage drop, null-rate spike, falling row count,
valid_for year-lag) — the ``current`` pointer only moves if they pass.
Writes crawler-out/<UniID>/publish-report.json alongside run-report.json.

--tail enables the gated LLM tail (ticket 02) for cascade-nulled fields:
the `claude` CLI, subscription auth, no ANTHROPIC_API_KEY needed. Per-call
cost/tokens land in <out>/<UniID>/llm-usage.jsonl. Without --tail a
cascade-nulled field ships an explicit null, same as before ticket 02
existed — zero LLM calls, ticket 01's original property.

``onboard`` (ticket 06, reshaped by ADR-0006) discovers candidate pages
from --seed URLs, surveys them for degree-program pages (the LLM's
page-is-a-program judgment — always UNVERIFIED, ADR-0002 can't check a
semantic match), verifies each selected page for real gate-PASSed field
values (tier G only, no bespoke config exists yet), and writes
<out>/<UniID>/onboarding-proposal.json. Never writes to
crawler/configs/ — a human reviews the proposal and promotes by hand.

``grade`` (ticket 07) grades an existing run-report.json against a frozen
answer key (crawler.grader's JSON format — see crawler/grader.py's module
docstring for the Phase-0 protocol and why this session cannot author the
key itself), matching every field by program_id — never by list position
(the defect that made spike C's grader silently cross-grade programs on
drift). CHECK entries (a shipped value the key doesn't auto-match) need a
human verdict via crawler.grader.write_manual_verdict before the gate
result is anything but PENDING. Exit code: 0 PASS, 1 FAIL, 2 PENDING.

``labelkit`` (ticket 13) generates the BLANK Phase-0 worksheet ``grade``
needs a key for — every configured program's 5 fields, grouped by page so
each real page is visited once, with no pipeline-extracted values
anywhere in it. A human fills it in by reading the real pages; see
crawler/labelkit.py and crawler/grader.py for why that human must not be
whoever built the pipeline being graded.
"""
import argparse
import json
import sys
from pathlib import Path

from crawler import grader, labelkit, llm_tail, onboarding, publish as publish_mod, runner, staleness, validate as validate_mod
from crawler.config import load_site_config


def _summarize(report):
    summary = report["summary"]
    lines = [
        "{0} [{1}] {2} programs x {3} fields".format(
            report["uni_id"], report["mode"], summary["programs"],
            len(report["programs"][0]["fields"]) if report["programs"]
            else 0),
        "  documents resolved: {0} (failures: {1})".format(
            summary["documents"], summary["document_failures"]),
        "  status counts:      {0}".format(json.dumps(
            {k: v for k, v in summary["status_counts"].items() if v},
            ensure_ascii=False)),
        "  tier split (PASS):  {0}".format(json.dumps(
            summary["tier_counts"], ensure_ascii=False)),
        "  gate failures:      {0}".format(summary["gate_failures"]),
    ]
    if summary.get("tail_calls"):
        lines.append("  tail calls:          {0} ({1} escalated to Sonnet)"
                     .format(summary["tail_calls"], summary["tail_escalations"]))
    return "\n".join(lines)


def _summarize_publish(pr):
    lines = [
        "run_id:    {0}".format(pr["run_id"]),
        "promoted:  {0}".format(pr["promoted"]),
    ]
    if pr["blocked_reasons"]:
        lines.append("BLOCKED — expectation checks failed:")
        for r in pr["blocked_reasons"]:
            lines.append("  - " + r)
    lines.append("coverage:  {0:.1%} ({1}/{2} programs)".format(
        pr["summary"]["coverage"], pr["summary"]["covered_programs"],
        pr["summary"]["programs"]))
    ch = pr.get("program_set_change") or {}
    if ch.get("added") or ch.get("removed"):
        lines.append("program set: +{0} added, -{1} removed"
                     "{2}".format(
                         len(ch.get("added") or []),
                         len(ch.get("removed") or []),
                         " (delta checks compared {0} program(s) present "
                         "in both runs)".format(ch["compared_on"])
                         if ch.get("compared_on") is not None else ""))
    d = pr["value_diff_summary"]
    lines.append("value diff vs {0}: +{1} added, -{2} removed, "
                 "~{3} changed, {4} currency-only".format(
                     pr["previous_run_id"] or "(none)", d["added"],
                     d["removed"], d["changed"], d["currency_only"]))
    return "\n".join(lines)


def _draft_config_summary(report):
    if report.draft_config_valid is None:
        return "n/a (nothing proposed)"
    if report.draft_config_valid:
        return "valid"
    return "INVALID -- {0}".format(report.draft_config_error)


def _summarize_onboarding(report):
    adapter_errors = sum(1 for p in report.proposals if p.adapter_error)
    lines = []
    if getattr(report, "seed_failures", ()):
        lines.append("SEED FETCH FAILURES ({0}) — fix these before reading "
                     "the result below:".format(len(report.seed_failures)))
        for f in report.seed_failures:
            lines.append("  ! {0} -> HTTP {1}{2}".format(
                f["seed"], f["status"],
                " ({0})".format(f["error"]) if f["error"] else ""))
    lines += [
        "{0} candidate proposals ({1} with a proposed page, {2} adapter "
        "error -- worth a retry, not a decline)".format(
            len(report.proposals),
            sum(1 for p in report.proposals if p.proposed_url),
            adapter_errors),
        "cost:      ${0:.4f}".format(report.total_cost_usd),
        "draft config shape: {0}".format(_draft_config_summary(report)),
    ]
    for p in report.proposals:
        if p.proposed_url:
            lines.append("  {0} -> {1} ({2} field(s) gate-verified, "
                         "UNCONFIRMED assignment)".format(
                             p.proposed_name, p.proposed_url,
                             p.field_pass_count))
        elif p.adapter_error:
            lines.append("  {0} -> ADAPTER ERROR, retry ({1})".format(
                p.proposed_name, p.adapter_error))
        else:
            lines.append("  {0} -> no match ({1})".format(
                p.proposed_name, p.match_reasoning))
    return "\n".join(lines)


def _summarize_grade(report):
    lines = ["{0} fields graded".format(len(report.rows))]
    for category, count in sorted(report.tallies.items(),
                                  key=lambda kv: kv[0].value):
        lines.append("  {0:22} {1}".format(category.value, count))
    lines.append("fabrications:         {0} ({1:.1%})".format(
        report.fabrication_count, report.fabrication_rate))
    if report.wrong_rate is None:
        lines.append(
            "wrong-but-gate-green: PENDING -- {0} unresolved CHECK "
            "entr{1} (resolve via crawler.grader.write_manual_verdict "
            "before the gate result can be PASS/FAIL)".format(
                report.unresolved_check_count,
                "y" if report.unresolved_check_count == 1 else "ies"))
    else:
        lines.append("wrong-but-gate-green: {0} ({1:.1%})".format(
            report.wrong_count, report.wrong_rate))
    verdict = ("PENDING" if report.gate_pass is None
              else "PASS" if report.gate_pass else "FAIL")
    lines.append(
        "gate: {0} (thresholds: fabrications=0, "
        "wrong-but-gate-green<=3%)".format(verdict))
    return "\n".join(lines)


def _add_run_args(p):
    p.add_argument("uni_id", help="university id — a crawler/configs/<UniID>.json")
    p.add_argument(
        "--replay", metavar="DIR", default=None,
        help="spike-A cache/ dir: replay entirely offline (PDF renderings "
             "come from the sibling out/ dir; zero network)")
    p.add_argument(
        "--configs", metavar="DIR", default=None,
        help="site-config directory (default: crawler/configs)")
    p.add_argument(
        "--out", metavar="DIR", default=None,
        help="output root; the report lands at <DIR>/<UniID>/"
             "run-report.json (default: crawler-out)")
    p.add_argument(
        "--docling-url", metavar="URL", default=None,
        help="Docling Serve base URL for live table-pdf rendering "
             "(default: {0})".format(runner.DOCLING_URL))
    p.add_argument(
        "--tail", action="store_true",
        help="enable the gated LLM tail for cascade-nulled fields "
             "(claude CLI, subscription auth; cost/tokens logged to "
             "<out>/<UniID>/llm-usage.jsonl)")


def _make_tail(args):
    if not args.tail:
        return None
    out_root = args.out or runner.DEFAULT_OUT_ROOT
    usage_ledger = llm_tail.UsageLedger(
        "{0}/{1}/llm-usage.jsonl".format(out_root, args.uni_id))
    return llm_tail.CLIAdapter(usage_ledger=usage_ledger)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m crawler",
        description="StudyStream university crawler v2 (STA-78 spine)")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run", help="run the extraction spine for one university")
    _add_run_args(run_parser)

    publish_parser = subparsers.add_parser(
        "publish", help="run + ledger + expectation checks + pointer move")
    _add_run_args(publish_parser)
    publish_parser.add_argument(
        "--academic-year", metavar="YYYY/YYYY", default=None,
        help="declared cycle for values that don't state their own year "
             "(default: computed from today's date)")

    onboard_parser = subparsers.add_parser(
        "onboard", help="survey a university's site for degree-program "
                        "pages (nothing is written to "
                        "crawler/configs/ -- human review only)")
    onboard_parser.add_argument(
        "uni_id", help="university id (a crawler/configs/<UniID>.json is "
                       "optional -- if present, its already-configured "
                       "pages are skipped)")
    onboard_parser.add_argument(
        "--seed", metavar="URL", action="append", required=True,
        help="a page to discover candidate program-page links from "
             "(repeatable)")
    onboard_parser.add_argument("--replay", metavar="DIR", default=None)
    onboard_parser.add_argument("--configs", metavar="DIR", default=None)
    onboard_parser.add_argument("--out", metavar="DIR", default=None)
    onboard_parser.add_argument("--docling-url", metavar="URL", default=None)
    onboard_parser.add_argument(
        "--max-pages", type=int, default=10,
        help="cap on surveyed pages to verify -- each verify is a real "
             "fetch and render; pass a higher number deliberately once "
             "you're ready to spend more (default: 10)")

    grade_parser = subparsers.add_parser(
        "grade", help="grade a run-report.json against a frozen answer "
                      "key (blind fresh-university benchmark, ticket 07)")
    grade_parser.add_argument(
        "uni_id", help="university id -- only used to locate "
                       "<out>/<UniID>/manual-verdicts.json")
    grade_parser.add_argument(
        "--run-report", metavar="PATH", required=True,
        help="path to a run-report.json produced by `run`/`publish`")
    grade_parser.add_argument(
        "--key", metavar="PATH", required=True,
        help="path to a frozen answer-key JSON (crawler.grader's format "
             "-- see crawler/grader.py for why this session cannot "
             "author one itself)")
    grade_parser.add_argument("--out", metavar="DIR", default=None)

    staleness_parser = subparsers.add_parser(
        "check-pins", help="report version-pinned sources whose plan has "
                           "been superseded (stale-green drift, ticket 22)")
    staleness_parser.add_argument("uni_id")
    staleness_parser.add_argument("--configs", metavar="DIR", default=None)
    staleness_parser.add_argument(
        "--list-html", metavar="PATH", default=None,
        help="a captured listing to read instead of fetching")

    validate_parser = subparsers.add_parser(
        "validate", help="re-measure the pipeline scorecard against the "
                         "locked bars; archive scorecard + dossier "
                         "(ticket 23)")
    validate_parser.add_argument(
        "--no-archive", action="store_true",
        help="print only; do not write the timestamped archive")

    labelkit_parser = subparsers.add_parser(
        "labelkit", help="generate a BLANK Phase-0 labeling worksheet for "
                         "a human to fill in by reading the real pages "
                         "(ticket 13)")
    labelkit_parser.add_argument(
        "uni_id", help="university id — a crawler/configs/<UniID>.json")
    labelkit_parser.add_argument("--configs", metavar="DIR", default=None)
    labelkit_parser.add_argument(
        "--out-file", metavar="PATH", default=None,
        help="write the worksheet here instead of stdout")

    args = parser.parse_args(argv)

    if args.command not in ("run", "publish", "onboard",
                            "grade", "labelkit", "check-pins", "validate"):
        parser.print_help()
        return 2

    if args.command == "validate":
        rows = validate_mod.run_checks()
        text = validate_mod.render_scorecard(rows)
        print(text)
        if not args.no_archive:
            try:
                json_path, md_path = validate_mod.write_archive(rows, text)
            except RuntimeError as exc:
                print()
                print("archive NOT written: {0}".format(exc))
                return 2
            print()
            print("archived: {0}".format(json_path))
            print("dossier : {0}".format(md_path))
        failing = [r for r in rows if r["verdict"] == "FAIL"]
        return 1 if failing else 0

    if args.command == "check-pins":
        config_path = Path(
            args.configs or runner.DEFAULT_CONFIGS_DIR
        ) / "{0}.json".format(args.uni_id)
        site = load_site_config(config_path)
        pinned = staleness.pinned_sources(site)
        if args.list_html:
            listing = Path(args.list_html).read_text(encoding="utf-8")
        else:
            import requests
            listing = requests.get(staleness.CURRICULUM_LIST_URL,
                                   timeout=30).text
        drift = staleness.check_version_drift(
            site, staleness.listed_versions(listing))
        print("{0}: {1} version-pinned source(s), {2} drifted".format(
            args.uni_id, len(pinned), len(drift)))
        for d in drift:
            print("  {0}  {1}  pinned v{2} -> current v{3}".format(
                d["status"].upper(), d["where"], d["pinned_version"],
                d["current_version"]))
            print("      {0}".format(d["detail"]))
        # WARN, never block. `degree` is not in expectations.KEY_FIELDS, so
        # nothing gates on it today; and a superseded plan still ships a
        # TRUE value from a real document -- it is stale, not wrong. Making
        # it fatal would block a publish over data that is still accurate.
        return 0

    if args.command == "labelkit":
        config_path = Path(
            args.configs or runner.DEFAULT_CONFIGS_DIR
        ) / "{0}.json".format(args.uni_id)
        site = load_site_config(config_path)
        worksheet = labelkit.build_worksheet(site)
        if args.out_file:
            out_path = Path(args.out_file)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(worksheet, encoding="utf-8")
            print("worksheet: {0}".format(out_path))
        else:
            print(worksheet)
        return 0

    if args.command == "onboard":
        out_root = args.out or runner.DEFAULT_OUT_ROOT
        usage_ledger = llm_tail.UsageLedger(
            "{0}/{1}/llm-usage.jsonl".format(out_root, args.uni_id))
        adapter = llm_tail.CLIAdapter(
            usage_ledger=usage_ledger, system_prompt=onboarding.SYSTEM_PROMPT)
        report = onboarding.run_onboarding(
            args.uni_id, args.seed, adapter, configs_dir=args.configs,
            out_dir=args.out, replay_dir=args.replay,
            docling_url=args.docling_url,
            max_pages=args.max_pages)
        print(_summarize_onboarding(report))
        print("proposal: {0}/{1}/{2}".format(
            out_root, args.uni_id, onboarding.PROPOSAL_NAME))
        return 0

    if args.command == "grade":
        out_root = args.out or runner.DEFAULT_OUT_ROOT
        run_report = json.loads(
            Path(args.run_report).read_text(encoding="utf-8"))
        key = grader.load_frozen_key(args.key)
        manual = grader.read_manual_verdicts(None, args.uni_id)
        report = grader.grade_report(key, run_report, manual_verdicts=manual)
        print(_summarize_grade(report))
        if report.gate_pass is None:
            return 2
        return 0 if report.gate_pass else 1

    tail = _make_tail(args)

    if args.command == "run":
        report = runner.run(args.uni_id, configs_dir=args.configs,
                            out_dir=args.out, replay_dir=args.replay,
                            docling_url=args.docling_url, tail=tail)
        print(_summarize(report))
        print("report: {0}/{1}/run-report.json".format(
            args.out or runner.DEFAULT_OUT_ROOT, args.uni_id))
        # non-zero when anything was rejected by the gate: a failed check
        # must be impossible to miss in CI and cron logs alike
        return 1 if report["summary"]["gate_failures"] else 0

    pr = publish_mod.publish(
        args.uni_id, configs_dir=args.configs, out_dir=args.out,
        replay_dir=args.replay, docling_url=args.docling_url, tail=tail,
        academic_year=args.academic_year)
    print(_summarize_publish(pr))
    print("publish report: {0}/{1}/{2}".format(
        args.out or runner.DEFAULT_OUT_ROOT, args.uni_id,
        publish_mod.PUBLISH_REPORT_NAME))
    return 0 if pr["promoted"] else 1


if __name__ == "__main__":
    sys.exit(main())
