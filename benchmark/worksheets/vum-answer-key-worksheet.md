# Phase-0 labeling worksheet -- VUM

Why this exists and why it has to be a human who did NOT build the extraction pipeline being graded: building the pipeline that produces run-report.json and building the "ground truth" it is graded against, in the same session, is the exact contamination ticket 07 exists to prevent -- every accuracy number is otherwise an upper bound. This file has to be filled in by reading the real pages, without looking at `crawler-out/VUM/run-report.json` first -- otherwise you'll anchor on what the pipeline already said instead of judging the page independently.

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

## VUM — vum-sst — Software Systems and Technologies
Page: https://vum.bg/software-systems-and-technologies/

- field: degree
  value: Професионален бакалавър
- field: duration
  value: 3 години / 6 семестъра
- field: language
  value: Английски
- field: tuition
  value: 1500 € на семестър за граждани на ЕС и ЕИП / 1950 € на семестър за граждани на страни извън ЕС и ЕИП
- field: admission
  (read this field from: https://vum.bg/application-requirement-for-bachelor-degree-programmes/)
  value: whole process is inside this link

## VUM — vum-gca — Gastronomy and Culinary Arts
Page: https://vum.bg/gastronomy-and-culinary-arts/

- field: degree
  value: Професионален бакалавър
- field: duration
  value: 3 години / 6 семестъра
- field: language
  value: Английски
- field: tuition
  value: 1500 € на семестър за граждани на ЕС и ЕИП / 2400 € на семестър за граждани на страни извън ЕС и ЕИП - https://vum.bg/bg/stipendii-taksi/
- field: admission
  (read this field from: https://vum.bg/application-requirement-for-bachelor-degree-programmes/)
  value: whole process is inside this link

## VUM — vum-mba — Master of Business Administration
Page: https://vum.bg/master-of-business-administration/

- field: degree
  value: Магистър по „Бизнес администрация“
- field: duration
  value: 1,5 години / 3 семестъра	1,5 години / 3 семестъра / 4 семестъра	2 години / 4 семестъра	2 години / 4 семестъра
- field: language
  value: Английски
- field: tuition
  value: 1500 € на семестър за граждани на ЕС и ЕИП / 2400 € на семестър за граждани на страни извън ЕС и ЕИП - https://vum.bg/bg/stipendii-taksi/
- field: admission
  value: https://vum.bg/bg/iziskwaniq-za-magistar/

## VUM — vum-corr — Fighting Corruption and Conflict of Interest
Page: https://vum.bg/coruption/

- field: degree
  value: Магистър
- field: duration
  value: 3 – 4 семестъра (в зависимост от професионалното направление и придобитата образователно-квалификационна степен на висше образование)
- field: language
  value: Английски
- field: tuition
  value: 1500 € на семестър за граждани на ЕС и ЕИП / 1950 € на семестър за граждани на страни извън ЕС и ЕИП - https://vum.bg/bg/stipendii-taksi/
- field: admission
  value: https://vum.bg/bg/iziskwaniq-za-magistar/

## VUM — vum-ibm-pb — Международен бизнес и мениджмънт
## VUM — vum-ibm-b — Международен бизнес и мениджмънт
Page (shared by all 2 above): https://vum.bg/bg/mejdunaroden-biznes-menidvjmant/

This page describes multiple programs -- check whether it actually distinguishes what applies to each specific program vs. what it says generically for the whole page/faculty. If a value is stated only generically, not tied to one specific program, say so -- that's a real distinction the grader needs (a program-specific claim vs. a page-wide one aren't the same kind of evidence).

### vum-ibm-pb
- field: degree
  value: Професионален бакалавър
- field: duration
  value: Професионален бакалавър	3 години / 6 семестъра	3.5 години / 6 семестъра
- field: language
  value: Английски
- field: tuition
  value: https://vum.bg/bg/stipendii-taksi/
- field: admission
  value: https://vum.bg/bg/usloviq-za-bakalavri/

### vum-ibm-b
- field: degree
  value: бакалавър
- field: duration
  value: Бакалавър	4 години / 8 семестъра	4,5 години / 8 семестъра
- field: language
  value: Английски
- field: tuition
  value: https://vum.bg/bg/stipendii-taksi/
- field: admission
  value: https://vum.bg/bg/usloviq-za-bakalavri/

## VUM — vum-hm-pb — Хотелски мениджмънт
## VUM — vum-hm-b — Хотелски мениджмънт
Page (shared by all 2 above): https://vum.bg/bg/hotelski-menidjmant

This page describes multiple programs -- check whether it actually distinguishes what applies to each specific program vs. what it says generically for the whole page/faculty. If a value is stated only generically, not tied to one specific program, say so -- that's a real distinction the grader needs (a program-specific claim vs. a page-wide one aren't the same kind of evidence).

### vum-hm-pb
- field: degree
  value: Професионален бакалавър
- field: duration
  value: Професионален бакалавър	3 години / 6 семестъра	3.5 години / 6 семестъра
- field: language
  value: Английски
- field: tuition
  value: https://vum.bg/bg/stipendii-taksi/
- field: admission
  value:https://vum.bg/bg/usloviq-za-bakalavri/

### vum-hm-b
- field: degree
  value: Бакалавър
- field: duration
  value: Бакалавър	4 години / 8 семестъра	4,5 години / 8 семестъра
- field: language
  value: Английски
- field: tuition
  value: https://vum.bg/bg/stipendii-taksi/
- field: admission
  value: https://vum.bg/bg/usloviq-za-bakalavri/

## VUM — vum-ped-econ — Педагогика на обучението по Икономика и мениджмънт
Page: https://vum.bg/bg/pedagogika-obuchenieto-ikonomika-menidjmant/

- field: degree
  value: ПРОФЕСИОНАЛЕН БАКАЛАВЪР
- field: duration
  value: 3 ГОДИНИ (6 СЕМЕСТЪРА)
- field: language
  value: Български
- field: tuition
  value: 750 евро
- field: admission
  value: https://vum.bg/bg/usloviq-za-bakalavri/

## VUM — vum-ped-hosp — Педагогика на обучението по хотелиерство и ресторантьорство
Page: https://vum.bg/bg/pedagogika-obuchenieto-hotelierstvo-restorantiorstvo/

- field: degree
  value: ПРОФЕСИОНАЛЕН БАКАЛАВЪР
- field: duration
  value: 3 ГОДИНИ (6 СЕМЕСТЪРА)
- field: language
  value: Български
- field: tuition
  value: 750 евро
- field: admission
  value: https://vum.bg/bg/usloviq-za-bakalavri/

## VUM — vum-hce — Хотелиерство и кулинарни изкуства
Page: https://vum.bg/bg/hotelierstvo-kulinarni-izkustva/

- field: degree
  value: Бакалавър
- field: duration
  value: 4 години / 8 семестъра	4,5 години / 8 семестъра
- field: language
  value: Английски
- field: tuition
  value: https://vum.bg/bg/stipendii-taksi/
- field: admission
  value: https://vum.bg/bg/usloviq-za-bakalavri/

## VUM — vum-int-tour — Международен туризъм
Page: https://vum.bg/bg/magistratura-mejdunaroden-turizam/

- field: degree
  value: Магистър по „Международен туризъм“
- field: duration
  value: 3 семестъра	1,5 години / 3 семестъра	1,5 години / 3 семестъра / 4 семестъра	2 години / 4 семестъра	2 години / 4 семестъра
- field: language
  value: Английски
- field: tuition
  value: https://vum.bg/bg/stipendii-taksi/
- field: admission
  value: https://vum.bg/bg/iziskwaniq-za-magistar/

## VUM — vum-tour-phd — Туризъм
Page: https://vum.bg/bg/doktorska-programa-turizam/

- field: degree
  value: Български и английски
- field: duration
  value: Редовна и самостоятелна форма на обучение – 3 години / Задочна форма на обучение – 4 години
- field: language
  value: Английски
- field: tuition
  value: Годишна такса за граждани на страни от ЕС и ЕИП – 2400 € / Годишна такса за граждани на страни извън ЕС и ЕИП4800 € / Такса за защита на дисертационен труд – 1500 €
- field: admission
  value: Подаване на Заявление до ректора на ВУМ-Варна за желаната форма на обучение

## VUM — vum-food — Хранителни технологии в кулинарните изкуства
Page: https://vum.bg/bg/hranitelni-tehnologii-kulinarni-izkustva/

- field: degree
  value: ПРОФЕСИОНАЛЕН БАКАЛАВЪР
- field: duration
  value: 3 ГОДИНИ (6 СЕМЕСТЪРА)
- field: language
  value: Български
- field: tuition
  value: https://vum.bg/bg/stipendii-taksi/
- field: admission
  value: https://vum.bg/bg/usloviq-za-bakalavri/

## VUM — vum-mkt — Маркетинг и мениджмънт
Page: https://vum.bg/bg/marketing-menidjmant/

- field: degree
  value: БАКАЛАВЪР
- field: duration
  value: 3 години (6 семестъра)
- field: language
  value: Български
- field: tuition
  value: https://vum.bg/bg/stipendii-taksi/
- field: admission
  value: https://vum.bg/bg/usloviq-za-bakalavri/
