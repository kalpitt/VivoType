"""Tests for core/commands.py — literal phrase detection + text transforms."""

import unittest

from core.commands import (
    SCRATCH_SIGNAL,
    TRANSFORM_NONE,
    apply_transform,
    detect_command,
)


class CommandDetectionTests(unittest.TestCase):
    """Only standalone or trailing literal phrases are commands; anything
    else passes through untouched (a false positive DELETES user text)."""

    def test_standalone_scratch(self):
        for phrase in ("scratch that", "delete that"):
            with self.subTest(phrase=phrase):
                signal, remaining, transform = detect_command(phrase)
                self.assertEqual(signal, SCRATCH_SIGNAL)
                self.assertEqual(remaining, "")
                self.assertEqual(transform, TRANSFORM_NONE)

    def test_trailing_scratch_is_not_a_command(self):
        # Scratch is destructive, so it matches STANDALONE only — a sentence
        # that merely ENDS with the phrase must type literally.
        signal, remaining, transform = detect_command("hello world scratch that")
        self.assertIsNone(signal)
        self.assertEqual(remaining, "hello world scratch that")
        self.assertEqual(transform, TRANSFORM_NONE)

    def test_case_insensitive(self):
        for phrase in ("SCRATCH THAT", "Delete That"):
            with self.subTest(phrase=phrase):
                signal, _, _ = detect_command(phrase)
                self.assertEqual(signal, SCRATCH_SIGNAL)

    def test_standalone_punctuation_tolerated(self):
        signal, remaining, _ = detect_command("scratch that.")
        self.assertEqual(signal, SCRATCH_SIGNAL)
        self.assertEqual(remaining, "")

    def test_leading_phrase_is_not_a_command(self):
        # Deliberate: only standalone/trailing placement counts.
        signal, remaining, transform = detect_command("scratch that hello world")
        self.assertIsNone(signal)
        self.assertEqual(remaining, "scratch that hello world")
        self.assertEqual(transform, TRANSFORM_NONE)

    def test_near_phrases_do_not_match(self):
        for text in ("scratch that please", "please scratch that",
                     "scratched that", "scratch the"):
            with self.subTest(text=text):
                signal, remaining, transform = detect_command(text)
                self.assertIsNone(signal)
                self.assertEqual(remaining, text)
                self.assertEqual(transform, TRANSFORM_NONE)

    def test_non_command_passthrough_unchanged(self):
        raw = "  move the meeting to Monday  "
        signal, remaining, transform = detect_command(raw)
        self.assertIsNone(signal)
        self.assertEqual(remaining, raw)  # untouched — caller strips later
        self.assertEqual(transform, TRANSFORM_NONE)

    def test_empty_and_whitespace_passthrough(self):
        # Nothing command-like: caller receives the input unchanged.
        for raw in ("", "   "):
            with self.subTest(raw=raw):
                signal, remaining, transform = detect_command(raw)
                self.assertIsNone(signal)
                self.assertEqual(remaining, raw)
                self.assertEqual(transform, TRANSFORM_NONE)

    def test_second_phrase_stays_content(self):
        # Scratch is standalone-only, so a trailing scratch after another
        # phrase is just dictation; transforms still anchor at the end.
        signal, remaining, transform = detect_command("new paragraph scratch that")
        self.assertIsNone(signal)
        self.assertEqual(remaining, "new paragraph scratch that")
        self.assertEqual(transform, TRANSFORM_NONE)
        signal, remaining, transform = detect_command("hello there new line")
        self.assertEqual(remaining, "hello there")
        self.assertEqual(transform, "newline")

    def test_each_transform_phrase_detected(self):
        cases = {
            "hello there all caps that": ("upper", "hello there"),
            "hello there cap that": ("cap", "hello there"),
            "item one item two make that a bullet list": ("bullet", "item one item two"),
            "done new paragraph": ("newpara", "done"),
            "done new line": ("newline", "done"),
        }
        for text, (expected_transform, expected_remaining) in cases.items():
            with self.subTest(text=text):
                signal, remaining, transform = detect_command(text)
                self.assertIsNone(signal)
                self.assertEqual(remaining, expected_remaining)
                self.assertEqual(transform, expected_transform)

    def test_leading_fillers_stripped_for_scratch(self):
        # Whisper habitually prefixes fillers; a filler must not turn a
        # spoken command into typed text (audit finding).
        signal, remaining, transform = detect_command(
            "um scratch that", leading_fillers=["um"])
        self.assertEqual(signal, SCRATCH_SIGNAL)
        self.assertEqual(remaining, "")

        signal, remaining, _ = detect_command(
            "um, uhh delete that", leading_fillers=["um", "uhh"])
        self.assertEqual(signal, SCRATCH_SIGNAL)
        self.assertEqual(remaining, "")

    def test_leading_fillers_do_not_create_transform_commands(self):
        # Filler stripping is scratch-only; transform phrases still anchor at
        # the end of the ORIGINAL text and keep fillers in the remainder for
        # post-processing to own.
        signal, remaining, transform = detect_command(
            "um hello world all caps that", leading_fillers=["um"])
        self.assertIsNone(signal)
        self.assertEqual(remaining, "um hello world")
        self.assertEqual(transform, "upper")

    def test_without_fillers_utterance_types_literally(self):
        signal, remaining, _ = detect_command("um scratch that")
        self.assertIsNone(signal)
        self.assertEqual(remaining, "um scratch that")

    def test_unicode_content_with_trailing_command(self):
        signal, remaining, transform = detect_command("मैं बाज़ार गया new line")
        self.assertIsNone(signal)
        self.assertEqual(remaining, "मैं बाज़ार गया")
        self.assertEqual(transform, "newline")


class TransformTests(unittest.TestCase):
    """Exact outputs per transform token; transforms run AFTER postprocess on
    clean text, so whitespace they insert must survive."""

    def test_upper(self):
        self.assertEqual(apply_transform("hello World", "upper"), "HELLO WORLD")

    def test_cap_capitalizes_only_first_letter(self):
        self.assertEqual(apply_transform("iPhone notes here", "cap"),
                         "IPhone notes here")

    def test_cap_skips_leading_symbols_and_digits(self):
        # First alpha char gets the capital; symbols/digits before it survive.
        self.assertEqual(apply_transform("₹500 paid", "cap"), "₹500 Paid")

    def test_bullet_prefixes_each_line(self):
        self.assertEqual(
            apply_transform("one\ntwo three\nfour", "bullet"),
            "- one\n- two three\n- four")

    def test_bullet_leaves_blank_lines_unprefixed(self):
        self.assertEqual(
            apply_transform("one\n\ntwo", "bullet"),
            "- one\n\n- two")

    def test_bullet_single_line(self):
        self.assertEqual(apply_transform("groceries", "bullet"), "- groceries")

    def test_newpara_appends_double_newline(self):
        self.assertEqual(apply_transform("first part", "newpara"), "first part\n\n")

    def test_newline_appends_single_newline(self):
        self.assertEqual(apply_transform("first part", "newline"), "first part\n")

    def test_none_returns_text(self):
        self.assertEqual(apply_transform("as dictated", TRANSFORM_NONE), "as dictated")
        self.assertEqual(apply_transform("as dictated", "unknown-token"), "as dictated")

    def test_empty_remainder_never_crashes(self):
        for transform in ("upper", "cap", "bullet", "newpara", "newline"):
            with self.subTest(transform=transform):
                self.assertEqual(apply_transform("", transform), "")


if __name__ == "__main__":
    unittest.main()
