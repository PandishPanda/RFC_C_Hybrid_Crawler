"""RSVU registry rows -- the true Coverage denominator (ticket 05, CONTEXT.md).

rsvu.mon.bg is an Angular SPA whose data lives behind a real JSON API:
``/api/universities/bg``, ``/api/major/<rsvuUniId>/bg``, ``/api/degree/bg``,
``/api/universities/minors/<rsvuUniId>/<majorId>/<degreeCode>/bg`` --
discovered 2026-08-15 from a JS-executing browser session's network log
while looking up VUM's real registry rows.

Plain HTTP clients cannot reach it: rsvu.mon.bg runs a Cloudflare managed
challenge that requires JS execution plus a resulting ``cf_clearance``
cookie. Verified 2026-08-15 -- both ``curl`` and this module's own
``RegistryClient`` get an HTTP 403 "Just a moment..." challenge page on
every path tried, including the API paths, not just the document root.

``RegistryClient`` is therefore a seam-complete but NOT runnable adapter in
this repo today, same shape as ``llm_tail.APIAdapter`` -- it exists so a
session with a working bypass (a maintained ``cf_clearance`` cookie, a
browser-driving fetch, a Cloudflare-bypass service) has somewhere to plug
in, and it fails loudly (``RegistryUnavailable``) rather than silently
returning nothing.

The working path today is ``load_captured_export()``: a hand-captured JSON
snapshot of the real API responses, taken via a JS-executing browser
session and committed under ``crawler/registry_exports/<UniID>.json``. This
is data (RFC v2 Q7 philosophy: site knowledge is data, not code), and its
own capture provenance travels with it in the file (captured_at, source) --
refreshed by hand, exactly like the anchor tier this whole project is
trying to retire.

Measured discrepancy (VUM, 2026-08-15): ticket 05's text cites "35 rows"
for VUM from earlier RFC-stage research; the live API returns 18 unique
rows across all 6 professional fields x 5 non-"general" degree codes for
BOTH rsvu ids 114 (parent accreditation entity) and 125 (the vum.bg
branch) -- identical results from both ids. The 35 figure is unreconciled
and should not be treated as authoritative; 18 is live-measured and is
what this module ships. Whether the API silently excludes de-accredited /
historical rows (which would matter for adjudicating truly dormant
Programs) is not established either way.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

__all__ = [
    "RegistryRow", "RegistryExport", "load_captured_export",
    "RegistryClient", "RegistryUnavailable", "parse_edu_forms", "EduForm",
]

DEFAULT_EXPORTS_DIR = Path(__file__).resolve().parent / "registry_exports"

RSVU_API_BASE = "https://rsvu.mon.bg/api"

RSVU_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


class RegistryUnavailable(RuntimeError):
    """The live RSVU API could not be reached (see module docstring)."""


# One "<form> - <years>" item, e.g. "редовна - 4.5". The duration is kept
# as the STRING the registry printed, never coerced to float: "4.5" and
# "4.50" are different things to say, and an Offering's identity should
# not depend on this module's rounding.
#
# The form must open with a WORD character, not merely a non-space: "\S"
# let "--4" parse as the form "-", inventing a degenerate Offering out of
# punctuation. The duration accepts only a DOT decimal -- a comma decimal
# cannot reach here (items are comma-separated, so "4,5" is split first),
# and pretending to accept one would be dead code hiding a real hazard;
# see _BARE_NUMBER_RX.
_EDU_FORM_ITEM_RX = re.compile(
    r"^\s*(\w[^-]*?)\s*-\s*(\d+(?:\.\d+)?)\s*$", re.UNICODE)

# A fragment that is only a number. This can only arise from splitting a
# COMMA DECIMAL ("редовна - 4,5" -> "редовна - 4" + "5"), and it is the
# reason comma decimals must be refused rather than parsed: the leading
# fragment matches perfectly and yields a TRUNCATED duration -- 4,5 years
# read as 4 -- while looking like a clean pair. In ticket 18's terms that
# silently attaches the "задочна - 4" recipe to a 4.5-year Offering. No
# committed export uses comma decimals today; this keeps that latent
# defect from becoming a live wrong value.
_BARE_NUMBER_RX = re.compile(r"^\s*\d+\s*$")


@dataclass(frozen=True)
class EduForm:
    """One (attendance form, duration) pair a registry row states.

    `item` is the verbatim text this was parsed from -- ADR-0004 requires
    every Offering record to carry its `edu_forms_item`, and a bare
    (form, duration) tuple cannot supply it.

    `key` is the canonical recipe key config addresses an Offering by
    ("задочна - 4.5"). This module FORMATS that key; crawler.config
    PARSES it, with its own copy of the separator and a comment pointing
    here -- config is a leaf schema module that deliberately imports no
    crawler module (see its ROUTES note), so the two agree by a stated
    contract rather than by an import.
    """
    form: str
    duration_years: str          # the STRING the registry printed
    item: str                    # verbatim source text

    @property
    def key(self):
        # type: () -> str
        return "{0} - {1}".format(self.form, self.duration_years)


def parse_edu_forms(edu_forms):
    # type: (Optional[str]) -> Tuple[Tuple[EduForm, ...], Tuple[str, ...]]
    """Split a registry row's own `edu_forms` into EduForm records.

    Returns (forms, unparsed) -- forms in the order the REGISTRY states
    them, duplicates preserved as-is (deduping would silently change the
    Offering count, which is the denominator of every completeness
    figure), and every item that did not parse returned verbatim rather
    than guessed at or dropped in silence.

    Parsing is PER ITEM on purpose: one malformed entry must not discard
    the row's other Offerings, because a row that half-parses is still
    mostly usable and a row that silently loses Offerings is not
    detectable downstream.

    A COMMA DECIMAL is refused as a unit rather than half-read. Items are
    comma-separated, so "редовна - 4,5" arrives split as "редовна - 4"
    and "5"; the first half matches cleanly and would ship a 4.5-year
    Offering as a 4-year one. Whenever a bare-number fragment follows a
    parsed pair, that pair is withdrawn and the two fragments are
    reported together, verbatim. Reporting beats guessing: no committed
    export uses comma decimals, and if one ever does it should surface
    loudly rather than quietly truncate.

    The parser is deliberately OPEN where config is closed -- an
    unrecognised form parses fine here and simply has no recipe to
    attach. The registry is the enumerating authority (ADR-0004); this
    module reports what it says, it does not police it.
    """
    if not edu_forms or not edu_forms.strip():
        return (), ()
    forms = []
    unparsed = []
    for raw in edu_forms.split(","):
        if not raw.strip():
            continue
        if _BARE_NUMBER_RX.match(raw) and forms:
            withdrawn = forms.pop()
            unparsed.append("{0},{1}".format(withdrawn.item, raw.strip()))
            continue
        m = _EDU_FORM_ITEM_RX.match(raw)
        if m is None:
            unparsed.append(raw.strip())
            continue
        forms.append(EduForm(form=m.group(1), duration_years=m.group(2),
                             item=raw.strip()))
    return tuple(forms), tuple(unparsed)


@dataclass(frozen=True)
class RegistryRow:
    """One RSVU registry row -- CONTEXT.md's Program identity unit."""
    id: int
    code: str
    name: str
    major_id: int
    major_name: str
    degree_code: int
    degree_name: str
    edu_forms: str = ""


@dataclass(frozen=True)
class RegistryExport:
    uni_id: str
    rsvu_uni_id: int
    rsvu_uni_name: str
    captured_at: str
    source: str
    rows: Tuple[RegistryRow, ...]


def _row_from_dict(r):
    return RegistryRow(
        id=r["id"], code=r["code"], name=r["name"],
        major_id=r["major_id"], major_name=r["major_name"],
        degree_code=r["degree_code"], degree_name=r["degree_name"],
        edu_forms=r.get("edu_forms", ""))


def load_captured_export(uni_id, exports_dir=None):
    # type: (str, Optional[str]) -> RegistryExport
    """Load a hand-captured registry export (the working path today).

    Raises FileNotFoundError with the expected path if this university has
    no export yet -- never a silent empty registry.
    """
    path = Path(exports_dir or DEFAULT_EXPORTS_DIR) / "{0}.json".format(uni_id)
    if not path.exists():
        raise FileNotFoundError(
            "no captured registry export for {0!r} at {1} -- capture one "
            "via a JS-executing browser session against rsvu.mon.bg (see "
            "crawler/registry.py module docstring)".format(uni_id, path))
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["uni_id"] != uni_id:
        raise ValueError(
            "{0}: file's uni_id {1!r} does not match the requested "
            "{2!r} -- wrong export file, or it was copied/renamed "
            "without updating its content".format(
                path, data["uni_id"], uni_id))
    rows = tuple(_row_from_dict(r) for r in data["rows"])
    return RegistryExport(
        uni_id=data["uni_id"], rsvu_uni_id=data["rsvu_uni_id"],
        rsvu_uni_name=data.get("rsvu_uni_name", ""),
        captured_at=data["captured_at"], source=data["source"], rows=rows)


class RegistryClient:
    """Live RSVU API client -- seam-complete, NOT runnable in this repo
    (see module docstring: a Cloudflare managed challenge blocks plain HTTP
    clients). Raises RegistryUnavailable on the first blocked call rather
    than silently returning nothing, same discipline as llm_tail.APIAdapter.
    """

    def __init__(self, session=None):
        import requests
        self.session = session or requests.Session()

    def _get(self, path):
        resp = self.session.get(
            RSVU_API_BASE + path, timeout=30,
            headers={"User-Agent": RSVU_USER_AGENT, "Accept": "application/json"})
        if resp.status_code == 403:
            raise RegistryUnavailable(
                "rsvu.mon.bg returned HTTP 403 for {0!r} -- plain HTTP "
                "clients cannot pass its Cloudflare managed challenge "
                "(verified 2026-08-15). Use load_captured_export() with a "
                "hand-captured export instead, or re-capture one via a "
                "JS-executing browser session.".format(path))
        resp.raise_for_status()
        return resp.json()

    def universities(self):
        return self._get("/universities/bg")

    def majors(self, rsvu_uni_id):
        return self._get(
            "/major/{0}/bg?includeOnlyDoctors=false".format(rsvu_uni_id))

    def degrees(self):
        return self._get("/degree/bg")

    def specialties(self, rsvu_uni_id, major_id, degree_code):
        return self._get("/universities/minors/{0}/{1}/{2}/bg".format(
            rsvu_uni_id, major_id, degree_code))

    def fetch_export(self, uni_id, rsvu_uni_id):
        # type: (str, int) -> RegistryExport
        """Walk every (major x degree) combination and union the rows.

        Never runnable today (see class docstring) -- kept for the day the
        Cloudflare block is lifted, so this doesn't need re-deriving.
        """
        majors = self.majors(rsvu_uni_id)
        degrees = [d for d in self.degrees() if d.get("code")]
        rows = []
        seen = set()
        for major in majors:
            for degree in degrees:
                for r in self.specialties(rsvu_uni_id, major["id"], degree["code"]):
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                    rows.append(RegistryRow(
                        id=r["id"], code=r["code"], name=r["name"],
                        major_id=major["id"], major_name=major["name"],
                        degree_code=degree["code"], degree_name=degree["name"],
                        edu_forms=r.get("eduForms", "")))
        return RegistryExport(
            uni_id=uni_id, rsvu_uni_id=rsvu_uni_id, rsvu_uni_name="",
            captured_at="", source="live RegistryClient.fetch_export",
            rows=tuple(rows))
