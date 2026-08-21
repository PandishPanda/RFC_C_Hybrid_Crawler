# Phase-0 labeling worksheet -- NBU

Why this exists and why it has to be a human who did NOT build the extraction pipeline being graded: building the pipeline that produces run-report.json and building the "ground truth" it is graded against, in the same session, is the exact contamination ticket 07 exists to prevent -- every accuracy number is otherwise an upper bound. This file has to be filled in by reading the real pages, without looking at `crawler-out/NBU/run-report.json` first -- otherwise you'll anchor on what the pipeline already said instead of judging the page independently.

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

**Sample:** 5 of NBU's 20 programs, spread across subject areas
(animation, anthropology, business economics, English studies, Arabic studies).

**⚠️ This worksheet measures something different from the others — please read.**

NBU currently ships only `degree`; the other four fields come back as nulls.
The recorded reason is that they are JS-loaded and our fetcher can't reach them.
This grade is what tests that claim, so **label what YOU can see in a normal
browser**, including anything that only appears after the page finishes loading
or after clicking a tab (Обща информация / Прием / etc.).

That gives us two honest numbers at once:

- a field you CAN see but we shipped null → a **MISS**, which measures exactly
  what the JS limit costs us;
- a field genuinely not stated anywhere on the program's pages → write
  `NOT STATED`, which confirms our null was honest rather than a failure.

Both outcomes are useful. Please don't skip a field just because you suspect
we couldn't reach it — that suspicion is the thing being measured.

**Field reminder:** `degree` is the LEVEL awarded (бакалавърска/магистърска
програма, ОКС), not the professional qualification («режисьор на анимационни
филми») and not the department. Quote values verbatim.

## NBU — nbu-2520 — Англицистика (Английски език, култура и литература)
Page: https://ecatalog.nbu.bg/default.asp?V_Year=2026&PageShow=programpresent&P_Menu=generalinfo&Fac_ID=3&M_PHD=0&P_ID=2520&TabIndex=1&l=0

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: завършилите средно образование - с полагане на кандидатстудентски изпит тест за общообразователна подготовка (ТОП) и представяне на диплома за средно образование;

## NBU — nbu-1835 — Анимационно кино
Page: https://ecatalog.nbu.bg/default.asp?V_Year=2026&PageShow=programpresent&P_Menu=generalinfo&Fac_ID=3&M_PHD=0&P_ID=1835&TabIndex=1&l=0

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: завършилите средно образование - с полагане на кандидатстудентски изпит тест за общообразователна подготовка (ТОП) и представяне на диплома за средно образование;

## NBU — nbu-821 — Антропология
Page: https://ecatalog.nbu.bg/default.asp?V_Year=2026&PageShow=programpresent&P_Menu=generalinfo&Fac_ID=3&M_PHD=0&P_ID=821&TabIndex=1&l=0

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: завършилите средно образование - с полагане на кандидатстудентски изпит тест за общообразователна подготовка (ТОП) и представяне на диплома за средно образование;

## NBU — nbu-2539 — Арабистика (Арабски език, култура и литература)
Page: https://ecatalog.nbu.bg/default.asp?V_Year=2026&PageShow=programpresent&P_Menu=generalinfo&Fac_ID=3&M_PHD=0&P_ID=2539&TabIndex=1&l=0

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: завършилите средно образование - с полагане на кандидатстудентски изпит тест за общообразователна подготовка (ТОП) и представяне на диплома за средно образование;

## NBU — nbu-3185 — Бизнес икономика (съвместна програма с университета в Йорк)
Page: https://ecatalog.nbu.bg/default.asp?V_Year=2026&PageShow=programpresent&P_Menu=generalinfo&Fac_ID=3&M_PHD=0&P_ID=3185&TabIndex=1&l=0

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: NOT STATED
- field: tuition
  value: NOT STATED
- field: admission
  value: NOT STATED
