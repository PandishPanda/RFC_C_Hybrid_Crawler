"""urls.json — the per-university URL export (url-scheme ticket 04).

The export is the frontend's whole contract: canonical PATHS (no
domain, no trailing slash), redirects derived from the retired_slugs
ledger by resolving IDS to their CURRENT slugs at export time (a second
rename must not strand the first redirect), and an honest "missing"
section — the export never invents a path. Written by publish() AFTER
the gates: a blocked publish leaves no fresh export.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import publish, urls  # noqa: E402
from crawler.config import parse_site_config  # noqa: E402
from crawler.tests.test_ledger import (  # noqa: E402
    FULL_COVERAGE,
    field_record,
    make_report,
)


def _site(programs, **top):
    data = {"uni_id": "TestUni", "sources": {}, "programs": programs}
    data.update(top)
    return parse_site_config(data)


def _prog(pid, **extra):
    prog = {"id": pid, "name": pid.upper(),
            "page": "https://x.example/" + pid}
    prog.update(extra)
    return prog


class BuildUrlsReportTest(unittest.TestCase):
    def test_canonical_paths(self):
        report = urls.build_urls_report(_site(
            [_prog("p1", slug="pravo", subject="pravo")],
            slug="uni-x"))
        self.assertEqual(report["university"],
                         {"slug": "uni-x", "path": "/uni-x"})
        self.assertEqual(report["programs"], [
            {"program_id": "p1", "slug": "pravo", "subject": "pravo",
             "path": "/uni-x/pravo"}])
        self.assertEqual(report["missing"], [])

    def test_a_double_rename_redirects_both_old_slugs_to_current(self):
        report = urls.build_urls_report(_site(
            [_prog("p1", slug="pravo-i-red")],
            slug="uni-x",
            retired_slugs={"pravo": "p1", "pravo-2": "p1"}))
        self.assertEqual(sorted(report["redirects"],
                                key=lambda r: r["from"]), [
            {"from": "/uni-x/pravo", "to": "/uni-x/pravo-i-red"},
            {"from": "/uni-x/pravo-2", "to": "/uni-x/pravo-i-red"}])

    def test_a_retired_uni_slug_fans_out_over_every_program(self):
        report = urls.build_urls_report(_site(
            [_prog("p1", slug="pravo"), _prog("p2", slug="himia")],
            slug="nov-slug", retired_slugs={"star-slug": "TestUni"}))
        self.assertEqual(sorted(report["redirects"],
                                key=lambda r: r["from"]), [
            {"from": "/star-slug", "to": "/nov-slug"},
            {"from": "/star-slug/himia", "to": "/nov-slug/himia"},
            {"from": "/star-slug/pravo", "to": "/nov-slug/pravo"}])

    def test_missing_slugs_ship_null_paths_never_invented_ones(self):
        report = urls.build_urls_report(_site(
            [_prog("p1", slug="pravo"), _prog("p2")], slug="uni-x"))
        by_id = {p["program_id"]: p for p in report["programs"]}
        self.assertEqual(by_id["p2"]["path"], None)
        self.assertEqual(report["missing"], ["p2"])

    def test_no_uni_slug_means_no_paths_at_all(self):
        report = urls.build_urls_report(_site(
            [_prog("p1", slug="pravo")]))
        self.assertIsNone(report["university"]["path"])
        self.assertEqual(report["programs"][0]["path"], None)
        self.assertEqual(report["missing"], ["p1"])

    def test_retired_uni_and_program_slugs_cross_product(self):
        # A wild URL can combine BOTH retirements (/old-uni/old-prog);
        # both mappings are in the ledger, so the redirect is derivable
        # without inventing anything (review ruling, ticket 04).
        report = urls.build_urls_report(_site(
            [_prog("p1", slug="pravo")], slug="nov-slug",
            retired_slugs={"star-slug": "TestUni", "staro-pravo": "p1"}))
        self.assertIn({"from": "/star-slug/staro-pravo",
                       "to": "/nov-slug/pravo"}, report["redirects"])

    def test_a_retired_program_slug_without_a_current_one_is_skipped(self):
        # Rollout gap: the ledger knows where "pravo" went, but p1 has
        # no current slug yet — no redirect target exists to invent.
        report = urls.build_urls_report(_site(
            [_prog("p1")], slug="uni-x", retired_slugs={"pravo": "p1"}))
        self.assertEqual(report["redirects"], [])


class PublishWritesUrlsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.site = _site(
            [_prog("p1", slug="pravo"), _prog("p2", slug="himia")],
            slug="uni-x")

    def _urls_path(self):
        return Path(self.dir) / "TestUni" / urls.URLS_REPORT_NAME

    def test_a_promoted_publish_writes_urls_json(self):
        result = publish.publish("TestUni", out_dir=self.dir,
                                 ledger_dir=self.dir, report=FULL_COVERAGE,
                                 academic_year="2026/2027", site=self.site)
        self.assertTrue(result["promoted"])
        data = json.loads(self._urls_path().read_text(encoding="utf-8"))
        self.assertEqual(data["university"]["path"], "/uni-x")
        self.assertEqual(data["generated_at"], result["generated_at"])

    def test_a_blocked_publish_leaves_no_fresh_export(self):
        publish.publish("TestUni", out_dir=self.dir, ledger_dir=self.dir,
                        report=FULL_COVERAGE, academic_year="2026/2027",
                        site=self.site)
        first = self._urls_path().read_text(encoding="utf-8")

        dropped = make_report({
            "p1": {"degree": field_record("PASS", "бакалавър")},
            "p2": {"degree": field_record("NULL_OK")},
        })
        result = publish.publish("TestUni", out_dir=self.dir,
                                 ledger_dir=self.dir, report=dropped,
                                 academic_year="2026/2027", site=self.site)
        self.assertFalse(result["promoted"])
        self.assertEqual(self._urls_path().read_text(encoding="utf-8"),
                         first)

    def test_publish_without_a_site_still_works(self):
        # report= injection without a config file (the pre-URL-scheme
        # test surface) — no export, no crash.
        result = publish.publish("TestUni", out_dir=self.dir,
                                 ledger_dir=self.dir, report=FULL_COVERAGE,
                                 academic_year="2026/2027")
        self.assertTrue(result["promoted"])
        self.assertFalse(self._urls_path().exists())


if __name__ == "__main__":
    unittest.main()
