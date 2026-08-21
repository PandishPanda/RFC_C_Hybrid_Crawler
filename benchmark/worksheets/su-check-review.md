# SU CHECK review (verdict: ok / wrong)

6 rows. Judge the SHIPPED value against the source named.

## su-kn · admission
- source: https://www.uni-sofia.bg/index.php/bul/priem/.../baloobrazuvane
- expected (key): SENTINEL-MANUAL-VERDICT балообразуване
- shipped: изпит по математика
- verdict: ok

## su-ch · duration
- source: https://www.uni-sofia.bg/index.php/bul/universitet_t/fakulteti/fakultet_po_himiya_i_farmaciya/obuchenie/bakalav_rski_programi/fakultet_po_himiya_i_farmaciya/kompyut_rna_himiya
- expected (key): 8 семестъра
- shipped: Продължителност на обучението (брой семестри): осем
- verdict: ok

## su-ch · admission
- source: https://www.uni-sofia.bg/index.php/bul/universitet_t/fakulteti/fakultet_po_himiya_i_farmaciya/obuchenie/bakalav_rski_programi/fakultet_po_himiya_i_farmaciya/kompyut_rna_himiya
- expected (key): Приемът на студенти за специалността „Компютърна химия” се извършва чрез конкурсен изпит по химия, математика, физика ил
- shipped: конкурсен изпит по химия, математика, физика или биология
- verdict: ok

## su-psy · admission
- source: https://www.uni-sofia.bg/index.php/bul/priem/.../baloobrazuvane
- expected (key): SENTINEL-MANUAL-VERDICT балообразуване
- shipped: изпит по английски език
- verdict: ok

## su-inf · admission
- source: https://www.uni-sofia.bg/index.php/bul/priem/.../baloobrazuvane
- expected (key): SENTINEL-MANUAL-VERDICT балообразуване
- shipped: изпит по математика
- verdict: ok

## su-cult · tuition
- source: https://www.uni-sofia.bg/index.php/bul/priem/godishni_taksi_za_uchebnata_2026_2027_g
- expected (key): 310 EUR
- shipped: освободени
- verdict: 310 EUR
