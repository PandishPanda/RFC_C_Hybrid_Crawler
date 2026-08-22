"""Version-pinned source detection (ticket 22) -- zero network.

The list-page fixture is CAPTURED from the real e-curriculum listing
(crawler/tests/fixtures_ecurriculum_list.html), per the ticket: a test
that fetches would prove nothing about parsing on a day the site is down.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import staleness  # noqa: E402
from crawler.config import load_configs_dir, parse_site_config  # noqa: E402

FIXTURE = (Path(__file__).parent / "fixtures_ecurriculum_list.html"
           ).read_text(encoding="utf-8")
PLAN = "https://e-curriculum.uni-ruse.bg/app/View/Curriculum?code={0}&version={1}"


def site_with_anchor(code, version):
    return parse_site_config({
        "uni_id": "X", "sources": {},
        "anchors": {"a1": {"source": PLAN.format(code, version),
                           "pattern": r'ОКС\s*"([^"]+)"',
                           "scope": "names-program"}},
        "programs": [{"id": "p", "name": "P", "page": "https://x/p",
                      "field_anchors": {"degree": "a1"}}],
    }, origin="<t>")


class ListedVersionsTest(unittest.TestCase):
    def test_parses_the_real_captured_listing(self):
        got = staleness.listed_versions(FIXTURE)
        self.assertEqual(got["CB3.7.4.1"], "5")
        self.assertEqual(got["CB5.2.1.1"], "6")
        self.assertEqual(got["CB3.4.1.1"], "3")

    def test_an_empty_or_junk_page_yields_nothing_rather_than_raising(self):
        for text in ("", None, "<html><body>no plans</body></html>"):
            self.assertEqual(staleness.listed_versions(text), {})


class PinnedSourcesTest(unittest.TestCase):
    def test_a_versioned_anchor_url_is_detected_with_its_config_path(self):
        found = staleness.pinned_sources(site_with_anchor("CB3.7.4.1", "5"))
        self.assertEqual(len(found), 1)
        where, _url, code, version = found[0]
        self.assertEqual((where, code, version), ("anchors['a1']",
                                                  "CB3.7.4.1", "5"))

    def test_an_unversioned_url_is_not_pinned(self):
        site = parse_site_config({
            "uni_id": "X", "sources": {},
            "programs": [{"id": "p", "name": "P",
                          "page": "https://x/p?code=CB1"}]}, origin="<t>")
        self.assertEqual(staleness.pinned_sources(site), [])

    def test_detection_is_generic_not_curriculum_specific(self):
        # Any configured URL carrying an explicit version is pinned,
        # whatever the site -- only the CURRENT-version lookup is
        # curriculum-specific.
        site = parse_site_config({
            "uni_id": "X",
            "sources": {"s": {"url": "https://other.example/f.pdf?version=2",
                              "route": "prose-pdf"}},
            "programs": [{"id": "p", "name": "P", "page": "https://x/p"}]},
            origin="<t>")
        found = staleness.pinned_sources(site)
        self.assertEqual([f[0] for f in found], ["sources['s']"])


class VersionDriftTest(unittest.TestCase):
    def setUp(self):
        self.current = staleness.listed_versions(FIXTURE)

    def test_a_pin_matching_the_listing_is_not_drift(self):
        site = site_with_anchor("CB3.7.4.1", "5")
        self.assertEqual(
            staleness.check_version_drift(site, self.current), [])

    def test_a_superseded_pin_is_reported_with_both_versions(self):
        site = site_with_anchor("CB3.7.4.1", "4")
        drift = staleness.check_version_drift(site, self.current)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["status"], "superseded")
        self.assertEqual((drift[0]["pinned_version"],
                          drift[0]["current_version"]), ("4", "5"))
        self.assertEqual(drift[0]["where"], "anchors['a1']")

    def test_a_pin_whose_code_vanished_is_reported_as_unknown(self):
        # Silence would read as "fine" for a plan that was withdrawn.
        site = site_with_anchor("CB9.9.9.9", "1")
        drift = staleness.check_version_drift(site, self.current)
        self.assertEqual(drift[0]["status"], "unknown")
        self.assertIsNone(drift[0]["current_version"])

    def test_drift_never_rewrites_the_config(self):
        # ADR-0003: which plan an Offering belongs to is a human-confirmed
        # assignment. Following a pin to a newer document nobody confirmed
        # would stack an unverifiable guess on a confirmed one.
        site = site_with_anchor("CB3.7.4.1", "4")
        before = site.anchors["a1"].source
        staleness.check_version_drift(site, self.current)
        self.assertEqual(site.anchors["a1"].source, before)


class ShippedConfigTest(unittest.TestCase):
    def test_uniruse_11_degree_anchors_are_all_version_pinned(self):
        # Ticket 14 wired these knowingly; this is the check that keeps
        # the exposure visible rather than remembered.
        site = load_configs_dir("crawler/configs")["UniRuse"]
        found = staleness.pinned_sources(site)
        # 13 = the 11 degree anchors + 2 curriculum plan URLs migrated to
        # extra_pages when ADR-0006 dropped Offerings (the pins the
        # offerings' CurriculumRefs used to carry).
        self.assertEqual(len(found), 13)
        wheres = [w for w, _, _, _ in found]
        self.assertEqual(sum(1 for w in wheres if w.startswith("anchors[")),
                         11)
        self.assertEqual(
            sum(1 for w in wheres if w.endswith(".extra_pages")), 2)

    def test_no_other_shipped_config_pins_a_version_today(self):
        sites = load_configs_dir("crawler/configs")
        pinned = {u: staleness.pinned_sources(s) for u, s in sites.items()}
        self.assertEqual({u for u, p in pinned.items() if p}, {"UniRuse"})


if __name__ == "__main__":
    unittest.main()
