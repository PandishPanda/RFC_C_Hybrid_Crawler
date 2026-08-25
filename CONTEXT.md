# StudyStream

Aggregates Bulgarian university degree-program data (tuition, admission, curricula, language, enrollment status) into one searchable product, sourced from 51 heterogeneous university websites (ADR-0006: the RSVU registry is no longer used).

## Language

**Program**:
A hand-authored entry in `crawler/configs/<UniID>.json`, identified by its config program id (e.g. `aubg-cs`) — the unit of identity for everything we aggregate (ADR-0006). A Program exists because a human configured it; nothing external enumerates programs for us.
_Avoid_: course, degree, specialty, registry row

**Snapshot**:
The raw fetched bytes of one source at one retrieval time, content-addressed and append-only. Never edited, never re-rendered in place.
_Avoid_: cache entry, page copy

**Artifact**:
The deterministic rendering of a Snapshot by an identified renderer at a pinned version (canonical HTML text, pdftotext output, Docling table TSV). The only text Provenance is ever checked against — renderer identity travels with it.
_Avoid_: extracted text, normalized output

**Provenance**:
The proof attached to every non-null extracted value: `value · source_url · verbatim source_snippet · retrieved_at · method`. A value whose snippet does not literally contain it has no provenance, whatever the prompt claimed.

**Stated Fee**:
A Program's tuition read from the cell/section that prices it, with Provenance and its exact currency. A Program with no stated fee may be genuinely unpriced or merely unconfigured — the two are not distinguishable, so fee completeness is a floor, never a rate.
_Avoid_: price, cost

**Direction (Професионално направление)**:
The national classifier field a Program belongs to (e.g. 1.3 «Педагогика на обучението по...», 8.1 «Теория на изкуствата»). Fee orders commonly price per Direction, not per Program, so the Direction is the join key of the направление→такса chain: the Program→Direction tie is ATTESTED config data (ADR-0003 — the config diff is the attestation; the documentary evidence, typically curriculum-plan headers or fee-table rows, lives in the attribution-review record), while the fee value itself ships with verbatim Provenance from the fee document. Executed by the `direction-fees` join (flow-text clauses keyed by Direction) or a `fee-row` grid join whose alias is a Direction row label.
_Avoid_: field of study, category, faculty (a faculty is an org unit — AMTII's справочник groups by faculty and states no Direction)

**Shared source**:
A source (central fee order, admission ordinance, catalog, university fees page) that backs a field's value for two or more Programs. The value is still per-Program data; Provenance records the shared source.
_Avoid_: general info

**Stale-green drift**:
The failure mode where a university publishes next year's data on a new page while the old page stays live and unchanged (HTTP 200, same content hash), so freshness checks stay green while the data goes stale.

**Changed cell**:
A (Program, field) pair whose status, value, method, Artifact, verbatim snippet or derivation differs between two runs. The unit the attribution review reads — deliberately wider than a changed *value*, because a value that stays identical while its provenance moves is the misattribution case. `crawler diff` enumerates them; the append-only ledger's diff compares values alone and cannot. A cell's persisted form is the **Field record** (`crawler/field_record.py`): status, value, and the proof appropriate to the status — Provenance for extracted values, a derivation for Derived values, a verdict detail for rejects. One module constructs, serializes and parses it, so a shape no constructor emits cannot exist and a malformed record fails loudly instead of comparing silently equal.
_Avoid_: diff row, changed value

**Attribution review**:
The third review axis on any ticket that can move a shipped value: read the Provenance of every changed cell, then have an independent agent try to refute that reading. Answers "did this value come from the right place?", which the Provenance gate (presence) and the blind benchmark (a sample) both leave open. A process gate, enforced by the ticket's done-check rather than by code.
_Avoid_: provenance check, spot check, audit

**Readable set**:
The documents one Program-field may draw values from — the Program's own page narrowed to its own regions, its routed pages (lang/adm/tuition), and its extra sources, each participating once under its Artifact ref. Computed by one module (`crawler/field_sources.py`) and consumed identically by the deterministic cascade and the LLM tail; a value from outside the Readable set is a misattribution even when verbatim-present, which the Provenance gate cannot see. Join sources are attributed by config and executed as mechanisms, not read as text.
_Avoid_: candidate docs, document selection (those name the mechanics, not the guarantee)

**Derived value**:
A value that is true but that no document states — a language assumed where none is declared. Distinct from an extracted value, which the Artifact says verbatim. A derived value ships under its own status, `DERIVED` (ADR-0007): it never enters the Provenance gate, because there is nothing to check, and instead of a snippet it carries the rule that produced it. It can never displace an extracted value, and the rule that produces it is per-site config, not code. The blind key scores it in its own category — the key answers "what does the page say", a derived value answers "what is true" — so it counts as neither correct nor fabricated.
_Avoid_: inferred value, assumed value, default (those name the method, not the standing)

**Attention (item)**:
One unit of work only a human can advance: a blocked publish, an open repair-queue row, an unresolved CHECK verdict, a pending onboarding proposal, a drift warning. Attention items age from the moment they open; the pipeline never resolves one on its own. The set of open items is the pipeline's whole claim on human time — anything not an Attention item must be safe to leave unwatched.
_Avoid_: alert, todo, notification (those are delivery mechanisms, not the work itself)
