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
