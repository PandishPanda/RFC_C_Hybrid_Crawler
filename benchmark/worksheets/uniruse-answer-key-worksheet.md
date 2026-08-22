# Phase-0 labeling worksheet -- UniRuse

Why this exists and why it has to be a human who did NOT build the extraction pipeline being graded: building the pipeline that produces run-report.json and building the "ground truth" it is graded against, in the same session, is the exact contamination ticket 07 exists to prevent -- every accuracy number is otherwise an upper bound. This file has to be filled in by reading the real pages, without looking at `crawler-out/UniRuse/run-report.json` first -- otherwise you'll anchor on what the pipeline already said instead of judging the page independently.

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

**Sample:** 5 of UniRuse's 20 programs, chosen to test one specific risk.
UniRuse has **no per-program pages at all** — every program lives on a shared
faculty page with up to five siblings. The sample is deliberately built to
expose that: **Софтуерно инженерство and Компютърни науки share ONE page**,
Бизнес мениджмънт and Компютърни системи и технологии come from two other
shared pages, and Право has a page to itself as a control.

**⚠️ The judgement that matters here is per-program vs page-wide.**
For each field, ask: *does the page state this value for THIS program, or is it
stated once somewhere on the page for a different program / for the faculty as a
whole?*

- Stated for this program → quote it verbatim.
- Stated only elsewhere on the page, or only generally → **`NOT STATED`**,
  even though a value is visibly present on the page.

That distinction is the entire point of this worksheet. A crawler reading a
shared page can pick up a neighbouring program's value and look perfectly
correct doing it — the value really is on the page, so a provenance check
passes it. Your labels are the only thing that can catch it. Please be
especially careful with **degree**: a faculty page may list bachelor's and
master's programs together.

**Where the other fields live:**
- **tuition** → the fee order (a spreadsheet):
  https://www.uni-ruse.bg/education/students/Documents/Zapoved_taksi_2025_2026_3811_14.10.2025.xlsx
  Note it is the **2025/2026** order — if there is no 2026/2027 figure, say so;
  that itself is a finding we already track.
- **admission** → the faculty page's admission section or UniRuse's приём pages.

`NOT STATED` is a fully valid answer everywhere and is as useful to us as a value.

## UniRuse — uniruse-bizmgmt — Бизнес мениджмънт
Page: https://www.uni-ruse.bg/admission/bachelors/guide/specialities/faculty-of-business-and-management

- field: degree
  value: NOT STATED
- field: duration
  value: NOT STATED
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: 1) оценка от държавен зрелостен изпит или един избираем изпит по: български език, история на България или биология; 2) оценка от дипломата по български език.

## UniRuse — uniruse-compsystems — Компютърни системи и технологии
Page: https://www.uni-ruse.bg/admission/bachelors/guide/specialities/faculty-of-electrical-and-electronic-engineering-and-automation

- field: degree
  value: NOT STATED
- field: duration
  value: NOT STATED
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: 1) оценка от държавен зрелостен изпит или един избираем изпит по: математика, български език, ИИТ или ОТП; 2) оценка от дипломата по математика; 3) оценка от дипломата по български език.

## UniRuse — uniruse-se — Софтуерно инженерство
## UniRuse — uniruse-cs — Компютърни науки
Page (shared by all 2 above): https://www.uni-ruse.bg/admission/bachelors/guide/specialities/faculty-of-natural-science-and-education

This page describes multiple programs -- check whether it actually distinguishes what applies to each specific program vs. what it says generically for the whole page/faculty. If a value is stated only generically, not tied to one specific program, say so -- that's a real distinction the grader needs (a program-specific claim vs. a page-wide one aren't the same kind of evidence).

### uniruse-se
- field: degree
  value: NOT STATED
- field: duration
  value: NOT STATED
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: 1) оценка от държавен зрелостен изпит или един избираем изпит по: математика, български език, ИИТ или ОТП; 2) оценка от дипломата по български език; 3) оценка от дипломата по математика.

### uniruse-cs
- field: degree
  value: NOT STATED
- field: duration
  value: NOT STATED
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: 1) оценка от държавен зрелостен изпит или един избираем изпит по: математика, български език, ИИТ или ОТП; 2) оценка от дипломата по български език; 3) оценка от дипломата по математика.

## UniRuse — uniruse-law — Право
Page: https://www.uni-ruse.bg/admission/bachelors/guide/specialities/faculty-of-law-studies

- field: degree
  value: магистър
- field: duration
  value: NOT STATED
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: 1) оценка от държавен зрелостен изпит по БЕЛ, а за завършили преди 2008г се взема общият успех от дипломата за средно образование, 2) оценка от кандидатстудентски изпит по история на България; 3) оценка от дипломата по български език
