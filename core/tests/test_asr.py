"""Tests for core/asr.py segment helpers (no mlx import — those are lazy)."""

import unittest

from core.asr import _Segment, speech_segments


class SpeechSegmentsTests(unittest.TestCase):
    def test_drops_high_no_speech_segment(self):
        # On silence, Whisper echoes the initial_prompt back with a high
        # no_speech_prob — these must be dropped so nothing is inserted.
        segs = [_Segment(0.0, 1.0, " VivoType", -0.2, no_speech_prob=0.95)]
        self.assertEqual(speech_segments(segs), [])

    def test_keeps_real_speech(self):
        segs = [_Segment(0.0, 1.0, " hello there", -0.2, no_speech_prob=0.05)]
        self.assertEqual([s.text for s in speech_segments(segs)], [" hello there"])

    def test_mixed_keeps_only_speech(self):
        segs = [
            _Segment(0.0, 1.0, " hello", -0.2, no_speech_prob=0.1),
            _Segment(1.0, 2.0, " menu", -0.3, no_speech_prob=0.9),  # hallucinated echo
        ]
        self.assertEqual([s.text for s in speech_segments(segs)], [" hello"])

    def test_segment_without_no_speech_prob_is_kept(self):
        # Default 0.0 (e.g. older fixtures) must pass through untouched.
        self.assertEqual(len(speech_segments([_Segment(0.0, 1.0, " hi", -0.2)])), 1)


if __name__ == "__main__":
    unittest.main()
