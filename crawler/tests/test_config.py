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


class RsvuIdTest(unittest.TestCase):
    def test_defaults_to_none(self):
        cfg = parse_site_config(dict(MINIMAL))
        self.assertIsNone(cfg.rsvu_id)

    def test_accepts_an_int(self):
        cfg = parse_site_config(dict(MINIMAL, rsvu_id=125))
        self.assertEqual(cfg.rsvu_id, 125)

    def test_rejects_a_non_int(self):
        with self.assertRaises(ConfigError):
            parse_site_config(dict(MINIMAL, rsvu_id="125"))

    def test_vum_config_carries_its_real_rsvu_id(self):
        from crawler.config import load_site_config
        cfg = load_site_config(
            Path(__file__).resolve().parents[1] / "configs" / "VUM.json")
        self.assertEqual(cfg.rsvu_id, 125)


class RsvuCodeTest(unittest.TestCase):
    def test_defaults_to_none(self):
        cfg = parse_site_config(dict(MINIMAL))
        self.assertIsNone(cfg.programs[0].rsvu_code)

    def test_accepts_a_string(self):
        data = dict(MINIMAL, programs=[dict(MINIMAL["programs"][0],
                                            rsvu_code="40600931")])
        cfg = parse_site_config(data)
        self.assertEqual(cfg.programs[0].rsvu_code, "40600931")

    def test_rejects_an_empty_string(self):
        data = dict(MINIMAL, programs=[dict(MINIMAL["programs"][0],
                                            rsvu_code="")])
        with self.assertRaises(ConfigError):
            parse_site_config(data)

    def test_vum_configs_carry_their_real_rsvu_codes(self):
        # Derived from the registry export, not pinned: the pinned
        # four-program set went stale the day VUM gained 11 programs
        # (2026-08-17). Invariants: every configured code exists in the
        # export, no two programs share a code, and the four ORIGINAL
        # programs keep their audited codes.
        from crawler.config import load_site_config
        from crawler.registry import load_captured_export
        cfg = load_site_config(
            Path(__file__).resolve().parents[1] / "configs" / "VUM.json")
        codes = {p.id: p.rsvu_code for p in cfg.programs if p.rsvu_code}
        export_codes = {r.code for r in load_captured_export("VUM").rows}
        self.assertEqual(set(codes.values()) - export_codes, set())
        self.assertEqual(len(set(codes.values())), len(codes),
                         "no two programs may claim one registry row")
        for pid, code in (("vum-sst", "40600931"), ("vum-gca", "30900371"),
                          ("vum-mba", "30700013"), ("vum-corr", "30806933")):
            self.assertEqual(codes[pid], code)


if __name__ == "__main__":
    unittest.main()
