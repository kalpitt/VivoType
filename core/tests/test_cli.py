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

    def test_empty_wav_prints_nothing_and_exits_0(self):
        # A-F4: a zero-frame recording is silence, not an error; exit 1 would
        # surface as a failed dictation in the app's CLI fallback.
        called = []
        orig = cli.transcribe
        cli.transcribe = lambda *a, **k: called.append(1) or []
        try:
            for raw in ([], ["--raw"]):
                with tempfile.TemporaryDirectory() as d:
                    p = Path(d) / "empty.wav"
                    with wave.open(str(p), "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(16000)
                    out, err = io.StringIO(), io.StringIO()
                    with redirect_stdout(out), redirect_stderr(err):
                        rc = cli.main([str(p)] + raw)
                self.assertEqual(rc, 0)
                self.assertEqual(out.getvalue().strip(), "")
                self.assertEqual(err.getvalue(), "")
        finally:
            cli.transcribe = orig
        self.assertEqual(called, [])


class CliOutputTests(unittest.TestCase):
    def setUp(self):
        self._orig_load = cli.load_audio
        self._orig_tx = cli.transcribe
        # Speech-level tone (not zeros): the silence gate must not trip here.
        t = np.arange(16000, dtype=np.float64) / 16000.0
        stub_audio = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        cli.load_audio = lambda path: stub_audio
        cli.transcribe = lambda audio, model, initial_prompt="": [_FakeSeg(0.0, 1.0, " Um, moving to blr.", -0.3)]
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
        # The stripped leading "Um, " restores the capital: "Moving", not "moving".
        self.assertEqual(out.strip(), "Moving to Bengaluru.")

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

    def test_silent_clip_prints_nothing_and_skips_model(self):
        # Silence must short-circuit before transcription: exit 0, no text.
        cli.load_audio = lambda path: np.zeros(16000, dtype=np.float32)
        called = []
        cli.transcribe = lambda audio, model, initial_prompt="": called.append(True) or []
        rc, out = self._run([self.path])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")
        self.assertEqual(called, [])

    def test_initial_prompt_echo_is_dropped(self):
        cli.transcribe = lambda audio, model, initial_prompt="": [
            _FakeSeg(0.0, 1.0, " VivoType, menu", -0.1)
        ]
        rc, out = self._run([self.path, "--initial-prompt", "VivoType, menu"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_filler_prefixed_prompt_echo_is_dropped(self):
        cli.transcribe = lambda audio, model, initial_prompt="": [
            _FakeSeg(0.0, 1.0, " um VivoType, menu", -0.1)
        ]
        rc, out = self._run([self.path, "--initial-prompt", "VivoType, menu"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_primer_only_echo_is_dropped(self):
        cli.transcribe = lambda audio, model, initial_prompt="": [
            _FakeSeg(0.0, 1.0, " Hello, welcome to my lecture.", -0.1)
        ]
        rc, out = self._run([self.path, "--initial-prompt", "VivoType, menu"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_composed_prompt_passed_to_transcribe(self):
        captured = []

        def fake_tx(audio, model, initial_prompt=""):
            captured.append(initial_prompt)
            return [_FakeSeg(0.0, 1.0, " hello", -0.1)]

        cli.transcribe = fake_tx
        rc, out = self._run([self.path, "--initial-prompt", "VivoType, menu"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured, ["Hello, welcome to my lecture. VivoType, menu"])

    def test_postprocess_failure_returns_raw(self):
        from unittest import mock
        cli.transcribe = lambda audio, model, initial_prompt="": [
            _FakeSeg(0.0, 1.0, " hello world", -0.1)
        ]
        err = io.StringIO()
        with mock.patch("core.cli.postprocess", side_effect=RuntimeError("bad regex")), \
             redirect_stderr(err):
            rc, out = self._run([self.path])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "hello world")
        self.assertIn("post-processing failed", err.getvalue())

    # --- --profile (per-app contexts; mirrors the daemon's profile field) ---

    def _write_profile_config(self):
        """A config with a 'code' context: currency + filler removal OFF."""
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({
                "fillers": ["um"],
                "replacements": {"blr": "Bengaluru"},
                "profiles": {
                    "code": {"convert_currency": False, "remove_fillers": False},
                },
            }, fh)
        self.addCleanup(os.unlink, path)
        return path

    def test_profile_flag_changes_postprocessing(self):
        cfg = self._write_profile_config()
        cli.transcribe = lambda audio, model, initial_prompt="": [
            _FakeSeg(0.0, 1.0, " Um, that is $10k.", -0.3)
        ]
        _, default_out = self._run([self.path, "--config", cfg])
        _, code_out = self._run([self.path, "--config", cfg, "--profile", "code"])
        # Same utterance: default converts currency and strips fillers; the
        # code context leaves both alone (dictionary still applies).
        self.assertEqual(default_out.strip(), "That is ₹10 lakh.")
        self.assertEqual(code_out.strip(), "Um, that is $10k.")

    def test_fallback_scratch_degrades_to_noop_not_literal(self):
        # Audit fix: the one-shot CLI cannot signal a structural delete, so a
        # spoken "scratch that" must NOT land in the document as words.
        cli.transcribe = lambda audio, model, initial_prompt="": [
            _FakeSeg(0.0, 1.0, " scratch that", -0.3)
        ]
        err = io.StringIO()
        with redirect_stderr(err):
            rc, out = self._run([self.path, "--voice-commands"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")
        self.assertIn("cannot delete", err.getvalue())

    def test_commands_off_by_default_types_words(self):
        cli.transcribe = lambda audio, model, initial_prompt="": [
            _FakeSeg(0.0, 1.0, " scratch that", -0.3)
        ]
        rc, out = self._run([self.path])
        self.assertEqual(rc, 0)
        self.assertIn("scratch that", out.lower())

    def test_fallback_transform_applied_like_daemon(self):
        cfg = self._write_profile_config()
        cli.transcribe = lambda audio, model, initial_prompt="": [
            _FakeSeg(0.0, 1.0, " Um, that is $10k.", -0.3)
        ]
        _, out = self._run([self.path, "--config", cfg, "--profile", "code"])
        # Same rules as the daemon's code context: currency + fillers kept.
        self.assertEqual(out.strip(), "Um, that is $10k.")

    def test_no_clean_skips_command_detection(self):
        cli.transcribe = lambda audio, model, initial_prompt="": [
            _FakeSeg(0.0, 1.0, " scratch that", -0.3)
        ]
        rc, out = self._run([self.path, "--no-clean"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "scratch that")  # diagnostic mode: literal

    def test_unknown_cli_profile_falls_back_to_default(self):
        cfg = self._write_profile_config()
        cli.transcribe = lambda audio, model, initial_prompt="": [
            _FakeSeg(0.0, 1.0, " Um, that is $10k.", -0.3)
        ]
        _, out = self._run([self.path, "--config", cfg, "--profile", "no-such"])
        self.assertEqual(out.strip(), "That is ₹10 lakh.")


if __name__ == "__main__":
    unittest.main()
