"""Coverage for crawler/slugs.py — the slug core (url-scheme ticket 01).

The rules under test are the URL-scheme spec's (.scratch/url-scheme/
spec.md): official Streamlined-System transliteration with the
word-final -ия→ia exception applied PER CYRILLIC LETTER-RUN (the
prototype found „Английска филология: …" defeats a whitespace-token
check — the colon rides on the word), parentheticals stripped before
transliteration, and an explicit refusal to mint an empty slug.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.slugs import (  # noqa: E402
    RESERVED_ROOT_SLUGS,
    SLUG_RE,
    SlugError,
    slugify,
    transliterate,
)


class TransliterateTest(unittest.TestCase):
    """Streamlined System (2009 law), lowercased output."""

    def test_basic_letter_map(self):
        self.assertEqual(transliterate("Компютърни науки"),
                         "kompyutarni nauki")

    def test_multi_char_expansions(self):
        self.assertEqual(transliterate("общ жужащ юг"), "obsht zhuzhasht yug")

    def test_er_goljam_is_a(self):
        self.assertEqual(transliterate("български"), "balgarski")

    def test_word_final_ia_exception(self):
        self.assertEqual(transliterate("София"), "sofia")

    def test_ia_exception_survives_attached_punctuation(self):
        # The prototype's finding: the colon rides on the word, so the
        # rule must scan letter-runs, not whitespace tokens.
        self.assertEqual(transliterate("Английска филология: Лингвистика"),
                         "angliyska filologia: lingvistika")

    def test_ia_mid_word_is_not_the_exception(self):
        self.assertEqual(transliterate("пиявица"), "piyavitsa")

    def test_bare_ia_word_is_below_the_length_floor(self):
        # The exception needs a stem; a two-letter run keeps the map.
        self.assertEqual(transliterate("ия"), "iya")

    def test_latin_passes_through(self):
        self.assertEqual(transliterate("BA in Computer Science"),
                         "ba in computer science")

    def test_i_grave_normalizes_to_i(self):
        self.assertEqual(transliterate("ѝ"), "i")


class SlugifyTest(unittest.TestCase):
    def test_plain_bulgarian_name(self):
        self.assertEqual(slugify("Компютърни науки"), "kompyutarni-nauki")

    def test_parenthetical_stripped_before_transliteration(self):
        self.assertEqual(
            slugify("Английски език и професионална комуникация "
                    "(Съвместна програма с университета Йорк)"),
            "angliyski-ezik-i-profesionalna-komunikatsia")

    def test_punctuation_and_bulgarian_quotes_become_hyphens(self):
        self.assertEqual(slugify("Английска филология: „Лингвистика и превод“"),
                         "angliyska-filologia-lingvistika-i-prevod")

    def test_no_leading_trailing_or_doubled_hyphens(self):
        slug = slugify("  Право -- и ред  ")
        self.assertEqual(slug, "pravo-i-red")
        self.assertRegex(slug, SLUG_RE)

    def test_english_name_passes_through(self):
        self.assertEqual(slugify("BA in Computer Science"),
                         "ba-in-computer-science")

    def test_empty_after_stripping_raises(self):
        with self.assertRaises(SlugError):
            slugify("(за специалност от друга специалност)")

    def test_whitespace_only_raises(self):
        with self.assertRaises(SlugError):
            slugify("   ")


class ConstantsTest(unittest.TestCase):
    def test_reserved_root_slugs(self):
        self.assertEqual(RESERVED_ROOT_SLUGS,
                         frozenset({"specialnosti", "gradove",
                                    "ucheben-plan"}))

    def test_slug_re_accepts_canonical_slugs(self):
        for slug in ("pravo", "kompyutarni-nauki", "mu-varna", "fizika-2"):
            self.assertRegex(slug, SLUG_RE)

    def test_slug_re_rejects_malformed_slugs(self):
        for bad in ("", "-pravo", "pravo-", "pravo--i-red", "Pravo",
                    "право", "pravo_i_red"):
            self.assertNotRegex(bad, SLUG_RE)


if __name__ == "__main__":
    unittest.main()
