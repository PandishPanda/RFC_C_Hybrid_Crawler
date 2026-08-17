# Onboarding proposes row↔page assignment only; join/alias correspondence and table-pdf routing stay human-authored, after confirmation

Status: accepted (2026-08-15)

Ticket 06's original scope asked the onboarding agent (`crawler/onboarding.py`)
to propose full site config for a new/redesigned university — URL maps,
renderer routing, recipe/bundle join definitions, fee-table row aliases — not
just which page documents a registry row. What shipped proposes the row↔page
ASSIGNMENT plus tier-G field verification on the confirmed page only;
renderer routing and join/alias correspondence remain exclusively
human-authored config, proposed by nobody. We decided this split by
architecture, not convenience, after three independent adversarial reviews
tried and failed to construct a gate-checkable shape for the missing pieces
that would justify agent-proposing them pre-confirmation — a first draft of
this reasoning ("no verbatim-quote shape exists at all") was itself refuted
in the process and is corrected below.

The row↔page assignment already has no verbatim-quote shape `gate()` can
check — it is a cross-language semantic judgment ("this Bulgarian registry
row IS this page"), which is why `ProposedProgram.assignment_verified` is
hardcoded to always return `False` (ADR-0002). Two more config classes turn
out to share that property, for two *different* mechanical reasons, each
confirmed by adversarial review rather than assumed:

**Join/alias CORRESPONDENCE** (which table row/column belongs to which
program) is a hard, measured `gate()` limit, not an absence of quotable
text — the adversarial review's own first attempt to refute this showed
`gate()` genuinely *can* confirm an alias literal exists verbatim in the
right table (`REJECT_CONTAINMENT` catches a hallucinated or wrong-artifact
alias). What it cannot confirm is that *this* occurrence is the correct,
unique binding to *one* program: containment + value-token support has no
notion of position or uniqueness across an artifact. This is
`provenance.py`'s own documented, twice-measured blind spot (RFC v2 §3 Q4:
"a truthful segment from the wrong row/column of a table passes this
gate") — exactly why `cascade.py` resolves table joins with deterministic,
header-aware code (`_resolve_columns`/`fee_row_join`) instead of trusting
`gate()` with correspondence, and exactly why an agent-proposed alias could
pass the gate while still being bound to the wrong program.

**Table-pdf ROUTING** is a narrower, structurally circular case: `gate()`
only ever checks text against an *already-rendered* Artifact, but which
renderer produces that Artifact is exactly what a route proposal would
decide — there is no artifact to check a "this should be table-pdf" claim
against until after the question is already answered. `render.py`'s
`resolve_route()` reflects this by design (table-pdf is "NEVER sniffed, it
is config opt-in", RFC v2 Q2) rather than by oversight. This is distinct
from html-vs-prose-pdf routing, which `resolve_route()` *does* classify
mechanically from content-type/PDF-magic bytes, with no proposal or gate
check needed at all — a real, latent capability, though currently unused by
either `runner.document_plan()` (defaults every plain page to html) or
`onboarding.verify_page()` (hardcodes `ROUTE_HTML`), so it is not live
behavior anywhere in this codebase today, only an accurate distinction to
keep in mind if it is ever wired up.

Why: an agent proposing join/alias correspondence or table-pdf routing
before a human confirms the row↔page assignment would stack an ungate-able
guess on top of another ungate-able guess — ticket 05's 0/14
automatic-match rate already showed the bigger, more urgent gap was
"nobody has looked at this page yet," not "the recipes for pages we have
are wrong." Deferring correspondence/routing authorship to the moment
right after a human confirms the assignment is not a lesser version of the
original ask — it is the only point in the pipeline where a human is
already reading the real page, which is exactly when writing its recipe
stops being a guess.

Consequences: `crawler/onboarding.py`'s scope (row↔page assignment +
tier-G field verification) is deliberately narrower than ticket 06's
original spec text — the missing pieces (URL maps beyond one page,
join/alias config, table-pdf routing) remain real, unimplemented,
human-authored work, not proven unnecessary. This does not satisfy ticket
06's stated acceptance bar (2 universities onboarded end-to-end,
person-time measured, config promoted by hand) — it explains the correct
order of the remaining work, not that the remaining work is done. Revisit
if `gate()` ever gains a positional/uniqueness check (it does not have one
today, and none of RFC v2's known blind-spot fixes propose adding one to
`gate()` itself — the fix on record is tier F's deterministic column
resolver, never a `gate()` capability increase).
