"""The snapshot store — content-addressed, append-only (RFC v2 §2, Q1/Q6).

A Snapshot is the raw fetched bytes of one source at one retrieval time,
content-addressed and append-only — never edited, never re-rendered in place
(CONTEXT.md). This module owns the store layout and both fetch adapters:

    LiveFetcher(store).fetch(url, site_config)    -> SnapshotRef   (network)
    ReplayFetcher(store).fetch(url, site_config)  -> SnapshotRef   (dir only)

Both adapters speak the same interface, so everything downstream (render,
cascade, gate) is written once against ``fetch(url, site_config) ->
SnapshotRef`` and tested offline against a vendored snapshot dir with zero
network.

Store layout (a plain directory — a snapshot dir IS the replay adapter's
whole input):

    <root>/bodies/<sha256>     raw response bytes, content-addressed; a body
                               is written at most once (dedupe by digest)
    <root>/manifest.jsonl      append-only event log, one JSON object/line

Manifest records (kind="fetch") carry the HASH PAIR the change-detection
design needs (RFC Q5): ``sha256`` of the raw body, set at fetch time, and
``canonical_sha256`` of the canonical rendering, null at fetch time and
recorded later by the render module through ``record_canonical()`` — the
store just stores; it never renders. Because the manifest is append-only,
"filling in" the canonical hash means appending a kind="canonical" record
keyed by the raw sha256; lookups merge the newest one in.

Politeness lives inside the live adapter (ported from spike A's fetch.py,
hardened with spike B's body digest and the retry the spikes both lacked):
  - >= 1 s between requests to the same host, enforced on a per-host
    monotonic clock (robots.txt fetches count against the budget too);
  - robots.txt honored; HTTP >= 400 or unreachable robots => permissive,
    with the decision RECORDED in every manifest record ("robots" field);
  - real browser UA + bg Accept-Language on the default session;
  - per-site cookies from site_config — cookies are part of snapshot
    identity (the AUBG interstitial page differs with/without its cookie);
  - ONE retry with backoff on 5xx responses and timeouts, nothing else.

Cache-first: the live adapter consults the store before the network, so a
URL is fetched at most once per store unless force=True; failed fetches and
5xx responses are recorded but never satisfy the cache (they retry on the
next run), while 4xx snapshots DO satisfy it — a recorded 404 is evidence
for "page affirmatively gone" (Coverage, CONTEXT.md).

No LLM anywhere; given the same server responses everything here is
reproducible byte-for-byte.
"""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

__all__ = [
    "UA",
    "SnapshotMiss",
    "SnapshotRef",
    "SnapshotStore",
    "LiveFetcher",
    "ReplayFetcher",
]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MANIFEST_NAME = "manifest.jsonl"
BODIES_DIR = "bodies"


class SnapshotMiss(KeyError):
    """The replay adapter has no snapshot for (url, cookies).

    Raised instead of returning an error record: in replay mode a miss means
    the snapshot dir does not cover what the caller asked for — a fixture or
    config bug that must fail loudly, never a silent null.
    """


def _cookie_key(cookies):
    """Canonical cookie identity — cookies are part of snapshot identity."""
    return json.dumps(cookies or {}, sort_keys=True, ensure_ascii=False)


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True)
class SnapshotRef:
    """A reference to one snapshot (or one failed fetch) in the store.

    Fields mirror the manifest fetch-record schema:
      url               the URL as requested (cache identity, with cookies)
      final_url         URL after redirects (== url when unknown)
      status            HTTP status, or None when no response was obtained
      sha256            content address of the raw body (None on failure)
      canonical_sha256  hash of the canonical rendering, recorded later by
                        the render module via record_canonical(); None until
                        then — the raw/canonical HASH PAIR of RFC Q5
      bytes             body length in bytes (None on failure)
      content_type      Content-Type response header ("" on failure)
      retrieved_at      UTC timestamp of the network fetch that produced it
      path              absolute path of the body file (None on failure)
      from_cache        True when served from the store, not the network
      robots            recorded robots.txt decision note for this fetch
      error             transport error / "blocked by robots.txt" / None
      attempts          network attempts made (2 after the one retry;
                        0 when robots.txt blocked before any attempt)
    """
    url: str
    final_url: str
    status: Optional[int]
    sha256: Optional[str]
    canonical_sha256: Optional[str]
    bytes: Optional[int]
    content_type: str
    retrieved_at: str
    path: Optional[str]
    from_cache: bool
    robots: str
    error: Optional[str]
    attempts: int = 1

    @property
    def ok(self):
        """True for a usable snapshot (a response below 400, no error)."""
        return (self.error is None and self.status is not None
                and self.status < 400)

    def read_bytes(self):
        """The raw snapshot bytes this ref addresses."""
        if self.path is None:
            raise SnapshotMiss(
                "SnapshotRef for {0!r} has no body (error={1!r})".format(
                    self.url, self.error))
        return Path(self.path).read_bytes()


class SnapshotStore:
    """The content-addressed, append-only snapshot dir + manifest index.

    Owns layout, lookup and the append-only manifest. It does NOT fetch —
    LiveFetcher and ReplayFetcher are the two adapters that do.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.bodies = self.root / BODIES_DIR
        self.manifest_path = self.root / MANIFEST_NAME
        self.bodies.mkdir(parents=True, exist_ok=True)
        self._fetch_index = {}   # type: Dict[Tuple[str, str], dict]
        self._canonical = {}     # type: Dict[str, dict]
        self._tail_newline = True
        self._load()

    # ------------------------------------------------------------- manifest
    def _load(self):
        """Replay the manifest into memory, tolerating a killed writer.

        A run killed mid-append can leave a truncated final line; it is
        skipped (that fetch simply re-runs) and the next append repairs the
        missing newline so the log stays one-JSON-object-per-line.
        """
        if not self.manifest_path.exists():
            return
        raw = self.manifest_path.read_bytes()
        self._tail_newline = (not raw) or raw.endswith(b"\n")
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # truncated tail from a killed run
            if isinstance(rec, dict):
                self._index(rec)

    def _index(self, rec):
        if rec.get("kind") == "canonical":
            if rec.get("sha256"):
                self._canonical[rec["sha256"]] = rec
        else:
            self._fetch_index[(rec.get("url"),
                               _cookie_key(rec.get("cookies")))] = rec

    def _append(self, rec):
        with open(self.manifest_path, "a", encoding="utf-8") as f:
            if not self._tail_newline:
                f.write("\n")
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
        self._tail_newline = True
        self._index(rec)

    def record_fetch(self, rec):
        """Append one fetch event (network interactions only — cache and
        replay hits are reads, not events, and are never logged)."""
        rec.setdefault("kind", "fetch")
        self._append(rec)

    def record_canonical(self, sha256, canonical_sha256,
                         renderer_id="", renderer_version=""):
        """Record the canonical-text hash for a raw body (render module's
        entry point — the second half of the RFC Q5 hash pair). Append-only:
        this appends a kind="canonical" record; lookups merge the newest."""
        self._append({
            "kind": "canonical",
            "sha256": sha256,
            "canonical_sha256": canonical_sha256,
            "renderer_id": renderer_id,
            "renderer_version": renderer_version,
            "recorded_at": _utcnow(),
        })

    # --------------------------------------------------------------- bodies
    def body_path(self, sha256):
        return self.bodies / sha256

    def put_body(self, body):
        """Store raw bytes content-addressed; returns the sha256 address.

        Written at most once per digest (dedupe), via temp-file + atomic
        rename so a killed run never leaves a truncated body."""
        digest = hashlib.sha256(body).hexdigest()
        dest = self.body_path(digest)
        if not dest.exists():
            tmp = dest.with_name("{0}.tmp{1}".format(dest.name, os.getpid()))
            tmp.write_bytes(body)
            tmp.replace(dest)
        return digest

    # --------------------------------------------------------------- lookup
    def lookup(self, url, cookies=None):
        """Newest usable snapshot for (url, cookies), or None.

        Usable = a real HTTP response below 500 whose body is on disk.
        Error records and 5xx are never served from cache (they retry);
        4xx snapshots are (a recorded 404 is page-gone evidence)."""
        rec = self._fetch_index.get((url, _cookie_key(cookies)))
        if rec is None or not self._cacheable(rec):
            return None
        if not self.body_path(rec["sha256"]).exists():
            return None
        return self._ref(rec, from_cache=True)

    @staticmethod
    def _cacheable(rec):
        return (rec.get("error") is None and bool(rec.get("sha256"))
                and rec.get("status") is not None and rec["status"] < 500)

    def _ref(self, rec, from_cache):
        sha = rec.get("sha256")
        canonical = rec.get("canonical_sha256")
        if sha and sha in self._canonical:
            canonical = self._canonical[sha].get("canonical_sha256")
        return SnapshotRef(
            url=rec.get("url", ""),
            final_url=rec.get("final_url") or rec.get("url", ""),
            status=rec.get("status"),
            sha256=sha,
            canonical_sha256=canonical,
            bytes=rec.get("bytes"),
            content_type=rec.get("content_type") or "",
            retrieved_at=rec.get("retrieved_at", ""),
            path=str(self.body_path(sha)) if sha else None,
            from_cache=from_cache,
            robots=rec.get("robots", ""),
            error=rec.get("error"),
            attempts=rec.get("attempts", 1),
        )


class ReplayFetcher:
    """Second adapter: a snapshot dir, zero network — for tests, shadow runs
    and re-audits. Same ``fetch(url, site_config) -> SnapshotRef`` interface
    as LiveFetcher; a miss raises SnapshotMiss instead of touching the net.

    Deliberately holds NO session and imports no transport: there is nothing
    on this object that could perform IO beyond reading the snapshot dir.
    """

    def __init__(self, store):
        self.store = store if isinstance(store, SnapshotStore) \
            else SnapshotStore(store)

    def fetch(self, url, site_config=None):
        cfg = site_config or {}
        cookies = cfg.get("cookies") or {}
        ref = self.store.lookup(url, cookies)
        if ref is None:
            raise SnapshotMiss(
                "no snapshot for {0!r} with cookies {1} in {2}".format(
                    url, _cookie_key(cookies), self.store.root))
        return ref


class LiveFetcher:
    """Network adapter: cache-first polite fetcher writing into the store.

    site_config keys the store consumes (extra keys are other stages'
    business and are ignored here):
      cookies   per-site cookie dict — part of snapshot identity
      timeout   per-request timeout in seconds (default 60)
      label     free-form tag recorded in the manifest

    ``sleep`` and ``monotonic`` are injectable so politeness and backoff are
    testable without real waiting; production callers leave the defaults.
    """

    def __init__(self, store, session=None, min_interval=1.05, timeout=60,
                 retry_backoff=2.0, sleep=time.sleep,
                 monotonic=time.monotonic):
        self.store = store
        self.session = session if session is not None else _default_session()
        self.min_interval = min_interval
        self.timeout = timeout
        self.retry_backoff = retry_backoff
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_hit = {}   # host -> monotonic time of last request
        self._robots = {}     # host -> (parser or None, recorded note)

    # ---------------------------------------------------------------- fetch
    def fetch(self, url, site_config=None, force=False):
        cfg = site_config or {}
        cookies = cfg.get("cookies") or {}
        if not force:
            hit = self.store.lookup(url, cookies)
            if hit is not None:
                return hit

        allowed, robots_note = self._robots_ok(url)
        base = {
            "kind": "fetch",
            "url": url,
            "label": cfg.get("label", ""),
            "cookies": cookies,
            "robots": robots_note,
            "retrieved_at": _utcnow(),
        }
        if not allowed:
            rec = dict(base, status=None, final_url=url, sha256=None,
                       canonical_sha256=None, bytes=None, content_type="",
                       elapsed_s=0.0, attempts=0,
                       error="blocked by robots.txt")
            self.store.record_fetch(rec)
            return self.store._ref(rec, from_cache=False)

        host = urllib.parse.urlsplit(url).netloc
        timeout = cfg.get("timeout", self.timeout)
        t0 = self._monotonic()
        response, error, attempts = self._get_with_retry(
            url, cookies, timeout, host)
        elapsed = round(self._monotonic() - t0, 2)

        if response is None:
            rec = dict(base, status=None, final_url=url, sha256=None,
                       canonical_sha256=None, bytes=None, content_type="",
                       elapsed_s=elapsed, attempts=attempts, error=error)
        else:
            body = response.content
            sha = self.store.put_body(body)
            rec = dict(base, status=response.status_code,
                       final_url=getattr(response, "url", "") or url,
                       sha256=sha, canonical_sha256=None, bytes=len(body),
                       content_type=response.headers.get("Content-Type", ""),
                       elapsed_s=elapsed, attempts=attempts, error=None)
        self.store.record_fetch(rec)
        return self.store._ref(rec, from_cache=False)

    def _get_with_retry(self, url, cookies, timeout, host):
        """ONE retry with backoff, on 5xx responses and timeouts only.

        Returns (response, error, attempts); response is the last obtained
        response (possibly still 5xx — recorded honestly, never cached),
        error is set only when no response was obtained at all."""
        last_response, last_error, attempts = None, None, 0
        for attempt in (1, 2):
            attempts = attempt
            self._throttle(host)
            try:
                r = self.session.get(url, cookies=cookies, timeout=timeout,
                                     allow_redirects=True)
            except requests.Timeout as exc:
                last_response = None
                last_error = "{0}: {1}".format(type(exc).__name__, exc)
            except requests.RequestException as exc:
                # not 5xx, not a timeout -> not retryable
                return (None,
                        "{0}: {1}".format(type(exc).__name__, exc),
                        attempt)
            else:
                if r.status_code < 500:
                    return r, None, attempt
                last_response, last_error = r, None
            if attempt == 1:
                self._sleep(self.retry_backoff)
        return last_response, last_error, attempts

    # ----------------------------------------------------------- politeness
    def _throttle(self, host):
        """>= min_interval between any two requests to the same host,
        measured on a monotonic clock (spike A's proven implementation)."""
        now = self._monotonic()
        prev = self._last_hit.get(host)
        if prev is not None and now - prev < self.min_interval:
            self._sleep(self.min_interval - (now - prev))
        self._last_hit[host] = self._monotonic()

    def _robots_ok(self, url):
        """robots.txt decision for url, fetched once per host.

        HTTP >= 400 (the common 404) or an unreachable robots.txt is
        recorded as permissive — the decision travels in every manifest
        record so an audit can see WHY a fetch was allowed."""
        parts = urllib.parse.urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            robots_url = "{0}://{1}/robots.txt".format(
                parts.scheme or "https", host)
            self._throttle(host)
            try:
                r = self.session.get(robots_url, timeout=20)
                if r.status_code >= 400:
                    self._robots[host] = (
                        None,
                        "robots.txt HTTP {0} -> permissive".format(
                            r.status_code))
                else:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(r.text.splitlines())
                    self._robots[host] = (
                        rp, "robots.txt HTTP {0}, parsed".format(
                            r.status_code))
            except requests.RequestException as exc:
                self._robots[host] = (
                    None,
                    "robots.txt unreachable ({0}) -> permissive".format(
                        type(exc).__name__))
        rp, note = self._robots[host]
        return (rp.can_fetch(UA, url) if rp else True), note


def _default_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept-Language": "bg,en;q=0.8",
    })
    return session
