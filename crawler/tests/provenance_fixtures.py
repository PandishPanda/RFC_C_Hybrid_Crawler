"""Shared fixture loading for the provenance gate test suites.

This module is test infrastructure: it is the ONE place outside the (future)
artifact store that builds Artifact values, and it may do so only because it
lives under crawler/tests/ — the ADR-0002 grep test exempts this directory.

Fixtures were vendored from spike A (.scratch/sta-78/spikes/a) by a one-shot
build script that mirrored e3_extract.doc_text_for(): each golden record is
paired with the exact artifact text the spike's gate checked it against
(bs4-lxml canonical HTML text, pdftotext flow+layout text, or Docling
table-TSV text), with renderer identity recorded in artifacts-manifest.json.
"""
import hashlib
import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
CRAWLER_DIR = TESTS_DIR.parent
REPO_ROOT = CRAWLER_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crawler.provenance import Artifact  # noqa: E402

_manifest = None
_artifact_cache = {}


def manifest():
    global _manifest
    if _manifest is None:
        _manifest = json.loads(
            (FIXTURES_DIR / "artifacts-manifest.json").read_text())
    return _manifest


def load_artifact(ref):
    """Build the Artifact for a vendored fixture ref (store stand-in)."""
    if ref not in _artifact_cache:
        m = manifest()[ref]
        text = (FIXTURES_DIR / m["text_file"]).read_text()
        _artifact_cache[ref] = Artifact(
            text=text,
            renderer_id=m["renderer_id"],
            renderer_version=m["renderer_version"],
            ref=m["source_ref"],
        )
    return _artifact_cache[ref]


def artifact_sha256(ref):
    m = manifest()[ref]
    text = (FIXTURES_DIR / m["text_file"]).read_text()
    return hashlib.sha256(text.encode()).hexdigest()


def golden_records():
    data = json.loads((FIXTURES_DIR / "golden-records.json").read_text())
    return data["records"]


def synthetic_artifact(text, ref="synthetic"):
    """Artifact wrapper for hand-written regression texts (tests only)."""
    return Artifact(text=text, renderer_id="test-synthetic",
                    renderer_version="0", ref=ref)
