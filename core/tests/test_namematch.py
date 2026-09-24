"""Tests for core/namematch.py — fuzzy proper-noun correction.

Tests pass explicit names + english-word sets so they don't depend on the
git-ignored personal lexicon or the host's /usr/share/dict/words.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from core.namematch import correct_names, load_names

NAMES = ["Kalpit", "Srivastava", "Souza", "Govind", "Arjun", "Lakshmi", "Shivanshu",
         "Varun", "Tarun"]
ENGLISH = {"money", "more", "brown", "the", "hello", "office", "panther"}


def fix(text):
    return correct_names(text, names=NAMES, english=ENGLISH)


class NameMatchTests(unittest.TestCase):
    def test_near_miss_is_corrected(self):
        self.assertEqual(fix("Kalpith"), "Kalpit")

    def test_exact_known_name_unchanged(self):
        self.assertEqual(fix("Arjun"), "Arjun")

    def test_real_english_word_never_touched(self):
        # "Money" is a real word; must not snap toward a name.
        self.assertEqual(fix("Money"), "Money")
        self.assertEqual(fix("Brown"), "Brown")

    def test_lowercase_word_skipped(self):
        # Not capitalized -> not treated as a proper noun.
        self.assertEqual(fix("kalpith"), "kalpith")

    def test_allcaps_skipped(self):
        self.assertEqual(fix("UPIX"), "UPIX")

    def test_short_word_skipped(self):
        self.assertEqual(fix("Ram"), "Ram")

    def test_far_word_unchanged(self):
        self.assertEqual(fix("Kubernetes"), "Kubernetes")

    def test_punctuation_preserved_in_sentence(self):
        self.assertEqual(fix("Hello Kalpith, welcome."), "Hello Kalpit, welcome.")

    def test_empty_lexicon_is_noop(self):
        self.assertEqual(correct_names("Kalpith", names=[], english=ENGLISH), "Kalpith")

    def test_ambiguous_tie_left_unchanged(self):
        # "Karun" is edit-distance 1 from BOTH "Varun" and "Tarun". Per the
        # docstring rule 3 (near exactly one known name), an ambiguous near-miss
        # must be left as-is rather than snapped to an arbitrary contact.
        self.assertEqual(fix("Karun"), "Karun")

    def test_unambiguous_near_miss_still_corrected(self):
        # Guard the fix: a word near exactly one name must still be corrected.
        self.assertEqual(fix("Tarunn"), "Tarun")

    def test_accented_near_miss_corrected(self):
        # An accented proper noun must be treated as one token. With the old
        # ASCII-only regex "Chloé" tokenized as "Chlo" + leftover "é", producing
        # garbage like "Chloëé"; a Unicode-aware match snaps it cleanly.
        self.assertEqual(
            correct_names("Chloé", names=["Chloë"], english=set()), "Chloë"
        )


class EverydayWordGuardTests(unittest.TestCase):
    """Everyday words that /usr/share/dict/words lacks (informal speech,
    brands, Indian places, inflected forms) must never snap to a contact.
    Each pair below was a reproduced false snap against a synthetic lexicon."""

    LEXICON = ["Kay", "Guy", "Donna", "Anna", "June", "Gail", "Tessa",
               "Rakesh", "Tamer", "Stopper", "Varis", "Hopin",
               "Shrivastava", "Kalpit"]
    # A small synthetic English list standing in for the system dictionary:
    # base forms only, so inflected forms must be derived.
    WORDS = {"rake", "tame", "stop", "vary", "hope"}

    def fix(self, text):
        return correct_names(text, names=self.LEXICON, english=self.WORDS)

    def test_reproduced_false_snaps_stay_put(self):
        for word in ("Okay", "Guys", "Gonna", "Wanna", "Pune", "Gmail", "Tesla"):
            with self.subTest(word=word):
                self.assertEqual(self.fix(word), word)

    def test_in_sentence_false_snaps_stay_put(self):
        text = "Okay Guys, Gonna check Gmail in Pune. Wanna buy a Tesla?"
        self.assertEqual(self.fix(text), text)

    def test_inflected_english_words_are_english(self):
        # Only the base form is listed, so each of these used to be a 1-edit
        # snap ("Rakes" -> "Rakesh", "Stopped" -> "Stopper", ...).
        for word in ("Rakes", "Tamed", "Stopped", "Varies", "Hoping"):
            with self.subTest(word=word):
                self.assertEqual(self.fix(word), word)

    def test_first_letter_must_match(self):
        self.assertEqual(correct_names("Gonna", names=["Donna"], english=set()), "Gonna")
        self.assertEqual(correct_names("Kunaal", names=["Kunal"], english=set()), "Kunal")

    def test_whisper_first_letter_confusions_still_snap(self):
        # B/V, V/W and C/K are routine Whisper swaps on Indian names.
        self.assertEqual(correct_names("Barun", names=["Varun"], english=set()), "Varun")
        self.assertEqual(correct_names("Wijay", names=["Vijay"], english=set()), "Vijay")
        self.assertEqual(correct_names("Cunal", names=["Kunal"], english=set()), "Kunal")

    def test_four_letter_word_snaps_only_on_a_substitution(self):
        # Same length, same first letter, one letter swapped: a real near miss.
        self.assertEqual(correct_names("Ravy", names=["Ravi"], english=set()), "Ravi")
        self.assertEqual(correct_names("Ajai", names=["Ajay"], english=set()), "Ajay")
        # A 4-letter token never gains or loses a letter ("Okay" -> "Kay").
        self.assertEqual(correct_names("Okay", names=["Kay"], english=set()), "Okay")
        self.assertEqual(correct_names("Umaa", names=["Uma"], english=set()), "Umaa")

    def test_tie_across_first_letters_stays_ambiguous(self):
        # The first-letter rule must not break a tie in favour of one name.
        self.assertEqual(correct_names("Reena", names=["Reema", "Leena"], english=set()), "Reena")
        self.assertEqual(correct_names("Nasha", names=["Nisha", "Asha"], english=set()), "Nasha")

    def test_inflection_guard_applies_at_sentence_start_only(self):
        # Mid-sentence capitals are ASR's proper-noun signal: "-esh" names
        # missing their "h" still snap there.
        self.assertEqual(self.fix("I met Rakes today."), "I met Rakesh today.")
        self.assertEqual(self.fix("Rakes the leaves. Stopped."), "Rakes the leaves. Stopped.")
        self.assertEqual(self.fix("Done. Rakes the leaves."), "Done. Rakes the leaves.")

    def test_inflection_guard_after_leading_filler_comma_and_colon(self):
        # Filler removal can leave ", Rakes ..." before cleanup tidies it,
        # and Whisper capitalizes after a colon.
        self.assertEqual(self.fix(", Rakes the leaves."), ", Rakes the leaves.")
        self.assertEqual(self.fix("Note: Rakes the leaves."), "Note: Rakes the leaves.")

    def test_plain_es_stem_needs_a_sibilant(self):
        # "Hites" is not "hit" + "es"; "Boxes" is "box" + "es".
        self.assertEqual(correct_names("I met Hites.", names=["Hitesh"], english={"hit"}),
                         "I met Hitesh.")
        self.assertEqual(correct_names("Boxes", names=["Boxer"], english={"box"}), "Boxes")

    def test_real_near_miss_names_still_snap(self):
        self.assertEqual(self.fix("Shrivastav"), "Shrivastava")
        self.assertEqual(self.fix("Kalpith"), "Kalpit")
        self.assertEqual(self.fix("Hello Kalpith, meet Shrivastav."),
                         "Hello Kalpit, meet Shrivastava.")


class LoadNamesTests(unittest.TestCase):
    def test_non_string_names_are_skipped(self):
        # A hand-edited lexicon with a number or null in "names" must not crash
        # the dictation pipeline (len()/lower() on a non-string).
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "contacts.json"
            p.write_text('{"names": ["Rahul", 123, null, ["x"], "", "Arjun"]}',
                         encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(load_names(p), ["Rahul", "Arjun"])

    def test_names_not_a_list_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "contacts.json"
            p.write_text('{"names": "Rahul"}', encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(load_names(p), [])

    def test_correct_names_skips_non_string_names(self):
        self.assertEqual(
            correct_names("Hello Rahull", names=["Rahul", 123, None], english=set()),
            "Hello Rahul")

    def test_corrupt_lexicon_is_noop(self):
        # A malformed contacts.json must NOT raise — otherwise the JSONDecodeError
        # propagates through correct_names -> postprocess -> the CLI exits 1 and
        # ALL dictation breaks. A corrupt lexicon should behave like a missing one.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "contacts.json"
            p.write_text("{ this is not valid json", encoding="utf-8")
            self.assertEqual(load_names(p), [])

    def test_valid_lexicon_still_loads(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "contacts.json"
            p.write_text('{"names": ["Kalpit", "Arjun", "Kalpit"]}', encoding="utf-8")
            self.assertEqual(load_names(p), ["Kalpit", "Arjun"])  # deduped, ordered

    def test_corrupt_lexicon_warns_on_stderr(self):
        # Falling back silently leaves the user wondering why contacts stopped
        # working — a warning (to stderr, never stdout) must explain it.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "contacts.json"
            p.write_text("{ broken", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(load_names(p), [])
            msg = err.getvalue().lower()
            self.assertIn("lexicon", msg)
            self.assertIn(str(p), err.getvalue())

    def test_valid_lexicon_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "contacts.json"
            p.write_text('{"names": ["Kalpit"]}', encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                load_names(p)
            self.assertEqual(err.getvalue(), "")  # no noise on the happy path


if __name__ == "__main__":
    unittest.main()
