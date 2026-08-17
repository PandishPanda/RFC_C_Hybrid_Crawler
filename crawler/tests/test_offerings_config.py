"""`offerings` config schema (ticket 18) -- loader only, zero behaviour.

A Program is not one offering (ADR-0004): the registry row enumerates
(form, duration) pairs, and config attaches a RECIPE per form. Config
never restates the list -- it is a MAP keyed by form, so config and
registry cannot drift.

This ticket lands the schema ONLY. Nothing reads `offerings` at runtime
yet, so `offerings` absent must leave all 9 shipped configs and the whole
suite byte-identical -- proved below rather than asserted.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.config import (  # noqa: E402
    ATTENDANCE_FORMS, ConfigError, load_configs_dir, parse_site_config,
)

FEES_SOURCE = {
    "url": "https://x.example/fees.xlsx", "route": "spreadsheet",
    "join": {"kind": "fee-row", "name": "f", "match_header": "специалност",
             "value_headers": ["редовно", "лв."]},
}


def config(offerings=None, rsvu_code="TEST-CODE-1"):
    """A minimal loadable site. rsvu_code is present by default because
    offerings REQUIRE it -- pass None to exercise that rejection."""
    program = {"id": "p1", "name": "P1", "page": "https://x.example/p1"}
    if rsvu_code is not None:
        program["rsvu_code"] = rsvu_code
    if offerings is not None:
        program["offerings"] = offerings
    return {"uni_id": "X", "sources": {"fees": FEES_SOURCE},
            "programs": [program]}


def recipe(**kw):
    base = {"tuition_join": {"source": "fees", "alias": "Бизнес мениджмънт"}}
    base.update(kw)
    return base


class OfferingsAbsentIsInertTest(unittest.TestCase):
    def test_a_program_without_offerings_gets_an_empty_map(self):
        site = parse_site_config(config(), origin="<t>")
        self.assertEqual(dict(site.programs[0].offerings), {})

    def test_all_shipped_configs_still_load(self):
        sites = load_configs_dir("crawler/configs")
        self.assertEqual(len(sites), 9)

    def test_a_shipped_program_without_the_key_gets_an_empty_map(self):
        # Ticket 19 deliberately wires offerings for one UniRuse program;
        # the inertness claim is about programs that do NOT declare them.
        raw = json.loads(
            Path("crawler/configs/UniRuse.json").read_text(encoding="utf-8"))
        undeclared = {p["id"] for p in raw["programs"] if "offerings" not in p}
        self.assertTrue(undeclared, "fixture assumption")
        site = load_configs_dir("crawler/configs")["UniRuse"]
        for program in site.programs:
            if program.id in undeclared:
                self.assertEqual(dict(program.offerings), {}, program.id)


class OfferingKeyTest(unittest.TestCase):
    def test_a_bare_form_key_is_accepted(self):
        site = parse_site_config(config({"редовна": recipe()}), origin="<t>")
        self.assertIn("редовна", site.programs[0].offerings)

    def test_a_form_and_duration_key_specialises_one_duration(self):
        site = parse_site_config(
            config({"задочна - 4.5": recipe()}), origin="<t>")
        self.assertIn("задочна - 4.5", site.programs[0].offerings)

    def test_the_closed_vocabulary_covers_every_form_the_registry_states(self):
        # The test with teeth: iterating ATTENDANCE_FORMS and asserting the
        # loader accepts them only proves the loader accepts its own
        # tuple. What matters is that config is closed over a vocabulary
        # WIDE ENOUGH for the real exports -- a form the registry states
        # but config cannot name is an Offering nobody can configure.
        from crawler.registry import DEFAULT_EXPORTS_DIR  # test-only dep
        from crawler.registry import load_captured_export, parse_edu_forms
        stated = set()
        for export in sorted(p.stem for p in DEFAULT_EXPORTS_DIR.glob("*.json")):
            for row in load_captured_export(export).rows:
                for f in parse_edu_forms(row.edu_forms)[0]:
                    stated.add(f.form)
        self.assertTrue(stated, "no exports to measure against")
        self.assertEqual(stated - set(ATTENDANCE_FORMS), set())

    def test_a_key_that_is_not_a_form_says_so_rather_than_unknown_form(self):
        # "4" and "_" are not misspelled forms, they are not keys at all;
        # reporting them as an unknown FORM would misdirect the fix.
        for key in ("4", "_", "4 - 5"):
            with self.assertRaises(ConfigError, msg=key) as ctx:
                parse_site_config(config({key: recipe()}), origin="<t>")
            self.assertIn("not an offering key", str(ctx.exception), key)

    def test_a_misspelled_form_is_rejected_with_its_path(self):
        # "редовно" (neuter) is the adjective the FEE TABLE uses; the
        # registry says "редовна". Silently accepting it would attach a
        # recipe to an Offering that never matches.
        with self.assertRaises(ConfigError) as ctx:
            parse_site_config(config({"редовно": recipe()}), origin="<t>")
        self.assertIn("offerings", str(ctx.exception))
        self.assertIn("редовно", str(ctx.exception))

    def test_a_form_outside_the_vocabulary_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_site_config(config({"вечерна": recipe()}), origin="<t>")

    def test_a_malformed_key_is_rejected(self):
        for key in ("", "   ", "редовна -", "редовна - x", "- 4"):
            with self.assertRaises(ConfigError, msg=key):
                parse_site_config(config({key: recipe()}), origin="<t>")


class OfferingRecipeTest(unittest.TestCase):
    def test_an_empty_recipe_is_rejected(self):
        # An offering key with nothing to do is almost certainly a
        # half-finished edit, not an intent to configure nothing.
        with self.assertRaises(ConfigError):
            parse_site_config(config({"редовна": {}}), origin="<t>")

    def test_an_unknown_key_inside_a_recipe_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            parse_site_config(
                config({"редовна": recipe(tution_join={})}), origin="<t>")
        self.assertIn("tution_join", str(ctx.exception))

    def test_a_recipe_join_must_point_at_a_real_source(self):
        with self.assertRaises(ConfigError):
            parse_site_config(
                config({"редовна": {"tuition_join":
                                    {"source": "nope", "alias": "x"}}}),
                origin="<t>")

    def test_offerings_require_the_program_to_carry_an_rsvu_code(self):
        # Offerings are enumerated from the registry row; without a code
        # there is no row to enumerate from.
        with self.assertRaises(ConfigError) as ctx:
            parse_site_config(
                config({"редовна": recipe()}, rsvu_code=None), origin="<t>")
        self.assertIn("rsvu_code", str(ctx.exception))


class TuitionJoinKindTest(unittest.TestCase):
    """An Offering's fee is a COLUMN of a fee table. sectioned-fee-row
    selects by SECTION (a language track) and carries no value_headers at
    all, so it cannot express one -- and admitting it also admitted the
    only alias shape no resolver can use (cascade.sectioned_fee_join
    compiles alias_pattern as a regex; a JoinRef built here takes a
    literal alias). Found by spec review 2026-08-16, refuting this
    ticket's own claim that the case was unreachable."""

    SECTIONED = {"url": "https://x.example/f.pdf", "route": "table-pdf",
                 "join": {"kind": "sectioned-fee-row", "name": "s",
                          "sections": [{"track": "bg", "match": "БГ"}],
                          "fee_pattern": r"(\d+)"}}

    def test_a_sectioned_source_is_refused_for_an_offering(self):
        cfg = config({"редовна": {"tuition_join": {"source": "s",
                                                   "alias": "X"}}})
        cfg["sources"]["s"] = self.SECTIONED
        with self.assertRaises(ConfigError) as ctx:
            parse_site_config(cfg, origin="<t>")
        self.assertIn("sectioned-fee-row", str(ctx.exception))


class OfferingKeyCanonicalisationTest(unittest.TestCase):
    """EduForm.key only ever emits "<form> - <duration>". A key spelled
    any other way could never match the Offering it names, so it is
    canonicalised at load -- and two keys naming one offering are refused
    rather than silently collapsed."""

    def _keys(self, offerings):
        site = parse_site_config(config(offerings), origin="<t>")
        return sorted(site.programs[0].offerings)

    def test_a_missing_space_around_the_separator_is_canonicalised(self):
        self.assertEqual(self._keys({"задочна-4.5": recipe()}),
                         ["задочна - 4.5"])

    def test_padding_is_canonicalised(self):
        self.assertEqual(self._keys({"  редовна  ": recipe()}), ["редовна"])

    def test_a_non_breaking_space_is_canonicalised(self):
        self.assertEqual(self._keys({"редовна\xa0- 4": recipe()}),
                         ["редовна - 4"])

    def test_two_keys_naming_one_offering_are_refused(self):
        with self.assertRaises(ConfigError) as ctx:
            parse_site_config(
                config({"редовна": recipe(), "редовна ": recipe()}),
                origin="<t>")
        self.assertIn("same offering", str(ctx.exception))


class CurriculumRefTest(unittest.TestCase):
    def _cur(self, **kw):
        base = {"url": "https://e-curriculum.example/x",
                "form_phrase": "Редовно"}
        base.update(kw)
        return base

    def test_a_curriculum_only_recipe_loads(self):
        # curriculum alone is a complete recipe -- no tuition_join.
        site = parse_site_config(
            config({"редовна": {"curriculum": self._cur()}}), origin="<t>")
        self.assertIsNone(site.programs[0].offerings["редовна"].tuition_join)
        self.assertIsNotNone(site.programs[0].offerings["редовна"].curriculum)

    def test_a_curriculum_reference_loads(self):
        site = parse_site_config(
            config({"редовна": recipe(curriculum=self._cur(
                code="CB3.7.4.1", version="5"))}), origin="<t>")
        cur = site.programs[0].offerings["редовна"].curriculum
        self.assertEqual(cur.code, "CB3.7.4.1")
        self.assertEqual(cur.form_phrase, "Редовно")

    def test_form_phrase_is_required(self):
        # form_phrase is the gate-able attestation that the fetched plan
        # is THIS offering's -- without it the reference proves nothing.
        bad = self._cur()
        del bad["form_phrase"]
        with self.assertRaises(ConfigError) as ctx:
            parse_site_config(config({"редовна": recipe(curriculum=bad)}),
                              origin="<t>")
        self.assertIn("form_phrase", str(ctx.exception))

    def test_a_blank_form_phrase_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_site_config(
                config({"редовна": recipe(curriculum=self._cur(
                    form_phrase="  "))}), origin="<t>")

    def test_an_unknown_curriculum_key_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_site_config(
                config({"редовна": recipe(curriculum=self._cur(
                    versoin="5"))}), origin="<t>")


if __name__ == "__main__":
    unittest.main()
