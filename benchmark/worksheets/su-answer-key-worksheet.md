# Phase-0 labeling worksheet -- SofiaUniversity

Why this exists and why it has to be a human who did NOT build the extraction pipeline being graded: building the pipeline that produces run-report.json and building the "ground truth" it is graded against, in the same session, is the exact contamination ticket 07 exists to prevent -- every accuracy number is otherwise an upper bound. This file has to be filled in by reading the real pages, without looking at `crawler-out/SofiaUniversity/run-report.json` first -- otherwise you'll anchor on what the pipeline already said instead of judging the page independently.

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

**Sample:** 5 of SU's 20 programs, chosen to span the range rather than the
best case — 3 from the uni-sofia.bg faculty pages (Компютърни науки,
Информатика, Компютърна химия) and 2 from the separate phls.uni-sofia.bg
site (Психология, Културология), including the weakest extractor.

**SU source hints** (VUM/MUPleven/AUBG lessons — quote VALUES verbatim, never
paste a URL): tuition and admission for SU come from two university-wide PDFs,
not the program page — the fee order (ТАКСИ 2026-2027) and Приложение 2
(балообразуване). If a field is only stated in one of those, quote it from
there and say so. If the program page genuinely doesn't state a field and you
can't find it in the PDFs, write `NOT STATED`.

## SofiaUniversity — su-kn — Компютърни науки
Page: https://www.uni-sofia.bg/index.php/bul/universitet_t/fakulteti/fakultet_po_matematika_i_informatika2/specialnosti/bakalav_rski_programi/fakultet_po_matematika_i_informatika/4_6_informatika_i_kompyut_rni_nauki/kompyut_rni_nauki

- field: degree
  value: бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: Български
- field: tuition
  value: https://www.uni-sofia.bg/index.php/bul/priem/godishni_taksi_za_uchebnata_2026_2027_g
- field: admission
  value: https://www.uni-sofia.bg/index.php/bul/priem/priem_za_obrazovatelno_kvalifikacionna_stepen_bakalav_r_i_magist_r_sled_sredno_obrazovanie/minali_ksk/kandidatstudentska_kampaniya_2025/baloobrazuvane/fakultet_po_matematika_i_informatika/kompyut_rni_nauki

## SofiaUniversity — su-ch — Компютърна химия
Page: https://www.uni-sofia.bg/index.php/bul/universitet_t/fakulteti/fakultet_po_himiya_i_farmaciya/obuchenie/bakalav_rski_programi/fakultet_po_himiya_i_farmaciya/kompyut_rna_himiya

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: Български
- field: tuition
  value: https://www.uni-sofia.bg/index.php/bul/priem/godishni_taksi_za_uchebnata_2026_2027_g
- field: admission
  value: Приемът на студенти за специалността „Компютърна химия” се извършва чрез конкурсен изпит по химия, математика, физика или биология, както и с ДЗИ по химия, математика, физика и опазване на околната среда, информатика, информационни технологии, биология и здравно образование или български език. В балообразуващата оценка участва и оценката по химия и опазване на околната среда от дипломата за средно образование.

## SofiaUniversity — su-psy — Психология
Page: https://phls.uni-sofia.bg/obuchenie/bakalavarski-programi/psihologiya/

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: Български
- field: tuition
  value: https://www.uni-sofia.bg/index.php/bul/priem/godishni_taksi_za_uchebnata_2026_2027_g
- field: admission
  value: https://www.uni-sofia.bg/index.php/bul/priem/priem_za_obrazovatelno_kvalifikacionna_stepen_bakalav_r_i_magist_r_sled_sredno_obrazovanie/minali_ksk/kandidatstudentska_kampaniya_2025/baloobrazuvane/filosofski_fakultet/psihologiya

## SofiaUniversity — su-inf — Информатика
Page: https://www.uni-sofia.bg/index.php/bul/universitet_t/fakulteti/fakultet_po_matematika_i_informatika2/specialnosti/bakalav_rski_programi/fakultet_po_matematika_i_informatika/4_6_informatika_i_kompyut_rni_nauki/informatika

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: Български
- field: tuition
  value: https://www.uni-sofia.bg/index.php/bul/priem/godishni_taksi_za_uchebnata_2026_2027_g
- field: admission
  value: https://www.uni-sofia.bg/index.php/bul/priem/priem_za_obrazovatelno_kvalifikacionna_stepen_bakalav_r_i_magist_r_sled_sredno_obrazovanie/minali_ksk/kandidatstudentska_kampaniya_2025/baloobrazuvane/fakultet_po_matematika_i_informatika/informatika

## SofiaUniversity — su-cult — Културология
Page: https://phls.uni-sofia.bg/obuchenie/bakalavarski-programi/kulturologiya/

- field: degree
  value: Бакалавър
- field: duration
  value: 8 семестъра
- field: language
  value: Български
- field: tuition
  value: https://www.uni-sofia.bg/index.php/bul/priem/godishni_taksi_za_uchebnata_2026_2027_g
- field: admission
  value: https://www.uni-sofia.bg/index.php/bul/priem/priem_za_obrazovatelno_kvalifikacionna_stepen_bakalav_r_i_magist_r_sled_sredno_obrazovanie/minali_ksk/kandidatstudentska_kampaniya_2025/baloobrazuvane/filosofski_fakultet/kulturologiya
