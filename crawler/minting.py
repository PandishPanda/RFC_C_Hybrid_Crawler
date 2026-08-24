"""`crawler slugs <UniID>` — the URL-scheme minting proposer.

Proposes, never writes (ADR-0003: onboarding proposes, humans promote —
the same discipline for slugs). For every program entry missing a
`slug`, the proposal is crawler.slugs.slugify(name), printed as a
ready-to-paste JSON fragment; everything a human must DECIDE is a flag,
printed loudly:

  collision    two programs of one university land on the same slug
               (bachelor/master twins, BG/EN language tracks). The spec
               forbids auto-suffixes — a human names them apart.
  reserved     the proposal is a reserved URL segment (ucheben-plan...).
  unsluggable  the name slugs to nothing (parenthetical-only) — a human
               authors a slug from scratch.

The university slug is never really proposed: the spec wants a short
COMMON name (sofiyski-universitet), which is a human judgment call, so
the slugified uni_id ships only as a clearly-marked placeholder.

Exit contract: 0 iff nothing is missing and nothing is flagged — so the
command doubles as the backfill's completeness check per university.

Python 3.9 compatible; stdlib only.
"""
import json

from crawler.slugs import RESERVED_ROOT_SLUGS, SlugError, slugify

__all__ = ["propose_slugs", "render_report"]


def propose_slugs(site):
    # type: (...) -> dict
    """The minting report for one SiteConfig: proposals + flags.

    Pure over the config — reads nothing, writes nothing."""
    proposals = []
    flags = []
    # slug -> [program ids], existing slugs and viable proposals alike:
    # a proposal that collides with an already-minted slug is the same
    # human decision as two colliding proposals.
    claims = {}
    for program in site.programs:
        if program.slug is not None:
            claims.setdefault(program.slug, []).append(program.id)
            continue
        try:
            slug = slugify(program.name)
        except SlugError:
            flags.append({
                "kind": "unsluggable", "program_id": program.id,
                "name": program.name,
                "detail": "the name slugs to nothing — author one by "
                          "hand"})
            continue
        if slug in RESERVED_ROOT_SLUGS:
            flags.append({
                "kind": "reserved", "program_id": program.id,
                "name": program.name, "slug": slug,
                "detail": "{0!r} is a reserved URL segment".format(slug)})
            continue
        proposals.append({"program_id": program.id, "name": program.name,
                          "slug": slug})
        claims.setdefault(slug, []).append(program.id)

    for slug, ids in sorted(claims.items()):
        if len(ids) > 1:
            flags.append({
                "kind": "collision", "slug": slug, "program_ids": ids,
                "detail": "one university, one slug — name the twins "
                          "apart by hand (no auto-suffix)"})

    missing = [key for key in ("slug", "display_name")
               if getattr(site, key) is None]
    university = {
        "slug": site.slug,
        "display_name": site.display_name,
        "missing": missing,
        "needs_human": bool(missing),
        "placeholder": None,
    }
    if site.slug is None:
        try:
            university["placeholder"] = slugify(site.uni_id)
        except SlugError:
            pass

    complete = (not proposals and not flags
                and not university["needs_human"])
    return {"uni_id": site.uni_id, "university": university,
            "proposals": proposals, "flags": flags, "complete": complete}


def render_report(report):
    # type: (dict) -> str
    """Human-readable minting report, pasteable fragments included."""
    lines = ["slug minting — {0}".format(report["uni_id"])]
    uni = report["university"]
    if "slug" in uni["missing"]:
        lines.append(
            '  university slug MISSING — spec wants a short COMMON name '
            '(human judgment); placeholder only: "slug": {0}'.format(
                json.dumps(uni["placeholder"])))
    if "display_name" in uni["missing"]:
        lines.append(
            "  university display_name MISSING — the official Bulgarian "
            "name, authored by hand")
    for prop in report["proposals"]:
        lines.append('  {0} ({1}): add "slug": {2}'.format(
            prop["program_id"], prop["name"],
            json.dumps(prop["slug"], ensure_ascii=False)))
    for flag in report["flags"]:
        if flag["kind"] == "collision":
            lines.append("  COLLISION on {0!r}: {1} — {2}".format(
                flag["slug"], " vs ".join(flag["program_ids"]),
                flag["detail"]))
        else:
            lines.append("  {0} {1} ({2}): {3}".format(
                flag["kind"].upper(), flag["program_id"], flag["name"],
                flag["detail"]))
    if report["complete"]:
        lines.append("  complete: every slug minted, nothing flagged")
    return "\n".join(lines)
