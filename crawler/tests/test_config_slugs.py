"""Coverage for config.py's URL-scheme keys (url-scheme ticket 02).

New optional-during-rollout keys: top-level display_name / slug / city /
retired_slugs, per-program slug / subject, and configs/subjects.json.
The loader VALIDATES slugs (charset, uniqueness, reserved words,
retired-vs-live), it never generates them — minting is the ``crawler
slugs`` proposer plus a human (spec: .scratch/url-scheme/spec.md).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.config import (  # noqa: E402
    ConfigError,
    load_configs_dir,
    load_subjects,
    parse_site_config,
)

MINIMAL = {
    "uni_id": "X",
    "sources": {},
    "programs": [{"id": "x1", "name": "X1", "page": "https://x.example/p"}],
}


def _site(programs=None, **top):
    data = dict(MINIMAL, **top)
    if programs is not None:
        data["programs"] = programs
    return data


def _prog(pid="x1", **extra):
    prog = {"id": pid, "name": pid.upper(),
            "page": "https://x.example/" + pid}
    prog.update(extra)
    return prog


class UniversityRecordTest(unittest.TestCase):
    """Top-level display_name / slug / city."""

    def test_defaults_to_none(self):
        cfg = parse_site_config(dict(MINIMAL))
        self.assertIsNone(cfg.display_name)
        self.assertIsNone(cfg.slug)
        self.assertIsNone(cfg.city)

    def test_accepts_the_university_record(self):
        cfg = parse_site_config(_site(
            display_name="Софийски университет „Св. Климент Охридски“",
            slug="sofiyski-universitet", city="София"))
        self.assertEqual(cfg.slug, "sofiyski-universitet")
        self.assertEqual(cfg.city, "София")

    def test_rejects_a_malformed_uni_slug(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_site(slug="Sofiyski Universitet"))

    def test_rejects_a_reserved_uni_slug(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_site(slug="specialnosti"))


class ProgramSlugTest(unittest.TestCase):
    def test_defaults_to_none(self):
        cfg = parse_site_config(dict(MINIMAL))
        self.assertIsNone(cfg.programs[0].slug)
        self.assertIsNone(cfg.programs[0].subject)

    def test_accepts_slug_and_subject(self):
        cfg = parse_site_config(_site(
            programs=[_prog(slug="pravo", subject="pravo")]))
        self.assertEqual(cfg.programs[0].slug, "pravo")
        self.assertEqual(cfg.programs[0].subject, "pravo")

    def test_rejects_a_malformed_program_slug(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_site(programs=[_prog(slug="Право")]))

    def test_rejects_duplicate_program_slugs_within_a_university(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_site(programs=[
                _prog("x1", slug="pravo"), _prog("x2", slug="pravo")]))

    def test_allows_the_same_slug_at_two_universities(self):
        # Uniqueness is per university — the path disambiguates.
        for uni in ("X", "Y"):
            cfg = parse_site_config(dict(
                _site(programs=[_prog(slug="pravo")]), uni_id=uni))
            self.assertEqual(cfg.programs[0].slug, "pravo")

    def test_rejects_the_reserved_child_segment_as_a_program_slug(self):
        # /<uni>/ucheben-plan must stay free for the reserved child page.
        with self.assertRaises(ConfigError):
            parse_site_config(_site(programs=[_prog(slug="ucheben-plan")]))


class EveryNewKeyTogetherTest(unittest.TestCase):
    def test_one_green_fixture_exercises_every_new_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = _site(
                programs=[
                    _prog("x1", slug="pravo", subject="pravo"),
                    _prog("x2", slug="himia", subject="himia")],
                display_name="Университет X", slug="uni-x", city="София",
                retired_slugs={"staro-pravo": "x1", "star-uni-x": "X"})
            (Path(tmp) / "X.json").write_text(
                json.dumps(site, ensure_ascii=False), encoding="utf-8")
            (Path(tmp) / "subjects.json").write_text(json.dumps(
                {"subjects": [{"slug": "pravo", "name": "Право"},
                              {"slug": "himia", "name": "Химия"}]},
                ensure_ascii=False), encoding="utf-8")
            cfg = load_configs_dir(tmp)["X"]
        self.assertEqual(cfg.display_name, "Университет X")
        self.assertEqual((cfg.slug, cfg.city), ("uni-x", "София"))
        self.assertEqual(cfg.retired_slugs,
                         {"staro-pravo": "x1", "star-uni-x": "X"})
        self.assertEqual([(p.slug, p.subject) for p in cfg.programs],
                         [("pravo", "pravo"), ("himia", "himia")])


class RetiredSlugsTest(unittest.TestCase):
    """The redirect ledger: old slug -> program id (or the uni's own id)."""

    def test_accepts_a_retired_program_slug(self):
        cfg = parse_site_config(_site(
            programs=[_prog(slug="pravo-i-red")],
            retired_slugs={"pravo": "x1"}))
        self.assertEqual(cfg.retired_slugs, {"pravo": "x1"})

    def test_accepts_a_retired_uni_slug(self):
        cfg = parse_site_config(_site(slug="nov-slug",
                                      retired_slugs={"star-slug": "X"}))
        self.assertEqual(cfg.retired_slugs, {"star-slug": "X"})

    def test_rejects_a_dangling_target(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_site(retired_slugs={"pravo": "no-such"}))

    def test_rejects_a_retired_slug_that_is_still_live(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_site(
                programs=[_prog(slug="pravo")],
                retired_slugs={"pravo": "x1"}))

    def test_rejects_a_malformed_retired_slug(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_site(retired_slugs={"Право": "x1"}))


class SubjectsFileTest(unittest.TestCase):
    def _write(self, tmp, payload):
        path = Path(tmp) / "subjects.json"
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def test_loads_the_taxonomy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"subjects": [
                {"slug": "pravo", "name": "Право"},
                {"slug": "meditsina", "name": "Медицина"}]})
            subjects = load_subjects(path)
        self.assertEqual(subjects,
                         {"pravo": "Право", "meditsina": "Медицина"})

    def test_rejects_duplicate_subject_slugs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"subjects": [
                {"slug": "pravo", "name": "Право"},
                {"slug": "pravo", "name": "Право 2"}]})
            with self.assertRaises(ConfigError):
                load_subjects(path)

    def test_rejects_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"subjects": [
                {"slug": "pravo", "name": "Право", "seo": "x"}]})
            with self.assertRaises(ConfigError):
                load_subjects(path)


class ConfigsDirTest(unittest.TestCase):
    """load_configs_dir: subjects.json is taxonomy, not a site config;
    subject references and cross-university slug uniqueness are checked
    here — the one place that sees every file."""

    def _dir(self, tmp, sites, subjects=None):
        for site in sites:
            (Path(tmp) / (site["uni_id"] + ".json")).write_text(
                json.dumps(site, ensure_ascii=False), encoding="utf-8")
        if subjects is not None:
            (Path(tmp) / "subjects.json").write_text(
                json.dumps(subjects, ensure_ascii=False), encoding="utf-8")
        return tmp

    def test_subjects_json_is_not_parsed_as_a_site_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._dir(tmp, [dict(MINIMAL)],
                      subjects={"subjects": [
                          {"slug": "pravo", "name": "Право"}]})
            configs = load_configs_dir(tmp)
        self.assertEqual(sorted(configs), ["X"])

    def test_a_dangling_subject_reference_is_a_load_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._dir(tmp, [_site(programs=[_prog(subject="himia")])],
                      subjects={"subjects": [
                          {"slug": "pravo", "name": "Право"}]})
            with self.assertRaises(ConfigError):
                load_configs_dir(tmp)

    def test_a_subject_with_no_taxonomy_file_is_a_load_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._dir(tmp, [_site(programs=[_prog(subject="pravo")])])
            with self.assertRaises(ConfigError):
                load_configs_dir(tmp)

    def test_rejects_two_universities_sharing_a_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._dir(tmp, [
                dict(_site(slug="su"), uni_id="X"),
                dict(_site(slug="su"), uni_id="Y")])
            with self.assertRaises(ConfigError):
                load_configs_dir(tmp)


if __name__ == "__main__":
    unittest.main()
