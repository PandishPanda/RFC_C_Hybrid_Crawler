"""direction-fees join (.scratch/direction-fees/issues/01): the
направление→такса chain as a tier-F mechanism.

A fee order prices per професионално направление; the program's
направление — attested config data (ADR-0003), documentary evidence in
the attribution-review record — picks the clause. The mechanism absorbs
what the AMTII round spelled as O(programs) bespoke anchors."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.config import (  # noqa: E402
    ConfigError, DirectionFeesJoin, parse_site_config)
from crawler.cascade import (  # noqa: E402
    TIER_F, TextSource, TableSource, direction_fee_join)


def _site(sources=None, programs=None):
    return {
        "uni_id": "X",
        "sources": sources or {},
        "programs": programs or [
            {"id": "x-a", "name": "А", "page": "https://x/a"}],
    }


FEES_SOURCE = {
    "url": "https://x/fees.pdf",
    "route": "prose-pdf",
    "join": {
        "kind": "direction-fees",
        "name": "x-fees",
        "clauses": {
            "Теория на изкуствата": "(Теория на изкуствата: за редовно"
                                    " обучение – 780 евро)",
        },
    },
}


class DirectionFeesConfigTest(unittest.TestCase):
    def test_parses_join_and_wiring(self):
        site = parse_site_config(_site(
            sources={"x-fees": FEES_SOURCE},
            programs=[{"id": "x-a", "name": "А", "page": "https://x/a",
                       "tuition_join": {"source": "x-fees",
                                        "alias": "Теория на изкуствата"}}]))
        join = site.sources["x-fees"].join
        self.assertIsInstance(join, DirectionFeesJoin)
        self.assertEqual(site.programs[0].tuition_join.alias,
                         "Теория на изкуствата")

    def test_unknown_key_rejected(self):
        bad = dict(FEES_SOURCE, join=dict(FEES_SOURCE["join"], window=5))
        with self.assertRaises(ConfigError):
            parse_site_config(_site(sources={"x-fees": bad}))

    def test_groupless_clause_rejected(self):
        bad = dict(FEES_SOURCE, join=dict(
            FEES_SOURCE["join"],
            clauses={"Теория на изкуствата": "780 евро"}))
        with self.assertRaises(ConfigError):
            parse_site_config(_site(sources={"x-fees": bad}))

    def test_grid_route_rejected(self):
        bad = dict(FEES_SOURCE, route="table-pdf")
        with self.assertRaises(ConfigError):
            parse_site_config(_site(sources={"x-fees": bad}))

    def test_alias_must_be_a_clause_key(self):
        with self.assertRaises(ConfigError):
            parse_site_config(_site(
                sources={"x-fees": FEES_SOURCE},
                programs=[{"id": "x-a", "name": "А", "page": "https://x/a",
                           "tuition_join": {"source": "x-fees",
                                            "alias": "Изобразително"}}]))

    def test_empty_clauses_rejected(self):
        bad = dict(FEES_SOURCE, join=dict(FEES_SOURCE["join"], clauses={}))
        with self.assertRaises(ConfigError):
            parse_site_config(_site(sources={"x-fees": bad}))

    def test_wrong_want_kind_path_rejected(self):
        fees_page = {
            "url": "https://x/fees", "route": "html",
            "join": {"kind": "fees-page", "name": "x",
                     "value_pattern": "\\d+ EUR"}}
        with self.assertRaises(ConfigError):
            parse_site_config(_site(
                sources={"x-page": fees_page},
                programs=[{"id": "x-a", "name": "А", "page": "https://x/a",
                           "tuition_join": {"source": "x-page",
                                            "alias": "А"}}]))


class DirectionFeeJoinTest(unittest.TestCase):
    def _join(self):
        site = parse_site_config(_site(sources={"x-fees": FEES_SOURCE}))
        return site.sources["x-fees"].join

    def test_hit_ships_whole_clause_as_value_and_segment(self):
        src = TextSource(
            ref="pdftext:x-fees",
            text="А. бакалавър. Б.4. Теория на изкуствата: за редовно "
                 "обучение – 780 евро; край.")
        ext = direction_fee_join(src, self._join(), "Теория на изкуствата")
        self.assertEqual(
            ext.value, "Теория на изкуствата: за редовно обучение – 780 евро")
        self.assertEqual(ext.tier, TIER_F)
        self.assertEqual(ext.method, "direction-fee:Теория на изкуствата")
        self.assertTrue(any("780 евро" in s for s in ext.segments))
        self.assertEqual(ext.artifact_ref, "pdftext:x-fees")

    def test_miss_returns_none(self):
        src = TextSource(ref="pdftext:x-fees", text="няма такси тук")
        self.assertIsNone(
            direction_fee_join(src, self._join(), "Теория на изкуствата"))

    def test_table_source_raises(self):
        grid = TableSource(ref="tsv:x", tables=((("а", "б"),),))
        with self.assertRaises(TypeError):
            direction_fee_join(grid, self._join(), "Теория на изкуствата")


class LongLabelHeaderZoneTest(unittest.TestCase):
    """SWU's fee table heads its alias column with a 70-char LABEL cell
    («Области на висше образование, професионални направления и
    специалности») — a label, not a prose title. The header-cell guard
    must admit it while still rejecting UniRuse's ~170-char preamble
    sentence (test_spreadsheet pins that side)."""

    def test_seventy_char_label_cell_is_still_a_header(self):
        from crawler.cascade import fee_row_join
        site = parse_site_config({
            "uni_id": "X",
            "sources": {"x-fees": {
                "url": "https://x/fees", "route": "html",
                "join": {"kind": "fee-row", "name": "x",
                         "match_header": "области на висше образование",
                         "value_headers": ["бакалавър", "редовно"],
                         "value_pattern": "(\\d+)"}}},
            "programs": [{"id": "x-a", "name": "А", "page": "https://x/a",
                          "tuition_join": {"source": "x-fees",
                                           "alias": "Педагогика"}}],
        })
        grid = TableSource(ref="html-tables:x-fees", tables=((
            ("№ по ред",
             "Области на висше образование, професионални направления и "
             "специалности", "Образователно-квалификационна степени",
             "", "", ""),
            ("", "", "бакалавър", "", "магистър след бакалавър", ""),
            ("", "", "редовно", "задочно", "редовно", "задочно"),
            ("1.2.", "Педагогика", "550", "353", "550", "353"),
        ),))
        ext = fee_row_join("tuition", grid,
                           site.sources["x-fees"].join, "Педагогика")
        self.assertIsNotNone(ext)
        self.assertEqual(ext.value, "550")


if __name__ == "__main__":
    unittest.main()
