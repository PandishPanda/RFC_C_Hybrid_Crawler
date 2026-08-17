"""Version-pinned source detection (ticket 22).

CONTEXT.md names **stale-green drift**: a university publishes next
year's data on a new page while the old page stays live and unchanged, so
freshness checks stay green while the data goes stale. Usually that is
something a site does TO us.

A version-pinned URL is the same failure, self-inflicted. Ticket 14 wired
11 tier-B anchors at
``.../View/Curriculum?code=CB3.7.4.1&version=5``. When Ruse publishes
version 6 that URL keeps returning HTTP 200 with byte-identical content
FOREVER: the fetch succeeds, the content hash is unchanged, the gate
passes, the value is still verbatim in the artifact. Every signal this
project has stays green, and the data is simply from a superseded plan.

Two halves, deliberately scoped differently:

* DETECTION is generic -- any configured URL carrying an explicit
  ``version=`` query parameter is pinned, whatever site it belongs to.
* The CURRENT-version lookup is curriculum-SPECIFIC. Only the e-curriculum
  list view is a document we know how to read (it states ``data-code`` and
  ``data-version`` per plan, server-rendered -- verified 2026-08-16, 113
  entries without JS). Another site's "what is current" page would need
  its own reader, and inventing a general one against a single known
  example would be guessing at a shape nobody has seen.

This module REPORTS and never re-points. Which plan an Offering belongs to
is a human-confirmed assignment (ADR-0003); silently following a pin to a
newer document nobody confirmed would stack an unverifiable guess on top
of a confirmed one -- the exact move that ADR forbids.
"""
import re
from urllib.parse import parse_qs, urlsplit

__all__ = ["pinned_sources", "listed_versions", "check_version_drift",
           "CURRICULUM_LIST_URL"]

# The e-curriculum list view, whose rows state the CURRENT version of each
# plan code. degreeCode 002 is the bachelor's list.
CURRICULUM_LIST_URL = (
    "https://e-curriculum.uni-ruse.bg/app/View/List?degreeCode=002")

_ROW_RX = re.compile(
    r'data-code="([^"]+)"[^>]*?data-version="([^"]+)"'
    r'|data-version="([^"]+)"[^>]*?data-code="([^"]+)"')


def _pin_of(url):
    """(code, version) if URL pins an explicit version, else None."""
    if not url:
        return None
    q = parse_qs(urlsplit(url).query)
    version = (q.get("version") or [None])[0]
    if version is None:
        return None
    return (q.get("code") or [None])[0], version


def pinned_sources(site):
    """[(where, url, code, version)] for every version-pinned config URL.

    `where` is the config path a human edits to fix it -- an anchor id, a
    source id, or a program id -- so a drift report points at the line to
    change rather than at a URL to go hunting for.
    """
    found = []
    for anchor_id, anchor in sorted(site.anchors.items()):
        pin = _pin_of(getattr(anchor, "source", None))
        if pin:
            found.append(("anchors[{0!r}]".format(anchor_id),
                          anchor.source) + pin)
    for source_id, source in sorted(site.sources.items()):
        pin = _pin_of(source.url)
        if pin:
            found.append(("sources[{0!r}]".format(source_id),
                          source.url) + pin)
    for program in site.programs:
        for label, url in (("page", program.page),
                           ("lang_page", program.lang_page),
                           ("adm_page", program.adm_page),
                           ("tuition_page", program.tuition_page)):
            pin = _pin_of(url)
            if pin:
                found.append(("programs[{0!r}].{1}".format(program.id, label),
                              url) + pin)
        for url in program.extra_pages:
            pin = _pin_of(url)
            if pin:
                found.append(("programs[{0!r}].extra_pages".format(program.id),
                              url) + pin)
    return found


def listed_versions(list_html):
    """{plan code: current version} from an e-curriculum list page.

    Server-rendered, so a plain fetch is enough -- no JS execution and no
    browser in the refresh loop.
    """
    out = {}
    for m in _ROW_RX.finditer(list_html or ""):
        code = m.group(1) or m.group(4)
        version = m.group(2) or m.group(3)
        if code and version:
            out[code] = version
    return out


def check_version_drift(site, current_by_code):
    """Drift entries for every pinned source the listing has superseded.

    A pin whose code the listing does not mention is reported too, as
    `unknown` -- a plan that vanished from the list is at least as
    interesting as one that moved on, and silence would read as fine.
    """
    drift = []
    for where, url, code, version in pinned_sources(site):
        if code is None:
            continue
        current = current_by_code.get(code)
        if current is None:
            drift.append({"where": where, "url": url, "code": code,
                          "pinned_version": version, "current_version": None,
                          "status": "unknown",
                          "detail": "this plan code is not in the listing; "
                                    "it may have been withdrawn or renamed"})
        elif str(current) != str(version):
            drift.append({"where": where, "url": url, "code": code,
                          "pinned_version": version,
                          "current_version": str(current),
                          "status": "superseded",
                          "detail": "a newer version is published; the "
                                    "pinned URL still returns 200 with "
                                    "unchanged content, so no freshness "
                                    "signal can see this"})
    return drift
