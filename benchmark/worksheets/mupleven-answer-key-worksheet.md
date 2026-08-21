# Phase-0 labeling worksheet -- MUPleven

Why this exists and why it has to be a human who did NOT build the extraction pipeline being graded: building the pipeline that produces run-report.json and building the "ground truth" it is graded against, in the same session, is the exact contamination ticket 07 exists to prevent -- every accuracy number is otherwise an upper bound. This file has to be filled in by reading the real pages, without looking at `crawler-out/MUPleven/run-report.json` first -- otherwise you'll anchor on what the pipeline already said instead of judging the page independently.

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

**MUPleven source hints (learned from the VUM round — quote VALUES, never
paste URLs):** tuition for all four programs lives in the fee-order PDF
https://mu-pleven.bg/forms/ksk2026/taksi_za_kandidatstvane_obuchenie_2026_2027.pdf
— open it and quote the row for the program (state currency exactly as
printed: лв./€). Admission requirements live in the справочник PDF
https://mu-pleven.bg/forms/ksk2026/Spavochnik%20KSK%202026%20-%2015-12-2025.pdf
(and/or the program page) — quote the requirement sentence verbatim. If a
field is stated only per-group (not per-program), say so.

## MUPleven — mu-med — Медицина
Page: https://mu-pleven.bg/index.php/bg/specialities/medicine/479-annotation-of-specialty

- field: degree
  value: МАГИСТЪР
- field: duration
  value: 6-годишен период на обучение (10 семестъра и 1 година преддипломен стаж).
- field: language
  value: Български
- field: tuition
  value: 620€ / семестър от 1 до 4ти семестър, 565€ за 5ти и 6ти семестър
- field: admission
  value: https://mu-pleven.bg/forms/ksk2026/Spavochnik%20KSK%202026%20-%2015-12-2025.pdf

## MUPleven — mu-pharm — Фармация
Page: https://mu-pleven.bg/index.php/bg/specialities/farmacy/3602-2016-05-19-07-23-24

- field: degree
  value: МАГИСТЪР
- field: duration
  value: 5-годишен период на обучение (9 семестъра и 1 семестър преддипломен стаж).
- field: language
  value: Български
- field: tuition
  value: 620€ / семестър от 1 до 4ти, 565€ 5ти семестър
- field: admission
  value: https://mu-pleven.bg/forms/ksk2026/Spavochnik%20KSK%202026%20-%2015-12-2025.pdf

## MUPleven — mu-nurse — Медицинска сестра
Page: https://mu-pleven.bg/index.php/bg/specialities/medical-nurse/492-annotation-of-specialty

- field: degree
  value: Бакалавър
- field: duration
  value: 4-годишен период на обучение
- field: language
  value: Български
- field: tuition
  value: 410 € / семестър
- field: admission
  value: Положилите държавен зрелостен изпит по биология, могат да кандидатстват по избор с оценката от него или с приемен изпит-тест по биология; Неположилите държавен зрелостен изпит по биология кандидатстват с приемен изпит-тест по биология;

## MUPleven — mu-ap — Помощник-фармацевт
Page: https://mu-pleven.bg/index.php/bg/specialities/assistant-pharmacist

- field: degree
  value: 
- field: duration
  value:
- field: language
  value: Български
- field: tuition
  value: 410 / семестър
- field: admission
  value: https://mu-pleven.bg/forms/ksk2026/Spavochnik%20KSK%202026%20-%2015-12-2025.pdf
