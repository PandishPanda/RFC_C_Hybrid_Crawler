# Phase-0 labeling worksheet -- AUBG

Why this exists and why it has to be a human who did NOT build the extraction pipeline being graded: building the pipeline that produces run-report.json and building the "ground truth" it is graded against, in the same session, is the exact contamination ticket 07 exists to prevent -- every accuracy number is otherwise an upper bound. This file has to be filled in by reading the real pages, without looking at `crawler-out/AUBG/run-report.json` first -- otherwise you'll anchor on what the pipeline already said instead of judging the page independently.

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

**AUBG source hints (VUM/MUPleven lessons — quote VALUES verbatim, never URLs):**
tuition/fees usually live on admissions/fees pages and the academic catalog PDF
https://www.aubg.edu/wp-content/uploads/2026/06/AY-2026-27-1st-ed.pdf ;
admission requirements on https://www.aubg.edu/admissions/bachelors/how-to-apply/
(EMBA: https://www.aubg.edu/admissions/emba/how-to-apply/). The site is
JS-heavy — if a value only appears after clicking/expanding, note "JS-only"
on that cell: that distinction is itself evidence for this benchmark.

## AUBG — aubg-cs — BA in Computer Science
Page: https://www.aubg.edu/academics/bachelor-degrees/computer-science/

- field: degree
  value: Major
- field: duration
  value: NOT STATED
- field: language
  (read this field from: https://www.aubg.edu/admissions/bachelors/how-to-apply/)
  value: English
- field: tuition
  value: €6,900
- field: admission
  (read this field from: https://www.aubg.edu/admissions/bachelors/how-to-apply/)
  value: in URL

## AUBG — aubg-ba — BA in Business Administration
Page: https://www.aubg.edu/academics/bachelor-degrees/business-administration/

- field: degree
  value: Major
- field: duration
  value: NOT STATED
- field: language
  (read this field from: https://www.aubg.edu/admissions/bachelors/how-to-apply/)
  value: English
- field: tuition
  value: €6,900
- field: admission
  (read this field from: https://www.aubg.edu/admissions/bachelors/how-to-apply/)
  value: in URL

## AUBG — aubg-econ — BA in Economics
Page: https://www.aubg.edu/academics/bachelor-degrees/economics/

- field: degree
  value: Major
- field: duration
  value: NOT STATED
- field: language
  (read this field from: https://www.aubg.edu/admissions/bachelors/how-to-apply/)
  value: English
- field: tuition
  value: €6,900
- field: admission
  (read this field from: https://www.aubg.edu/admissions/bachelors/how-to-apply/)
  value: in URL

## AUBG — aubg-emba — Executive MBA
Page: https://www.aubg.edu/academics/executive-mba/

- field: degree
  value: NOT STATED
- field: duration
  value: NOT STATED
- field: language
  (read this field from: https://www.aubg.edu/admissions/bachelors/how-to-apply/)
  value: English
- field: tuition
  (read this field from: https://www.aubg.edu/admissions/emba/tuition-and-financing/)
  value: €19,100
- field: admission
  (read this field from: https://www.aubg.edu/admissions/emba/how-to-apply/)
  value: in URL
