# benchmark/ — durable human evidence

**Tracked on purpose.** Everything here is original human judgment that
the pipeline cannot regenerate, and that the Phase-0 protocol forbids
re-deriving after the fact: re-labelling a key or re-judging a verdict
once you have seen the grade is exactly the contamination the blind
benchmark exists to prevent.

That is why this lives here and not under `crawler-out/` (gathered,
gitignored, wipe-at-will) or `.scratch/` (local working notes,
gitignored). Losing this directory would mean the accuracy numbers can
never be reproduced — not "re-run it", but *gone*.

| Path | What | Regenerable? |
| -- | -- | -- |
| `keys/<uni>-frozen-key.json` | Frozen answer keys — the ground truth every accuracy number is measured against | **Never** |
| `verdicts/<UniID>.json` | Manual ok/wrong verdicts resolving CHECK rows | **Never** |
| `worksheets/` | The labelling worksheets as filled in, plus their review sheets — the audit trail behind each key | **Never** |
| `baseline.json` | Archived graded results the scorecard reads | No (records archived runs) |
| `site-findings.json` | Per-university negative findings | No (observations, retired by fixing) |

## Rules

- A frozen key is **frozen**. Amend it only with a recorded reason, and
  only for a labeller-confirmed miss — never to make a grade look better.
- Verdicts are keyed by `program_id + field`. A verdict carries over to a
  later run only when the shipped value is byte-identical; anything that
  changed goes back to the human.
- `crawler validate` reads `baseline.json` and `site-findings.json` from
  here. `crawler.grader` reads `verdicts/` from here, falling back to the
  legacy `crawler-out/<uni>/manual-verdicts.json` for un-migrated clones.
