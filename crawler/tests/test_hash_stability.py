"""Hash-stability harness suite (ticket 04) — history/churn-report logic
only, no network. measure()'s live-fetch path is exercised by hand (see
the issue file's data-point log), not here.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import hash_stability as hs  # noqa: E402
from crawler.store import SnapshotStore  # noqa: E402


class HistoryChurnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SnapshotStore(self.tmp.name)

    def _fetch_event(self, url, sha, week, status=200):
        self.store.record_fetch({
            "url": url, "final_url": url, "status": status, "sha256": sha,
            "bytes": 100, "content_type": "text/html",
            "retrieved_at": "2026-08-{0:02d}T00:00:00Z".format(week),
            "robots": "", "error": None,
        })

    def test_stable_page_no_churn(self):
        for week in (1, 8, 15, 22):
            self._fetch_event("https://a.test/p", "sha-same", week)
            self.store.record_canonical("sha-same", "canon-same")
        report = hs.churn_report(self.tmp.name)
        stats = report["per_host"]["a.test"]
        self.assertEqual(stats["pairs"], 3)
        self.assertEqual(stats["raw_churn"], 0)
        self.assertEqual(stats["ambiguous"], 0)
        self.assertEqual(report["max_weeks_seen"], 4)
        self.assertTrue(report["acceptance_met"])

    def test_raw_churn_without_canonical_churn_is_not_ambiguous(self):
        # nonce/CSRF-class noise: raw bytes differ, rendered text is identical
        self._fetch_event("https://a.test/p", "sha-week1", 1)
        self.store.record_canonical("sha-week1", "canon-stable")
        self._fetch_event("https://a.test/p", "sha-week2", 2)
        self.store.record_canonical("sha-week2", "canon-stable")
        report = hs.churn_report(self.tmp.name)
        stats = report["per_host"]["a.test"]
        self.assertEqual(stats["raw_churn"], 1)
        self.assertEqual(stats["ambiguous"], 0)

    def test_canonical_change_is_flagged_ambiguous_not_confirmed(self):
        self._fetch_event("https://a.test/p", "sha-week1", 1)
        self.store.record_canonical("sha-week1", "canon-A")
        self._fetch_event("https://a.test/p", "sha-week2", 2)
        self.store.record_canonical("sha-week2", "canon-B")
        report = hs.churn_report(self.tmp.name)
        stats = report["per_host"]["a.test"]
        self.assertEqual(stats["ambiguous"], 1)
        pair = report["pages"][0]["pairs"][0]
        self.assertIn("AMBIGUOUS", pair["verdict"])

    def test_verdict_reflects_churn_rate_against_threshold(self):
        # 1 stable pair, 1 ambiguous pair -> 50% > 10% threshold
        self._fetch_event("https://a.test/p1", "s1", 1)
        self.store.record_canonical("s1", "c1")
        self._fetch_event("https://a.test/p1", "s1", 2)  # unchanged
        self._fetch_event("https://a.test/p2", "s2", 1)
        self.store.record_canonical("s2", "c2")
        self._fetch_event("https://a.test/p2", "s3", 2)
        self.store.record_canonical("s3", "c3-different")
        report = hs.churn_report(self.tmp.name)
        self.assertIn("THREATENS", report["verdict"])

    def test_no_history_yet_gives_empty_report(self):
        report = hs.churn_report(self.tmp.name)
        self.assertEqual(report["max_weeks_seen"], 0)
        self.assertFalse(report["acceptance_met"])
        self.assertIsNone(report["verdict"])

    def test_history_reads_full_event_sequence_not_just_latest(self):
        # the store's own cache-index only exposes the newest record per
        # URL — history() must read the raw manifest to get all of them
        for i, sha in enumerate(["s1", "s2", "s3"], start=1):
            self._fetch_event("https://a.test/p", sha, i)
        events = hs.history(self.tmp.name)["https://a.test/p"]
        self.assertEqual([e["sha256"] for e in events], ["s1", "s2", "s3"])


if __name__ == "__main__":
    unittest.main()
