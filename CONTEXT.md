# StudyStream

Aggregates Bulgarian university degree-program data (tuition, admission, curricula, language, enrollment status) into one searchable product, sourced from the RSVU registry and 51 heterogeneous university websites.

## Language

**Program**:
A row in the RSVU registry (`rsvu.mon.bg`) — the unit of identity for everything we aggregate. Existing in the registry does not imply the program is currently enrolling.
_Avoid_: course, degree, specialty

**Covered (program)**:
A Program whose data was extracted with Provenance, OR whose status was affirmatively resolved with Provenance (e.g. determined not-enrolling, page gone). Unreachable or skipped is not covered.

**Coverage**:
Covered Programs ÷ registry rows, per university or overall. The denominator is always registry rows — never "programs with links" or "enrolling programs".

**Snapshot**:
The raw fetched bytes of one source at one retrieval time, content-addressed and append-only. Never edited, never re-rendered in place.
_Avoid_: cache entry, page copy

**Artifact**:
The deterministic rendering of a Snapshot by an identified renderer at a pinned version (canonical HTML text, pdftotext output, Docling table TSV). The only text Provenance is ever checked against — renderer identity travels with it.
_Avoid_: extracted text, normalized output

**Provenance**:
The proof attached to every non-null extracted value: `value · source_url · verbatim source_snippet · retrieved_at · method`. A value whose snippet does not literally contain it has no provenance, whatever the prompt claimed.

**Offering**:
One (attendance form, duration) pair a Program is accredited for — "редовна - 4". The RSVU registry row enumerates them in its own `edu_forms`; config never restates the list, it attaches a recipe per form. A Program with one name can be six Offerings priced differently.
_Avoid_: variant, mode, track

**Stated Fee**:
An Offering's tuition read from the cell that prices THAT form and funding band, with Provenance. An Offering with no stated fee may be genuinely unpriced or merely unconfigured — the two are not distinguishable today, so any completeness figure over Offerings is a floor, never a rate.
_Avoid_: price, cost

**Shared source**:
A source (central fee order, admission ordinance, catalog, university fees page) that backs a field's value for two or more Programs. The value is still per-Program data; Provenance records the shared source.
_Avoid_: general info

**Stale-green drift**:
The failure mode where a university publishes next year's data on a new page while the old page stays live and unchanged (HTTP 200, same content hash), so freshness checks stay green while the data goes stale.
