# Phase-0 labeling worksheet -- MUVarna

Why this exists and why it has to be a human who did NOT build the extraction pipeline being graded: building the pipeline that produces run-report.json and building the "ground truth" it is graded against, in the same session, is the exact contamination ticket 07 exists to prevent -- every accuracy number is otherwise an upper bound. This file has to be filled in by reading the real pages, without looking at `crawler-out/MUVarna/run-report.json` first -- otherwise you'll anchor on what the pipeline already said instead of judging the page independently.

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

**Sample:** 5 of MUVarna's 15 programs, proportional and spanning the range.
MUVarna is two populations: 5 configured earlier (Кинезитерапия extracts 5/5,
the PhD programs 2/5) and 10 added in this build-out (all 1/5). The sample is
2 from the first group and 3 from the second.

**⚠️ Nine of MUVarna's programs share ONE page** — the «Специалности» page.
For any program whose page below is `.../BG/specialnosti`, the important
judgement is: **does the page state this value for THIS program specifically,
or only generically for the whole page?** If a value is stated only generally
(e.g. one duration mentioned once, for a different specialty), that is NOT this
program's value — write `NOT STATED`. A shared page is exactly where a crawler
can pick up a neighbouring program's value and look correct doing it, so this
distinction is the most valuable thing in this worksheet.

**Where the other fields live** (not on the specialties page):
- **tuition** → fee order: https://www.mu-varna.bg/BG/Admission/Documents/2026/Taksi%202026-2027.pdf
- **PhD tuition** → https://www.mu-varna.bg/BG/Research/taksi-doktorantsko-uchiliste
- **admission** → the program's own «Кандидатстване»/приём pages, if it has one.

Quote verbatim, say which document it came from, and use `NOT STATED` freely —
confirming a null was honest is as useful to us as finding a value.

**Field reminder:** `degree` is the LEVEL awarded (ОКС „бакалавър"/„магистър"/
„доктор"), not the professional qualification.

## MUVarna — MUVarna-rsvu31893-BSC-BG — Кинезитерапия
## MUVarna — muv-medicina — Медицина
## MUVarna — muv-akusherka — Акушерка
## MUVarna — muv-rentgenov-laborant — Рентгенов лаборант
Page (shared by all 4 above): https://www.mu-varna.bg/BG/specialnosti

This page describes multiple programs -- check whether it actually distinguishes what applies to each specific program vs. what it says generically for the whole page/faculty. If a value is stated only generically, not tied to one specific program, say so -- that's a real distinction the grader needs (a program-specific claim vs. a page-wide one aren't the same kind of evidence).

### MUVarna-rsvu31893-BSC-BG
- field: degree
  value: бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: български
- field: tuition
  value: 500 € за първи курс, 410 € за втори, трети и четвърти курс
- field: admission
  value: NOT STATED

### muv-medicina
- field: degree
  value: МАГИСТЪР
- field: duration
  value: 10 семестъра
- field: language
  value: български
- field: tuition
  value: 800 € за първи курс, 720 € от втори до четвърти курс, 620 € от пети до осми курс
- field: admission
  value: оценката от държавния зрелостен изпит по български език и литература и утроените оценки от конкурсните изпити по биология и химия в МУ-Варна. Максималният бал е 42,00, а минималният бал за участие в класирането за обучение по държавна поръчка е 25

### muv-akusherka
- field: degree
  value: бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: български
- field: tuition
  value: първи и втори курс - освободени от такса, трети и четвърти курс 410 €
- field: admission
  value: оценката от държавния зрелостен изпит по български език и литература и утроената оценка от конкурсния изпит тест по биология в МУ-Варна. Максималният бал е 24,00. Кандидат-студентите, които имат оценки от изпита по биология от предварителната и редовна сесия, участват в класирането с по-високата от двете.​

### muv-rentgenov-laborant
- field: degree
  value: професионален бакалавър
- field: duration
  value: 6 семетъра
- field: language
  value: български
- field: tuition
  value: 500 € първи курс, 390 € за втори и трети курс
- field: admission
  value: балообразуваща е оценката от дипломата за средно образование от ДЗИ по официалния език на държавата, в която дипломата е издадена, а при липса на такава – оценката от курса на обучение. Тези кандидати имат право да бъдат записани за студенти само след успешно полагане на изпит за владеене на български език или преминат подготвителен курс по български език в Департамента по чуждоезиково обучение, комуникации и спорт на МУ-Варна, или след представяне на свидетелство за езикова компетентност по български език – ниво не по-ниско от В2, или удостоверение за положен ДЗИ по БЕЛ

## MUVarna — MUVarna-rsvu11227-PHD-BG — Патофизиология
Page: https://www.mu-varna.bg/BG/Research/obyava-priem-doktoranti

- field: degree
  value: NOT STATED
- field: duration
  value: NOT STATED
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: NOT STATED
