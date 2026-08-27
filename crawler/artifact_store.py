"""The artifact store — the ONE snapshot -> Artifact resolver (ADR-0002).

Port of spike A's ``doc_text_for`` (+ ``tsv_artifact_text``, which lives in
crawler.render): given a document the config names, resolve the exact
Artifact provenance will be checked against, with renderer identity riding
along. Resolution knowledge — which renderer, which vendored rendering,
which working surfaces — lives HERE, never in the gate (whose purity is the
whole point) and never in callers (whose path-guessing caused the measured
15-null wrong-artifact incident). This module and crawler.render are the
only places Artifacts are constructed outside tests; the ADR-0002 grep test
enforces that by module name.

Two fetch adapters feed it (same ``fetch(url, site_config) -> SnapshotRef``
seam as crawler.store):

  LiveFetcher / ReplayFetcher   crawler.store's own adapters over the
                                content-addressed snapshot store
  SpikeCacheFetcher             the STA-78 spike-A cache replay adapter:
                                reads spike A's ``cache/`` layout
                                (<safe-tail>.<sha1(url+cookies)[:16]> body +
                                sibling .meta.json) — zero network by
                                construction (no session, no transport)

Replay renderings: with ``replay_out`` set (spike A's ``out/`` dir), PDF
routes are resolved from the vendored spike renderings instead of invoking
poppler/Docling — ``out/pdftext/<source-id>.txt`` for prose-pdf (composed
through crawler.render.compose_prose_text, the one composite rule) and
``out/docling/<source-id>/*.tsv`` for table-pdf (the ACTUAL per-table TSV
files the column-aware resolver reads). Their renderer_version is recorded
as "spike-a-vendored": honest identity for a replayed rendering. HTML is
always re-rendered from snapshot bytes with the pinned bs4 renderer — that
path is deterministic and offline already.

Every resolved document is registered by its artifact ref, so the runner
can hand cascade emissions' ``artifact_ref`` back to ``artifact(ref)`` and
gate against exactly the rendering the extractor read.
"""
import hashlib
import json
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from crawler import render as _render
from crawler.provenance import Artifact
from crawler.store import LiveFetcher, SnapshotMiss, SnapshotRef

__all__ = [
    "ReplayMiss",
    "ResolveError",
    "ResolvedDoc",
    "SpikeCacheFetcher",
    "ArtifactStore",
    "REPLAY_RENDERER_VERSION",
]

# Renderer version recorded on Artifacts rebuilt from spike A's vendored
# out/ renderings (the producing tools' own versions live in the spike's
# evidence trail; what matters here is that a replayed rendering says so).
REPLAY_RENDERER_VERSION = "spike-a-vendored"


class ReplayMiss(KeyError):
    """A vendored replay rendering is missing for a document the config
    names — a fixture/config bug that must fail loudly, never null."""


class ResolveError(Exception):
    """A document could not be resolved into an Artifact (failed fetch,
    unroutable snapshot). Carries enough detail for the run report."""


class SpikeCacheFetcher:
    """Replay adapter over spike A's cache/ directory (STA-78).

    Spike layout, ported from spikes/a/fetch.py: body at
    ``<safe-tail>.<sha1(url + "|" + sorted-cookie-json)[:16]>`` with a
    sibling ``.meta.json`` carrying url/status/content_type/ts. Speaks the
    same ``fetch(url, site_config) -> SnapshotRef`` interface as the store
    adapters; a miss raises SnapshotMiss (fixture/config bug — loud).

    Deliberately holds NO session and imports no transport: nothing on
    this object can perform IO beyond reading the cache dir.
    """

    def __init__(self, cache_dir):
        self.root = Path(cache_dir)
        if not self.root.is_dir():
            raise SnapshotMiss(
                "spike cache dir does not exist: {0}".format(self.root))

    def _cache_path(self, url, cookies):
        key = url + "|" + json.dumps(cookies or {}, sort_keys=True)
        digest = hashlib.sha1(key.encode()).hexdigest()[:16]
        tail = urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] \
            or "index"
        safe = "".join(c if c.isalnum() or c in "._-" else "_"
                       for c in urllib.parse.unquote(tail))[:60]
        return self.root / "{0}.{1}".format(safe, digest)

    def fetch(self, url, site_config=None):
        cfg = site_config or {}
        cookies = cfg.get("cookies") or {}
        body_path = self._cache_path(url, cookies)
        meta_path = body_path.with_suffix(body_path.suffix + ".meta.json")
        if not body_path.exists() or not meta_path.exists():
            raise SnapshotMiss(
                "no spike-cache snapshot for {0!r} with cookies {1} "
                "(expected {2})".format(url, json.dumps(cookies,
                                                        sort_keys=True),
                                        body_path))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        body = body_path.read_bytes()
        return SnapshotRef(
            url=url,
            final_url=meta.get("final_url") or url,
            status=meta.get("status"),
            sha256=hashlib.sha256(body).hexdigest(),
            canonical_sha256=None,
            bytes=len(body),
            content_type=meta.get("content_type", ""),
            retrieved_at=meta.get("ts", ""),
            path=str(body_path),
            from_cache=True,
            robots=meta.get("robots", ""),
            error=meta.get("error"),
            attempts=1,
        )


@dataclass(frozen=True)
class ResolvedDoc:
    """One resolved document: the Artifact plus everything the runner
    needs to build cascade sources and provenance records.

    ref           the artifact ref cascade emissions will carry
    route         the rendering route that produced it
    artifact      the store-constructed Artifact (gate input)
    source_url    the fetched URL (Provenance.source_url)
    retrieved_at  UTC fetch timestamp of the snapshot (Provenance)
    sha256        content address of the raw snapshot bytes
    layout        raw pdftotext -layout text (prose-pdf only) — the
                  line-anchored working surface ordinance joins need
    tables        parsed cell grids (table-pdf only) — per-table tuples of
                  row tuples of normalized cell strings, the column-aware
                  resolver's input; grid_artifact_text(tables) is exactly
                  artifact.text
    """
    ref: str
    route: str
    artifact: Artifact
    source_url: str
    retrieved_at: str
    sha256: Optional[str]
    layout: Optional[str] = None
    tables: Optional[Tuple] = None


class ArtifactStore:
    """Snapshot -> Artifact resolution + the ref registry for gating.

    fetcher      any adapter with fetch(url, site_config) -> SnapshotRef
    replay_out   spike A's out/ dir: resolve PDF routes from the vendored
                 renderings (out/pdftext, out/docling) instead of invoking
                 poppler/Docling — the fully-offline replay path
    docling_url  Docling Serve base URL (live table-pdf only)
    """

    def __init__(self, fetcher, replay_out=None,
                 docling_url=_render.DOCLING_URL,
                 backoff_s=_render.DOCLING_RETRY_BACKOFF_S):
        self.fetcher = fetcher
        self.replay_out = Path(replay_out) if replay_out is not None else None
        self.docling_url = docling_url
        self.backoff_s = backoff_s
        self._docs = {}   # ref -> ResolvedDoc

    # ------------------------------------------------------------ registry
    def artifact(self, ref):
        # type: (str) -> Artifact
        """The Artifact a cascade emission's artifact_ref names. KeyError
        for a ref this store never resolved — a wiring bug, not a null."""
        return self._docs[ref].artifact

    def doc(self, ref):
        # type: (str) -> ResolvedDoc
        return self._docs[ref]

    # ------------------------------------------------------------- resolve
    def resolve(self, url, route, cookies=None, source_id=None, label="",
                want_grid=False):
        # type: (str, str, Optional[dict], Optional[str], str, bool) -> ResolvedDoc
        """Fetch (or replay) one document and resolve its Artifact.

        source_id names the config source (and, in replay, the vendored
        rendering: out/pdftext/<source_id>.txt, out/docling/<source_id>/).
        Idempotent per ref within a store instance.

        want_grid (html route only): also parse <table> elements into
        cell grids from the SAME stripped soup as the canonical text —
        text + optional grid under one ref. Opt-in, so a plain html
        page never surprises a caller with tables (the runner sets it
        from the config join's kind; fill-rate ticket 01).
        """
        ref = self._ref(url, route, source_id)
        if ref in self._docs:
            cached = self._docs[ref]
            if not (want_grid and route == _render.ROUTE_HTML
                    and cached.tables is None):
                return cached

        snap = self.fetcher.fetch(url, {"cookies": cookies or {},
                                        "label": label})
        if not snap.ok:
            raise ResolveError(
                "fetch failed for {0!r}: status={1} error={2!r}".format(
                    url, snap.status, snap.error))

        layout = None
        tables = None
        if route == _render.ROUTE_HTML and want_grid:
            text, mode, grids = _render.html_text_and_grids(
                snap.read_bytes())
            artifact = Artifact(
                text=text,
                renderer_id=_render.RENDERER_HTML + ":" + mode,
                renderer_version=_render.HTML_RENDERER_VERSION,
                ref=ref)
            tables = grids or None
        elif route == _render.ROUTE_HTML:
            artifact = _render.render(snap.read_bytes(), snap.content_type,
                                      _render.ROUTE_HTML, ref=ref)
        elif route == _render.ROUTE_PROSE_PDF:
            artifact, layout = self._resolve_prose_pdf(snap, ref, source_id)
        elif route == _render.ROUTE_TABLE_PDF:
            artifact, tables = self._resolve_table_pdf(snap, ref, source_id)
        elif route == _render.ROUTE_SPREADSHEET:
            artifact, tables = self._resolve_spreadsheet(snap, ref)
        else:
            raise ResolveError("unknown route {0!r} for {1!r}".format(
                route, url))

        self._record_canonical(snap, artifact)
        doc = ResolvedDoc(ref=ref, route=route, artifact=artifact,
                          source_url=url, retrieved_at=snap.retrieved_at,
                          sha256=snap.sha256, layout=layout, tables=tables)
        self._docs[ref] = doc
        return doc

    @staticmethod
    def _ref(url, route, source_id):
        key = source_id or url
        if route == _render.ROUTE_PROSE_PDF:
            return "pdftext:" + key
        if route == _render.ROUTE_TABLE_PDF:
            return "docling-tsv:" + key
        if route == _render.ROUTE_SPREADSHEET:
            return "xlsx:" + key
        return "html:" + url

    # ----------------------------------------------------------- PDF routes
    def _replay_name(self, ref, route, source_id):
        if source_id is None:
            raise ReplayMiss(
                "replaying route {0!r} needs a config source id naming the "
                "vendored spike rendering (got none for {1!r})".format(
                    route, ref))
        return source_id

    def _resolve_prose_pdf(self, snap, ref, source_id):
        if self.replay_out is not None:
            name = self._replay_name(ref, _render.ROUTE_PROSE_PDF, source_id)
            path = self.replay_out / "pdftext" / (name + ".txt")
            if not path.exists():
                raise ReplayMiss(
                    "no vendored pdftotext rendering: {0}".format(path))
            raw = path.read_text(errors="ignore")
            text = _render.compose_prose_text(raw)
            version = REPLAY_RENDERER_VERSION
        else:
            text, raw, version = _render.prose_pdf_texts(snap.read_bytes())
        artifact = Artifact(text=text,
                            renderer_id=_render.RENDERER_PROSE_PDF,
                            renderer_version=version, ref=ref)
        return artifact, raw

    def _resolve_spreadsheet(self, snap, ref):
        """One table per sheet, in workbook order. No replay branch: an
        .xlsx is already its own machine-readable rendering, so unlike
        the PDF routes there is no vendored spike output to replay from
        -- the bytes ARE the source of truth in both modes."""
        grids = _render.spreadsheet_grids(snap.read_bytes())
        artifact = Artifact(text=_render.grid_artifact_text(grids),
                            renderer_id=_render.RENDERER_SPREADSHEET,
                            renderer_version=_render.SPREADSHEET_RENDERER_VERSION,
                            ref=ref)
        return artifact, grids

    def _resolve_table_pdf(self, snap, ref, source_id):
        if self.replay_out is not None:
            name = self._replay_name(ref, _render.ROUTE_TABLE_PDF, source_id)
            tsv_dir = self.replay_out / "docling" / name
            paths = sorted(tsv_dir.glob("*.tsv"))
            if not paths:
                raise ReplayMiss(
                    "no vendored docling TSV files in {0}".format(tsv_dir))
            grids = _render.table_grids_from_tsv(
                [p.read_text() for p in paths])
            # table_grids_from_tsv applies the same homoglyph fold a live
            # Docling grid gets (crawler/render.py) — record that in
            # identity too, distinct from prose-pdf's REPLAY_RENDERER_VERSION
            # use (prose-pdf never touches OCR, so it never folds).
            version = "{0}+homoglyph-fold={1}".format(
                REPLAY_RENDERER_VERSION, _render.HOMOGLYPH_FOLD_VERSION)
        else:
            grids = _render.docling_grids(snap.read_bytes(),
                                          self.docling_url, self.backoff_s)
            version = "{0}+homoglyph-fold={1}".format(
                _render.DOCLING_IMAGE_TAG, _render.HOMOGLYPH_FOLD_VERSION)
        artifact = Artifact(text=_render.grid_artifact_text(grids),
                            renderer_id=_render.RENDERER_TABLE_PDF,
                            renderer_version=version, ref=ref)
        return artifact, grids

    # ------------------------------------------------------- canonical hash
    def _record_canonical(self, snap, artifact):
        """Fill in the raw/canonical hash pair (RFC Q5) when the fetcher
        writes a manifest (live runs). Replay adapters have no manifest —
        nothing to record, by design."""
        if isinstance(self.fetcher, LiveFetcher) and snap.sha256:
            canonical = hashlib.sha256(
                artifact.text.encode("utf-8")).hexdigest()
            self.fetcher.store.record_canonical(
                snap.sha256, canonical, artifact.renderer_id,
                artifact.renderer_version)
