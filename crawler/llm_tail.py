"""The gated LLM tail (ticket 02, DEC-1/DEC-4).

Fields the deterministic cascade nulls (``cascade.resolve_field`` returned
``None``) fall through here. A model call proposes ``(source_ref, value,
segments)`` for the field; the SAME ``crawler.provenance.gate`` then checks
the result against the exact store-constructed Artifact ``source_ref``
names — the tail never constructs its own trust, it feeds the one gate
everything else feeds (ADR-0002). A rejection retries once with the gate's
own detail fed back into the prompt, then escalates model tier
(Haiku -> Sonnet) for one more attempt, then gives up into the repair queue.
Zero fabrications requires every accepted value to still pass containment +
value-token support; the tail cannot weaken the gate, only feed it.

Adapters (the seam ticket 02 asks for — CLI / API / fake):

  CLIAdapter   ``claude -p --json-schema ... --tools ""`` (subscription
               auth, no ANTHROPIC_API_KEY needed). ``--json-schema`` forces
               the response through Claude Code's own tool-use machinery —
               verified empirically: the CLI's JSON envelope reports
               ``stop_reason: "tool_use"`` and a top-level
               ``structured_output`` key that is guaranteed schema-valid.
               This IS forced tool-use structured output, not prompted
               JSON parsed out of free text (spike B's ``llm.py`` pattern,
               measured at 25% unparseable — copying it would reproduce
               the exact failure DEC-4 exists to prevent).
  APIAdapter   Messages API with ``tool_choice={"type": "tool", ...}`` —
               the textbook forced-tool-use path. Seam-complete and
               documented, but NOT runnable in this repo today (no
               ANTHROPIC_API_KEY configured) — raises a clear error at
               construction rather than silently degrading to something
               weaker.
  FakeAdapter  a canned ``(prompt, schema, model) -> structured_output``
               callable for tests. No IO, no subprocess.

Field shape: ONE artifact_ref per call (the existing Extraction/gate
contract is single-artifact — every tier-F join already works this way).
The model picks its source document via ``source_ref``, whose JSON-schema
type is an ``enum`` of the real candidate refs plus null — the schema
enforcement makes "the model named a document that was never shown to it"
structurally impossible, not just a validation step.

Admission returns the FULL requirement set (the measured
truthful-but-different-pick error class, 9/20 on the spike-B tail run) as
one joined value with one gate segment per requirement — each segment is
checked independently by the gate, exactly like a tier-F join's pieces.
"""
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

from crawler import cascade
from crawler.provenance import Status, Verdict, gate

__all__ = [
    "CLIAdapter", "APIAdapter", "FakeAdapter",
    "candidate_docs", "build_schema", "build_prompt",
    "TailResult", "resolve_via_tail", "UsageLedger",
]

SYSTEM_PROMPT = (
    "You are a deterministic-as-possible data-extraction assistant for "
    "Bulgarian university program pages. You are given one or more "
    "DOCUMENTs, each with a `ref`. You MUST: (1) pick exactly one document "
    "that states the requested field for the requested program, or none "
    "if no document states it; (2) copy every segment character-for-"
    "character from that document — no paraphrasing, no fixing typos or "
    "spacing, no joining fragments that aren't already adjacent; (3) never "
    "invent a value not printed in the documents.")

FIELD_HINT = {
    "degree": (
        "the DEGREE LEVEL awarded (e.g. 'бакалавър', 'магистър', 'доктор', "
        "or their English equivalents 'bachelor'/'master'/'doctor'), as "
        "printed. If the document ALSO prints a separate professional-"
        "qualification title near the degree level (e.g. 'ОКС бакалавър' "
        "with 'професионална квалификация електроинженер' nearby), pick "
        "the degree level, not the qualification title. Only return a "
        "qualification title if no degree level is printed anywhere in "
        "the document for this program."),
    "duration": "length of study as printed (semesters and/or years).",
    "language": (
        "language of instruction, ONLY if explicitly stated as such — "
        "the language a page happens to be written in is not evidence."),
    "tuition": (
        "the fee WITH currency exactly as printed for THIS program "
        "(annual or per-semester, full-time, current academic year)."),
    "admission": (
        "ALL admission requirements for THIS program as printed — return "
        "every distinct requirement/route as its own segment, not just "
        "the first one. Set value to the requirements joined by '; '."),
}


# ------------------------------------------------------------- candidate docs
def candidate_docs(site, program, field, docs):
    # type: (...) -> List[Tuple[str, object]]
    """Every doc the tail may read for one field, as (ref, source) pairs.

    The page-family documents come from the Readable set
    (field_sources.readable_sources) — the SAME return value the
    deterministic cascade harvests, so the two extractors cannot drift
    (they did, twice, while each kept its own copy of the selection: see
    field_sources' module docstring for the measured incidents). Each
    entry's model_view() is what the model may read: the original source
    when unscoped, the Program's own regions joined when the page is
    shared — same ref either way, because scoping only narrows what the
    model sees, never what counts as proof (the gate still checks quoted
    snippets against the FULL artifact).

    Join sources are appended after the Readable set: they are
    human-attributed config the cascade executes as mechanisms, outside
    the Readable-set seam by decision (nothing has ever drifted there).

    The returned ref is source.ref -- the artifact ref the STORE actually
    indexes by -- never the DOCS dict's lookup key (live-verified
    distinction: 2026-08-15 UniRuse --tail run; ref-vs-key is now a
    ScopedDoc invariant rather than a docstring warning).
    """
    seen = {}
    for sd in cascade.readable_sources(site, program, field, docs):
        if sd.ref not in seen:
            seen[sd.ref] = sd.model_view()

    def add(key):
        if key and key in docs:
            source = docs[key]
            if source.ref not in seen:
                seen[source.ref] = source

    if field == "language":
        if program.language_tracks is not None:
            add(program.language_tracks.source)

    if field == "admission":
        if program.spravochnik is not None:
            add(program.spravochnik.source)
        if program.admission_join is not None:
            add(program.admission_join.source)

    if field in ("degree", "duration") and program.spravochnik is not None:
        add(program.spravochnik.source)

    if field == "tuition":
        if program.tuition_join is not None:
            add(program.tuition_join.source)
        if program.fees_section is not None:
            add(program.fees_section.source)

    return list(seen.items())


# --------------------------------------------------------------- prompt/schema
def _serialize(source):
    """Render one TextSource/TableSource as prompt text.

    TableSource rows are joined by single spaces, one row per line — BYTE
    IDENTICAL to ``crawler.render.tsv_artifact_text``'s rendering (the text
    the gate actually checks table segments against). A model that copies
    a displayed row verbatim is copying a real substring of the artifact.
    """
    if isinstance(source, cascade.TableSource):
        lines = [" ".join(row) for row in source.rows()]
        return "\n".join(lines)
    return source.text


def build_prompt(program_name, uni_id, field, docs_list, feedback=None):
    # type: (str, str, str, List[Tuple[str, object]], Optional[str]) -> str
    parts = [
        'Extract ONE field, "{0}", for the program "{1}" at {2}.'.format(
            field, program_name, uni_id),
        "Field definition: " + FIELD_HINT[field],
        "",
    ]
    if feedback:
        parts += [
            "Your previous answer was REJECTED by the mechanical trust "
            "gate: " + feedback,
            "Fix exactly that problem — most often this means a segment "
            "was not a literal, character-for-character substring of the "
            "named document, or a value token (a number, currency, or "
            "word) wasn't present in any segment.",
            "",
        ]
    for ref, source in docs_list:
        parts.append("DOCUMENT (ref={0!r}):\n{1}\n".format(ref, _serialize(source)))
    return "\n".join(parts)


def build_schema(refs):
    # type: (List[str]) -> dict
    return {
        "type": "object",
        "properties": {
            "source_ref": {"type": ["string", "null"], "enum": [None] + list(refs)},
            "value": {"type": ["string", "null"]},
            "segments": {"type": "array", "items": {"type": "string"}},
            "null_reason": {"type": ["string", "null"]},
        },
        "required": ["source_ref", "value", "segments"],
    }


# -------------------------------------------------------------------- adapters
class FakeAdapter:
    """Test adapter: a callable table, no IO.

    Lookup order for a call tagged e.g. "p1:duration:2" at model "haiku":
    ``(model, "p1:duration:2")`` -> ``"p1:duration:2"`` (exact, per-attempt
    override) -> ``(model, "p1:duration")`` -> ``"p1:duration"`` (base tag,
    same response for every attempt — the common case for a test that
    doesn't care which attempt number produced it). Each entry is a
    schema-shaped dict, or a ``callable(prompt) -> dict`` for tests that
    need to vary the response across calls.
    """

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, prompt, schema, model, tag):
        self.calls.append({"tag": tag, "model": model, "prompt": prompt})
        base_tag = tag.rsplit(":", 1)[0] if tag.rsplit(":", 1)[-1].isdigit() else tag
        resp = (self.responses.get((model, tag))
               or self.responses.get(tag)
               or self.responses.get((model, base_tag))
               or self.responses.get(base_tag))
        if resp is None:
            raise KeyError("FakeAdapter: no canned response for tag {0!r} "
                          "(model {1!r}, base {2!r})".format(tag, model, base_tag))
        if callable(resp):
            resp = resp(prompt)
        usage = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
                 "elapsed_s": 0.0, "model": model}
        return dict(resp), usage


class CLIAdapter:
    """The `claude` CLI, subscription auth, forced structured output.

    No ANTHROPIC_API_KEY required — this runs against the same Claude Code
    subscription the rest of this session uses. ``--json-schema`` forces
    tool-use under the hood (verified: the envelope's ``stop_reason`` is
    ``"tool_use"`` and it carries a schema-valid ``structured_output`` key),
    which is what makes "parse-failure rate 0" achievable by construction
    rather than by hoping the model followed a prompted format.
    """

    def __init__(self, usage_ledger=None, timeout_s=180, system_prompt=None):
        self.usage_ledger = usage_ledger
        self.timeout_s = timeout_s
        self.system_prompt = system_prompt or SYSTEM_PROMPT

    def call(self, prompt, schema, model, tag):
        t0 = time.monotonic()
        proc = subprocess.run(
            ["claude", "-p", "--model", model, "--output-format", "json",
             "--tools", "", "--system-prompt", self.system_prompt,
             "--json-schema", json.dumps(schema)],
            input=prompt.encode("utf-8"),
            capture_output=True, timeout=self.timeout_s)
        elapsed = round(time.monotonic() - t0, 1)
        out = proc.stdout.decode("utf-8", errors="replace")
        try:
            env = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "CLI gave a non-JSON envelope for {0!r}: {1}".format(
                    tag, out[:300])) from exc
        usage = {
            "tag": tag, "model": model, "elapsed_s": elapsed,
            "cost_usd": env.get("total_cost_usd"),
            "input_tokens": env.get("usage", {}).get("input_tokens"),
            "cache_creation": env.get("usage", {}).get("cache_creation_input_tokens"),
            "cache_read": env.get("usage", {}).get("cache_read_input_tokens"),
            "output_tokens": env.get("usage", {}).get("output_tokens"),
            "is_error": env.get("is_error"),
            "stop_reason": env.get("stop_reason"),
        }
        if self.usage_ledger is not None:
            self.usage_ledger.record(usage)
        if env.get("is_error") or "structured_output" not in env:
            raise RuntimeError(
                "CLI call failed or returned no structured_output for "
                "{0!r}: is_error={1} result={2!r}".format(
                    tag, env.get("is_error"), env.get("result", "")[:300]))
        return env["structured_output"], usage


class APIAdapter:
    """Messages API, ``tool_choice={"type": "tool", "name": "extract"}`` —
    the textbook forced-tool-use path. Not runnable without
    ANTHROPIC_API_KEY; raises at construction so a misconfigured adapter
    fails loudly instead of silently degrading to prompted JSON.
    """

    def __init__(self, api_key=None, system_prompt=None):
        import os
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "APIAdapter requires ANTHROPIC_API_KEY (none set in this "
                "repo/session) — use CLIAdapter for subscription auth, or "
                "set the key explicitly if you intend to spend API credit")
        self.api_key = api_key
        self.system_prompt = system_prompt or SYSTEM_PROMPT

    def call(self, prompt, schema, model, tag):
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        t0 = time.monotonic()
        msg = client.messages.create(
            model=model, max_tokens=1024, system=self.system_prompt,
            tools=[{"name": "extract", "description": "Return the extracted field.",
                    "input_schema": schema}],
            tool_choice={"type": "tool", "name": "extract"},
            messages=[{"role": "user", "content": prompt}])
        elapsed = round(time.monotonic() - t0, 1)
        tool_use = next(b for b in msg.content if b.type == "tool_use")
        usage = {
            "tag": tag, "model": model, "elapsed_s": elapsed,
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "cost_usd": None,
        }
        return tool_use.input, usage


# --------------------------------------------------------------- usage ledger
class UsageLedger:
    """Append-only JSONL of every tail call's cost/tokens (ticket 02:
    "measured cost per call logged to a usage ledger")."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, usage):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(usage, ensure_ascii=False) + "\n")


# -------------------------------------------------------------- orchestration
@dataclass(frozen=True)
class TailResult:
    """What the runner needs.

    extraction    set ONLY when verdict.status is PASS — a caller can treat
                  a non-None extraction as shippable, exactly like a
                  cascade emission that passed the gate.
    last_attempt  the final attempt's Extraction-shaped data regardless of
                  verdict — None only when the model itself returned an
                  affirmative null or the adapter errored on every try.
                  This is what the repair-queue entry is built from on a
                  rejection (rejected_value/segments/artifact_ref/method),
                  the same fields _field_record already reports for a
                  rejected cascade emission.
    """
    verdict: Verdict
    extraction: Optional["cascade.Extraction"]
    last_attempt: Optional["cascade.Extraction"]
    attempts: int
    escalated: bool


HAIKU = "haiku"
SONNET = "sonnet"

# statuses worth retrying with feedback — there's something concrete to fix
_FEEDBACK_STATUSES = (Status.REJECT_CONTAINMENT, Status.REJECT_SUPPORT)
# statuses that are a legitimate terminal answer, not a failure to recover from
_TERMINAL_STATUSES = (Status.PASS, Status.NULL_OK)


def _one_attempt(adapter, program, uni_id, field, docs_list, model, tag,
                 feedback=None):
    refs = [ref for ref, _ in docs_list]
    schema = build_schema(refs)
    prompt = build_prompt(program.name, uni_id, field, docs_list, feedback)
    structured, usage = adapter.call(prompt, schema, model, tag)
    return structured, usage


def resolve_via_tail(adapter, store, site, program, field, docs, *,
                     tag_prefix=""):
    # type: (...) -> TailResult
    """Resolve one cascade-nulled field through the gated LLM tail.

    Retry policy: same-tier retry once with gate feedback on a REJECT_*;
    anything else (an affirmative null, a PARSE_FAILURE, or an adapter
    error) has no feedback to act on, so it skips straight to escalation
    if it isn't already a terminal PASS/NULL_OK. One escalated attempt
    (Sonnet) either way, then give up.
    """
    docs_list = candidate_docs(site, program, field, docs)
    tag = "{0}{1}:{2}".format(tag_prefix, program.id, field)

    if not docs_list:
        verdict = Verdict(Status.NULL_OK,
                          "llm-tail: no candidate documents for this field")
        return TailResult(verdict, None, None, 0, False)

    candidate_refs = {ref for ref, _ in docs_list}

    def attempt(model, suffix, feedback=None):
        try:
            structured, _ = _one_attempt(adapter, program, site.uni_id, field,
                                         docs_list, model, tag + suffix,
                                         feedback)
        except Exception as exc:  # noqa: BLE001 — adapter/transport failure
            return None, Verdict(Status.PARSE_FAILURE,
                                 "tail adapter error: {0}".format(exc))
        ref = structured.get("source_ref")
        value = structured.get("value")
        segments = tuple(s for s in structured.get("segments") or [] if s)
        if ref is None or value is None:
            return None, gate(None, [], None,
                              null_reason=structured.get("null_reason")
                              or "llm-tail: no document states this field")
        if ref not in candidate_refs:
            # Schema enforcement should make this impossible, but a store
            # lookup is real I/O — never trust the enum alone to gate it
            # (same defense-in-depth as onboarding.py's url-membership
            # check on an LLM-proposed URL before fetching it). Degrades
            # to PARSE_FAILURE, never an uncaught KeyError crashing the
            # whole run (runner.build_docs's own invariant: "a null is
            # repairable; a crashed run is not").
            return None, Verdict(
                Status.PARSE_FAILURE,
                "llm-tail: model named source_ref {0!r} outside the "
                "candidate document set".format(ref))
        artifact = store.artifact(ref)
        verdict = gate(value, list(segments), artifact)
        extraction = cascade.Extraction(
            field=field, value=value, segments=segments, artifact_ref=ref,
            method="llm-tail:{0}".format(model), tier="llm-tail")
        return extraction, verdict

    # attempt 1: Haiku, no feedback
    ext, verdict = attempt(HAIKU, ":1")
    attempts = 1
    if verdict.status in _TERMINAL_STATUSES:
        return TailResult(verdict, ext if verdict.status is Status.PASS else None,
                          ext, attempts, False)

    # same-tier retry only when there's feedback to act on
    if verdict.status in _FEEDBACK_STATUSES:
        ext, verdict = attempt(HAIKU, ":2", feedback=verdict.detail)
        attempts += 1
        if verdict.status in _TERMINAL_STATUSES:
            return TailResult(verdict, ext if verdict.status is Status.PASS else None,
                              ext, attempts, False)

    # escalate: one Sonnet attempt, with feedback if we have any to give
    feedback = verdict.detail if verdict.status in _FEEDBACK_STATUSES else None
    ext, verdict = attempt(SONNET, ":3", feedback=feedback)
    attempts += 1
    return TailResult(verdict, ext if verdict.status is Status.PASS else None,
                      ext, attempts, True)
