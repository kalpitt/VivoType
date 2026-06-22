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


class LoadNamesTests(unittest.TestCase):
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
