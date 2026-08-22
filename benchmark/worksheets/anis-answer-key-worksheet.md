# Phase-0 labeling worksheet -- ANIS

Why this exists and why it has to be a human who did NOT build the extraction pipeline being graded: building the pipeline that produces run-report.json and building the "ground truth" it is graded against, in the same session, is the exact contamination ticket 07 exists to prevent -- every accuracy number is otherwise an upper bound. This file has to be filled in by reading the real pages, without looking at `crawler-out/ANIS/run-report.json` first -- otherwise you'll anchor on what the pipeline already said instead of judging the page independently.

**How to fill this in:** for each `field:` line below, open the page listed above it, find whether that field is actually stated, and write:
- if stated: the value, in a short verbatim quote copied straight from the page (not paraphrased -- the grader checks token-for-token support, so an inexact quote will look like a mismatch even when you're right)
- if not stated: write `NOT STATED` and, if it's not obvious why, a one-line reason (e.g. "page covers 3 programs generically, doesn't break out tuition per program")

**Field meanings** (same 5 fields the pipeline extracts for every program, `crawler/config.py FIELDS`):
- `degree` -- the DEGREE LEVEL awarded ("бакалавър", "магистър", "доктор", or their English equivalents). If the page ALSO prints a professional-qualification title nearby (e.g. "професионална квалификация електроинженер"), the degree level is what belongs here, not the qualification title -- record the level. These are different claims, and conflating them is what failed the 2026-08-16 gate run.
- `duration` -- length of study (semesters/years)
- `language` -- language of instruction
- `tuition` -- the fee, per year/semester
- `admission` -- the admission requirement(s)/exam(s) -- if the page lists several valid routes (e.g. "matriculation exam OR an entrance exam"), note all of them, not just one

**When you're done:** convert this into `crawler/grader.py`'s frozen `KeyEntry` format and run `python3 -m crawler grade` for a real PASS/FAIL verdict. Once converted, the key is frozen -- re-opening it to "fix" a grade after seeing a bad result would be the same contamination this worksheet exists to avoid.

---

**Sample:** 5 of ANIS's 18 programs.

**⚠️ ANIS is the extreme shared-page case: ALL 18 programs live on ONE page**
(the master's admissions page). Unlike UniRuse, there is no single-program
control available anywhere in this university — so your labels are the only
possible check on whether a value belongs to the program it was attached to.

The sample deliberately mixes very different subjects — Криминалистика,
Финанси, Сигурност и изкуствен интелект — because a value that is genuinely
per-program should differ across them, and a contaminated one should not.

**The judgement that matters, for every field:**
*does the page state this value for THIS program, or once for a different
program / for ANIS as a whole?*

- Stated for this program → quote it verbatim.
- Stated only elsewhere on the page, or only university-wide → **`NOT STATED`**,
  even though the value is visibly on the page.

Please be strict about this. On a single-page university, a crawler can attach
one program's duration or fee to all eighteen and every value will still be
verbatim-present, so a provenance check cannot catch it. Two other Bulgarian
universities in this benchmark have already been measured doing exactly that.

**Note on `degree`:** ANIS's page covers master's programs, several of which
have entry variants («за специалисти от друга специалност», «след
неикономически специалности»). If the page states a degree level for the
program, quote it; if it only says so generally for all its master's programs,
that is `NOT STATED` for the individual program.

**Where the other fields live:** ANIS states tuition and admission conditions
on its admissions pages (https://www.vusi.bg/priem-3/ and the master's page
itself). If a field is nowhere stated, `NOT STATED` is the right answer and is
just as useful to us as a value.

## ANIS — ANIS-rsvu8494-MSC-BG — Киберсигурност
## ANIS — ANIS-rsvu9422-MSC-BG — Управление на човешките ресурси (след неикономически специалности)
## ANIS — anis-m03 — Криминалистика
## ANIS — anis-m06 — Сигурност и изкуствен интелект
## ANIS — anis-m11 — Финанси
Page (shared by all 5 above): https://www.vusi.bg/priem-3/priem-magister/

This page describes multiple programs -- check whether it actually distinguishes what applies to each specific program vs. what it says generically for the whole page/faculty. If a value is stated only generically, not tied to one specific program, say so -- that's a real distinction the grader needs (a program-specific claim vs. a page-wide one aren't the same kind of evidence).

### ANIS-rsvu8494-MSC-BG
- field: degree
  value: магистър
- field: duration
  value: 4 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: Право на обучение ползват кандидати, които имат минимален успех Добър от диплома за завършена степен на висшето образование (чл. 21, ал. 4 от ЗВО). Успехът на дипломата се формира като средноаритметична оценка от средния успех от семестриалните изпити и оценките от държавните изпити или от защитата на дипломната работа.

### ANIS-rsvu9422-MSC-BG
- field: degree
  value: магистър
- field: duration
  value: 4 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: Право на обучение ползват кандидати, които имат минимален успех Добър от диплома за завършена степен на висшето образование (чл. 21, ал. 4 от ЗВО). Успехът на дипломата се формира като средноаритметична оценка от средния успех от семестриалните изпити и оценките от държавните изпити или от защитата на дипломната работа.

### anis-m03
- field: degree
  value: магистър
- field: duration
  value: 4 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: Право на обучение ползват кандидати, които имат минимален успех Добър от диплома за завършена степен на висшето образование (чл. 21, ал. 4 от ЗВО). Успехът на дипломата се формира като средноаритметична оценка от средния успех от семестриалните изпити и оценките от държавните изпити или от защитата на дипломната работа.

### anis-m06
- field: degree
  value: магистър
- field: duration
  value: 4 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: Право на обучение ползват кандидати, които имат минимален успех Добър от диплома за завършена степен на висшето образование (чл. 21, ал. 4 от ЗВО). Успехът на дипломата се формира като средноаритметична оценка от средния успех от семестриалните изпити и оценките от държавните изпити или от защитата на дипломната работа.

### anis-m11
- field: degree
  value: магистър
- field: duration
  value: 4 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: Право на обучение ползват кандидати, които имат минимален успех Добър от диплома за завършена степен на висшето образование (чл. 21, ал. 4 от ЗВО). Успехът на дипломата се формира като средноаритметична оценка от средния успех от семестриалните изпити и оценките от държавните изпити или от защитата на дипломната работа.
