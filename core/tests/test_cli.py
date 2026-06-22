"""Tests for core/cli.py contract behavior.

Transcription itself is monkeypatched so these run fast and offline (no model
download). load_audio is exercised for real against a generated WAV to verify
the silent resample/downmix contract.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import wave

import numpy as np

from core import cli
from core.audioio import write_wav


class _FakeSeg:
    def __init__(self, start, end, text, avg_logprob):
        self.start = start
        self.end = end
        self.text = text
        self.avg_logprob = avg_logprob


class LoadAudioTests(unittest.TestCase):
    def test_stereo_44k_becomes_mono_16k_float32(self):
        sr = 44100
        seconds = 0.5
        t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
        tone = (0.1 * np.sin(2 * np.pi * 220 * t)).astype("float32")
        # Write stereo WAV using stdlib wave (no soundfile).
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.wav"
            pcm = np.clip(tone * 32768.0, -32768, 32767).astype(np.int16)
            stereo_pcm = np.column_stack([pcm, pcm])
            with wave.open(str(p), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(stereo_pcm.tobytes())
            audio = cli.load_audio(p)
        self.assertEqual(audio.dtype, np.float32)
        self.assertEqual(audio.ndim, 1)
        # ~16000 * 0.5 = 8000 samples after resampling.
        self.assertTrue(7000 < len(audio) < 9000, len(audio))


class CliErrorTests(unittest.TestCase):
    def test_missing_file_exits_1_with_clean_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["definitely_missing_file.wav"])
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), "")  # never pollute stdout on error
        self.assertIn("file not found", err.getvalue())

    def test_directory_path_exits_1(self):
        with tempfile.TemporaryDirectory() as d:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main([d])
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("not a file", err.getvalue())

    def test_unreadable_audio_exits_1_with_clean_stdout(self):
        # A real file that isn't valid audio must exit 1 with the error on stderr
        # only — load_audio raises and the CLI must not print to stdout.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bogus.wav"
            p.write_text("this is not a wav", encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main([str(p)])
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("could not read audio", err.getvalue())


class CliOutputTests(unittest.TestCase):
    def setUp(self):
        self._orig_load = cli.load_audio
        self._orig_tx = cli.transcribe
        cli.load_audio = lambda path: np.zeros(16000, dtype=np.float32)
        cli.transcribe = lambda audio, model: [_FakeSeg(0.0, 1.0, " Um, moving to blr.", -0.3)]
        fd, self.path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)

    def tearDown(self):
        cli.load_audio = self._orig_load
        cli.transcribe = self._orig_tx
        os.unlink(self.path)

    def _run(self, args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main(args)
        return rc, out.getvalue()

    def test_normal_mode_is_postprocessed(self):
        rc, out = self._run([self.path])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "moving to Bengaluru.")

    def test_no_clean_skips_postprocessing(self):
        rc, out = self._run([self.path, "--no-clean"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "Um, moving to blr.")

    def test_raw_mode_emits_segment_json_untouched(self):
        rc, out = self._run([self.path, "--raw"])
        self.assertEqual(rc, 0)
        obj = json.loads(out.strip())
        self.assertEqual(set(obj), {"start", "end", "text", "avg_logprob"})
        self.assertEqual(obj["text"], " Um, moving to blr.")  # raw is not cleaned


if __name__ == "__main__":
    unittest.main()
