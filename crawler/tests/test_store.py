"""Snapshot-store suite (RFC v2 §2) — replay adapter + fake transport only.

ZERO network: the replay tests run against the snapshot dir vendored from
spike A's cache (fixtures/snapshots/ — a snapshot dir IS the second
adapter), and the live-adapter tests drive LiveFetcher through a scripted
in-memory FakeSession with an injected fake clock, so politeness spacing and
retry backoff are asserted without real sleeping and without a socket ever
opening.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests  # noqa: E402

from crawler.store import (  # noqa: E402
    UA,
    LiveFetcher,
    ReplayFetcher,
    SnapshotMiss,
    SnapshotStore,
)

FIXTURE_SNAPSHOTS = Path(__file__).resolve().parent / "fixtures" / "snapshots"

# The three snapshots vendored from spike A's cache (see manifest.jsonl,
# "vendored_from" keys): two SU HTML pages + one MU fee-order PDF.
SU_INDEX = "https://www.uni-sofia.bg/index.php/bul/obrazovanie/bakalav_rski_programi"
SU_STAT = ("https://www.uni-sofia.bg/index.php/bul/universitet_t/fakulteti/"
           "fakultet_po_matematika_i_informatika2/specialnosti/"
           "bakalav_rski_programi/fakultet_po_matematika_i_informatika/"
           "4_5_matematika/statistika")
MU_FEES_PDF = ("https://mu-pleven.bg/forms/ksk2026/"
               "taksi_za_kandidatstvane_obuchenie_2026_2027.pdf")


# ---------------------------------------------------------------- test doubles
class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None, url="",
                 text=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.url = url
        self._text = text

    @property
    def text(self):
        if self._text is not None:
            return self._text
        return self.content.decode("utf-8", "replace")


class FakeSession:
    """Scripted transport double.

    script maps url -> list of items served in order (a FakeResponse, or an
    Exception instance to raise). The last item is sticky. Every call is
    recorded with the fake-clock time so tests can assert politeness spacing
    at the transport boundary.
    """

    def __init__(self, script=None, clock=None):
        self.script = {u: list(items) for u, items in (script or {}).items()}
        self.clock = clock
        self.calls = []  # dicts: url, cookies, timeout, t

    def get(self, url, cookies=None, timeout=None, allow_redirects=True):
        self.calls.append({
            "url": url,
            "cookies": dict(cookies or {}),
            "timeout": timeout,
            "t": self.clock.monotonic() if self.clock else None,
        })
        if url not in self.script:
            if url.endswith("/robots.txt"):
                return FakeResponse(404, url=url)  # default: no robots file
            raise AssertionError("unscripted url fetched: " + url)
        items = self.script[url]
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, Exception):
            raise item
        return item

    def calls_for(self, url):
        return [c for c in self.calls if c["url"] == url]


class FakeClock:
    """monotonic()/sleep() pair: time advances only when someone sleeps."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.t += seconds


def live_fetcher(store, script, **kw):
    clock = FakeClock()
    session = FakeSession(script, clock=clock)
    fetcher = LiveFetcher(store, session=session,
                          sleep=clock.sleep, monotonic=clock.monotonic, **kw)
    return fetcher, session, clock


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------- replay adapter
class TestReplayAdapter(unittest.TestCase):
    """The vendored snapshot dir served through ReplayFetcher — no network."""

    @classmethod
    def setUpClass(cls):
        cls.replay = ReplayFetcher(FIXTURE_SNAPSHOTS)
        cls.manifest_before = (FIXTURE_SNAPSHOTS / "manifest.jsonl").read_bytes()

    def test_replay_serves_vendored_snapshots_content_addressed(self):
        for url in (SU_INDEX, SU_STAT, MU_FEES_PDF):
            ref = self.replay.fetch(url, {"cookies": {}})
            self.assertTrue(ref.from_cache)
            self.assertTrue(ref.ok)
            self.assertEqual(ref.status, 200)
            self.assertEqual(ref.url, url)
            body = ref.read_bytes()
            self.assertEqual(len(body), ref.bytes)
            # content address verified: sha256 field == digest of the body,
            # and the body file is NAMED by it (spike A's missing digest,
            # fixed as this store's identity scheme)
            self.assertEqual(sha256(body), ref.sha256)
            self.assertEqual(Path(ref.path).name, ref.sha256)
            self.assertEqual(Path(ref.path).parent.name, "bodies")

    def test_replay_pdf_keeps_content_type_and_exact_bytes(self):
        ref = self.replay.fetch(MU_FEES_PDF)  # site_config optional
        self.assertEqual(ref.content_type, "application/pdf")
        self.assertEqual(ref.bytes, 125673)
        self.assertTrue(ref.read_bytes().startswith(b"%PDF"))

    def test_replay_miss_raises_snapshot_miss(self):
        with self.assertRaises(SnapshotMiss):
            self.replay.fetch("https://example.invalid/never-fetched")

    def test_cookies_are_part_of_snapshot_identity(self):
        # vendored SU snapshot was recorded with no cookies; asking for the
        # same URL under a cookie jar is a DIFFERENT snapshot -> miss
        # (the AUBG interstitial is why identity includes cookies)
        with self.assertRaises(SnapshotMiss):
            self.replay.fetch(SU_INDEX, {"cookies": {"aubg_location": "bulgaria"}})

    def test_replay_is_read_only_and_offline(self):
        # structurally no transport: nothing on the adapter can open a socket
        self.assertFalse(hasattr(self.replay, "session"))
        for url in (SU_INDEX, MU_FEES_PDF):
            self.replay.fetch(url)
        with self.assertRaises(SnapshotMiss):
            self.replay.fetch("https://example.invalid/x")
        self.assertEqual(
            (FIXTURE_SNAPSHOTS / "manifest.jsonl").read_bytes(),
            self.manifest_before,
            "replay reads must never append to the manifest")

    def test_vendored_manifest_carries_the_hash_pair_schema(self):
        # RFC Q5: raw sha256 at fetch time, canonical_sha256 reserved for
        # the render module — BOTH keys present on every fetch record
        lines = (FIXTURE_SNAPSHOTS / "manifest.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            rec = json.loads(line)
            self.assertIn("sha256", rec)
            self.assertIn("canonical_sha256", rec)
            self.assertIsNotNone(rec["sha256"])
            self.assertIsNone(rec["canonical_sha256"])
            self.assertIn("robots", rec)
            self.assertIn("retrieved_at", rec)


# ---------------------------------------------------------------- live adapter
class TestLiveFetcher(unittest.TestCase):
    """LiveFetcher against a scripted FakeSession — still zero network."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="store-test-")
        self.store = SnapshotStore(self._tmp)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def manifest_lines(self):
        p = self.store.manifest_path
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def test_fetch_stores_body_content_addressed_and_appends_manifest(self):
        url = "https://uni.example/program"
        body = "<html>Такси: 900 лв.</html>".encode("utf-8")
        fetcher, session, clock = live_fetcher(self.store, {
            url: [FakeResponse(200, body,
                               {"Content-Type": "text/html; charset=utf-8"},
                               url=url)],
        })
        ref = fetcher.fetch(url, {"label": "spec-page"})
        self.assertTrue(ref.ok)
        self.assertFalse(ref.from_cache)
        self.assertEqual(ref.status, 200)
        self.assertEqual(ref.sha256, sha256(body))
        self.assertEqual(ref.attempts, 1)
        self.assertIsNone(ref.canonical_sha256)
        self.assertEqual(Path(ref.path).read_bytes(), body)
        self.assertEqual(Path(ref.path).name, ref.sha256)
        recs = self.manifest_lines()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["kind"], "fetch")
        self.assertEqual(rec["sha256"], ref.sha256)
        self.assertIsNone(rec["canonical_sha256"])  # hash pair reserved
        self.assertEqual(rec["label"], "spec-page")
        self.assertIn("permissive", rec["robots"])   # robots 404 recorded

    def test_cache_first_second_fetch_hits_store_not_network(self):
        url = "https://uni.example/page"
        fetcher, session, clock = live_fetcher(self.store, {
            url: [FakeResponse(200, b"stable body", {}, url=url)],
        })
        first = fetcher.fetch(url)
        second = fetcher.fetch(url)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(second.sha256, first.sha256)
        self.assertEqual(len(session.calls_for(url)), 1,
                         "cache-first: the network is asked at most once")
        self.assertEqual(len(self.manifest_lines()), 1,
                         "cache hits are reads, not manifest events")
        # and a NEW fetcher over the same dir resumes from disk (killed-run
        # checkpoint discipline): no network at all
        fetcher2, session2, _ = live_fetcher(SnapshotStore(self._tmp), {})
        resumed = fetcher2.fetch(url)
        self.assertTrue(resumed.from_cache)
        self.assertEqual(session2.calls, [])

    def test_per_site_cookies_sent_and_split_cache_identity(self):
        url = "https://www.aubg.example/bachelor-degrees/"
        with_cookie = FakeResponse(200, b"<html>degree page</html>", {}, url=url)
        fetcher, session, clock = live_fetcher(self.store, {
            url: [with_cookie],
        })
        cfg = {"cookies": {"aubg_location": "bulgaria"}}
        ref = fetcher.fetch(url, cfg)
        self.assertEqual(session.calls_for(url)[0]["cookies"],
                         {"aubg_location": "bulgaria"})
        # same URL, no cookies -> different snapshot identity -> network again
        fetcher.fetch(url)
        self.assertEqual(len(session.calls_for(url)), 2)
        # while the cookie'd identity stays cached
        again = fetcher.fetch(url, cfg)
        self.assertTrue(again.from_cache)
        self.assertEqual(again.sha256, ref.sha256)

    def test_one_retry_with_backoff_on_5xx_then_success(self):
        url = "https://slow.example/doc.pdf"
        fetcher, session, clock = live_fetcher(self.store, {
            url: [FakeResponse(503, b"busy", {}, url=url),
                  FakeResponse(200, b"%PDF-1.7 fee order",
                               {"Content-Type": "application/pdf"}, url=url)],
        }, retry_backoff=2.0)
        ref = fetcher.fetch(url)
        self.assertTrue(ref.ok)
        self.assertEqual(ref.status, 200)
        self.assertEqual(ref.attempts, 2)
        self.assertEqual(len(session.calls_for(url)), 2)
        self.assertIn(2.0, clock.sleeps, "backoff slept between attempts")
        self.assertEqual(len(self.manifest_lines()), 1,
                         "one fetch event even when it took the retry")

    def test_one_retry_on_timeout_then_success(self):
        url = "https://flaky.example/page"
        fetcher, session, clock = live_fetcher(self.store, {
            url: [requests.Timeout("read timed out"),
                  FakeResponse(200, b"recovered", {}, url=url)],
        })
        ref = fetcher.fetch(url)
        self.assertTrue(ref.ok)
        self.assertEqual(ref.attempts, 2)
        self.assertEqual(ref.sha256, sha256(b"recovered"))

    def test_5xx_twice_recorded_honestly_and_never_cached(self):
        url = "https://down.example/page"
        fetcher, session, clock = live_fetcher(self.store, {
            url: [FakeResponse(503, b"maintenance", {}, url=url),
                  FakeResponse(503, b"maintenance", {}, url=url),
                  FakeResponse(200, b"back up", {}, url=url)],
        })
        ref = fetcher.fetch(url)
        self.assertEqual(ref.status, 503)
        self.assertEqual(ref.attempts, 2)
        self.assertFalse(ref.ok)
        self.assertEqual(len(session.calls_for(url)), 2, "exactly ONE retry")
        # a 5xx never satisfies cache-first: next fetch goes out again
        ref2 = fetcher.fetch(url)
        self.assertTrue(ref2.ok)
        self.assertFalse(ref2.from_cache)
        self.assertEqual(len(self.manifest_lines()), 2)

    def test_timeout_twice_is_an_error_record_not_a_snapshot(self):
        url = "https://dead.example/page"
        fetcher, session, clock = live_fetcher(self.store, {
            url: [requests.Timeout("t1"), requests.Timeout("t2")],
        })
        ref = fetcher.fetch(url)
        self.assertIsNone(ref.status)
        self.assertIsNone(ref.sha256)
        self.assertIsNone(ref.path)
        self.assertEqual(ref.attempts, 2)
        self.assertIn("Timeout", ref.error)
        with self.assertRaises(SnapshotMiss):
            ref.read_bytes()
        rec = self.manifest_lines()[0]     # failure still recorded (audit)
        self.assertIn("Timeout", rec["error"])
        self.assertIsNone(self.store.lookup(url), "errors are not cacheable")

    def test_non_retryable_transport_error_single_attempt(self):
        url = "https://refused.example/page"
        fetcher, session, clock = live_fetcher(self.store, {
            url: [requests.ConnectionError("connection refused")],
        })
        ref = fetcher.fetch(url)
        self.assertEqual(ref.attempts, 1,
                         "retry is for 5xx/timeout only (brief), not for "
                         "arbitrary transport errors")
        self.assertIn("ConnectionError", ref.error)
        self.assertEqual(len(session.calls_for(url)), 1)

    def test_404_is_a_snapshot_and_is_cached_as_page_gone_evidence(self):
        url = "https://uni.example/removed-program"
        fetcher, session, clock = live_fetcher(self.store, {
            url: [FakeResponse(404, b"<html>not found</html>",
                               {"Content-Type": "text/html"}, url=url)],
        })
        ref = fetcher.fetch(url)
        self.assertEqual(ref.status, 404)
        self.assertFalse(ref.ok)
        self.assertIsNotNone(ref.sha256, "404 body is evidence, stored")
        cached = fetcher.fetch(url)
        self.assertTrue(cached.from_cache)
        self.assertEqual(len(session.calls_for(url)), 1)

    def test_robots_disallow_blocks_and_is_recorded(self):
        host = "https://closed.example"
        url = host + "/private/page"
        fetcher, session, clock = live_fetcher(self.store, {
            host + "/robots.txt": [FakeResponse(
                200, text="User-agent: *\nDisallow: /private/\n")],
        })
        ref = fetcher.fetch(url)
        self.assertEqual(ref.error, "blocked by robots.txt")
        self.assertIsNone(ref.status)
        self.assertEqual(ref.attempts, 0)
        self.assertIn("parsed", ref.robots)
        self.assertEqual(session.calls_for(url), [],
                         "blocked URL itself never requested")
        rec = self.manifest_lines()[0]
        self.assertEqual(rec["error"], "blocked by robots.txt")
        # robots decision cached per host: second try re-blocks w/o refetch
        fetcher.fetch(url)
        self.assertEqual(len(session.calls_for(host + "/robots.txt")), 1)

    def test_robots_404_permissive_and_decision_travels_in_manifest(self):
        url = "https://open.example/page"
        fetcher, session, clock = live_fetcher(self.store, {
            url: [FakeResponse(200, b"ok", {}, url=url)],
        })
        ref = fetcher.fetch(url)
        self.assertTrue(ref.ok)
        self.assertIn("404", ref.robots)
        self.assertIn("permissive", ref.robots)
        self.assertEqual(self.manifest_lines()[0]["robots"], ref.robots)

    def test_politeness_one_per_second_per_host_monotonic(self):
        a1 = "https://uni.example/a"
        a2 = "https://uni.example/b"
        b1 = "https://other.example/c"
        fetcher, session, clock = live_fetcher(self.store, {
            a1: [FakeResponse(200, b"a1", {}, url=a1)],
            a2: [FakeResponse(200, b"a2", {}, url=a2)],
            b1: [FakeResponse(200, b"b1", {}, url=b1)],
        }, min_interval=1.05)
        fetcher.fetch(a1)   # robots + body on uni.example
        fetcher.fetch(a2)   # second body on same host -> must wait
        fetcher.fetch(b1)   # different host -> own budget
        same_host = [c["t"] for c in session.calls
                     if "uni.example" in c["url"]]
        self.assertEqual(len(same_host), 3)  # robots, a1, a2
        for earlier, later in zip(same_host, same_host[1:]):
            self.assertGreaterEqual(
                later - earlier, 1.0,
                "two hits on one host closer than 1 s: %r" % same_host)
        # cross-host does not inherit the wait: other.example's robots call
        # happened immediately after its throttle check, with no sleep due
        # to uni.example's clock
        self.assertTrue(all(s > 0 for s in clock.sleeps))

    def test_default_session_wears_the_real_browser_ua(self):
        fetcher = LiveFetcher(self.store)   # builds its own session; no IO
        self.assertEqual(fetcher.session.headers["User-Agent"], UA)
        self.assertTrue(UA.startswith("Mozilla/5.0"))
        self.assertIn("Accept-Language", fetcher.session.headers)


# ------------------------------------------------- manifest schema & append-only
class TestManifestAndCanonicalHashPair(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="store-test-")
        self.store = SnapshotStore(self._tmp)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def fetch_one(self, url="https://uni.example/p", body=b"<html>x</html>"):
        fetcher, _, _ = live_fetcher(self.store, {
            url: [FakeResponse(200, body,
                               {"Content-Type": "text/html"}, url=url)],
        })
        return fetcher.fetch(url)

    def test_record_canonical_appends_and_lookup_merges_hash_pair(self):
        ref = self.fetch_one()
        self.assertIsNone(ref.canonical_sha256)
        raw_line_before = self.store.manifest_path.read_text().splitlines()[0]

        canonical = sha256(b"canonical text of the rendering")
        self.store.record_canonical(ref.sha256, canonical,
                                    renderer_id="bs4-lxml-canonical",
                                    renderer_version="4.12.3")
        lines = self.store.manifest_path.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], raw_line_before,
                         "append-only: the fetch record is never edited")
        canon_rec = json.loads(lines[1])
        self.assertEqual(canon_rec["kind"], "canonical")
        self.assertEqual(canon_rec["sha256"], ref.sha256)
        self.assertEqual(canon_rec["canonical_sha256"], canonical)
        self.assertEqual(canon_rec["renderer_id"], "bs4-lxml-canonical")

        # the pair is merged on lookup — including by a FRESH store over the
        # same dir (what the change-detection pass will do next run)
        merged = self.store.lookup(ref.url)
        self.assertEqual(merged.canonical_sha256, canonical)
        reopened = SnapshotStore(self._tmp).lookup(ref.url)
        self.assertEqual(reopened.canonical_sha256, canonical)
        self.assertEqual(reopened.sha256, ref.sha256)

    def test_identical_bodies_dedupe_to_one_content_addressed_file(self):
        body = b"<html>same bytes</html>"
        u1, u2 = "https://uni.example/p1", "https://uni.example/p2"
        fetcher, _, _ = live_fetcher(self.store, {
            u1: [FakeResponse(200, body, {}, url=u1)],
            u2: [FakeResponse(200, body, {}, url=u2)],
        })
        r1, r2 = fetcher.fetch(u1), fetcher.fetch(u2)
        self.assertEqual(r1.sha256, r2.sha256)
        self.assertEqual(r1.path, r2.path)
        bodies = list((Path(self._tmp) / "bodies").iterdir())
        self.assertEqual([p.name for p in bodies], [r1.sha256])
        self.assertEqual(len(self.store.manifest_path.read_text()
                             .splitlines()), 2, "two events, one body")

    def test_truncated_tail_from_killed_run_is_survivable(self):
        ref = self.fetch_one()
        # simulate a run killed mid-append: garbage half-line, no newline
        with open(self.store.manifest_path, "a", encoding="utf-8") as f:
            f.write('{"kind": "fetch", "url": "https://uni.example/killed')
        resumed = SnapshotStore(self._tmp)
        self.assertIsNotNone(resumed.lookup(ref.url),
                             "intact records survive a truncated tail")
        resumed.record_canonical(ref.sha256, sha256(b"c"), "bs4", "1")
        lines = resumed.manifest_path.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        json.loads(lines[2])  # the append landed on its own clean line
        self.assertEqual(json.loads(lines[2])["kind"], "canonical")

    def test_fetch_records_keep_both_hash_fields_always(self):
        """The hash-pair schema is present even on freshly written records
        (canonical explicitly null until the render module fills it)."""
        self.fetch_one()
        rec = json.loads(self.store.manifest_path.read_text().splitlines()[0])
        for key in ("sha256", "canonical_sha256", "url", "final_url",
                    "status", "bytes", "content_type", "retrieved_at",
                    "robots", "cookies", "attempts", "error", "elapsed_s"):
            self.assertIn(key, rec)
        self.assertIsNone(rec["canonical_sha256"])


if __name__ == "__main__":
    unittest.main()
