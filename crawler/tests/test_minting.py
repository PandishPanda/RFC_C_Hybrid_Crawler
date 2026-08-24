"""`crawler slugs <UniID>` — the minting proposer (url-scheme ticket 03).

Proposes, never writes (ADR-0003): output is ready-to-paste fragments
plus loud flags for everything a human must decide — collisions,
reserved words, names that slug to nothing. Exit code 0 means "nothing
missing, nothing flagged", so the command doubles as the backfill's
completeness check.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import minting  # noqa: E402
from crawler.__main__ import main as cli_main  # noqa: E402
from crawler.config import parse_site_config  # noqa: E402


def _site(programs, **top):
    data = {"uni_id": "X", "sources": {}, "programs": programs}
    data.update(top)
    return data


def _prog(pid, name, **extra):
    prog = {"id": pid, "name": name, "page": "https://x.example/" + pid}
    prog.update(extra)
    return prog


class ProposeSlugsTest(unittest.TestCase):
    """The pure proposer over one SiteConfig."""

    def test_proposes_for_programs_missing_a_slug(self):
        cfg = parse_site_config(_site([
            _prog("x1", "Компютърни науки"),
            _prog("x2", "Право", slug="pravo")]))
        report = minting.propose_slugs(cfg)
        self.assertEqual(report["proposals"], [
            {"program_id": "x1", "name": "Компютърни науки",
             "slug": "kompyutarni-nauki"}])

    def test_flags_a_collision_between_two_proposals(self):
        cfg = parse_site_config(_site([
            _prog("x1", "Хотелски мениджмънт"),
            _prog("x2", "Хотелски мениджмънт")]))
        report = minting.propose_slugs(cfg)
        kinds = [f["kind"] for f in report["flags"]]
        self.assertIn("collision", kinds)
        flag = [f for f in report["flags"] if f["kind"] == "collision"][0]
        self.assertEqual(sorted(flag["program_ids"]), ["x1", "x2"])

    def test_flags_a_proposal_colliding_with_an_existing_slug(self):
        cfg = parse_site_config(_site([
            _prog("x1", "Право", slug="pravo"),
            _prog("x2", "Право")]))
        report = minting.propose_slugs(cfg)
        flag = [f for f in report["flags"] if f["kind"] == "collision"][0]
        self.assertEqual(sorted(flag["program_ids"]), ["x1", "x2"])

    def test_flags_a_reserved_word_proposal(self):
        cfg = parse_site_config(_site([_prog("x1", "Учебен план")]))
        report = minting.propose_slugs(cfg)
        self.assertEqual(report["flags"][0]["kind"], "reserved")

    def test_flags_a_name_that_slugs_to_nothing(self):
        cfg = parse_site_config(_site([_prog("x1", "(преустановена)")]))
        report = minting.propose_slugs(cfg)
        self.assertEqual(report["flags"][0]["kind"], "unsluggable")
        self.assertEqual(report["proposals"], [])

    def test_uni_placeholder_is_marked_for_human_judgment(self):
        cfg = parse_site_config(_site([_prog("x1", "Право", slug="pravo")]))
        report = minting.propose_slugs(cfg)
        self.assertTrue(report["university"]["needs_human"])
        self.assertEqual(report["university"]["placeholder"], "x")

    def test_missing_display_name_is_not_complete(self):
        cfg = parse_site_config(_site(
            [_prog("x1", "Право", slug="pravo")], slug="uni-x"))
        report = minting.propose_slugs(cfg)
        self.assertFalse(report["complete"])
        self.assertTrue(report["university"]["needs_human"])
        self.assertIn("display_name", report["university"]["missing"])

    def test_complete_config_reports_complete(self):
        cfg = parse_site_config(_site(
            [_prog("x1", "Право", slug="pravo")],
            slug="uni-x", display_name="Университет X"))
        report = minting.propose_slugs(cfg)
        self.assertTrue(report["complete"])
        self.assertEqual(report["proposals"], [])
        self.assertEqual(report["flags"], [])


class SlugsCliTest(unittest.TestCase):
    def _run(self, site):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / (site["uni_id"] + ".json")
        path.write_text(json.dumps(site, ensure_ascii=False),
                        encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli_main(["slugs", site["uni_id"],
                             "--configs", tmp.name])
        return code, out.getvalue() + err.getvalue()

    def test_prints_pasteable_proposals_and_exits_nonzero(self):
        code, output = self._run(_site([_prog("x1", "Компютърни науки")]))
        self.assertNotEqual(code, 0)
        self.assertIn('"slug": "kompyutarni-nauki"', output)
        self.assertIn("x1", output)

    def test_flags_collisions_with_both_ids(self):
        code, output = self._run(_site([
            _prog("x1", "Хотелски мениджмънт"),
            _prog("x2", "Хотелски мениджмънт")]))
        self.assertNotEqual(code, 0)
        self.assertIn("COLLISION", output)
        self.assertIn("x1", output)
        self.assertIn("x2", output)

    def test_exits_zero_when_nothing_is_missing_or_flagged(self):
        code, output = self._run(_site(
            [_prog("x1", "Право", slug="pravo")],
            slug="uni-x", display_name="Университет X"))
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
