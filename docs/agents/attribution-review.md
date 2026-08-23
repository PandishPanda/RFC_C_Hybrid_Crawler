# Attribution review — the third review axis

Every ticket in this repo gets a two-axis review: **standards** (does the
code follow the repo's documented practice?) and **spec** (does it do what
the issue asked?). Tickets that change what the pipeline *extracts* get a
third: **attribution** — did every value that moved arrive from the right
place?

This axis exists because the other two, and the blind benchmark, all missed
the same defect class. In the rev.5 round, seven fabrications were closed
that sat in cells **no answer key covered**: a programme taking its
neighbour's degree, another taking an unconfigured section's duration, a
master's programme billed at the bachelor's fee. Every one was
verbatim-present and provenance-gate-clean. Sampled grading measures the
pipeline; it does not police it. They were found by reading the provenance
of every changed cell, and by a second agent whose only job was to refute
the first.

Be honest about what this is: the loader, the provenance gate and the
dataset expectations are enforced by code. **This one is enforced by the
ticket's done-check** — it is a process gate, and it holds only as long as
it is actually run. `crawler diff` (a provenance-aware changed-cell verb) is
the natural mechanisation; it does not exist yet.

## When it runs

Run it on any ticket that can change a shipped value or where that value
came from:

- config changes — anchors, `field_anchors`, joins, regions, scopes,
  `suppress_labels`, new program entries, new sources
- cascade / render / provenance mechanism changes
- LLM-tail prompt or gating changes
- onboarding promotions that add program pages

Skip it only for changes that cannot move a cell (docs, tests-only,
tooling). If you are unsure, run it — the diff is cheap when nothing moved.

It does **not** hang off `crawler grade`. Grading covers a 5-program sample;
this axis covers every changed cell in the university, keyed or not. A
review scoped to the benchmark sample is dead on arrival.

## Step 0 — take the baseline first

`crawler run` overwrites `crawler-out/<UniID>/run-report.json` in place, so
the "before" side has to be captured before you touch anything:

```bash
cp crawler-out/<UniID>/run-report.json "$SCRATCH/before-<UniID>.json"
```

If you forgot: `crawler.ledger` keeps every prior run append-only under
`crawler-out/<UniID>/`, carrying value, `method`, `tier` and `source_url`
per cell — enough to reconstruct most of the comparison, but **not** the
verbatim snippets. Note the gap in the review rather than pretending the
diff was complete.

## Step 1 — the changed-cell set

```bash
python3 -m crawler run <UniID>
python3 scripts/changed-cells.py "$SCRATCH/before-<UniID>.json" \
    crawler-out/<UniID>/run-report.json --snippets
```

A cell is in scope when **any** of status, value, `method`, artifact ref,
or verbatim snippets changed. Status transitions are labelled on both sides
(`REJECT_NOT_VERBATIM->PASS`, `PASS->NULL_OK`) so a value that *newly ships*
is never read as a value that merely changed — and so a value the gate
refused is never mistaken for the current state. Cells whose value is unchanged but whose
attribution moved are reported as `attribution-only`, and they are not
noise: a value that stays right for a newly-wrong reason is exactly the
akusherka/neighbour-degree shape. `crawler.ledger.diff_runs` compares values
alone and will not show them.

## Step 2 — read the provenance of every changed cell

Every cell, not a sample. For each one, against the artifact itself:

1. **Whose is it?** Does the snippet sit in a region anchored to *this*
   program — or in a sibling's section, a page-wide preamble, or prose that
   merely mentions the program's name?
2. **Is it the same claim?** A master's programme reading a bachelor's fee
   row, or a part-time duration read from the full-time block, is
   verbatim-present and wrong.
3. **Is it whole?** A schedule stating one row per year, shipped as its
   first row, is a wrong value — not a partial one.
4. **Why did it move?** A cell the ticket did not intend to touch changing
   is a finding in itself, whichever direction it moved.

Record a per-cell verdict — `ok` with the reason, or the defect — in the
ticket. "Reviewed the diff" is not a verdict.

## Step 3 — the independent refuter

Dispatch a second agent whose only job is to **refute** the first. The
independence is a contract, not an adjective:

- it gets the cell, its provenance block, and the artifact
- it does **not** get the first reviewer's reasoning, verdicts, or the claim
  that the round is clean
- it is told to default to "misattributed" when the evidence does not
  positively tie the span to this program

**Decision rule:** any refutation the first reviewer cannot answer from the
artifact returns the cell to CHECK and blocks the ticket. Ties do not go to
the incumbent value.

## Step 4 — mechanism fixes must be exercised through the production seam

If the fix is a mechanism (a region rule, an anchor rule, a scope check),
its regression test must run through the same seam production uses. The
caps-heading rule computed regions on raw text in its unit tests and on
normalised text in production, so it never fired while its tests stayed
green — four latent fabrications survived behind it. A green unit test on a
hand-fed input is not evidence the rule runs.

## Done-check

A ticket carrying value changes is not done until:

- [ ] baseline captured before the run, changed-cell set produced
- [ ] every changed cell has a recorded verdict with its reason
- [ ] an independent refuter ran against those cells and its objections are
      answered from the artifact
- [ ] mechanism fixes assert through the production seam
- [ ] any cell that stayed CHECK is either fixed or shipped as an honest
      null — never shipped on the benefit of the doubt
