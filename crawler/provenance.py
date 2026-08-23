"""The provenance gate — StudyStream's one mechanical trust check (ADR-0002).

A pure function over store-constructed Artifacts:

    gate(value, segments, artifact) -> Verdict(status, detail)

No IO, no LLM, no opinions: a non-null value exists only if every verbatim
segment of its provenance is literally contained (under ONE normalization
policy) in the canonical text of the exact artifact it names, and every
checkable token of the value is supported by those segments. Anything else
is rejected into the repair queue by the caller — nothing unverified is
ever loaded.

Status vocabulary (the one spoken by the extraction cascade, the LLM tail,
status adjudication, the onboarding proposer, and the graders):

    PASS                verbatim provenance holds; the value exists
    REJECT_CONTAINMENT  a segment is not literal text of the artifact
                        (composed snippets, wrong artifact, paraphrase)
    REJECT_SUPPORT      segments are real, but value tokens lack support
                        (wrong number, missing year, missing currency)
    NULL_OK             an affirmative null with its reason (see gate_null)
    PARSE_FAILURE       the record cannot be checked at all (empty value,
                        no segments, tokenless value) — travels in the type
                        so it can never grade as a correct null downstream

Normalization policy (applied identically to values, segments and artifact
text — the ONE policy, exported as normalize()):
  - unicode NFC
  - case-insensitive (casefold)
  - all double-quote variants fold to '"', apostrophe variants to "'"
  - all dash variants fold to '-', including U+2212 MINUS SIGN
  - whitespace collapses to single spaces

Value-support rules:
  - number tokens (\\d[\\d.,]*) compare exactly after separator stripping —
    inflection leniency never applies to numbers
  - currency tokens (7-token vocabulary: € евро лева leva eur bgn лв) match
    by SUBSTRING in the segments, never token membership — glued forms like
    "€6,900" count (found-bug fix from STA-78)
  - word tokens match with 5-character-prefix leniency (Bulgarian
    inflection: "семестра" supports "семестри"); words shorter than 5
    characters require an exact token match

Known, documented blind spot (RFC v2 §3 Q4, measured twice): a truthful
segment from the wrong row/column of a table passes this gate. The fix is
the column-aware table resolver in tier F, never extra cleverness here.
"""
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import List, Optional, Tuple

__all__ = ["Status", "Verdict", "Artifact", "gate", "gate_null", "normalize"]


class Status(Enum):
    PASS = "PASS"
    REJECT_CONTAINMENT = "REJECT_CONTAINMENT"
    REJECT_SUPPORT = "REJECT_SUPPORT"
    NULL_OK = "NULL_OK"
    PARSE_FAILURE = "PARSE_FAILURE"
    # ADR-0007: true, but stated in no document — so there is nothing for
    # gate() to check and it never enters the gate. Its own status keeps
    # it impossible to mistake for a proven value: PASS still means, and
    # only means, "the gate proved this string is in the Artifact".
    DERIVED = "DERIVED"


@dataclass(frozen=True)
class Verdict:
    """The gate's answer: a Status plus a human-readable detail string."""
    status: Status
    detail: str = ""


@dataclass(frozen=True, repr=False)
class Artifact:
    """The deterministic rendering of a Snapshot by an identified renderer
    at a pinned version — the ONLY text provenance is ever checked against.

    STORE-ONLY CONSTRUCTION INVARIANT (ADR-0002): instances are constructed
    exclusively by the artifact-store module, which owns the snapshot ->
    renderer resolution (the port of spike A's doc_text_for and
    tsv_artifact_text). Building one anywhere else — hand-wrapping raw text,
    re-rendering ad hoc — reintroduces the wrong-artifact failure class that
    nulled 15 join values in spike A's first E3 run. A grep test over
    constructor call sites in crawler/ enforces this; only render/store
    modules and the provenance test fixtures may construct Artifacts.

    Fields:
      text              canonical text of the rendering
      renderer_id       e.g. "bs4-lxml-canonical", "pdftotext-flow+layout",
                        "docling-serve-tsv"
      renderer_version  pinned renderer version string
      ref               identifier of the rendered snapshot (URL or store key)
    """
    text: str
    renderer_id: str
    renderer_version: str
    ref: str

    def __repr__(self):
        return ("Artifact<ref={0!r}, renderer={1}@{2}, {3} chars>".format(
            self.ref, self.renderer_id, self.renderer_version, len(self.text)))


# --------------------------------------------------------------- normalization
_DOUBLE_QUOTES = "„“”‟«»‹›″"
_APOSTROPHES = "‘’‚‛`´ʼʹ′"
_DASHES = "‐‑‒–—―−"
_FOLD = {ord(c): '"' for c in _DOUBLE_QUOTES}
_FOLD.update({ord(c): "'" for c in _APOSTROPHES})
_FOLD.update({ord(c): "-" for c in _DASHES})

_WS_RX = re.compile(r"\s+")


def normalize(text):
    # type: (str) -> str
    """THE normalization policy — the only one in the package.

    Unicode NFC, quote/apostrophe/dash folding (incl. U+2212), casefold,
    whitespace collapse. Applied identically to values, segments and
    artifact text; snippet producers must use this same function so that
    what they store is what the gate checks.
    """
    text = unicodedata.normalize("NFC", text or "")
    text = text.translate(_FOLD).casefold()
    return _WS_RX.sub(" ", text).strip()


@lru_cache(maxsize=64)
def _normalized_artifact_text(artifact):
    # type: (Artifact) -> str
    return normalize(artifact.text)


# ----------------------------------------------------------- value tokenizing
_NUM_RX = re.compile(r"\d[\d.,]*")
_WORD_RX = re.compile(r"[^\W\d_]+")
# 7-token currency vocabulary; matched by SUBSTRING in segments (never token
# membership), so glued forms like "€6,900" or "1200лв." count as support.
_CURRENCY_TOKENS = ("€", "евро", "лева", "leva", "eur", "bgn", "лв")
_CURRENCY_WORDS = frozenset(t for t in _CURRENCY_TOKENS if t != "€")

_PREFIX_LEN = 5  # Bulgarian-inflection leniency, WORDS ONLY


def _strip_separators(num_token):
    # type: (str) -> str
    return num_token.replace(".", "").replace(",", "")


def _segment_index(nseg):
    # type: (str) -> Tuple[set, set, set]
    """(number tokens separator-stripped, word tokens, word 5-prefixes)."""
    numbers = {_strip_separators(t) for t in _NUM_RX.findall(nseg)}
    words = set(_WORD_RX.findall(nseg))
    prefixes = {w[:_PREFIX_LEN] for w in words if len(w) >= _PREFIX_LEN}
    return numbers, words, prefixes


# ---------------------------------------------------------------------- gate
def gate_null(reason):
    # type: (str) -> Verdict
    """The affirmative-null entry point.

    A null is a legitimate outcome only when the cascade affirmatively
    established there is no value (field absent from the sources, program
    not enrolling, page affirmatively gone). That decision needs no
    artifact and no segments — only its reason, which travels in
    Verdict.detail so graders and the repair queue can tell an affirmative
    null from a miss. Callers holding value=None may equivalently call
    gate(None, ..., null_reason=reason); both routes produce the same
    NULL_OK verdict.
    """
    return Verdict(Status.NULL_OK, reason)


def gate(value, segments, artifact, *, null_reason=""):
    # type: (Optional[str], List[str], Optional[Artifact], str) -> Verdict
    """The provenance gate. Pure; no IO.

    Arguments:
      value        the extracted value, or None for an affirmative null
      segments     verbatim provenance segments. Single-snippet values pass
                   [snippet]; joined values (tier-F family joins) pass each
                   verbatim piece separately — composing pieces into one
                   string is the v1 failure class and rejects on containment
      artifact     the store-constructed Artifact the segments claim to come
                   from. Never raw text (TypeError) — ADR-0002. Ignored (and
                   may be None) only on the value-is-None path
      null_reason  reason recorded when value is None (see gate_null)

    Checks, in order:
      1. value is None                          -> NULL_OK (null_reason)
      2. malformed record (empty value, no
         usable segments, tokenless value)      -> PARSE_FAILURE
      3. every segment literally contained
         (normalized) in artifact.text          -> else REJECT_CONTAINMENT
      4. every value token supported by the
         segments (numbers exact, currency by
         substring, words with 5-char-prefix
         leniency)                              -> else REJECT_SUPPORT
      5. PASS
    """
    if value is None:
        return gate_null(null_reason)

    if not isinstance(artifact, Artifact):
        raise TypeError(
            "gate() requires a store-constructed Artifact, not {0} — "
            "checking against raw text reproduces the wrong-artifact "
            "failure class (ADR-0002)".format(type(artifact).__name__))
    if isinstance(segments, str):
        raise TypeError(
            "segments must be a list of verbatim segment strings, "
            "not a single string")

    nvalue = normalize(value)
    if not nvalue:
        return Verdict(Status.PARSE_FAILURE,
                       "value is empty after normalization")

    seg_pairs = [(s, normalize(s)) for s in segments]
    seg_pairs = [(orig, ns) for orig, ns in seg_pairs if ns]
    if not seg_pairs:
        return Verdict(Status.PARSE_FAILURE,
                       "no non-empty provenance segments for a non-null value")

    # tokens first: a tokenless value can never be supported, whatever the
    # segments say — surface it as PARSE_FAILURE, not a vacuous PASS
    number_tokens = _NUM_RX.findall(nvalue)
    currency_tokens = [c for c in _CURRENCY_TOKENS if c in nvalue]
    word_tokens = [w for w in _WORD_RX.findall(nvalue)
                   if w not in _CURRENCY_WORDS]
    if not (number_tokens or currency_tokens or word_tokens):
        return Verdict(Status.PARSE_FAILURE,
                       "value has no checkable tokens: {0!r}".format(value))

    # 3. containment: each segment must be literal text of THIS artifact
    ntext = _normalized_artifact_text(artifact)
    for orig, ns in seg_pairs:
        if ns not in ntext:
            shown = orig if len(orig) <= 90 else orig[:90] + "…"
            return Verdict(
                Status.REJECT_CONTAINMENT,
                "segment not contained in artifact {0!r} "
                "({1}@{2}): {3!r}".format(artifact.ref, artifact.renderer_id,
                                          artifact.renderer_version, shown))

    # 4. value-token support against the (contained) segments
    indexes = [_segment_index(ns) for _, ns in seg_pairs]
    nsegs = [ns for _, ns in seg_pairs]
    missing = []
    for tok in number_tokens:
        stripped = _strip_separators(tok).strip()
        if not stripped:
            continue
        if not any(stripped in numbers for numbers, _, _ in indexes):
            missing.append(tok)
    for cur in currency_tokens:
        if not any(cur in ns for ns in nsegs):
            missing.append(cur)
    for w in word_tokens:
        if any(w in words for _, words, _ in indexes):
            continue
        if (len(w) >= _PREFIX_LEN
                and any(w[:_PREFIX_LEN] in prefixes
                        for _, _, prefixes in indexes)):
            continue
        missing.append(w)
    if missing:
        return Verdict(
            Status.REJECT_SUPPORT,
            "unsupported value tokens: {0} (numbers compare exactly, "
            "currency by substring, words with {1}-char-prefix "
            "leniency)".format(", ".join(sorted(set(missing))), _PREFIX_LEN))

    return Verdict(
        Status.PASS,
        "{0} segment(s) contained in {1!r}; {2} value token(s) "
        "supported".format(
            len(seg_pairs), artifact.ref,
            len(number_tokens) + len(currency_tokens) + len(word_tokens)))
