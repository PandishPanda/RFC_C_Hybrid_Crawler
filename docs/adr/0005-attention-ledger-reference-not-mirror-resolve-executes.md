# One Attention Ledger owns everything awaiting a human: reference-not-mirror, resolve-executes, storage split by regenerability

Status: accepted (2026-08-17)

The 2026-08-17 architecture review ("lowering the human factor") found that
every human touchpoint in the pipeline fails safe — blocked publishes keep
old data live, unresolved CHECKs keep grades PENDING, rejects ship as
nulls — but the backlog of work awaiting a human lived on six
differently-shaped surfaces (repair-queue.json, overwritten
publish-reports, overwritten gate_failures, onboarding proposals,
grade-time CHECK lists, stdout drift reports), two of which are destroyed
by the pipeline's own next run. "Fails safe" therefore degraded into
"fails silent": nothing measured, aged, aggregated, or announced the
backlog. We decided to concentrate all of it behind one module —
`crawler/attention.py`, the Attention Ledger — with the term **Attention
item** canonized in CONTEXT.md: one unit of work only a human can advance;
anything that is not an Attention item must be safe to leave unwatched.

**Reference, not mirror — with a carve-out decided by volatility.** Items
carry `{id, kind, uni_id, subject, opened_at, last_seen, status}` and a
*pointer* to their source store, not a copy of its contents. A mirroring
ledger was rejected because duplicated evidence drifts from its source and
turns the ledger into a second place the truth must be maintained. The
carve-out: the two kinds whose evidence the next run destroys
(gate-failure lists and blocked-publish reasons, both living inside
per-run reports that are overwritten in place — a measured incident
already erased a graded n=33 result within a day) snapshot their evidence
into the item at open time. Reference where the source is durable,
snapshot where it is not.

**Identity is a natural key; age is the point.** An item's id is
`kind:uni:subject` (`blocked-publish:VUM`, `check:vum-corr:tuition`).
Re-detection on a later refresh tick updates `last_seen` and preserves the
original `opened_at` — the alternative (append per detection) resets the
age clock every tick and floods the list, defeating the SLA semantics
(warn at 7 days open, escalate at 30, constants in the policy module). An
open item that stops being detected without any human resolution closes as
`lapsed` — the world fixed itself, and the ledger says so rather than
showing phantom work.

**Resolve executes; it never merely records.** `crawler resolve <item-id>`
dispatches by kind to the existing deep, gate-disciplined functions —
promotion through the ledger pointer write with a required reason,
repair rows through `adjudication.resolve_repair_entry`, verdicts through
`grader.write_manual_verdict`. A record-only resolve was rejected because
a judgment recorded but not performed is a brand-new silent-rot channel —
precisely the failure class this ADR exists to close. ADR-0002 is
untouched: the deep functions still gate every resolution ("no exemption
for humans"); the ledger only orchestrates and records who/why.

**Storage splits by regenerability.** Open items are derived state and
live in gitignored `crawler-out/attention.jsonl`, consistent with the
invariant that `crawler-out/` is regenerable. Human resolutions are NOT
regenerable — "I cleared this block because X" is original data, like the
answer key — so they append to tracked `attention/resolutions.jsonl` at
the repo root. Keeping everything in `crawler-out` (simplest) was rejected
because it repeats the evidence-evaporation problem; keeping everything
tracked was rejected because open items are recomputable and would spam
history. One global ledger, not per-university files: the whole point is
a single worklist across 51 universities; per-uni views are a CLI filter,
not a storage shape.

**The consumer is the refresh orchestrator.** `crawler refresh` iterates
every configured university — run (deterministic only by default; the LLM
tail is opt-in per invocation, keeping ADR-0001's no-LLM-in-the-refresh-
loop default), publish, adjudicate, then the previously-unwired checks
(version-pin drift, registry-export age, unreviewed auto-resolution
counts) — and emits attention items instead of expecting a human to watch.
One university's crash becomes a `refresh-error` item and the loop
continues; the tick ends with `crawler-out/refresh-report.json` and a
non-zero exit iff anything new needs a human, so a weekly cron plus exit
code is a complete v1 alerting story (a notification adapter seam exists;
no channel is committed until one is actually chosen). Nine kinds at v1:
blocked-publish, repair-row, gate-failure, proposal, check-verdict,
drift, export-age, unreviewed-auto-resolution, refresh-error. Grade and
validate stay human-driven acts outside the tick.

Severity fields, per-kind SLA pairs, per-uni policy config, and mirroring
were all considered and rejected for v1: kind + age is the priority
signal, and policy stays code constants (like `expectations.py`'s
thresholds) until two policies genuinely diverge — these are pipeline
policy, not site knowledge, so ADR-0001's config-is-site-data line stays
clean.

Revisit if: the reference model forces the CLI to dereference stores that
themselves become volatile (then widen the snapshot carve-out); or a
second operator appears and per-person assignment/notification becomes
real (then the adapter seam gets its first channel and `resolved_by`
stops defaulting to git identity).
