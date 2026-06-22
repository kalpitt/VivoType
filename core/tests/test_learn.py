"""Tests for core/learn.py — headless correction logging."""

import io
import json
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

    def test_accented_word_recorded_whole(self):
        # The tokenizer must keep non-ASCII letters; otherwise "José" tokenizes as
        # "Jos" and the learned correction records the wrong (truncated) target.
        out = learn.diff_corrections("I met Jose yesterday", "I met José yesterday")
        self.assertEqual(out, [{"from": "Jose", "to": "José", "single_word": True}])


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


if __name__ == "__main__":
    unittest.main()
