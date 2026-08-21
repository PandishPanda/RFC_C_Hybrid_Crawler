# Phase-0 labeling worksheet -- SHU

Why this exists and why it has to be a human who did NOT build the extraction pipeline being graded: building the pipeline that produces run-report.json and building the "ground truth" it is graded against, in the same session, is the exact contamination ticket 07 exists to prevent -- every accuracy number is otherwise an upper bound. This file has to be filled in by reading the real pages, without looking at `crawler-out/SHU/run-report.json` first -- otherwise you'll anchor on what the pipeline already said instead of judging the page independently.

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

**Sample:** 5 of SHU's 20 programs, chosen proportionally rather than
flatteringly. SHU is really two populations: 5 programs configured earlier
(3–4 of 5 fields extract) and 15 added in this build-out (0–1 of 5). The
sample is 1 from the first group (География и регионална политика) and 4 from
the second, spanning the full 0/5–4/5 range.

**⚠️ SHU is the all-PDF case.** Every "page" below is a PDF — the program's
квалификационна характеристика. Open it and read the values there.

**Where the other fields live** (they are NOT in the program PDF):
- **tuition** → the fee orders:
  bachelor/prof. bachelor: https://www.shu.bg/wp-content/uploads/file-manager-advanced/users/students/taksi-DP-profBak-i-bak_2026-2027.pdf
  master's: https://www.shu.bg/wp-content/uploads/file-manager-advanced/users/students/taksi-magistri_2026-2027.pdf
  The rows are numbered by professional field (e.g. `2.1.3. Английска филология`).
- **admission** → Справочник 2026:
  https://www.shu.bg/wp-content/uploads/file-manager-advanced/users/ksk/Spravochnici/Spravochnik%202026%20g..pdf
  (Приложение 4 lists specialties, forms and entrance exams.)

Quote values **verbatim** from whichever document states them, and say which.
If a field genuinely isn't stated in any of them, write `NOT STATED` — that
confirms our null was honest rather than a miss, and both outcomes are useful.

**Field reminder:** `degree` is the LEVEL awarded (ОКС „бакалавър"/„магистър"),
not the professional qualification the PDF may also print.

## SHU — SHU-rsvu9749-BSC-BG — География и регионална политика
Page: https://www.shu.bg/wp-content/uploads/file-manager-advanced/users/ksk/fpn/bakalavyr/GRP.pdf

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: 175 €
- field: admission
  value: тест по география; ДЗИ по география и
икономика; тест по физика; ДЗИ по физика и астрономия; тест по химия; ДЗИ
по химия и опазване на околната среда; тест по биология; ДЗИ по биология
и здравно образование; тест по български език и литература; ДЗИ по
български език и литература; състезание по химия; състезание по география;
състезание по физика; състезание по биология.

## SHU — shu-122 — Икономика
Page: https://www.shu.bg/wp-content/uploads/studentski-ekomplekt/kvalifikaciya/122-kvalifikaciya-07-07-2026.pdf

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: 175 €
- field: admission
  value: тест по икономика; тест по география;
ДЗИ по география и икономика; тест по математика; ДЗИ по математика; тест
по български език и литература; ДЗИ по български език и литература;
състезание по икономика; състезание по математика.

## SHU — shu-216 — Английска филология
Page: https://www.shu.bg/wp-content/uploads/studentski-ekomplekt/kvalifikaciya/216-kvalifikaciya-07-22-2026.pdf

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: 165€
- field: admission
  value: тест по английски език; ДЗИ по
английски език; сертификати за владеене на английски език на В1, В2, С1 или
С2; международни сертификати (IELTS, TOEFL, FCE, CAE, CPE или SAT);
класиране на областен или национален кръг на олимпиада по английски език.

## SHU — shu-88 — Астрономия
Page: https://www.shu.bg/wp-content/uploads/studentski-ekomplekt/kvalifikaciya/88-kvalifikaciya-08-07-2026.pdf

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: 165€
- field: admission
  value: тест по физика; ДЗИ по физика и
астрономия; тест по математика; ДЗИ по математика; тест по химия; ДЗИ по
химия и опазване на околната среда; тест по български език и литература;
ДЗИ по български език и литература; състезание по химия; състезание по
физика.

## SHU — shu-1111 — Иновации в началното образование
Page: https://www.shu.bg/wp-content/uploads/studentski-ekomplekt/kvalifikaciya/1111-kvalifikaciya-08-04-2026.pdf

- field: degree
  value: Магистър
- field: duration
  value: 4 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: 489 €
- field: admission
  value: NOT STATED
