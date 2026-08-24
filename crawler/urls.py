"""urls.json — the per-university URL export (url-scheme ticket 04).

The frontend's whole contract with the URL scheme, one artifact per
university in crawler-out/: canonical PATHS only (no domain, no
trailing slash — no domain exists, and a global sitemap is one step
away from the per-uni exports plus subjects.json), plus the redirect
table the retired_slugs ledger implies.

Redirects resolve IDS to their CURRENT slug at export time: the ledger
maps old slug -> program id (ADR-0006's stable anchor), so after a
second rename every old slug still lands on the program's live path —
nothing is stranded, nothing chains. A retired UNIVERSITY slug fans out
over every program: the old uni prefix once covered them all.

The export never invents a path: a program without a minted slug (or a
university without one) ships "path": null and its id under "missing" —
the honest-null discipline, applied to URLs.

Python 3.9 compatible; stdlib only.
"""
__all__ = ["build_urls_report", "URLS_REPORT_NAME"]

URLS_REPORT_NAME = "urls.json"


def build_urls_report(site, generated_at=None):
    # type: (...) -> dict
    """The urls.json payload for one SiteConfig. Pure over the config."""
    uni_path = "/" + site.slug if site.slug else None
    programs = []
    missing = []
    current_paths = {}   # program id -> live path, for redirect targets
    for p in site.programs:
        path = None
        if uni_path and p.slug:
            path = "{0}/{1}".format(uni_path, p.slug)
            current_paths[p.id] = path
        else:
            missing.append(p.id)
        programs.append({"program_id": p.id, "slug": p.slug,
                         "subject": p.subject, "path": path})

    redirects = []
    for old, target in sorted(site.retired_slugs.items()):
        if target == site.uni_id:
            # A retired uni slug: the old prefix covered the uni page
            # and every program under it.
            if uni_path:
                redirects.append({"from": "/" + old, "to": uni_path})
                for p in site.programs:
                    if p.id in current_paths:
                        redirects.append({
                            "from": "/{0}/{1}".format(old, p.slug),
                            "to": current_paths[p.id]})
                # A wild URL can combine both retirements
                # (/old-uni/old-prog); both mappings are in the ledger,
                # so the redirect is derivable, not invented.
                for old_prog, prog_target in sorted(
                        site.retired_slugs.items()):
                    if prog_target in current_paths:
                        redirects.append({
                            "from": "/{0}/{1}".format(old, old_prog),
                            "to": current_paths[prog_target]})
        elif target in current_paths and uni_path:
            redirects.append({"from": "{0}/{1}".format(uni_path, old),
                              "to": current_paths[target]})
        # else: rollout gap — the target has no live path yet, and a
        # redirect without a destination would be an invented URL.

    report = {
        "uni_id": site.uni_id,
        "university": {"slug": site.slug, "path": uni_path},
        "programs": programs,
        "redirects": redirects,
        "missing": missing,
    }
    if generated_at is not None:
        report["generated_at"] = generated_at
    return report
