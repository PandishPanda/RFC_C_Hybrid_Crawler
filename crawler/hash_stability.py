"""Canonical-hash stability measurement (ticket 04).

The refresh cost model's core assumption — an unchanged page's CANONICAL
text hash is stable, so an unchanged run costs fetch-only — was only ever
measured over ~100 minutes (audit-corrected from spike C's "next-day"
claim). This module is the weekly instrument: force-refetch the benchmark
pages, render + record their canonical hash, and let
``crawler.store.SnapshotStore``'s own append-only manifest accumulate one
fetch event per page per week for 4+ weeks running.

Deliberately reuses the store's own history rather than a bespoke log:
``SnapshotStore.record_fetch``/``record_canonical`` already write exactly
the (raw_sha256, canonical_sha256, retrieved_at) triple this measurement
needs, append-only, one line per event — this module only adds "fetch
weekly with force=True" and a churn report over what accumulates.

Known methodological wrinkle, not hidden: over WEEKS (unlike the original
~100-minute measurement), a page's TRUE content can legitimately change
between two fetches. A raw-sha change with no canonical-sha change is
unambiguous (churn on volatile markup, real content held still); a
canonical-sha change is NOT unambiguous — it could be real content change
or genuine canonical instability, and this module cannot tell them apart
without a human reading the diff. The report flags these as AMBIGUOUS,
never auto-classifies them as "churn."
"""
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from crawler.config import load_configs_dir
from crawler.render import DOCLING_URL, render
from crawler.runner import DEFAULT_CONFIGS_DIR, document_plan
from crawler.store import LiveFetcher, SnapshotStore

__all__ = ["BENCHMARK_UNIS", "measure", "history", "churn_report"]

BENCHMARK_UNIS = ("AUBG", "MUPleven", "SofiaUniversity", "VUM")


def _host(url):
    return urlparse(url).netloc


# ------------------------------------------------------------------ measure
def measure(snapshot_dir, configs_dir=None, docling_url=None, fetcher=None):
    # type: (str, ...) -> list
    """Force-refetch every benchmark page once, render it, record both
    hashes into the snapshot store's manifest. Returns this pass's
    per-page results (including any fetch/render errors — a page that
    fails to render this week just has no canonical_sha256 for this
    week's history entry, it doesn't abort the others)."""
    configs = load_configs_dir(configs_dir or DEFAULT_CONFIGS_DIR)
    store = SnapshotStore(snapshot_dir)
    fetcher = fetcher or LiveFetcher(store)

    results = []
    for uni_id in BENCHMARK_UNIS:
        site = configs[uni_id]
        for _key, (url, route, sid) in sorted(document_plan(site).items()):
            entry = {"uni_id": uni_id, "url": url, "route": route,
                     "host": _host(url)}
            try:
                ref = fetcher.fetch(url, {"cookies": dict(site.cookies),
                                          "label": uni_id}, force=True)
            except Exception as exc:  # noqa: BLE001 — one page must not abort the pass
                entry["fetch_error"] = "{0}: {1}".format(type(exc).__name__, exc)
                results.append(entry)
                continue
            entry["raw_sha256"] = ref.sha256
            entry["retrieved_at"] = ref.retrieved_at
            if ref.sha256 is None:
                entry["fetch_error"] = ref.error or "no body"
                results.append(entry)
                continue
            try:
                raw_bytes = ref.read_bytes()
                artifact = render(raw_bytes, ref.content_type, route,
                                  ref=ref.sha256,
                                  docling_url=docling_url or DOCLING_URL)
                canonical_sha = hashlib.sha256(
                    artifact.text.encode("utf-8")).hexdigest()
                store.record_canonical(ref.sha256, canonical_sha,
                                       artifact.renderer_id,
                                       artifact.renderer_version)
                entry["canonical_sha256"] = canonical_sha
            except Exception as exc:  # noqa: BLE001 — render failure logged, not fatal
                entry["render_error"] = "{0}: {1}".format(type(exc).__name__, exc)
            results.append(entry)
    return results


# ------------------------------------------------------------------ history
def history(snapshot_dir):
    """Every fetch event ever recorded for the benchmark pages, grouped
    by URL, oldest first — read straight off the manifest file (not the
    store's cache-index API, which only exposes the latest record per
    URL; this needs the full history)."""
    manifest_path = Path(snapshot_dir) / "manifest.jsonl"
    if not manifest_path.exists():
        return {}
    fetches = defaultdict(list)
    canonicals = {}  # raw_sha256 -> canonical_sha256 (newest wins)
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("kind") == "canonical":
            if rec.get("sha256"):
                canonicals[rec["sha256"]] = rec.get("canonical_sha256")
        elif rec.get("sha256"):
            fetches[rec["url"]].append(rec)
    for url, events in fetches.items():
        events.sort(key=lambda r: r.get("retrieved_at", ""))
        for e in events:
            e["canonical_sha256"] = canonicals.get(e["sha256"])
    return dict(fetches)


# ------------------------------------------------------------- churn report
def churn_report(snapshot_dir):
    """Per-host churn table: for every page with >=2 recorded fetches,
    how many consecutive week-pairs changed raw sha256 vs canonical
    sha256. week_count is the number of distinct fetch events seen so
    far (the ticket's "4 weekly data points" acceptance target)."""
    events_by_url = history(snapshot_dir)
    per_host = defaultdict(lambda: {
        "pages": 0, "pairs": 0, "raw_churn": 0, "canonical_churn": 0,
        "ambiguous": 0, "max_weeks_seen": 0,
    })
    page_detail = []

    for url, events in events_by_url.items():
        if not events:
            continue
        host = _host(url)
        stats = per_host[host]
        stats["pages"] += 1
        stats["max_weeks_seen"] = max(stats["max_weeks_seen"], len(events))
        pairs = list(zip(events, events[1:]))
        page_pairs = []
        for a, b in pairs:
            raw_changed = a["sha256"] != b["sha256"]
            canon_changed = a.get("canonical_sha256") != b.get("canonical_sha256")
            stats["pairs"] += 1
            if raw_changed and not canon_changed:
                stats["raw_churn"] += 1
                verdict = "raw-only (expected: nonce/CSRF-class noise)"
            elif canon_changed:
                stats["ambiguous"] += 1
                verdict = "AMBIGUOUS: canonical text changed — real content " \
                          "change or genuine instability, needs a human look"
            else:
                verdict = "stable"
            page_pairs.append({
                "from": a.get("retrieved_at"), "to": b.get("retrieved_at"),
                "raw_changed": raw_changed, "canonical_changed": canon_changed,
                "verdict": verdict,
            })
        page_detail.append({"url": url, "host": host,
                            "weeks_seen": len(events), "pairs": page_pairs})

    verdict = None
    total_pairs = sum(s["pairs"] for s in per_host.values())
    total_canon_churn = sum(s["ambiguous"] for s in per_host.values())
    if total_pairs:
        rate = total_canon_churn / total_pairs
        verdict = (
            "canonical churn {0:.1%} across {1} week-pairs ({2} flagged "
            "AMBIGUOUS, not auto-confirmed as instability) — {3} the "
            "refresh cost model's core assumption".format(
                rate, total_pairs, total_canon_churn,
                "THREATENS" if rate > 0.10 else "supports"))
    max_weeks = max((s["max_weeks_seen"] for s in per_host.values()), default=0)
    return {
        "per_host": dict(per_host),
        "pages": page_detail,
        "max_weeks_seen": max_weeks,
        "data_points_target": 4,
        "acceptance_met": max_weeks >= 4,
        "verdict": verdict,
    }
