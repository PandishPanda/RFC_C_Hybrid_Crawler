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

**Shared source**:
A source (central fee order, admission ordinance, catalog, university fees page) that backs a field's value for two or more Programs. The value is still per-Program data; Provenance records the shared source.
_Avoid_: general info

**Stale-green drift**:
The failure mode where a university publishes next year's data on a new page while the old page stays live and unchanged (HTTP 200, same content hash), so freshness checks stay green while the data goes stale.

**Changed cell**:
A (Program, field) pair whose status, value, method, Artifact or verbatim snippet differs between two runs. The unit the attribution review reads — deliberately wider than a changed *value*, because a value that stays identical while its provenance moves is the misattribution case. `crawler diff` enumerates them; the append-only ledger's diff compares values alone and cannot.
_Avoid_: diff row, changed value

**Attribution review**:
The third review axis on any ticket that can move a shipped value: read the Provenance of every changed cell, then have an independent agent try to refute that reading. Answers "did this value come from the right place?", which the Provenance gate (presence) and the blind benchmark (a sample) both leave open. A process gate, enforced by the ticket's done-check rather than by code.
_Avoid_: provenance check, spot check, audit

**Attention (item)**:
One unit of work only a human can advance: a blocked publish, an open repair-queue row, an unresolved CHECK verdict, a pending onboarding proposal, a drift warning. Attention items age from the moment they open; the pipeline never resolves one on its own. The set of open items is the pipeline's whole claim on human time — anything not an Attention item must be safe to leave unwatched.
_Avoid_: alert, todo, notification (those are delivery mechanisms, not the work itself)
