"""crawler.registry suite (ticket 05) -- zero network.

load_captured_export() is tested against a synthetic fixture (isolated
from the real VUM.json data, which can change) plus one smoke test against
the real committed VUM export. RegistryClient is tested with a FakeSession
that returns canned Cloudflare-403 responses -- it must raise
RegistryUnavailable, never silently return an empty list.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.registry import (  # noqa: E402
    RegistryClient,
    RegistryUnavailable,
    load_captured_export,
)

FIXTURE_EXPORT = {
    "uni_id": "FakeUni",
    "rsvu_uni_id": 999,
    "rsvu_uni_name": "Fake University",
    "captured_at": "2026-08-15T00:00:00Z",
    "source": "test fixture",
    "rows": [
        {"id": 1, "code": "10000001", "name": "Специалност А",
         "major_id": 100, "major_name": "Направление 1",
         "degree_code": 3, "degree_name": "Бакалавър",
         "edu_forms": "редовна - 4"},
        {"id": 2, "code": "10000002", "name": "Специалност Б",
         "major_id": 100, "major_name": "Направление 1",
         "degree_code": 6, "degree_name": "Магистър след висше"},
    ],
}


class LoadCapturedExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "FakeUni.json").write_text(
            json.dumps(FIXTURE_EXPORT), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_loads_rows(self):
        export = load_captured_export("FakeUni", exports_dir=self.dir)
        self.assertEqual(export.rsvu_uni_id, 999)
        self.assertEqual(len(export.rows), 2)
        self.assertEqual(export.rows[0].name, "Специалност А")

    def test_edu_forms_defaults_to_empty_string(self):
        export = load_captured_export("FakeUni", exports_dir=self.dir)
        self.assertEqual(export.rows[1].edu_forms, "")

    def test_missing_export_raises_with_the_expected_path(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            load_captured_export("NoSuchUni", exports_dir=self.dir)
        self.assertIn("NoSuchUni.json", str(ctx.exception))

    def test_filename_uni_id_mismatch_with_content_raises(self):
        # A copy/rename of an export without updating its own uni_id
        # field must fail loudly, mirroring config.load_configs_dir's
        # filename-vs-content check for site configs.
        (self.dir / "OtherUni.json").write_text(
            json.dumps(FIXTURE_EXPORT), encoding="utf-8")  # still says "FakeUni" inside
        with self.assertRaises(ValueError):
            load_captured_export("OtherUni", exports_dir=self.dir)


class RealVumExportSmokeTest(unittest.TestCase):
    """The real committed export loads and shapes correctly."""

    def test_vum_export_loads(self):
        export = load_captured_export("VUM")
        self.assertEqual(export.uni_id, "VUM")
        self.assertEqual(export.rsvu_uni_id, 125)
        self.assertGreaterEqual(len(export.rows), 18)
        ids = [row.id for row in export.rows]
        self.assertEqual(len(ids), len(set(ids)), "row ids must be unique")


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP {0}".format(self.status_code))


class FakeSession:
    """Canned Cloudflare-403 on every call, like the real domain today."""

    def __init__(self):
        self.requested = []

    def get(self, url, timeout=None, headers=None):
        self.requested.append(url)
        return FakeResponse(403, text="Just a moment...")


class RegistryClientBlockedTest(unittest.TestCase):
    def test_majors_call_raises_registry_unavailable_not_empty_list(self):
        client = RegistryClient(session=FakeSession())
        with self.assertRaises(RegistryUnavailable):
            client.majors(125)

    def test_fetch_export_raises_on_the_first_blocked_call(self):
        client = RegistryClient(session=FakeSession())
        with self.assertRaises(RegistryUnavailable):
            client.fetch_export("VUM", 125)


class FakeWorkingSession:
    """A session that WOULD work, for testing the success path shape."""

    def __init__(self, routes):
        self.routes = routes

    def get(self, url, timeout=None, headers=None):
        for suffix, payload in self.routes.items():
            if url.endswith(suffix):
                return FakeResponse(200, json_data=payload)
        raise AssertionError("unexpected URL: " + url)


class RegistryClientSuccessShapeTest(unittest.TestCase):
    """If the block were ever lifted, fetch_export must union + dedup
    correctly -- exercised here against a fake session so the orchestration
    logic is tested even though the real domain can't be reached."""

    def test_fetch_export_unions_and_dedups_across_major_x_degree(self):
        routes = {
            "/major/125/bg?includeOnlyDoctors=false": [
                {"id": 100, "name": "Направление 1"},
            ],
            "/degree/bg": [
                {"id": 6, "code": 3, "name": "Бакалавър"},
                {"id": 13, "code": 6, "name": "Магистър след висше"},
                {"id": 11, "code": 0, "name": "Общи"},  # code 0 skipped
            ],
            "/universities/minors/125/100/3/bg": [
                {"id": 1, "code": "1", "name": "Специалност А", "eduForms": "редовна - 4"},
            ],
            "/universities/minors/125/100/6/bg": [
                {"id": 1, "code": "1", "name": "Специалност А", "eduForms": "редовна - 4"},
                {"id": 2, "code": "2", "name": "Специалност Б", "eduForms": "задочна - 2"},
            ],
        }
        client = RegistryClient(session=FakeWorkingSession(routes))
        export = client.fetch_export("VUM", 125)
        self.assertEqual({row.id for row in export.rows}, {1, 2})
        self.assertEqual(len(export.rows), 2)


if __name__ == "__main__":
    unittest.main()
