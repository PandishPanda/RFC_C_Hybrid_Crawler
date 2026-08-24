"""Tier G learns capitals and word-numbers (fill-rate ticket 02).

Measured blind spots that forced per-site anchors for statements the
shared library should own (VVVU carried 12 identical duration anchors
for one templated sentence; MUSofia three more):

- bg-okstepen required a lowercase-only quoted value, so
  „Бакалавър"/„Професионален бакалавър"/„ПРОФЕСИОНАЛЕН БАКАЛАВЪР" all
  missed, and an all-caps label heading missed too;
- durations required digits AND semesters, so „Срок на обучение: 4
  години", „– 3 години (6 семестъра)" and „срок на обучение три
  години" all missed.

The widening touches VALUE alternations and adds appended patterns
only (first hit wins, so existing catches keep their pattern and their
value); region rules and _is_caps_heading are deliberately untouched —
the 4-fabrication caps incident was about regions, not value patterns.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import cascade  # noqa: E402


def _src(text):
    return cascade.TextSource(ref="html:test", text=text)


class DegreeCapitalsTest(unittest.TestCase):
    def test_capitalized_value_matches(self):
        # VVVU's templated header (was: 3 per-site anchors)
        r = cascade.harvest_labels("degree", _src(
            "Образователно-квалификационна степен „Бакалавър” "
            "Професионална квалификация „Бакалавър-инженер”"))
        self.assertIsNotNone(r)
        self.assertEqual(r.value,
                         "Образователно-квалификационна степен „Бакалавър”")

    def test_capitalized_multiword_value_with_typographic_quotes(self):
        # MUSofia medlab: “ ” quotes and a capitalized first word
        r = cascade.harvest_labels("degree", _src(
            "с образователно-квалификационна степен “Професионален "
            "бакалавър”, със срок на обучение три години."))
        self.assertIsNotNone(r)
        self.assertEqual(
            r.value,
            "образователно-квалификационна степен “Професионален бакалавър”")

    def test_all_caps_label_and_value_match(self):
        # MUSofia inspektor's heading form
        r = cascade.harvest_labels("degree", _src(
            "ИЗИСКВАНИЯ ЗА ПРИДОБИВАНЕ НА ОБРАЗОВАТЕЛНО-КВАЛИФИКАЦИОННА "
            "СТЕПЕН „ПРОФЕСИОНАЛЕН БАКАЛАВЪР” ПО СПЕЦИАЛНОСТ"))
        self.assertIsNotNone(r)
        self.assertEqual(
            r.value,
            "ОБРАЗОВАТЕЛНО-КВАЛИФИКАЦИОННА СТЕПЕН „ПРОФЕСИОНАЛЕН "
            "БАКАЛАВЪР”")

    def test_lowercase_catch_is_unchanged(self):
        r = cascade.harvest_labels("degree", _src(
            "образователно-квалификационна степен „професионален "
            "бакалавър“ и професионална квалификация"))
        self.assertIsNotNone(r)
        self.assertEqual(
            r.value,
            "образователно-квалификационна степен „професионален бакалавър“")


class ContinuationGuardTest(unittest.TestCase):
    """The next-degree trap, generalized (refuter catches on SWU,
    2026-08-24): „завършилите могат да продължат образованието си за
    придобиване на ОКС/степен „магистър"" states where GRADUATES may go
    next, not this program's award. The guard was a lookbehind on
    bg-oks-inline only; once the widening made bg-okstepen reachable in
    such clauses, two bachelor programs shipped „магистър". The guard
    now lives in harvest_labels for the degree field: a match whose
    preceding window contains a продължат/да продължи continuation verb
    is skipped, and scanning continues to the next occurrence."""

    def test_continuation_clause_alone_ships_nothing(self):
        self.assertIsNone(cascade.harvest_labels("degree", _src(
            "Завършилите могат да продължат образованието си за "
            "придобиване на образователно-квалификационна степен "
            "„магистър“ в същото направление.")))

    def test_v_oks_variant_is_also_guarded_for_okstepen(self):
        # swu-130's shape: „да продължат образованието си в ОКС ..." —
        # bg-oks-inline's lookbehind already refuses it; bg-okstepen
        # (matching the longer label in the same clause) must too
        self.assertIsNone(cascade.harvest_labels("degree", _src(
            "Придобилите бакалавърска степен могат да продължат "
            "образованието си в образователно-квалификационната степен "
            "„магистър“ по същата специалност.")))

    def test_guard_skips_to_the_later_own_statement(self):
        r = cascade.harvest_labels("degree", _src(
            "могат да продължат образованието си за придобиване на "
            "образователно-квалификационна степен „магистър“. "
            "Специалността се изучава в образователно-квалификационна "
            "степен „бакалавър“ с четиригодишен курс."))
        self.assertIsNotNone(r)
        self.assertEqual(
            r.value, "образователно-квалификационна степен „бакалавър“")

    def test_pridobivane_heading_without_continuation_still_matches(self):
        # „ИЗИСКВАНИЯ ЗА ПРИДОБИВАНЕ НА ..." is a legit own-page heading
        # (MUSofia inspektor) — придобиване alone must not trigger
        r = cascade.harvest_labels("degree", _src(
            "ИЗИСКВАНИЯ ЗА ПРИДОБИВАНЕ НА ОБРАЗОВАТЕЛНО-КВАЛИФИКАЦИОННА "
            "СТЕПЕН „ПРОФЕСИОНАЛЕН БАКАЛАВЪР” ПО СПЕЦИАЛНОСТ"))
        self.assertIsNotNone(r)


class LtuDurationShapesTest(unittest.TestCase):
    def test_obuchenieto_prodalzhava_semestri(self):
        # ltu-veterinarna: „Обучението продължава 11 семестъра" — and
        # „продължава" (a duration verb) must NOT trip the degree
        # field's continuation guard vocabulary
        r = cascade.harvest_labels("duration", _src(
            "СПЕЦИАЛНОСТ „ВЕТЕРИНАРНА МЕДИЦИНА“ Обучението продължава "
            "11 семестъра и завършва с държавни изпити."))
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "11 семестъра")

    def test_per_form_years_continuation_ships_whole(self):
        # ltu-ekologia (refuter catch): „... за редовно обучение и 5
        # години (10 семестъра) за задочно обучение" — truncating at
        # the first parenthetical drops the qualifier AND the competing
        # задочна duration
        r = cascade.harvest_labels("duration", _src(
            "Обучението по специалност ЕООС-ОКС «Бакалавър» е с "
            "продължителност 4 години (8 семестъра) за редовно обучение "
            "и 5 години (10 семестъра) за задочно обучение."))
        self.assertIsNotNone(r)
        self.assertEqual(
            r.value,
            "с продължителност 4 години (8 семестъра) за редовно "
            "обучение и 5 години (10 семестъра) за задочно обучение")

    def test_bracketed_form_semesters_ship_whole(self):
        # ltu-gorsko: „Срок на обучение – 8 семестъра (редовна форма) и
        # 10 семестъра (задочна форма)" — both forms, one claim
        r = cascade.harvest_labels("duration", _src(
            "Форма на обучение – редовна и задочна. Срок на обучение – "
            "8 семестъра (редовна форма) и 10 семестъра (задочна "
            "форма). Професионална квалификация – инженер"))
        self.assertIsNotNone(r)
        self.assertEqual(
            r.value,
            "Срок на обучение – 8 семестъра (редовна форма) и 10 "
            "семестъра (задочна форма)")

    def test_za_form_master_phrasing_does_not_match_the_bracketed_pattern(self):
        # the same page's MASTER section phrases per-form durations as
        # „3 семестъра за редовно обучение, 4 семестъра за задочно
        # обучение" — deliberately NOT captured here: a pattern for that
        # shape could ship a master duration on a dual-section bachelor
        # page (the ltu-inzheneren defect class)
        self.assertIsNone(cascade.harvest_labels("duration", _src(
            "Срок на обучение – 3 семестъра за редовно обучение, 4 "
            "семестъра за задочно обучение. Професионална квалификация "
            "– магистър-инженер")))

    def test_digit_years_with_semesters_parenthetical(self):
        # ltu-alternativen/ekologia: „е с продължителност 4 години
        # (8 семестъра), в редовна форма"
        r = cascade.harvest_labels("duration", _src(
            "Обучението по специалност „Алтернативен туризъм“ (АТ) е с "
            "продължителност 4 години (8 семестъра), в редовна форма."))
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "с продължителност 4 години (8 семестъра)")


class ColonLabelledDegreeTest(unittest.TestCase):
    """The unquoted colon-labelled degree block (BFU's template, located
    by the attended LLM tail): „Образователно-квалификационна степен:
    Бакалавър" — bg-okstepen requires quotes, so this shipped via the
    tail until the shape joined the library."""

    def test_bachelor(self):
        r = cascade.harvest_labels("degree", _src(
            "Основно звено: Център по информатика "
            "Образователно-квалификационна степен: Бакалавър "
            "Професионална квалификация: Информатик"))
        self.assertIsNotNone(r)
        self.assertEqual(r.value,
                         "Образователно-квалификационна степен: Бакалавър")

    def test_master(self):
        r = cascade.harvest_labels("degree", _src(
            "Образователно-квалификационна степен: Магистър "
            "Професионална квалификация: Юрист"))
        self.assertEqual(r.value,
                         "Образователно-квалификационна степен: Магистър")

    def test_professional_bachelor(self):
        r = cascade.harvest_labels("degree", _src(
            "Образователно-квалификационна степен: професионален "
            "бакалавър и още текст"))
        self.assertEqual(
            r.value,
            "Образователно-квалификационна степен: професионален бакалавър")

    def test_open_ended_word_after_colon_does_not_match(self):
        # closed value list — a colon followed by prose must not ship
        self.assertIsNone(cascade.harvest_labels("degree", _src(
            "Образователно-квалификационна степен: виж таблицата")))


class ProgramNamedAdmissionTest(unittest.TestCase):
    def test_priemat_e_sentence_ships_whole(self):
        r = cascade.harvest_labels("admission", _src(
            "Друга възможна реализация е работа. Приемът в "
            "бакалавърската програма по специалност „Софтуерно "
            "инженерство” е с оценки от държавни зрелостни изпити "
            "(матури) или чрез приемни изпити. Учебен план и учебни "
            "програми"))
        self.assertIsNotNone(r)
        self.assertEqual(
            r.value,
            "Приемът в бакалавърската програма по специалност „Софтуерно "
            "инженерство” е с оценки от държавни зрелостни изпити "
            "(матури) или чрез приемни изпити.")

    def test_stava_verb_variant(self):
        r = cascade.harvest_labels("admission", _src(
            "Приемът в бакалавърската програма по специалност "
            "„Компютърни системи и технологии” става с оценки от "
            "държавни зрелостни изпити (матури) или чрез приемни "
            "изпити. Учебен план"))
        self.assertIsNotNone(r)
        self.assertTrue(r.value.endswith("приемни изпити."))

    def test_bare_po_spetsialnost_variant(self):
        # pravo's shape drops „...ската програма" entirely
        r = cascade.harvest_labels("admission", _src(
            "Прием Приемът по специалност „Право” става с ДЗИ (матура) "
            "по Български език и литература или с кандидатстудентски "
            "изпит по български език. Учебен план"))
        self.assertIsNotNone(r)
        self.assertTrue(r.value.startswith("Приемът по специалност"))
        self.assertTrue(r.value.endswith("български език."))

    def test_master_programme_variant(self):
        r = cascade.harvest_labels("admission", _src(
            "Приемът в магистърската програма по специалност „Право” е "
            "чрез конкурсен изпит. Още текст"))
        self.assertIsNotNone(r)
        self.assertTrue(r.value.startswith("Приемът в магистърската"))


class PerFormProdalzhitelnostTest(unittest.TestCase):
    """LTU's agronomy pages state BOTH forms in one sentence
    („с продължителност 8 семестъра в редовна форма и 10 семестъра за
    задочна форма") — capturing just the first number shipped a partial
    value (reviewer catch, 2026-08-24). The group extends over the
    continuation when present; single-number sentences (NBU's shape)
    keep their exact old capture."""

    def test_both_forms_extend_the_capture(self):
        r = cascade.harvest_labels("duration", _src(
            "Обучението е с продължителност 8 семестъра в редовна форма "
            "и 10 семестъра за задочна форма на обучение."))
        self.assertIsNotNone(r)
        self.assertEqual(
            r.value,
            "8 семестъра в редовна форма и 10 семестъра за задочна форма")

    def test_single_number_sentence_keeps_the_old_capture(self):
        r = cascade.harvest_labels("duration", _src(
            "Обучението е с продължителност 8 семестъра по учебен план."))
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "8 семестъра")


class StepenIFormaTest(unittest.TestCase):
    """UCTM's labelled fact block: „Степен и форма Бакалавър, редовно,
    задочно" — degree and forms in one unquoted labelled span. Closed
    value list; the forms tail rides along so the claim ships whole."""

    def test_bachelor_with_forms(self):
        r = cascade.harvest_labels("degree", _src(
            "фармацията и медицината . Степен и форма Бакалавър, "
            "редовно, задочно Професионална квалификация Инженер"))
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "Степен и форма Бакалавър, редовно, задочно")

    def test_master_single_form(self):
        r = cascade.harvest_labels("degree", _src(
            "Степен и форма Магистър, редовно Професионална"))
        self.assertEqual(r.value, "Степен и форма Магистър, редовно")

    def test_i_conjunction_form_list_ships_whole(self):
        # uctm-industrial-safety (refuter catch): „редовно и задочно
        # обучение" — a comma-only form tail truncated mid-list and
        # asserted half the claim
        r = cascade.harvest_labels("degree", _src(
            "Степен и форма Бакалавър, редовно и задочно обучение "
            "Професионална квалификация"))
        self.assertEqual(
            r.value, "Степен и форма Бакалавър, редовно и задочно обучение")

    def test_open_prose_after_label_does_not_match(self):
        self.assertIsNone(cascade.harvest_labels("degree", _src(
            "Степен и форма на обучение се определят от факултета")))


class PerFormSemestersTest(unittest.TestCase):
    """UCTM states duration PER FORM („Редовно обучение – 8 семестъра
    Задочно обучение – 9 семестъра"). One form alone is the partial
    value the labeller rules WRONG — the pattern captures BOTH
    consecutive statements or nothing."""

    def test_both_forms_ship_whole(self):
        r = cascade.harvest_labels("duration", _src(
            "Дипломиране Редовно обучение – 8 семестъра Задочно "
            "обучение – 9 семестъра Реализация Завършилите"))
        self.assertIsNotNone(r)
        self.assertEqual(
            r.value,
            "Редовно обучение – 8 семестъра Задочно обучение – 9 семестъра")

    def test_a_lone_form_does_not_match(self):
        self.assertIsNone(cascade.harvest_labels("duration", _src(
            "Дипломиране Редовно обучение – 8 семестъра Реализация")))


class DurationShapesTest(unittest.TestCase):
    def test_colon_years_no_semesters(self):
        r = cascade.harvest_labels("duration", _src(
            "Форма на обучение – редовна и задочна "
            "Срок на обучение: 4 години Насоченост на изграждането"))
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "Срок на обучение: 4 години")

    def test_dash_years_with_semester_parenthetical(self):
        r = cascade.harvest_labels("duration", _src(
            "Срок на обучение – 4 години (8 семестъра) 1. Насоченост"))
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "Срок на обучение – 4 години (8 семестъра)")

    def test_word_number_years(self):
        r = cascade.harvest_labels("duration", _src(
            "редовна със срок на обучение три години. Обучението"))
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "срок на обучение три години")

    def test_word_number_years_with_word_semesters(self):
        # CoTur's handbook shape
        r = cascade.harvest_labels("duration", _src(
            "съгласно чл. 42, срок на обучение – три години/ шест "
            "семестъра по следните специалности"))
        self.assertIsNotNone(r)
        self.assertEqual(r.value,
                         "срок на обучение – три години/ шест семестъра")

    def test_bare_year_count_without_the_label_does_not_match(self):
        self.assertIsNone(cascade.harvest_labels("duration", _src(
            "програмата съществува от 4 години и се радва на интерес")))

    def test_colon_word_number_duration(self):
        # BFU's labelled fact block (located by the attended LLM tail,
        # 2026-08-24): „Срок на обучение: четири години"
        r = cascade.harvest_labels("duration", _src(
            "Форма на обучение: редовно, задочно Срок на обучение: "
            "четири години Профил на специалността"))
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "Срок на обучение: четири години")

    def test_existing_semester_label_keeps_its_pattern(self):
        r = cascade.harvest_labels("duration", _src(
            "Срок на обучение: 8 семестъра (4 учебни години)"))
        self.assertIsNotNone(r)
        self.assertEqual(r.method, "label:bg-srok-label")


if __name__ == "__main__":
    unittest.main()
