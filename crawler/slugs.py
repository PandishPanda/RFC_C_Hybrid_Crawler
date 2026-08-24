"""Slug core for the public URL scheme (.scratch/url-scheme/spec.md).

A program's slug is part of its identity, minted once as config data and
validated — never generated — by the loader (the ADR-0006 doctrine
extended to URLs). This module is the single source of the rules both
sides share: the ``crawler slugs`` proposer and config.py's validator.

Transliteration is the official Streamlined System (the 2009
transliteration law): the standard students have seen on road signs and
ID cards, and the only codified — hence defensible and deterministic —
choice. The word-final -ия→ia exception („София" → sofia) is applied per
maximal Cyrillic LETTER-RUN, not per whitespace token: the real configs
carry „Английска филология: Лингвистика и превод", where the colon rides
on the word and a token-boundary check would mint filologiYA (measured,
url-scheme prototype 2026-08-23).

Parentheticals are stripped BEFORE transliteration: NBU-style
qualifiers („(Съвместна програма с университета Йорк)") are prose, not
identity, and their words would otherwise dominate the slug.

Python 3.9 compatible; stdlib only.
"""
import re

__all__ = [
    "RESERVED_ROOT_SLUGS",
    "SLUG_RE",
    "SlugError",
    "slugify",
    "transliterate",
]

# Streamlined System (Обтекаема система), lowercase side. ъ→a and й→y
# per the law; ь→y covers the rare soft-sign carriers (Кольо).
_STREAMLINED = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sht", "ъ": "a", "ь": "y", "ю": "yu", "я": "ya",
}

# Root URL segments no university slug may claim: /specialnosti/ (subject
# landings), /gradove/ (reserved city landings), /ucheben-plan (the
# reserved per-program child segment). config.py validates against this.
RESERVED_ROOT_SLUGS = frozenset({"specialnosti", "gradove", "ucheben-plan"})

# The canonical slug charset: lowercase latin/digit words joined by
# single hyphens. Anchored — use .match()/re.fullmatch-like semantics.
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# One maximal run of Cyrillic letters (а-я spans ъ and ь). Input is
# lowercased first; ѝ (U+045D, outside а-я) is normalized to и before.
_CYRILLIC_RUN = re.compile("[а-я]+")

_PARENTHETICAL = re.compile(r"\([^)]*\)")


class SlugError(ValueError):
    """A name yields no slug — the caller must resolve, never mint."""


def _transliterate_run(match):
    run = match.group(0)
    tail = ""
    if len(run) > 2 and run.endswith("ия"):
        run, tail = run[:-2], "ia"
    return "".join(_STREAMLINED[c] for c in run) + tail


def transliterate(text):
    # type: (str) -> str
    """Streamlined-System transliteration of ``text``, lowercased.

    Non-Cyrillic characters pass through unchanged (English names stay
    English); the word-final -ия→ia exception fires per letter-run so
    attached punctuation cannot defeat it.
    """
    lowered = text.lower().replace("ѝ", "и")  # ѝ → и
    return _CYRILLIC_RUN.sub(_transliterate_run, lowered)


def slugify(name):
    # type: (str) -> str
    """The slug the URL-scheme rules propose for ``name``.

    Raises SlugError when nothing survives — an empty slug must be a
    loud refusal at minting time, never an empty path segment.
    """
    stripped = _PARENTHETICAL.sub(" ", name)
    slug = re.sub(r"[^a-z0-9]+", "-", transliterate(stripped)).strip("-")
    if not slug:
        raise SlugError(
            "name {0!r} yields an empty slug — a human must author "
            "one".format(name))
    return slug
