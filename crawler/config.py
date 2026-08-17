"""Typed per-site config — site knowledge as DATA, never code (RFC v2 Q7).

What can rot is config, not code: URL maps, recipes, aliases. This module is
the schema those configs are validated against, ported from spike A's
uni_config.py (the measured per-site maintenance surface) into JSON files
under crawler/configs/<UniID>.json. The extraction cascade (crawler/cascade.py)
is shared code; everything a specific university needs — program pages,
shared-source URLs, row aliases, join regex recipes — lives here as data,
proposed by onboarding agents and promoted by a human (RFC v2 §2).

Strictness is the point: the loader REJECTS unknown keys at every level with
a ConfigError naming the offending key and its path. A typo'd key
("tution_join") must raise at load time, never silently null a field at
refresh time — tier-F config is supposed to break visibly (RFC v2 Q7), and
visibly starts at load.

Schema (JSON key = dataclass field, snake_case):

  SiteConfig            uni_id, cookies, sources{id: SourceConfig}, programs[]
  SourceConfig          url, route (html | prose-pdf | table-pdf |
                        spreadsheet), join?
  join (by "kind"):
    fee-row             column-aware fee-table row join (SU family):
                        match_header + value_headers resolve the alias column
                        and the value column FROM THE TABLE HEADER ROWS —
                        never by blind index, and the value is taken from
                        that one cell only (the resolver-side fix for the
                        wrong-row/column gate blind spot, RFC v2 Q4)
    sectioned-fee-row   section-tracked fee-table row join (MU family):
                        section header rows split language tracks; the fee
                        is matched inside the alias row (the Docling grid of
                        this family shifts columns per row — measured — so
                        the row, not a column, is the deterministic unit)
    ordinance           line-anchored row/clause joins over pdftotext -layout
                        text (SU admission ordinance, Приложение 2)
    spravochnik         sentence joins over PDF flow text (MU Справочник):
                        degree/duration sentence + section-scoped admission
    fees-page           labelled fee section on a shared HTML fees page
                        (VUM): per-program section_pattern + shared
                        value_pattern
  anchors               site-level tier-B bespoke anchors, ported from spike
                        A's ANCHORS table as DATA: {anchor_id: {source,
                        pattern}} where source is a configured source id or a
                        page URL and pattern's group 1 is the value. The
                        interim bridge until the gated LLM tail (ticket 02)
                        absorbs the anchor tier; the honest maintenance bill,
                        named per anchor so the B-tier count stays measurable
  ProgramConfig         id, name, page, extra_pages[], extra_sources[],
                        lang_page?, adm_page?, tuition_page?,
                        tuition_join?/admission_join?/spravochnik?/
                        language_tracks? (JoinRef: source + alias|alias_pattern),
                        fees_section? (SectionRef: source + section_pattern),
                        field_anchors? ({field: anchor_id} into site anchors),
                        rsvu_code?, offerings?
  offerings             {offering key: OfferingConfig} — the per-form recipe
                        map (ADR-0004). The KEY is an attendance form
                        ("редовна") or a form with one duration specialised
                        ("задочна - 4.5"), always from ATTENDANCE_FORMS;
                        config never lists the offerings themselves, since
                        the registry row enumerates them and a second list
                        could drift. Requires the Program to carry rsvu_code.
  OfferingConfig        tuition_join? (JoinRef), curriculum? (CurriculumRef)
  CurriculumRef         url, form_phrase (REQUIRED — the gate-able claim
                        that a fetched plan is THIS offering's), code?,
                        version?

All regexes (alias_pattern, join patterns) are compile-checked at load time;
join pattern templates carry a literal "{alias}" placeholder that the cascade
substitutes with the re.escape()d alias — plain string replacement, so regex
braces stay literal. Wiring is validated too: a tuition_join must point at a
fee-row/sectioned-fee-row source, an admission_join at an ordinance source,
and so on — pointing a field at the wrong kind of source is a config bug the
loader refuses, not a null the refresh loop discovers.

Python 3.9 compatible; stdlib only.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

__all__ = [
    "ConfigError",
    "ROUTES",
    "FIELDS",
    "AnchorConfig",
    "FeeRowJoin",
    "SectionSpec",
    "SectionedFeeRowJoin",
    "OrdinanceJoin",
    "SpravochnikJoin",
    "FeesPageJoin",
    "SourceConfig",
    "JoinRef",
    "SectionRef",
    "ProgramConfig",
    "OfferingConfig",
    "CurriculumRef",
    "ATTENDANCE_FORMS",
    "SiteConfig",
    "parse_site_config",
    "load_site_config",
    "load_configs_dir",
]

# Must stay equal to crawler.render.ROUTE_* (not imported: config is
# stdlib-only data schema; render owns the renderer side of the contract).
ROUTES = ("html", "prose-pdf", "table-pdf", "spreadsheet")

# The five StudyStream Program fields, in cascade order.
FIELDS = ("degree", "duration", "language", "tuition", "admission")

ALIAS_PLACEHOLDER = "{alias}"


class ConfigError(ValueError):
    """A config file is malformed: unknown key, bad type, bad wiring.

    Raised at LOAD time. Every message carries the JSON path of the
    offending node so a typo is a one-line fix, not a null-hunt.
    """


# --------------------------------------------------------------------- joins
@dataclass(frozen=True)
class FeeRowJoin:
    """Column-aware fee-table row join over Docling TSV tables (kind=fee-row).

    match_header / value_headers name the columns BY HEADER TEXT; the cascade
    resolves indices per table from the header rows, then reads the single
    (alias row x value column) cell — never a neighbouring column.
    """
    name: str                              # method suffix: "fee-join:<name>"
    match_header: str                      # header token of the alias column
    value_headers: Tuple[str, ...]         # ALL tokens of the value column
    value_pattern: Optional[str] = None    # optional cell cleanup, group 1
    context: Mapping[str, str] = field(default_factory=dict)
    table_marker: Optional[str] = None     # WHICH table, by a literal cell of
    # it ("Приложение 1"). Matched against a WHOLE CELL, never a substring:
    # "Приложение 1" is a substring of "Приложение 10" in a real workbook, so
    # substring matching silently selects the wrong table. Without a marker
    # the join scans every table in document order and takes the first hit,
    # which is safe only when one table can possibly match.
    funding: Optional[str] = None          # human label for the band this
    # table prices ("на държавна издръжка"). Ships as extraction context so a
    # consumer can tell a state-subsidised fee from a paid-place one. Must be
    # a literal substring of the marker ROW's text, checked at load time --
    # an unbacked label would be an assertion, and this module ships data.

    kind = "fee-row"


@dataclass(frozen=True)
class SectionSpec:
    """One section-header row recipe of a sectioned fee table."""
    track: str            # track label emitted for the language join
    match: str            # literal substring that identifies the header row
    foreign: bool = False  # excluded from language tracks (чл. 95 rows)


@dataclass(frozen=True)
class SectionedFeeRowJoin:
    """Section-tracked fee-table row join (kind=sectioned-fee-row)."""
    name: str
    sections: Tuple[SectionSpec, ...]
    fee_pattern: str                      # group 1 = fee, matched in the row
    currency_suffix: str = ""             # e.g. "евро" (stated in the header)
    context: Mapping[str, str] = field(default_factory=dict)

    kind = "sectioned-fee-row"


@dataclass(frozen=True)
class OrdinanceJoin:
    """Line-anchored ordinance row/clause joins (kind=ordinance).

    Patterns are templates over pdftotext -layout text with a literal
    "{alias}" placeholder; row_value_group names the value group of
    row_pattern; the clause fallback picks ONE bullet verbatim (the one
    containing clause_pick_token if present).
    """
    name: str
    row_pattern: str
    row_value_group: int = 2
    clause_pattern: Optional[str] = None
    clause_pick_token: Optional[str] = None

    kind = "ordinance"


@dataclass(frozen=True)
class SpravochnikJoin:
    """Sentence joins over PDF flow text (kind=spravochnik).

    sentence_pattern must define named groups (?P<degree>...) and
    (?P<duration>...); admission_pattern group 1 is the admission value.
    """
    name: str
    sentence_pattern: str
    admission_pattern: Optional[str] = None
    admission_label: str = "admission"    # method suffix, e.g. "chl30"

    kind = "spravochnik"


@dataclass(frozen=True)
class FeesPageJoin:
    """Labelled fee section on a shared HTML fees page (kind=fees-page).

    value_pattern is group-free; the cascade wraps it and anchors it within
    `window` chars after the program's section_pattern.
    """
    name: str
    value_pattern: str
    window: int = 120

    kind = "fees-page"


_JOIN_KINDS = ("fee-row", "sectioned-fee-row", "ordinance", "spravochnik",
               "fees-page")


@dataclass(frozen=True)
class AnchorConfig:
    """One tier-B bespoke anchor (spike A's ANCHORS table as config data).

    source is a configured source id (rendered per its route) or a page URL
    (rendered html); pattern's group 1 is the value. The extraction method
    is recorded as "anchor:<id>" so the per-anchor maintenance bill stays
    countable (RFC v2 Q3 — this tier is what the ticket-02 LLM tail
    replaces).
    """
    id: str
    source: str
    pattern: str


# ------------------------------------------------------------------- sources
@dataclass(frozen=True)
class SourceConfig:
    """One fetchable document: URL + renderer route + optional join recipe."""
    id: str
    url: str
    route: str
    join: Optional[object] = None   # one of the join dataclasses above


@dataclass(frozen=True)
class JoinRef:
    """A program's hook into a shared source's join: which row is MINE.

    Exactly one of alias (literal substring) / alias_pattern (regex) — which
    one is required depends on the join kind (fee-row and ordinance match
    literal aliases; sectioned-fee-row matches a pattern). In ticket 02 the
    onboarding agent/LLM proposes THIS row identity; the value cell is always
    taken deterministically (RFC v2 Q4).
    """
    source: str
    alias: Optional[str] = None
    alias_pattern: Optional[str] = None


@dataclass(frozen=True)
class SectionRef:
    """A program's section on a shared fees page (fees-page join)."""
    source: str
    section_pattern: str


# ------------------------------------------------------------------ programs
# The attendance forms the RSVU registry actually states, measured across
# every committed export (274 rows, 628 items, 2026-08-16). Config is
# CLOSED over this list while registry.parse_edu_forms stays open: the
# registry may legitimately name a form no config may yet declare, and
# that Offering then simply has no recipe. Closing config is what catches
# "редовно" -- the neuter adjective the FEE TABLE uses -- being pasted in
# where the registry says "редовна", which would attach a recipe to an
# Offering that can never match.
ATTENDANCE_FORMS = ("редовна", "задочна", "дистанционна", "самостоятелна")

# An offering key is either a bare form ("редовна", matching every duration
# the registry states for it) or a form with one duration specialised
# ("задочна - 4.5"). Must stay equal to crawler.registry.EduForm.key, which
# FORMATS what this PARSES -- not imported, for the same reason ROUTES is
# not imported from crawler.render: config is a leaf schema module, and
# crawler.registry carries an HTTP adapter this module must not depend on.
# The form must start with a LETTER: "\w" alone would make "4" and "_"
# look like addressable forms and surface as "unknown attendance form".
_OFFERING_FORM_RX = re.compile(r"^[^\W\d_][\w\s]*$", re.UNICODE)
_OFFERING_KEY_RX = re.compile(
    r"^([^\W\d_][^-]*?)\s*-\s*(\d+(?:\.\d+)?)$", re.UNICODE)


def _split_offering_key(key):
    # type: (str) -> Optional[Tuple[str, Optional[str]]]
    """(form, duration or None), or None if KEY is not an offering key."""
    if not key or not key.strip():
        return None
    text = key.strip()
    if "-" not in text:
        return (text, None) if _OFFERING_FORM_RX.match(text) else None
    m = _OFFERING_KEY_RX.match(text)
    return (m.group(1).strip(), m.group(2)) if m else None


@dataclass(frozen=True)
class CurriculumRef:
    """A per-Offering curriculum plan, with the attestation that binds it.

    form_phrase is REQUIRED and is the gate-able claim: a plan page states
    its own form ("Редовно") in its breadcrumb, so a fetched plan can be
    checked to be THIS Offering's rather than assumed. A reference without
    it proves nothing about what was fetched (ADR-0004).

    Schema only in ticket 18 -- nothing reads this at runtime yet, and the
    binding CHECK is ticket 21, gated behind a real captured plan.
    """
    url: str
    form_phrase: str
    code: Optional[str] = None      # both appear in the plan URL itself
    version: Optional[str] = None   # (?code=CB3.7.4.1&version=5)
    program_name: Optional[str] = None   # extra breadcrumb segments; when
    degree_phrase: Optional[str] = None  # present they anchor form_phrase
    # to the breadcrumb (see runner._curriculum_binding) instead of letting
    # one generic word match anywhere on the page. Restored 2026-08-17: they
    # were dropped in ticket 18 review as schema for an unobserved page;
    # the page is now captured, and its breadcrumb states all three.


@dataclass(frozen=True)
class OfferingConfig:
    """What config attaches to ONE (form, duration) the registry states.

    Never the Offering itself -- the registry enumerates those. This is
    only the recipe for reading that Offering's values.
    """
    tuition_join: Optional[JoinRef] = None
    curriculum: Optional[CurriculumRef] = None


@dataclass(frozen=True)
class ProgramConfig:
    id: str
    name: str
    page: str
    extra_pages: Tuple[str, ...] = ()
    extra_sources: Tuple[str, ...] = ()
    lang_page: Optional[str] = None
    adm_page: Optional[str] = None
    tuition_page: Optional[str] = None
    tuition_join: Optional[JoinRef] = None
    admission_join: Optional[JoinRef] = None
    spravochnik: Optional[JoinRef] = None
    language_tracks: Optional[JoinRef] = None
    fees_section: Optional[SectionRef] = None
    field_anchors: Mapping[str, str] = field(default_factory=dict)
    # {offering key -> recipe}. Keyed by FORM, never a list of offerings:
    # the registry enumerates those, so config restating them could drift.
    offerings: Mapping[str, OfferingConfig] = field(default_factory=dict)
    rsvu_code: Optional[str] = None  # the RSVU registry row this Program
    # corresponds to (ticket 05's covered_codes()) -- durable site data,
    # set by hand once someone has matched the two; None until then.


@dataclass(frozen=True)
class SiteConfig:
    uni_id: str
    sources: Mapping[str, SourceConfig]
    programs: Tuple[ProgramConfig, ...]
    cookies: Mapping[str, str] = field(default_factory=dict)
    anchors: Mapping[str, AnchorConfig] = field(default_factory=dict)
    rsvu_id: Optional[int] = None  # this uni's numeric id in the RSVU
    # registry API (rsvu.mon.bg) -- durable site knowledge (ticket 05),
    # not a per-run flag; None until someone looks it up and records it.
    program_markers: Tuple[str, ...] = ()  # words THIS site uses to
    # announce "a program we offer" ("Специалност", "Programme", ...),
    # read by adjudication.propose_enrolling to tell a real declaration
    # from an incidental mention. Empty = use the built-in multilingual
    # default; override only when a site's own vocabulary differs, so
    # the rule stays config data rather than per-site code (ADR-0001).

    def program(self, program_id):
        # type: (str) -> ProgramConfig
        for p in self.programs:
            if p.id == program_id:
                return p
        raise KeyError(program_id)


# ---------------------------------------------------------------- validators
def _reject_unknown(data, allowed, path):
    if not isinstance(data, dict):
        raise ConfigError("{0}: expected a JSON object, got {1}".format(
            path, type(data).__name__))
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ConfigError(
            "{0}: unknown key(s) {1} — allowed keys: {2}. A typo'd key must "
            "be fixed, not ignored".format(
                path, ", ".join(repr(k) for k in unknown),
                ", ".join(sorted(allowed))))


def _require(data, key, path):
    if key not in data:
        raise ConfigError("{0}: missing required key {1!r}".format(path, key))
    return data[key]


def _str(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("{0}: expected a non-empty string, got {1!r}".format(
            path, value))
    return value


def _int(value, path):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("{0}: expected an integer, got {1!r}".format(
            path, value))
    return value


def _bool(value, path):
    if not isinstance(value, bool):
        raise ConfigError("{0}: expected a boolean, got {1!r}".format(
            path, value))
    return value


def _str_list(value, path):
    if not isinstance(value, list) or any(
            not isinstance(v, str) for v in value):
        raise ConfigError("{0}: expected a list of strings, got {1!r}".format(
            path, value))
    return tuple(value)


def _str_dict(value, path):
    if not isinstance(value, dict) or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in value.items()):
        raise ConfigError(
            "{0}: expected an object of string values, got {1!r}".format(
                path, value))
    return dict(value)


def _regex(value, path, template=False):
    """Compile-check a regex (or an {alias}-templated regex)."""
    _str(value, path)
    probe = value.replace(ALIAS_PLACEHOLDER, "X") if template else value
    try:
        return re.compile(probe)
    except re.error as exc:
        raise ConfigError("{0}: invalid regex {1!r}: {2}".format(
            path, value, exc))


def _template(value, path):
    _str(value, path)
    if ALIAS_PLACEHOLDER not in value:
        raise ConfigError(
            "{0}: pattern template must contain the literal {1!r} "
            "placeholder".format(path, ALIAS_PLACEHOLDER))
    _regex(value, path, template=True)
    return value


# ------------------------------------------------------------- join builders
def _build_fee_row(data, path):
    _reject_unknown(data, ("kind", "name", "match_header", "value_headers",
                           "value_pattern", "context", "table_marker",
                           "funding"), path)
    value_headers = _str_list(_require(data, "value_headers", path),
                              path + ".value_headers")
    if not value_headers:
        raise ConfigError(path + ".value_headers: must not be empty")
    value_pattern = data.get("value_pattern")
    if value_pattern is not None:
        rx = _regex(value_pattern, path + ".value_pattern")
        if rx.groups < 1:
            raise ConfigError(
                path + ".value_pattern: needs a capturing group (group 1 "
                "is the cleaned value)")
    table_marker = data.get("table_marker")
    if table_marker is not None:
        table_marker = _str(table_marker, path + ".table_marker")
        if not table_marker.strip():
            raise ConfigError(path + ".table_marker: must not be blank")
    funding = data.get("funding")
    if funding is not None:
        funding = _str(funding, path + ".funding")
        if table_marker is None:
            raise ConfigError(
                path + ".funding: names the band a SPECIFIC table prices, "
                "so it requires table_marker -- without one the join may "
                "read any table and the label would be unbacked")
        if funding not in table_marker:
            raise ConfigError(
                "{0}.funding: {1!r} is not a literal substring of "
                "table_marker -- a funding label must be BACKED by the "
                "marker text it claims to describe, never asserted "
                "alongside it. Point table_marker at the table's own "
                "title cell (which states the band) rather than a bare "
                "number.".format(path, funding))
    return FeeRowJoin(
        name=_str(_require(data, "name", path), path + ".name"),
        match_header=_str(_require(data, "match_header", path),
                          path + ".match_header"),
        value_headers=value_headers,
        value_pattern=value_pattern,
        context=_str_dict(data.get("context", {}), path + ".context"),
        table_marker=table_marker,
        funding=funding,
    )


def _build_section(data, path):
    _reject_unknown(data, ("track", "match", "foreign"), path)
    return SectionSpec(
        track=_str(_require(data, "track", path), path + ".track"),
        match=_str(_require(data, "match", path), path + ".match"),
        foreign=_bool(data.get("foreign", False), path + ".foreign"),
    )


def _build_sectioned(data, path):
    _reject_unknown(data, ("kind", "name", "sections", "fee_pattern",
                           "currency_suffix", "context"), path)
    raw_sections = _require(data, "sections", path)
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ConfigError(path + ".sections: expected a non-empty list")
    sections = tuple(
        _build_section(s, "{0}.sections[{1}]".format(path, i))
        for i, s in enumerate(raw_sections))
    fee_pattern = _str(_require(data, "fee_pattern", path),
                       path + ".fee_pattern")
    if _regex(fee_pattern, path + ".fee_pattern").groups < 1:
        raise ConfigError(
            path + ".fee_pattern: needs a capturing group (group 1 is "
            "the fee)")
    return SectionedFeeRowJoin(
        name=_str(_require(data, "name", path), path + ".name"),
        sections=sections,
        fee_pattern=fee_pattern,
        currency_suffix=_str(data["currency_suffix"],
                             path + ".currency_suffix")
        if "currency_suffix" in data else "",
        context=_str_dict(data.get("context", {}), path + ".context"),
    )


def _build_ordinance(data, path):
    _reject_unknown(data, ("kind", "name", "row_pattern", "row_value_group",
                           "clause_pattern", "clause_pick_token"), path)
    clause_pattern = data.get("clause_pattern")
    if clause_pattern is not None:
        _template(clause_pattern, path + ".clause_pattern")
    return OrdinanceJoin(
        name=_str(_require(data, "name", path), path + ".name"),
        row_pattern=_template(_require(data, "row_pattern", path),
                              path + ".row_pattern"),
        row_value_group=_int(data.get("row_value_group", 2),
                             path + ".row_value_group"),
        clause_pattern=clause_pattern,
        clause_pick_token=_str(data["clause_pick_token"],
                               path + ".clause_pick_token")
        if "clause_pick_token" in data else None,
    )


def _build_spravochnik(data, path):
    _reject_unknown(data, ("kind", "name", "sentence_pattern",
                           "admission_pattern", "admission_label"), path)
    sentence_pattern = _template(_require(data, "sentence_pattern", path),
                                 path + ".sentence_pattern")
    probe = re.compile(sentence_pattern.replace(ALIAS_PLACEHOLDER, "X"))
    for group in ("degree", "duration"):
        if group not in probe.groupindex:
            raise ConfigError(
                "{0}.sentence_pattern: must define a named group "
                "(?P<{1}>...)".format(path, group))
    admission_pattern = data.get("admission_pattern")
    if admission_pattern is not None:
        _template(admission_pattern, path + ".admission_pattern")
    return SpravochnikJoin(
        name=_str(_require(data, "name", path), path + ".name"),
        sentence_pattern=sentence_pattern,
        admission_pattern=admission_pattern,
        admission_label=_str(data.get("admission_label", "admission"),
                             path + ".admission_label"),
    )


def _build_fees_page(data, path):
    _reject_unknown(data, ("kind", "name", "value_pattern", "window"), path)
    value_pattern = _str(_require(data, "value_pattern", path),
                         path + ".value_pattern")
    if _regex(value_pattern, path + ".value_pattern").groups != 0:
        raise ConfigError(
            path + ".value_pattern: must be group-free (the cascade wraps "
            "it in its own capturing group)")
    return FeesPageJoin(
        name=_str(_require(data, "name", path), path + ".name"),
        value_pattern=value_pattern,
        window=_int(data.get("window", 120), path + ".window"),
    )


_JOIN_BUILDERS = {
    "fee-row": _build_fee_row,
    "sectioned-fee-row": _build_sectioned,
    "ordinance": _build_ordinance,
    "spravochnik": _build_spravochnik,
    "fees-page": _build_fees_page,
}


def _build_join(data, path):
    if not isinstance(data, dict):
        raise ConfigError("{0}: expected a JSON object, got {1}".format(
            path, type(data).__name__))
    kind = _str(_require(data, "kind", path), path + ".kind")
    if kind not in _JOIN_KINDS:
        raise ConfigError("{0}.kind: unknown join kind {1!r} — one of: "
                          "{2}".format(path, kind, ", ".join(_JOIN_KINDS)))
    return _JOIN_BUILDERS[kind](data, path)


# Joins that read a parsed cell GRID (cascade's TableSource), not flow
# text -- only the grid-producing routes can feed them. Pointing one at
# an html/prose-pdf source raises TypeError deep inside the resolver at
# refresh time; this catches it at load time instead, same philosophy as
# every other wiring check here (tier-F config must break visibly).
_GRID_JOIN_KINDS = ("fee-row", "sectioned-fee-row")
_GRID_ROUTES = ("table-pdf", "spreadsheet")


# ------------------------------------------------------------ node builders
def _build_source(source_id, data, path):
    _reject_unknown(data, ("url", "route", "join"), path)
    route = _str(_require(data, "route", path), path + ".route")
    if route not in ROUTES:
        raise ConfigError("{0}.route: unknown route {1!r} — one of: "
                          "{2}".format(path, route, ", ".join(ROUTES)))
    join = data.get("join")
    built = _build_join(join, path + ".join") if join is not None else None
    if built is not None:
        wants_grid = built.kind in _GRID_JOIN_KINDS
        has_grid = route in _GRID_ROUTES
        if wants_grid and not has_grid:
            raise ConfigError(
                "{0}: join kind {1!r} reads a parsed cell grid, but route "
                "{2!r} renders flow text — use one of: {3}".format(
                    path, built.kind, route, ", ".join(_GRID_ROUTES)))
        if has_grid and not wants_grid:
            raise ConfigError(
                "{0}: route {1!r} renders a parsed cell grid, but join "
                "kind {2!r} reads flow text — a grid route needs one of: "
                "{3}".format(path, route, built.kind,
                             ", ".join(_GRID_JOIN_KINDS)))
    return SourceConfig(
        id=source_id,
        url=_str(_require(data, "url", path), path + ".url"),
        route=route,
        join=built,
    )


def _build_curriculum(data, path):
    _reject_unknown(data, ("url", "form_phrase", "code", "version",
                           "program_name", "degree_phrase"), path)
    form_phrase = _str(_require(data, "form_phrase", path),
                       path + ".form_phrase")
    if not form_phrase.strip():
        raise ConfigError(
            path + ".form_phrase: must not be blank -- it is the only "
            "gate-able attestation that a fetched plan is THIS offering's")
    def opt_str(key):
        value = data.get(key)
        return None if value is None else _str(value, path + "." + key)

    url = _str(_require(data, "url", path), path + ".url")
    code = opt_str("code")
    version = opt_str("version")
    # A stated code/version must MATCH the URL's own query parameters --
    # otherwise a config typo ships a false attestation ("code": "WRONG"
    # reported as bound). Checked here so it breaks at load, visibly.
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(url).query)
    for label, stated in (("code", code), ("version", version)):
        in_url = (q.get(label) or [None])[0]
        if stated is not None and in_url is not None and stated != in_url:
            raise ConfigError(
                "{0}.{1}: {2!r} contradicts the url's own {1}={3!r} -- an "
                "attestation must not disagree with the document it "
                "names".format(path, label, stated, in_url))
    return CurriculumRef(
        url=url, form_phrase=form_phrase, code=code, version=version,
        program_name=opt_str("program_name"),
        degree_phrase=opt_str("degree_phrase"))


def _build_offering(key, data, path, sources):
    split = _split_offering_key(key)
    if split is None:
        raise ConfigError(
            "{0}: {1!r} is not an offering key -- expected a form "
            "(\"редовна\") or a form with one duration specialised "
            "(\"задочна - 4.5\")".format(path, key))
    form, duration = split
    if form not in ATTENDANCE_FORMS:
        raise ConfigError(
            "{0}: unknown attendance form {1!r} -- one of: {2}. (The fee "
            "tables use neuter adjectives like \"редовно\"; the registry "
            "states feminine ones, and this key addresses the "
            "REGISTRY.)".format(path, form, ", ".join(ATTENDANCE_FORMS)))
    _reject_unknown(data, ("tuition_join", "curriculum"), path)
    if not data:
        raise ConfigError(
            path + ": empty offering recipe -- an offering key with nothing "
            "to do is a half-finished edit, not an intent to configure "
            "nothing")
    tuition_join = data.get("tuition_join")
    curriculum = data.get("curriculum")
    # fee-row ONLY. An Offering is a (form, duration) pair and its fee is
    # a COLUMN of a fee table; sectioned-fee-row selects by SECTION (a
    # language track) and carries no value_headers at all, so it cannot
    # express "this attendance form's fee". Admitting it also admitted
    # the one alias shape no resolver can use: cascade.sectioned_fee_join
    # compiles alias_pattern as a regex, while a JoinRef built here takes
    # a literal alias.
    offering = OfferingConfig(
        tuition_join=(_build_join_ref(
            tuition_join, path + ".tuition_join", sources,
            ("fee-row",), "alias")
            if tuition_join is not None else None),
        curriculum=(_build_curriculum(curriculum, path + ".curriculum")
                    if curriculum is not None else None))
    # Canonical spelling, so a stray space or a missing one around the
    # separator cannot produce a key EduForm.key can never match.
    canonical = form if duration is None else "{0} - {1}".format(form, duration)
    return canonical, offering


def _build_offerings(data, path, sources, rsvu_code):
    if not isinstance(data, dict):
        raise ConfigError("{0}: expected a JSON object keyed by attendance "
                          "form, got {1}".format(path, type(data).__name__))
    if data and rsvu_code is None:
        raise ConfigError(
            path + ": offerings are enumerated from this Program's REGISTRY "
            "row, so the Program must carry an rsvu_code naming that row")
    built = {}
    for key, value in data.items():
        node = "{0}[{1!r}]".format(path, key)
        canonical, offering = _build_offering(key, value, node, sources)
        if canonical in built:
            raise ConfigError(
                "{0}: {1!r} names the same offering as an earlier key -- "
                "both read as {2!r}. Two recipes for one offering is "
                "ambiguous, not additive.".format(node, key, canonical))
        built[canonical] = offering
    return built


def _build_join_ref(data, path, sources, want_kinds, alias_mode):
    """alias_mode: 'alias' | 'alias_pattern' | 'optional-alias'."""
    _reject_unknown(data, ("source", "alias", "alias_pattern"), path)
    source = _str(_require(data, "source", path), path + ".source")
    if source not in sources:
        raise ConfigError("{0}.source: {1!r} is not a configured source "
                          "id".format(path, source))
    join = sources[source].join
    if join is None or join.kind not in want_kinds:
        raise ConfigError(
            "{0}: source {1!r} carries join kind {2!r}, but this field "
            "needs one of: {3}".format(
                path, source, getattr(join, "kind", None),
                ", ".join(want_kinds)))
    alias = data.get("alias")
    alias_pattern = data.get("alias_pattern")
    if alias is not None:
        _str(alias, path + ".alias")
    if alias_pattern is not None:
        _regex(alias_pattern, path + ".alias_pattern")
    if alias is not None and alias_pattern is not None:
        raise ConfigError(path + ": give alias OR alias_pattern, not both")
    if alias_mode == "alias" and alias is None:
        raise ConfigError(path + ": this join matches a literal alias — "
                          "the 'alias' key is required")
    if alias_mode == "alias_pattern" and alias_pattern is None:
        raise ConfigError(path + ": this join matches a row pattern — "
                          "the 'alias_pattern' key is required")
    return JoinRef(source=source, alias=alias, alias_pattern=alias_pattern)


def _build_section_ref(data, path, sources):
    _reject_unknown(data, ("source", "section_pattern"), path)
    source = _str(_require(data, "source", path), path + ".source")
    if source not in sources:
        raise ConfigError("{0}.source: {1!r} is not a configured source "
                          "id".format(path, source))
    join = sources[source].join
    if join is None or join.kind != "fees-page":
        raise ConfigError(
            "{0}: source {1!r} carries join kind {2!r}, but fees_section "
            "needs 'fees-page'".format(path, source,
                                       getattr(join, "kind", None)))
    return SectionRef(
        source=source,
        section_pattern=_str(_require(data, "section_pattern", path),
                             path + ".section_pattern"))


def _build_anchor(anchor_id, data, path, sources):
    _reject_unknown(data, ("source", "pattern"), path)
    source = _str(_require(data, "source", path), path + ".source")
    if "://" not in source and source not in sources:
        raise ConfigError(
            "{0}.source: {1!r} is neither a URL nor a configured source "
            "id".format(path, source))
    pattern = _str(_require(data, "pattern", path), path + ".pattern")
    if _regex(pattern, path + ".pattern").groups < 1:
        raise ConfigError(
            path + ".pattern: needs a capturing group (group 1 is the "
            "anchored value)")
    return AnchorConfig(id=anchor_id, source=source, pattern=pattern)


_PROGRAM_KEYS = ("id", "name", "page", "offerings",
                 "extra_pages", "extra_sources",
                 "lang_page", "adm_page", "tuition_page", "tuition_join",
                 "admission_join", "spravochnik", "language_tracks",
                 "fees_section", "field_anchors", "rsvu_code")


def _build_field_anchors(data, path, anchors):
    field_anchors = _str_dict(data, path)
    for field_name, anchor_id in field_anchors.items():
        if field_name not in FIELDS:
            raise ConfigError(
                "{0}: {1!r} is not a Program field — one of: {2}".format(
                    path, field_name, ", ".join(FIELDS)))
        if anchor_id not in anchors:
            raise ConfigError(
                "{0}.{1}: {2!r} is not a declared anchor id".format(
                    path, field_name, anchor_id))
    return field_anchors


def _build_program(data, path, sources, anchors):
    _reject_unknown(data, _PROGRAM_KEYS, path)
    extra_sources = _str_list(data.get("extra_sources", []),
                              path + ".extra_sources")
    for sid in extra_sources:
        if sid not in sources:
            raise ConfigError(
                "{0}.extra_sources: {1!r} is not a configured source "
                "id".format(path, sid))

    def opt_str(key):
        return (_str(data[key], "{0}.{1}".format(path, key))
                if data.get(key) is not None else None)

    def opt_ref(key, want_kinds, alias_mode):
        if data.get(key) is None:
            return None
        return _build_join_ref(data[key], "{0}.{1}".format(path, key),
                               sources, want_kinds, alias_mode)

    tuition_join = None
    if data.get("tuition_join") is not None:
        # fee-row joins take a literal alias; sectioned ones a row pattern.
        ref_path = path + ".tuition_join"
        source_id = _require(data["tuition_join"], "source", ref_path) \
            if isinstance(data["tuition_join"], dict) else None
        want = ("fee-row", "sectioned-fee-row")
        mode = "alias"
        if (isinstance(source_id, str) and source_id in sources
                and sources[source_id].join is not None
                and sources[source_id].join.kind == "sectioned-fee-row"):
            mode = "alias_pattern"
        tuition_join = _build_join_ref(data["tuition_join"], ref_path,
                                       sources, want, mode)

    rsvu_code = opt_str("rsvu_code")
    program = ProgramConfig(
        id=_str(_require(data, "id", path), path + ".id"),
        name=_str(_require(data, "name", path), path + ".name"),
        page=_str(_require(data, "page", path), path + ".page"),
        extra_pages=_str_list(data.get("extra_pages", []),
                              path + ".extra_pages"),
        extra_sources=extra_sources,
        lang_page=opt_str("lang_page"),
        adm_page=opt_str("adm_page"),
        tuition_page=opt_str("tuition_page"),
        tuition_join=tuition_join,
        admission_join=opt_ref("admission_join", ("ordinance",), "alias"),
        spravochnik=opt_ref("spravochnik", ("spravochnik",),
                            "optional-alias"),
        language_tracks=opt_ref("language_tracks", ("sectioned-fee-row",),
                                "alias_pattern"),
        fees_section=(_build_section_ref(data["fees_section"],
                                         path + ".fees_section", sources)
                      if data.get("fees_section") is not None else None),
        field_anchors=_build_field_anchors(data.get("field_anchors", {}),
                                           path + ".field_anchors", anchors),
        rsvu_code=rsvu_code,
        offerings=_build_offerings(data.get("offerings", {}),
                                   path + ".offerings", sources, rsvu_code),
    )
    return program


# -------------------------------------------------------------- entry points
def parse_site_config(data, origin="<config>"):
    # type: (dict, str) -> SiteConfig
    """Validate one site-config JSON object into a SiteConfig.

    Raises ConfigError on ANY unknown key, bad type, bad regex, dangling
    source reference, duplicate program id, or field-to-join-kind
    mis-wiring. origin (usually the file path) prefixes every error.
    """
    _reject_unknown(data, ("uni_id", "cookies", "sources", "programs",
                           "anchors", "rsvu_id", "program_markers"), origin)
    uni_id = _str(_require(data, "uni_id", origin), origin + ".uni_id")
    cookies = _str_dict(data.get("cookies", {}), origin + ".cookies")
    rsvu_id = (_int(data["rsvu_id"], origin + ".rsvu_id")
              if "rsvu_id" in data else None)
    program_markers = tuple(_str_list(data.get("program_markers", []),
                                      origin + ".program_markers"))

    raw_sources = data.get("sources", {})
    if not isinstance(raw_sources, dict):
        raise ConfigError(origin + ".sources: expected an object of "
                          "source configs keyed by source id")
    sources = {}
    for sid, sdata in raw_sources.items():
        _str(sid, origin + ".sources key")
        sources[sid] = _build_source(
            sid, sdata, "{0}.sources[{1!r}]".format(origin, sid))

    raw_anchors = data.get("anchors", {})
    if not isinstance(raw_anchors, dict):
        raise ConfigError(origin + ".anchors: expected an object of "
                          "anchor configs keyed by anchor id")
    anchors = {}
    for aid, adata in raw_anchors.items():
        _str(aid, origin + ".anchors key")
        anchors[aid] = _build_anchor(
            aid, adata, "{0}.anchors[{1!r}]".format(origin, aid), sources)

    raw_programs = _require(data, "programs", origin)
    if not isinstance(raw_programs, list) or not raw_programs:
        raise ConfigError(origin + ".programs: expected a non-empty list")
    programs = tuple(
        _build_program(p, "{0}.programs[{1}]".format(origin, i), sources,
                       anchors)
        for i, p in enumerate(raw_programs))
    seen = set()
    for p in programs:
        if p.id in seen:
            raise ConfigError("{0}: duplicate program id {1!r}".format(
                origin, p.id))
        seen.add(p.id)

    return SiteConfig(uni_id=uni_id, cookies=cookies, sources=sources,
                      programs=programs, anchors=anchors, rsvu_id=rsvu_id,
                      program_markers=program_markers)


def load_site_config(path):
    # type: (str) -> SiteConfig
    """Load and validate one crawler/configs/<UniID>.json file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError("{0}: not valid JSON: {1}".format(path, exc))
    return parse_site_config(data, origin=str(path))


def load_configs_dir(directory):
    # type: (str) -> Dict[str, SiteConfig]
    """Load every *.json site config in a directory, keyed by uni_id.

    The filename stem must equal the uni_id inside — a mismatch is a
    config bug (the file IS the per-site maintenance surface)."""
    configs = {}
    for path in sorted(Path(directory).glob("*.json")):
        cfg = load_site_config(path)
        if cfg.uni_id != path.stem:
            raise ConfigError(
                "{0}: uni_id {1!r} does not match the filename".format(
                    path, cfg.uni_id))
        if cfg.uni_id in configs:
            raise ConfigError("duplicate uni_id {0!r}".format(cfg.uni_id))
        configs[cfg.uni_id] = cfg
    return configs
