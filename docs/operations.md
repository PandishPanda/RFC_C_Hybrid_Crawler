# Operating the pipeline unattended (ADR-0005)

The refresh tick walks every configured university and lands everything
needing judgment in one Attention Ledger. Nobody watches stdout or data
24/7: the operator's whole job is the backlog `crawler attention` shows,
and the alerting story is one cron line plus its exit code.

## The weekly tick

```bash
# crontab: Monday 06:00, deterministic (no LLM in the refresh loop, ADR-0001)
0 6 * * 1  cd /path/to/RFC_C_Hybrid_Crawler && \
           python3 -m crawler refresh || <your alert command>
```

`crawler refresh` per university: publish (run → ledger → expectation
gates → pointer), then version-pin drift against the curriculum listing,
then proposal re-sync. A crash in one university becomes a
`refresh-error` item — traceback snapshotted, loop continues.

**Exit code contract:** non-zero iff this tick opened NEW attention
items or a university errored. A standing backlog the operator already
knows about does not re-page — silence means "nothing new", never
"nothing pending". The full result is
`crawler-out/refresh-report.json`: one policy verdict per university
(`proceed | warn | block` + the kinds needing a human) and the tick's
attention delta (opened / refreshed / lapsed).

`--tail` enables the LLM tail for one invocation (VUM is the only site
that needs it); `--uni X --uni Y` restricts the tick.

## The backlog

```bash
python3 -m crawler attention              # open items, oldest first
python3 -m crawler attention --uni VUM --kind check-verdict
python3 -m crawler attention --age 7      # only items in SLA breach
```

Six kinds. Age runs from the moment the item first opened —
re-detection never resets it. **WARN at 7 days open, ESCALATE at 30**
(constants in `crawler/policy.py`).

| kind | opened by | evidence | closed by |
| -- | -- | -- | -- |
| `blocked-publish` | publish gates refused the pointer move | snapshot (report is overwritten) | `crawler resolve` — or lapse when a later publish passes |
| `gate-failure` | a value failed the provenance gate | snapshot (list is overwritten) | lapse only: fix config, re-run |
| `check-verdict` | grading hit a cell the key can't auto-match | reference (verdicts file is durable) | `crawler resolve --verdict` — or lapse on a re-grade |
| `proposal` | onboarding proposed program pages | reference (proposal file is durable) | `crawler resolve --reason` after the human promotes/rejects |
| `drift` | a pinned plan was superseded in the listing | snapshot (listing is live) | lapse only: repin config, next tick |
| `refresh-error` | a university's tick crashed | snapshot (traceback is tick-local) | lapse only: next clean tick |

After reviewing a proposal batch, empty (or regenerate) the
university's `onboarding-proposal.json` and record the disposition in
its note — the file is regenerable output, and a leftover pending list
would reopen the resolved item on the next tick.

An open item that stops being detected closes as `lapsed` — the world
fixed itself, and the ledger says so instead of showing phantom work.

## Resolving

Resolve **executes** the judgment through the same gate-disciplined
functions the pipeline uses — it never merely records one (ADR-0005: a
judgment recorded but not performed is a new silent-rot channel).

```bash
# a blocked publish, judged correct after reading the evidence:
python3 -m crawler resolve blocked-publish:VUM \
    --reason "coverage drop is real: two programmes retired"
# -> verifies the run is in the ledger, THEN moves the pointer;
#    the reason is appended to tracked attention/resolutions.jsonl

# a CHECK cell, judged against the artifact:
python3 -m crawler resolve check-verdict:VUM:vum-corr.tuition \
    --verdict ok --shipped-value "1250 EUR" --note "restated in EUR"
# -> writes the manual verdict, bound to the exact shipped value
```

`gate-failure`, `drift` and `refresh-error` refuse manual resolve by
design: their only honest fix is a config or world repair, after which
the next tick lapses them.

Resolutions are original data — "I cleared this because X" — and live
in **tracked** `attention/resolutions.jsonl` (commit them). Open items
are derived state in gitignored `crawler-out/attention.jsonl`.

## Seasonal cadence

Bulgarian universities publish next year's fees, ordinances and
admission pages **June–September**. Tighten the cron to twice weekly in
that window; expect a wave of `drift` and `blocked-publish` items in
July as pages restate (the year-lag expectation also arms after
1 July). October–May is quiet — weekly is enough, and a tick that pages
then deserves a look rather than a snooze.

## What the tick never does

Grade and validate stay human-driven acts outside the tick. Refresh
reads pending `check-verdict` items into its verdicts but never lapses
them — only a grade can say a CHECK stopped existing. Promotion of
onboarding proposals stays a human config edit (ADR-0003); the LLM tail
stays out of the loop unless a human passes `--tail` (ADR-0001).

## docling-serve: Cyrillic OCR

`docker compose up -d` alone is not enough for the table-pdf route
against a scanned (image-only) PDF. The EasyOCR Cyrillic recognizer
weight (`cyrillic_g2.pth`) is not baked into the
`docling-serve-cpu` image, `DOCLING_SERVE_ARTIFACTS_PATH` disables
runtime downloads, and the weight lives in `docker-compose.yml`'s
`docling-models` named volume — not the image — so it must be reseeded
after any fresh volume (new machine, `docker compose down -v`, a volume
prune):

```bash
docker compose up -d
./scripts/seed-docling-cyrillic-ocr.sh
```

Without it, OCR silently misreads Cyrillic as Latin lookalikes (no
error — `OBIIIECTBEHO 3IPABE` for `ОБЩЕСТВЕНО ЗДРАВЕ`) instead of
failing loudly, because `ocr_preset: "auto"` picks an engine (RapidOCR)
with no Cyrillic model at all; `crawler/render.py` requests
`ocr_preset: "easyocr"` with `ocr_lang: ["bg", "en"]` explicitly
(measured live on MU-Sofia's fee PDF, 2026-08-24). Confirmed
empirically to change nothing for text-layer PDFs (OCR only touches
bitmap content) — only genuinely scanned documents are affected.
