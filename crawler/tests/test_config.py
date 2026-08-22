"""Coverage for config.py's rsvu_id/rsvu_code fields (ticket 05) -- the
rest of the loader is already exercised end-to-end via test_replay.py
against the real crawler/configs/*.json files.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.config import ConfigError, parse_site_config  # noqa: E402

MINIMAL = {
    "uni_id": "X",
    "sources": {},
    "programs": [{"id": "x1", "name": "X1", "page": "https://x.example/p"}],
}



def _prog(**extra):
    prog = {"id": "x1", "name": "X1", "page": "https://x.example/p"}
    prog.update(extra)
    return dict(MINIMAL, programs=[prog])


class SuppressLabelsTest(unittest.TestCase):
    """suppress_labels: human-adjudicated per-program-field label
    suppression (stale-green verdicts as config data)."""

    def test_defaults_to_empty(self):
        cfg = parse_site_config(dict(MINIMAL))
        self.assertEqual(cfg.programs[0].suppress_labels, {})

    def test_accepts_field_to_label_ids(self):
        cfg = parse_site_config(_prog(
            suppress_labels={"tuition": ["en-semfee-label"]}))
        self.assertEqual(cfg.programs[0].suppress_labels,
                         {"tuition": ("en-semfee-label",)})

    def test_rejects_a_non_field_key(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_prog(suppress_labels={"fee": ["x"]}))

    def test_rejects_an_empty_id_list(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_prog(suppress_labels={"tuition": []}))

    def test_rejects_a_non_object(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_prog(suppress_labels=["en-semfee-label"]))


def _anchored_site(anchor_source, scope=None, page="https://x.example/p"):
    anchor = {"source": anchor_source, "pattern": "(степен [„\"][а-я]+[\"“])"}
    if scope is not None:
        anchor["scope"] = scope
    return {
        "uni_id": "X", "sources": {},
        "anchors": {"a1": anchor},
        "programs": [{"id": "x1", "name": "X1", "page": page,
                      "field_anchors": {"degree": "a1"}}],
    }


class AnchorScopeTest(unittest.TestCase):
    """An anchor aimed at a page other than the program's own must declare
    its scope: the measured MUVarna fabrication came from an anchor on an
    unrelated EU-project page shipping a plausible degree (2026-08-22).

    - own-page anchors need no declaration (scope is inherent);
    - off-page anchors: scope "names-program" (verified at resolve) or
      "page-wide" (the recorded human attestation, ADR-0003 escape hatch);
    - off-page without scope is a ConfigError at load, never a null at
      refresh."""

    def test_own_page_anchor_needs_no_scope(self):
        cfg = parse_site_config(_anchored_site("https://x.example/p"))
        self.assertEqual(cfg.anchors["a1"].scope, None)

    def test_off_page_anchor_without_scope_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            parse_site_config(_anchored_site("https://x.example/other"))
        self.assertIn("scope", str(ctx.exception))
        self.assertIn("a1", str(ctx.exception))

    def test_off_page_anchor_with_names_program_loads(self):
        cfg = parse_site_config(
            _anchored_site("https://x.example/other", scope="names-program"))
        self.assertEqual(cfg.anchors["a1"].scope, "names-program")

    def test_off_page_anchor_with_page_wide_loads(self):
        cfg = parse_site_config(
            _anchored_site("https://x.example/other", scope="page-wide"))
        self.assertEqual(cfg.anchors["a1"].scope, "page-wide")

    def test_unknown_scope_value_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_site_config(
                _anchored_site("https://x.example/other", scope="whatever"))


class AnchorScopeSourceIdTest(unittest.TestCase):
    def test_source_id_resolving_to_own_page_needs_no_scope(self):
        # SHU shape: the anchor addresses the program's own PDF via a
        # configured source id — same document, different addressing.
        cfg = parse_site_config({
            "uni_id": "X",
            "sources": {"own-pdf": {"url": "https://x.example/grp.pdf",
                                     "route": "prose-pdf"}},
            "anchors": {"a1": {"source": "own-pdf",
                               "pattern": "(степен [„\"][а-я]+[\"“])"}},
            "programs": [{"id": "x1", "name": "X1",
                          "page": "https://x.example/grp.pdf",
                          "field_anchors": {"degree": "a1"}}],
        })
        self.assertIsNone(cfg.anchors["a1"].scope)
