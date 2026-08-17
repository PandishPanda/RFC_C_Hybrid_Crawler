# Offerings are enumerated by the registry row's own `edu_forms` and carry no Provenance; config attaches per-form fee recipes, and the fee's funding band becomes quoted evidence

Status: proposed (2026-08-16) — one part LANDED, the rest not built

**Read this status line before the rest.** This ADR mixes a decision that is
already in `main`'s history with a design that is not. Splitting them:

*Landed* (commit `85a5075`, 375 tests green): the funding-band selector —
`FeeRowJoin.table_marker` + `FeeRowJoin.funding`, the whole-cell match rule, the
marker row shipping as a second provenance segment, and the load-time
`funding ⊆ table_marker` check. UniRuse is rewired to three explicitly-banded
sources; all 11 tuition values are unchanged and now carry a recorded band.

*Not built* — the Offering entity itself, `ProgramConfig.offerings`, the
per-form recipe map, the `CurriculumRef` binding check, Offering completeness
reporting, and declaring `table_marker`/`value_headers`/`funding` on a tuition
`JoinRef` (that last one would collapse UniRuse's three sources to one; the
landed change kept three, each naming its own band). Those remain proposals
pending T1–T7.

Every measured number below was independently reproduced against the real
artifacts before this ADR was accepted into the repo, not taken on the
prototype's word — see *Verification status* at the end.

A Program is not one offering. Row 30083 «Бизнес мениджмънт» (code `30701182`)
states its own `edu_forms` as `"редовна - 4, задочна - 5, задочна - 4.5,
дистанционна - 4.5, дистанционна - 4, дистанционна - 5"` — six (attendance
form, duration) pairs under one registry row. The flat schema
(`crawler/config.py` `FIELDS`, one value per Program × field) collapses them,
so tuition for a Program offered full-time AND at a distance can hold only one
number. Today `uniruse-bizmgmt.tuition` is `400`, which is the state-subsidised
full-time fee standing in for six offerings whose real prices are 400 / — / — /
760 / 760 / 760. `uniruse-pubadmin` ships `400` for a Program the registry
accredits редовна, задочна and задочна-4.5, where the workbook prices no
редовно place at all and the `400` is the задочно state fee.

Since the landed funding-band change, both values now record WHICH table and
band they came from, and that record is quoted from the artifact rather than
asserted — so neither is untraceable any more. What survives is the
attribution: a single Program-level number still stands for a set of offerings
priced differently, and nothing in the shape says which member of the set it
is. `gate()` passes both, correctly, because both really are printed where they
claim. This is a modelling gap, not a provenance one, and that distinction is
why the fix is a new entity rather than a stricter gate.

This ADR records what we build for that, and — more importantly — what we
refuse to build, because the first draft of this design was wrong in a way the
gate does not catch.

**Offerings are enumerated by MON, never by us.** An Offering is one (form,
duration) pair, parsed from its registry row's own `edu_forms` string. Config
never restates the list; it supplies a recipe MAP keyed by form
(`"редовна"`, or `"задочна - 4.5"` to specialise one duration), and the runner
attaches recipes to the forms the registry already named. Measured over the
three captured exports (`crawler/registry_exports/`, 2026-08-16): 274 rows,
100% carry `edu_forms`, 628 items, **0 unparsed**, 0 duplicate pairs within a
row, exactly four forms — редовна (295), задочна (269), самостоятелна (38),
дистанционна (26). University of Ruse: 189 rows → **517 Offerings** (mean 2.74,
max 8); the 11 currently configured Programs → **44**. Because config never
enumerates, config and registry cannot drift, and nobody hand-maintains 517
rows.

**The Offering key carries no Provenance, and dressing it as Provenance is the
mistake this ADR exists to prevent.** The first draft promoted the captured
RSVU export to a rendered Artifact so `(form, duration)` could be quoted
verbatim from a registry row line. That is wrong, and it is wrong mechanically,
not stylistically. Rendering row 30083 as a line and running the real gate:

```
gate("редовна - 4", [row_line], registry_artifact) -> PASS
gate("редовна - 5", [row_line], registry_artifact) -> PASS      # does not exist
gate("задочна - 4", [row_line], registry_artifact) -> PASS      # does not exist
```

Containment plus token support has no notion of which tokens *pair* — the same
documented blind spot as a truthful segment from the wrong row of a table
(ADR-0002, ADR-0003), one level down. An Artifact would therefore have bought
the appearance of verification and none of it. So the Offering key is
**identity, at exactly the tier `RegistryRow.code` and `RegistryRow.name`
already occupy** — the tier `adjudication.covered_codes()` and
`AdjudicationReport.total_rows` have always treated as given. Each Offering
record carries `{registry_row_id, edu_forms (verbatim), edu_forms_item}` plus
the export's own `captured_at`/`source`: a citation to where the key came from,
deliberately **not** named Provenance, and deliberately not a second trust
concept. Only an Offering's extracted values — today, its tuition — go through
`gate()`, against store-constructed Artifacts, exactly as before. Not promoting
the export also removes a hazard the first draft created: Coverage's numerator
can never be sourced from Coverage's own denominator, because the denominator
is not a citable Artifact at all.

**`duration_years` is key metadata, not the `duration` field.** They may
disagree — the registry says 4, a page may say "8 семестъра" — and neither
overwrites the other. Program-level `duration` keeps whatever the page /
справочник / tail tiers produce, including `NULL_OK`. This is not fastidiousness:
`grader._values_match("4 години / 8 семестъра", "редовна - 4")` is `False`, and
duration is currently `NULL_OK` for all 11 UniRuse Programs, so shipping the
registry pair as a Program field would move 11 frozen-key grades from MISS to
CHECK for a fact the registry never claimed to be a duration *phrase*.

**The funding band is the second one-to-many, and it is what makes fees
wrong today.** The real Ruse workbook (11 sheets, rendered by
`render.spreadsheet_grids` over the stored snapshot
`crawler-out/UniRuse/snapshots/bodies/5917f560a9fc…`) stacks funding bands:
Приложение 1 = state-subsidised, Приложение 2 = paid, Приложение 3/4 =
individual/reinstated plans, Приложение 5 = second higher education. **Приложение
2 and Приложение 5 spell their column headers identically** ("Редовна / Задочна
/ Дистанционна форма на обучение"), so `value_headers` cannot discriminate
them. Measured: `value_headers ["задочна","лв."]` + alias `Бизнес мениджмънт`
returns **850** — the second-higher-education fee — because Приложение 2's cell
is a dash that `_is_placeholder` correctly skips and the resolver falls through
to the next sheet. Gate-green, semantically wrong. Before the fix the config
escaped that only by an accident of Bulgarian inflection — `"задочно"` (neuter)
exists solely in Приложение 1 — and the same accident left `uniruse-digmgmt`
reading the **paid** sheet while `uniruse-bizmgmt` and `uniruse-pubadmin` read
the **state** sheet, with nothing recording it. Under the landed marker the 850
is unreachable from a state-marked join, and each of the three bands is named
in the config and quoted in the value's own segments.

The fix is a resolver-side selector plus a load-time evidence rule, in the
tradition of `_resolve_columns` rather than of gate cleverness. `table_marker`
selects a table by a literal string that must equal a **cell** of it, and that
cell's row is emitted as provenance segment #1. Exact-cell, never substring:
`"Приложение 1"` occurs as a substring of six *other* cells spread across five
sheets of this one workbook — `"Приложение 10"`, and five prose footnotes citing
"Приложение 1..." mid-sentence — so a substring rule would
silently admit the wrong sheet, which is the failure class the key exists to
close. An optional `funding` label may ship as data only if it is a **literal
substring of `table_marker`**, checked at load time; the label is then provably
inside a segment the gate later verifies, and the funding band stops being
tribal knowledge. All of the above is LANDED.

One further step is proposed and deliberately **not** taken yet: allowing
`table_marker` / `value_headers` / `funding` on the tuition `JoinRef` as well as
on the source, on the reasoning that the SOURCE is one document while the REF is
which cell of it is mine. That would collapse UniRuse's three same-URL
`SourceConfig`s into one and stop Offerings multiplying sources over one
workbook. It is held back because it inverts `runner.document_plan`'s own
documented rationale — that function was re-keyed by source id (commit
`1f76405`) precisely so several sources could share a URL, and the comment
explaining why is now load-bearing for the shipped UniRuse config. Reversing
that decision two commits later, before any Offering code exists to need it,
would be churn on a shared function for an unrealised benefit. It belongs with
T5, where an Offering actually needs a per-form cell reference, and where the
two rationales can be reconciled in one change rather than fighting.

Why this shape and not a bigger one: every piece is a resolver or loader
change, config stays data proposed by onboarding agents and promoted by a human
(ADR-0001), no LLM enters the refresh loop, `gate()` is untouched (ADR-0002),
and `offerings` defaults to `{}` so the other eight configs are byte-identical.

The landed half is regression-tested end to end rather than argued: the STA-78
benchmark four replay (`crawler/tests/test_replay.py`, offline, network stubbed
to fail the run) reproduces spike A's audited acceptance exactly — 94 PASS + 6
NULL_OK across 100 fields, tier split G:55 / F:27 / B:12, 0 fabrications, 0
misses, 0 gate failures. The benchmark exercises only two of the six shipped
grid-join configs (`su-fees`, `mu-fees` — the only ones with Docling replay
fixtures), so the three uncovered ones were additionally run live against real
sources and diffed field-by-field against the pre-change commit `c6861ce`:
TUG / SHU / MUVarna, 75 cells, **0 differences** in value, status or tier. TUG
matters most there — three `fee-row` joins with no `value_pattern`, making it
the only shipped config where the dash-placeholder filter is actually live.

**What is NOT being built, measured rather than assumed.**

*Circumstance bands.* Приложение 3 (individual plan) and Приложение 4
(reinstated rights) do not resolve at all today: their header block carries a
token-free group row (`ДИ | СФ`, resp. `ДП | СФ`) that ends `_header_zone` at
row 5, so the form headers fall outside the zone and `_resolve_columns` returns
`None`. Forcing the zone open makes it worse — `["задочна, дистанционна форма",
"лв."]` then resolves to column 3, the all-forms ДП column (450), not column 7
(1035), because column 3's stacked header contains that substring and wins
first. `FeeColumn` needs a group discriminator and the zone walk needs to span a
token-free row; neither is in this change. Consequence: Бизнес мениджмънт
задочна ships **no fee**, which is the truthful answer for a prospective
student — its only задочна prices (1370 / 1380 / 850) are bands nobody can
choose — but it is not the whole truth, and the model does not yet hold it.

*Приложение 6/7 (Магистър) and 8 (Доктор).* Different match column
("Магистърска програма (наименование)", "Професионално направление") and, for
doctorates, no per-specialty row to alias against at all. 58 of UniRuse's 189
rows are Доктор and 68 are Магистър: this design covers roughly the bachelor
third of one university.

*Evidenced nulls.* A dash cell is quotable text, but the whole data row
contains several dashes and `gate()` has no positional check, so an extension
of its `value is None` path could not tell "not offered задочно" from "not
offered дистанционно". We do not extend `gate()`. An Offering with no fee ships
`NULL_OK` with a resolver-authored `null_reason` naming the table and column
checked — explicitly *not* Provenance — and Offering completeness is therefore a
**floor**, which must be said wherever the number is quoted.

*Dropping Program-level `tuition_join`.* Deliberately kept. Removing it would
push `tuition` to `NULL_OK` for every offering-configured Program; `tuition` is
in `expectations.KEY_FIELDS`, so `key_field_null_rate` would sit permanently
high and never spike again, leaving **no** publication gate watching tuition
nulls. Dropping it is conditional on making that gate offering-aware first.

*Offering fields beyond tuition.* `degree` is 1:1 with the registry row;
`language` and `admission` have no offering-scoped source at any configured
university; `duration` is key metadata. One field per Offering is 517 records at
UniRuse, not 2,585, and adding a field later is purely additive.

**What remains unproven.** No e-curriculum plan page has ever been snapshotted
into this repo — every claim about what that page states (the breadcrumb
`Факултет … > Бизнес мениджмънт > ОКС "Бакалавър" > Редовно`, the `Година: 1..4`
sections, the absence of a duration phrase and of a language) comes from the
task brief, not from an Artifact. The curriculum binding is therefore specified
but gated behind capturing one real plan first. Its URL is version-pinned
(`?code=<code>&version=<n>`), which is a **guaranteed** instance of CONTEXT.md's
stale-green drift — the pinned URL will return HTTP 200 with an identical hash
forever after the university publishes version n+1 — and no mitigation is
designed. `ATTENDANCE_FORMS` is a closed vocabulary measured on 3 of 51 exports;
the parser stays open (an unknown form yields an unconfigured Offering, never a
crash) while config stays closed, so the registry can enumerate a form no config
is allowed to name. `samostoyatelna` (32 of UniRuse's 517) will never appear in
a fee workbook column, so Offering completeness must be reported per form or it
reads as permanent failure. And nothing grades Offering values: `grader.py` keys
on `(program_id, field)` against a frozen Phase-0 key containing no Offering
rows, so every Offering number ships unmeasured while sitting in the same report
as graded ones. That is the most dangerous property of this change and nothing
in it fixes that.

**Consequences.** Coverage is untouched in definition, denominator and reported
numbers: `adjudication.AdjudicationReport.coverage` is `rsvu_code`-based and
this change does not touch it, so VUM 22.2% still means 4 codes ÷ 18 rows and
UniRuse still means (11 config-matched + 7 resolutions) ÷ 189. Offering records
are a sibling list at `programs[].offerings[]` and never enter
`programs[].fields`, so `expectations.summarize`, `grade_report` and the
10-point / 15-point pointer gates see exactly what they see today. Offerings
materialise only for Programs that explicitly declare `offerings` (which the
loader requires to carry an `rsvu_code`), so VUM — a `hash_stability`
benchmark university with an export and codes — stays byte-identical by
construction rather than by luck. One real behaviour change is expected and must
not be delivered as a surprise: the evidential `table_marker` row reads "…за
учебната 2025/2026 г.", and `ledger.infer_academic_year` searches a tuition
value's own segments for a `YYYY/YYYY` token, so UniRuse's tuition entries move
from the run's declared 2026/2027 to the workbook's own 2025/2026. That is more
truthful — the order really is last cycle's — and it will produce 11 REMOVED +
11 ADDED in the value diff and a year-lag block on the pointer move, once, for a
human to read and clear. Revisit this ADR if `gate()` ever gains a positional or
pairing check (it has none today, and nothing on record proposes adding one).

**Verification status.** This ADR originated from a design workflow whose
agents measured against a prototype in a scratchpad. Every quantitative claim
was re-derived here, against the real repo and the real stored artifacts,
before the ADR was committed — because a design document that repeats numbers
it did not check is exactly the failure this project keeps finding in its own
extractions.

Independently reproduced: the gate's pairing blindness
(`gate("редовна - 5", …) -> PASS` on row 30083's rendered line, while
`"самостоятелна - 9"` correctly rejects — so the refutation stands on a
measurement, not a worry); the enumeration totals (274 rows, 628 items, 0
unparsed, four forms — редовна 295 / задочна 269 / самостоятелна 38 /
дистанционна 26; AMTII 67→86, UniRuse 189→517, VUM 18→25); the 850 trap
(`["задочна","лв."]` + `Бизнес мениджмънт` → 850 from Приложение 5, gate-green);
the cross-band mixing in the pre-fix config (bizmgmt/pubadmin state, digmgmt
paid); the substring hazard (`"Приложение 1"` really is a substring of
`"Приложение 10"` in this workbook); Приложение 3/4 failing to resolve
(`_resolve_columns` returns `None`, header zone `(4, 5)`); and the 8.5%
completeness ceiling (44 configured offerings of 517).

NOT independently verified, and inherited from the workflow's prototype: the
claim that `crawler/tests/test_ledger.py`'s 3-tuple assertions would break
under a widened ledger key, and the eight enumerated loader rejections. Both
are assertions about code that does not exist yet; treat them as design intent
to be re-checked when T4 and T6 are implemented, not as established facts.
