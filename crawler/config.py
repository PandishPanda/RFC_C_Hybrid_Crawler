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

  SiteConfig            uni_id, cookies, sources{id: SourceConfig}, programs[],
                        display_name?, slug?, city?, retired_slugs?
                        (URL scheme, .scratch/url-scheme/spec.md: the
                        hand-authored university record and the redirect
                        ledger {old slug: program id | uni_id}; slugs are
                        VALIDATED here — charset, uniqueness, reserved
                        root words — and minted by `crawler slugs` + a
                        human, never generated at load)
  subjects.json         {subjects: [{slug, name}]} — the subject-landing
                        taxonomy, loaded by load_subjects; program
                        `subject` keys must reference it (checked in
                        load_configs_dir, the one place that sees every
                        file, where cross-university slug uniqueness is
                        checked too)
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
                        suppress_labels? ({field: [label ids]}),
                        slug? (public path segment, unique per university),
                        subject? (a subjects.json slug)

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

from crawler.slugs import RESERVED_ROOT_SLUGS, SLUG_RE

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
    "SiteConfig",
    "parse_site_config",
    "load_site_config",
    "load_configs_dir",
    "load_subjects",
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
    # Compose EVERY matching row of the program's own track into one
    # value («(I курс): 500 евро; (II курс): 410 евро; ...») instead of
    # taking the first. Opt-in: fee orders that state one row per year
    # band need it, and a partial fee is a value the labeller grades
    # WRONG (measured on Кинезитерапия). Other users of this join kind
    # keep first-hit semantics.
    compose_bands: bool = False
    # Rows whose text matches are skipped outright: a fee order may list
    # a DIFFERENT schedule (задочна/part-time) for the same programme,
    # sometimes merged onto one line so its BGN column reads as a EUR
    # fee. Excluding the row beats contorting the alias, which would
    # drag table row numbers into the composed value.
    row_exclude: str = ""
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

    scope: required when a program uses this anchor on a page that is not
    its own (the measured MUVarna fabrication was an anchor on an
    unrelated project page shipping a plausible degree, 2026-08-22):
      "names-program"  the page must name the program at resolve time,
                       else the anchor yields nothing;
      "page-wide"      a human attested this page's claim applies to the
                       wired programs (the ADR-0003 escape hatch — the
                       config diff is the record).
    None is valid only for own-page anchors, where scope is inherent.
    """
    id: str
    source: str
    pattern: str
    scope: Optional[str] = None


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
    # {field -> label ids the shared library must NOT apply to this
    # program}. A human-adjudicated stale-green verdict as config data: a
    # verbatim-present value whose claim the site itself contradicts
    # elsewhere (e.g. an outdated fee on the program page vs the current
    # fees page). Suppression never invents a value — the field falls
    # through to the next mechanism or an honest null.
    suppress_labels: Mapping[str, Tuple[str, ...]] = field(
        default_factory=dict)
    # URL-scheme identity (.scratch/url-scheme/spec.md): the public path
    # segment and the subject-landing membership. Minted by a human from
    # the ``crawler slugs`` proposal — the loader validates, never
    # generates. Optional during rollout; completeness is the backfill's
    # concern, not the schema's.
    slug: Optional[str] = None
    subject: Optional[str] = None


@dataclass(frozen=True)
class SiteConfig:
    uni_id: str
    sources: Mapping[str, SourceConfig]
    programs: Tuple[ProgramConfig, ...]
    cookies: Mapping[str, str] = field(default_factory=dict)
    anchors: Mapping[str, AnchorConfig] = field(default_factory=dict)
    default_language: Optional[str] = None
    # ADR-0007: the language of instruction this site's programs use when
    # no document of theirs states one. Site knowledge, so it is config
    # data (ADR-0001) rather than a fleet-wide constant: AUBG teaches in
    # English and VUM in both, and a hardcoded default would ship a wrong
    # value at the first university that does not fit. Absent = derive
    # nothing. The config diff records who asserted this, the same
    # discipline as a page-wide anchor attestation.

    # URL-scheme university record (.scratch/url-scheme/spec.md): the
    # hand-authored public identity this repo otherwise lacks. slug is a
    # short COMMON name (sofiyski-universitet), a human judgment call per
    # ADR-0003; city is metadata only (no city URLs yet — /gradove/ is
    # reserved). retired_slugs is the redirect ledger: every slug ever
    # minted and since replaced, mapped to the PROGRAM ID (or this
    # university's own uni_id) it belonged to — ids, not paths, so a
    # second rename never strands the first redirect.
    display_name: Optional[str] = None
    slug: Optional[str] = None
    city: Optional[str] = None
    retired_slugs: Mapping[str, str] = field(default_factory=dict)

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


def _slug(value, path):
    value = _str(value, path)
    if not SLUG_RE.match(value):
        raise ConfigError(
            "{0}: {1!r} is not a valid slug — lowercase latin words and "
            "digits joined by single hyphens (mint one with `python3 -m "
            "crawler slugs`)".format(path, value))
    return value


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
                           "currency_suffix", "compose_bands",
                           "row_exclude", "context"), path)
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
        compose_bands=_bool(data.get("compose_bands", False),
                            path + ".compose_bands"),
        row_exclude=_str(data["row_exclude"], path + ".row_exclude")
        if "row_exclude" in data else "",
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
    _reject_unknown(data, ("source", "pattern", "scope"), path)
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
    scope = data.get("scope")
    if scope is not None and scope not in ("names-program", "page-wide"):
        raise ConfigError(
            path + ".scope: {0!r} is not a scope -- one of: "
            "names-program, page-wide".format(scope))
    return AnchorConfig(id=anchor_id, source=source, pattern=pattern,
                        scope=scope)


_PROGRAM_KEYS = ("id", "name", "page",
                 "extra_pages", "extra_sources",
                 "lang_page", "adm_page", "tuition_page", "tuition_join",
                 "admission_join", "spravochnik", "language_tracks",
                 "fees_section", "field_anchors", "suppress_labels",
                 "slug", "subject")


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


def _check_anchor_scopes(program, anchors, path, sources):
    """Off-page anchors must declare scope; own-page scope is inherent.
    An anchor may address the program's own document via a source id --
    resolve to the URL before comparing (SHU wires its per-program PDFs
    that way)."""
    for field_name, anchor_id in program.field_anchors.items():
        a = anchors[anchor_id]
        src_url = sources[a.source].url if a.source in sources else a.source
        if src_url != program.page and a.scope is None:
            raise ConfigError(
                "{0}.field_anchors.{1}: anchor {2!r} points at {3!r}, "
                "which is not this program's page -- declare its scope "
                "(names-program or page-wide). An unscoped off-page "
                "anchor shipped a fabricated degree from an unrelated "
                "page (measured 2026-08-22)".format(
                    path, field_name, anchor_id, a.source))


def _build_suppress_labels(data, path):
    if not isinstance(data, dict):
        raise ConfigError(path + ": must be an object of "
                          "{field: [label ids]}")
    out = {}
    for field_name, ids in data.items():
        if field_name not in FIELDS:
            raise ConfigError(
                "{0}: {1!r} is not a Program field — one of: {2}".format(
                    path, field_name, ", ".join(FIELDS)))
        out[field_name] = tuple(_str_list(
            ids, "{0}.{1}".format(path, field_name)))
        if not out[field_name]:
            raise ConfigError(
                "{0}.{1}: empty list — remove the key instead".format(
                    path, field_name))
    return out


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
        suppress_labels=_build_suppress_labels(
            data.get("suppress_labels", {}), path + ".suppress_labels"),
        slug=(_slug(data["slug"], path + ".slug")
              if data.get("slug") is not None else None),
        subject=opt_str("subject"),
    )
    # /<uni>/ucheben-plan must stay free: it is the reserved child
    # segment of every specialty page, so no program may claim it.
    if program.slug in RESERVED_ROOT_SLUGS:
        raise ConfigError(
            "{0}.slug: {1!r} is a reserved URL segment".format(
                path, program.slug))
    _check_anchor_scopes(program, anchors, path, sources)
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
                           "anchors", "program_markers",
                           "default_language", "display_name", "slug",
                           "city", "retired_slugs"), origin)
    uni_id = _str(_require(data, "uni_id", origin), origin + ".uni_id")
    display_name = (_str(data["display_name"], origin + ".display_name")
                    if data.get("display_name") is not None else None)
    city = (_str(data["city"], origin + ".city")
            if data.get("city") is not None else None)
    uni_slug = None
    if data.get("slug") is not None:
        uni_slug = _slug(data["slug"], origin + ".slug")
        if uni_slug in RESERVED_ROOT_SLUGS:
            raise ConfigError(
                "{0}.slug: {1!r} is a reserved root URL segment".format(
                    origin, uni_slug))
    cookies = _str_dict(data.get("cookies", {}), origin + ".cookies")
    program_markers = tuple(_str_list(data.get("program_markers", []),
                                      origin + ".program_markers"))
    default_language = data.get("default_language")
    if default_language is not None:
        default_language = _str(default_language,
                                origin + ".default_language")

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
    live_slugs = {}
    for p in programs:
        if p.slug is None:
            continue
        if p.slug in live_slugs:
            raise ConfigError(
                "{0}: programs {1!r} and {2!r} share the slug {3!r} — a "
                "human names the twins apart (no auto-suffix)".format(
                    origin, live_slugs[p.slug], p.id, p.slug))
        live_slugs[p.slug] = p.id

    retired_raw = data.get("retired_slugs", {})
    retired = _str_dict(retired_raw, origin + ".retired_slugs")
    for old, target in retired.items():
        rpath = "{0}.retired_slugs[{1!r}]".format(origin, old)
        _slug(old, rpath)
        if old in live_slugs or old == uni_slug:
            raise ConfigError(
                "{0}: {1!r} is still a live slug — retire it only after "
                "the rename".format(rpath, old))
        if target != uni_id and target not in seen:
            raise ConfigError(
                "{0}: target {1!r} is neither a program id nor this "
                "uni_id — the ledger maps to ids, and a dangling id "
                "cannot redirect".format(rpath, target))

    return SiteConfig(uni_id=uni_id, cookies=cookies, sources=sources,
                      programs=programs, anchors=anchors,
                      program_markers=program_markers,
                      default_language=default_language,
                      display_name=display_name, slug=uni_slug, city=city,
                      retired_slugs=retired)


def load_site_config(path):
    # type: (str) -> SiteConfig
    """Load and validate one crawler/configs/<UniID>.json file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError("{0}: not valid JSON: {1}".format(path, exc))
    return parse_site_config(data, origin=str(path))


def load_subjects(path):
    # type: (str) -> Dict[str, str]
    """Load configs/subjects.json: the subject-landing taxonomy.

    Free-form and search-led (.scratch/url-scheme/spec.md) — subjects
    are named the way students search, not the way the класификатор
    talks. Returns {subject slug: display name}. Program `subject` keys
    must reference a slug in here; load_configs_dir enforces that."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError("{0}: not valid JSON: {1}".format(path, exc))
    origin = str(path)
    _reject_unknown(data, ("subjects",), origin)
    raw = _require(data, "subjects", origin)
    if not isinstance(raw, list):
        raise ConfigError(origin + ".subjects: expected a list")
    subjects = {}
    for i, entry in enumerate(raw):
        epath = "{0}.subjects[{1}]".format(origin, i)
        _reject_unknown(entry, ("slug", "name"), epath)
        slug = _slug(_require(entry, "slug", epath), epath + ".slug")
        name = _str(_require(entry, "name", epath), epath + ".name")
        if slug in subjects:
            raise ConfigError("{0}: duplicate subject slug {1!r}".format(
                epath, slug))
        subjects[slug] = name
    return subjects


SUBJECTS_FILENAME = "subjects.json"


def load_configs_dir(directory):
    # type: (str) -> Dict[str, SiteConfig]
    """Load every *.json site config in a directory, keyed by uni_id.

    The filename stem must equal the uni_id inside — a mismatch is a
    config bug (the file IS the per-site maintenance surface).

    subjects.json is the URL-scheme taxonomy, not a site config; this
    is also the one place that sees every file, so the cross-file URL
    checks live here: program `subject` references must exist in the
    taxonomy, and no two universities may share a slug (they share the
    root URL namespace)."""
    directory = Path(directory)
    subjects_path = directory / SUBJECTS_FILENAME
    subjects = load_subjects(subjects_path) if subjects_path.exists() \
        else None
    configs = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == SUBJECTS_FILENAME:
            continue
        cfg = load_site_config(path)
        if cfg.uni_id != path.stem:
            raise ConfigError(
                "{0}: uni_id {1!r} does not match the filename".format(
                    path, cfg.uni_id))
        if cfg.uni_id in configs:
            raise ConfigError("duplicate uni_id {0!r}".format(cfg.uni_id))
        configs[cfg.uni_id] = cfg

    slug_owners = {}
    for cfg in configs.values():
        if cfg.slug is not None:
            if cfg.slug in slug_owners:
                raise ConfigError(
                    "universities {0!r} and {1!r} share the slug {2!r} — "
                    "uni slugs share one root namespace".format(
                        slug_owners[cfg.slug], cfg.uni_id, cfg.slug))
            slug_owners[cfg.slug] = cfg.uni_id
        for p in cfg.programs:
            if p.subject is None:
                continue
            if subjects is None:
                raise ConfigError(
                    "{0} program {1!r}: subject {2!r} but no {3} exists "
                    "in {4}".format(cfg.uni_id, p.id, p.subject,
                                    SUBJECTS_FILENAME, directory))
            if p.subject not in subjects:
                raise ConfigError(
                    "{0} program {1!r}: subject {2!r} is not in {3} — a "
                    "dangling reference cannot land on a subject "
                    "page".format(cfg.uni_id, p.id, p.subject,
                                  SUBJECTS_FILENAME))
    return configs
