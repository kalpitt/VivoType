"""Tests for core/learn.py — headless correction logging."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from core import learn


class DiffTests(unittest.TestCase):
    def test_single_word_substitution(self):
        out = learn.diff_corrections("My name is Kalpith", "My name is Kalpit")
        self.assertEqual(out, [{"from": "Kalpith", "to": "Kalpit", "single_word": True}])

    def test_no_change_yields_nothing(self):
        self.assertEqual(learn.diff_corrections("hello world", "hello world"), [])

    def test_multiword_span(self):
        out = learn.diff_corrections("meet Zovind Sousa today", "meet Zubin Souza today")
        self.assertTrue(any("Souza" in c["to"] for c in out))

    def test_digit_swap_not_recorded(self):
        # '7' -> 'driven' (observed in the field): a numeral being replaced by
        # a word is an edit, not a mishearing — it must never enter the queue.
        self.assertEqual(learn.diff_corrections("the 7 team", "the driven team"), [])

    def test_single_char_swap_not_recorded(self):
        self.assertEqual(learn.diff_corrections("plan a now", "plan b now"), [])

    def test_real_word_pair_still_recorded(self):
        corrections = learn.diff_corrections("call wani today", "call Vaani today")
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]["from"], "wani")
        self.assertEqual(corrections[0]["to"], "Vaani")

    def test_accented_word_recorded_whole(self):
        # The tokenizer must keep non-ASCII letters; otherwise "José" tokenizes as
        # "Jos" and the learned correction records the wrong (truncated) target.
        out = learn.diff_corrections("I met Jose yesterday", "I met José yesterday")
        self.assertEqual(out, [{"from": "Jose", "to": "José", "single_word": True}])


class SymbolFilterTests(unittest.TestCase):
    """Spoken words are words: only a pair that turns a word into a bare
    symbol is dropped."""

    def _assert_kept(self, original, corrected):
        raw = learn.diff_corrections(original, corrected)
        self.assertNotEqual(raw, [], original)
        self.assertEqual(learn.filter_corrections(raw), raw, original)

    def test_punctuation_names_are_ordinary_words(self):
        self._assert_kept("add a comma here", "add a semicolon here")
        self._assert_kept("ask Colin now", "ask colon now")
        self._assert_kept("the peer id ends", "the period ends")
        self._assert_kept("see Colin soon", "see Colon soon")

    def test_lowercase_mark_is_learned(self):
        self._assert_kept("call mark tomorrow", "call Marc tomorrow")
        self._assert_kept("call Marc now", "call MARK now")

    def test_spoken_phrases_are_ordinary_words(self):
        self._assert_kept("end with full stop", "end with fullstop")
        self._assert_kept("is that right question mark", "is that right questionmark")

    def test_names_that_double_as_punctuation_survive(self):
        self._assert_kept("call Marc tomorrow", "call Mark tomorrow")
        self._assert_kept("meet Dott at five", "meet Dot at five")
        self._assert_kept("ask Dasha today", "ask Dash today")

    def test_real_word_pair_survives_filter(self):
        self._assert_kept("My name is Kalpith", "My name is Kalpit")

    def test_symbol_target_dropped(self):
        for to in [",", ";", ".", "&", "?!"]:
            raw = [{"from": "comma", "to": to, "single_word": True}]
            self.assertEqual(learn.filter_corrections(raw), [], to)

    def test_symbol_source_dropped(self):
        raw = [{"from": "&", "to": "and", "single_word": True}]
        self.assertEqual(learn.filter_corrections(raw), [])

    def test_is_punctuation_pair_only_sees_symbols(self):
        self.assertTrue(learn.is_punctuation_pair("comma", ","))
        self.assertFalse(learn.is_punctuation_pair("comma", "semicolon"))
        self.assertFalse(learn.is_punctuation_pair("full stop", "fullstop"))
        self.assertFalse(learn.is_punctuation_pair("mark", "Marc"))

    def test_filter_can_be_disabled(self):
        raw = [{"from": "comma", "to": ";", "single_word": True}]
        self.assertEqual(learn.filter_corrections(raw, drop_punctuation=False), raw)
        self.assertEqual(learn.filter_corrections(raw), [])


class TokenizationTests(unittest.TestCase):
    def test_devanagari_words_stay_whole(self):
        self.assertEqual(learn._tokens("मैंने बाज़ार से"), ["मैंने", "बाज़ार", "से"])
        self.assertEqual(learn.diff_corrections("मेरा नाम राहुल है", "मेरा नाम रोहित है"),
                         [{"from": "राहुल", "to": "रोहित", "single_word": True}])

    def test_danda_is_not_part_of_a_word(self):
        self.assertEqual(learn._tokens("ठीक है।"), ["ठीक", "है"])

    def test_other_indic_scripts_stay_whole(self):
        self.assertEqual(learn._tokens("আমি ভাত খাই"), ["আমি", "ভাত", "খাই"])  # Bengali
        self.assertEqual(learn._tokens("நான் வந்தேன்"), ["நான்", "வந்தேன்"])   # Tamil

    def test_indic_symbols_separate_words(self):
        self.assertEqual(learn._tokens("৳500 word॰word डॉ॰शर्मा"),
                         ["500", "word", "word", "डॉ", "शर्मा"])

    def test_quote_marks_around_a_word_are_not_part_of_it(self):
        self.assertEqual(learn.diff_corrections("tell Rahul", "tell ‘Rahul’"), [])
        self.assertEqual(learn.diff_corrections("'hello' there", "‘hello’ there"), [])

    def test_curly_apostrophe_is_one_word_and_not_a_correction(self):
        self.assertEqual(learn.diff_corrections("I don't know", "I don’t know"), [])
        self.assertEqual(learn.diff_corrections("its fine", "it’s fine"),
                         [{"from": "its", "to": "it’s", "single_word": True}])


class WordsInsidePairTests(unittest.TestCase):
    def test_punctuation_words_inside_a_pair_are_kept(self):
        for frm in ["Raul full stop", "comma Raul", "dot dot dot Raul"]:
            raw = [{"from": frm, "to": "Rahul", "single_word": False}]
            self.assertEqual(learn.filter_corrections(raw), raw, frm)

    def test_word_on_both_sides_is_kept(self):
        raw = learn.diff_corrections("a period of tym", "a period of time")
        self.assertEqual(learn.filter_corrections(raw), raw)

    def test_compounds_are_kept_whole(self):
        for original, corrected in [("check point here", "checkpoint here"),
                                    ("a stop watch", "a stopwatch"),
                                    ("the dash board", "the dashboard")]:
            raw = learn.diff_corrections(original, corrected)
            self.assertEqual(learn.filter_corrections(raw), raw, original)
            self.assertFalse(raw[0]["single_word"])


class MainTests(unittest.TestCase):
    def test_logs_and_prints_count(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "c.jsonl"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = learn.main([
                    "--original", "My name is Kalpith",
                    "--corrected", "My name is Kalpit",
                    "--log", str(log),
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(out.getvalue().strip(), "1")
            lines = log.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            rec = json.loads(lines[0])
            self.assertEqual(rec["from"], "Kalpith")
            self.assertEqual(rec["to"], "Kalpit")
            self.assertIn("at", rec)

    def test_unrelated_copy_is_not_logged(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "c.jsonl"
            out = io.StringIO()
            with redirect_stdout(out):
                learn.main([
                    "--original", "the quick brown fox jumps",
                    "--corrected", "completely unrelated text about cats and dogs",
                    "--log", str(log),
                ])
            self.assertEqual(out.getvalue().strip(), "0")
            self.assertFalse(log.exists())

    def test_punctuation_pair_not_logged(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "c.jsonl"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = learn.main([
                    "--original", "say comma",
                    "--corrected", "say ,",
                    "--log", str(log),
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(out.getvalue().strip(), "0")
            self.assertFalse(log.exists())

    def test_pairs_json_prints_pairs_and_logs_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "c.jsonl"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = learn.main(["--original", "My name is Kalpith",
                                 "--corrected", "My name is Kalpit",
                                 "--pairs-json", "--log", str(log)])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out.getvalue()), {"pairs": [
                {"from": "Kalpith", "to": "Kalpit", "single_word": True}]})
            self.assertFalse(log.exists())

    def test_pairs_json_empty_when_unrelated_or_filtered(self):
        for original, corrected in [("totally different words", "nothing alike here"),
                                    ("and then we go", "& then we go")]:
            with tempfile.TemporaryDirectory() as d:
                log = Path(d) / "c.jsonl"
                out = io.StringIO()
                with redirect_stdout(out):
                    learn.main(["--original", original, "--corrected", corrected,
                                "--pairs-json", "--log", str(log)])
                self.assertEqual(json.loads(out.getvalue()), {"pairs": []})
                self.assertFalse(log.exists())

    def test_pairs_json_and_log_mode_agree(self):
        spans = ("we met Shrivastav and Kalpith", "we met Shrivastava and Kalpit")
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "c.jsonl"
            out = io.StringIO()
            with redirect_stdout(out):
                learn.main(["--original", spans[0], "--corrected", spans[1],
                            "--pairs-json", "--log", str(log)])
                learn.main(["--original", spans[0], "--corrected", spans[1],
                            "--log", str(log)])
            shown = json.loads(out.getvalue().splitlines()[0])["pairs"]
            logged = [{k: v for k, v in json.loads(line).items() if k != "at"}
                      for line in log.read_text().splitlines()]
            self.assertEqual(shown, logged)
            self.assertEqual(len(shown), 2)

    def test_stdin_span_pair(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "c.jsonl"
            payload = json.dumps({
                "original": "My name is Kalpith",
                "corrected": "My name is Kalpit",
            })
            proc = subprocess.run(
                [sys.executable, "-m", "core.learn", "--log", str(log)],
                input=payload,
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parents[2]),
            )
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout.strip(), "1")
            rec = json.loads(log.read_text(encoding="utf-8").strip())
            self.assertEqual(rec["from"], "Kalpith")
            self.assertEqual(rec["to"], "Kalpit")


if __name__ == "__main__":
    unittest.main()
