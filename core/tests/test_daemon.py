"""Tests for core/daemon.py — no model download required (mock patching)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

import core.daemon as daemon

_OMITTED = object()  # sentinel: build the request WITHOUT a "profile" field


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeSeg:
    def __init__(self, text, start=0.0, end=1.0, avg_logprob=-0.3, no_speech_prob=0.0):
        self.text = text
        self.start = start
        self.end = end
        self.avg_logprob = avg_logprob
        self.no_speech_prob = no_speech_prob


def _make_wav(path: Path, samplerate: int = 16000, duration: float = 0.1,
              amplitude: float = 0.1) -> None:
    """Write a minimal WAV carrying a speech-level tone (so the silence gate
    passes); use amplitude=0.0 to write true silence."""
    n = int(samplerate * duration)
    t = np.arange(n, dtype=np.float64) / samplerate
    tone = amplitude * np.sin(2 * np.pi * 220 * t)
    pcm = np.clip(tone * 32768.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(pcm.tobytes())


def _run_daemon(commands: list, model_mock=None) -> list[dict]:
    """Run main() against a list of commands; return parsed NDJSON output lines.

    Items in `commands` may be dicts (serialised as JSON) or raw strings
    (passed through verbatim, so you can inject malformed lines).
    """
    if model_mock is None:
        model_mock = mock.Mock()
        model_mock.transcribe.return_value = (
            [_FakeSeg(" hello world")], None
        )

    lines = []
    for c in commands:
        lines.append(c if isinstance(c, str) else json.dumps(c))
    stdin_text = "\n".join(lines) + "\n"

    out_buf = io.StringIO()
    with mock.patch("core.daemon._load_model", return_value=model_mock), \
         mock.patch("core.daemon._is_model_cached", return_value=True), \
         mock.patch("sys.stdin", io.StringIO(stdin_text)), \
         mock.patch("sys.stdout", out_buf):
        daemon.main()

    lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


# ---------------------------------------------------------------------------
# Boot sequence
# ---------------------------------------------------------------------------

class BootTests(unittest.TestCase):
    def test_ready_emitted_after_loading(self):
        msgs = _run_daemon([{"cmd": "shutdown"}])
        statuses = [m["status"] for m in msgs if "status" in m]
        self.assertIn("loading", statuses)
        self.assertIn("ready", statuses)
        self.assertLess(statuses.index("loading"), statuses.index("ready"))

    def test_ready_includes_model_name(self):
        msgs = _run_daemon([{"cmd": "shutdown"}])
        ready = next(m for m in msgs if m.get("status") == "ready")
        self.assertIn("model", ready)

    def test_downloading_emitted_when_model_not_cached(self):
        out_buf = io.StringIO()
        with mock.patch("core.daemon._load_model",
                        return_value=mock.Mock(transcribe=lambda *a, **k: ([], None))), \
             mock.patch("core.daemon._is_model_cached", return_value=False), \
             mock.patch("sys.stdin", io.StringIO('{"cmd":"shutdown"}\n')), \
             mock.patch("sys.stdout", out_buf):
            daemon.main()
        statuses = [json.loads(l).get("status") for l in out_buf.getvalue().splitlines() if l.strip()]
        self.assertIn("downloading", statuses)

    def test_error_status_on_model_load_failure(self):
        out_buf = io.StringIO()
        with mock.patch("core.daemon._load_model", side_effect=RuntimeError("boom")), \
             mock.patch("core.daemon._is_model_cached", return_value=True), \
             mock.patch("sys.stdin", io.StringIO("")), \
             mock.patch("sys.stdout", out_buf), \
             self.assertRaises(SystemExit):
            daemon.main()
        statuses = [json.loads(l).get("status") for l in out_buf.getvalue().splitlines() if l.strip()]
        self.assertIn("error", statuses)


# ---------------------------------------------------------------------------
# Pre-protocol startup failures (F8)
# ---------------------------------------------------------------------------

class PreProtocolCrashTests(unittest.TestCase):
    """Unexpected raises from load_settings / load_config (not corrupt JSON —
    those loaders swallow decode errors and return defaults) must still surface
    as an NDJSON error, not a bare traceback the Swift client can't see."""

    def test_settings_load_raise_emits_error_and_exits(self):
        out_buf = io.StringIO()
        with mock.patch("core.daemon.load_settings", side_effect=ValueError("bad json")), \
             mock.patch("sys.stdin", io.StringIO("")), \
             mock.patch("sys.stdout", out_buf), \
             self.assertRaises(SystemExit) as ctx:
            daemon.main()
        self.assertEqual(ctx.exception.code, 1)

        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["status"], "error")
        self.assertIn("bad json", msgs[0]["error"])

    def test_postprocess_config_load_raise_falls_back_and_serves(self):
        # A-F3: a raising post-processing config (e.g. a wrong-typed user
        # dictionary) must not stop dictation at boot — the daemon falls back
        # to the built-in rules, warns on stderr, and still reports ready.
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            model_mock = mock.Mock()
            model_mock.transcribe.return_value = ([_FakeSeg(" blr")], None)
            with mock.patch("core.daemon.load_config",
                            side_effect=TypeError("'int' object is not iterable")), \
                 mock.patch("core.daemon._load_model", return_value=model_mock), \
                 mock.patch("core.daemon._is_model_cached", return_value=True), \
                 mock.patch("sys.stdin", io.StringIO(
                     json.dumps({"id": 1, "wav": str(wav)}) + "\n")), \
                 mock.patch("sys.stdout", io.StringIO()) as out_buf, \
                 contextlib.redirect_stderr(err):
                daemon.main()

        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        self.assertIn("ready", [m.get("status") for m in msgs])
        self.assertNotIn("error", [m.get("status") for m in msgs])
        reply = next(m for m in msgs if m.get("id") == 1)
        self.assertEqual(reply["text"], "Bengaluru")  # built-in default rule
        self.assertIn("int", err.getvalue())

    def test_wrong_typed_user_dictionary_at_boot_uses_shipped_rules(self):
        # A-F3 end-to-end through the REAL loader: {"fillers": 5} in the user
        # overlay made the daemon exit at every (re)spawn. The shipped base
        # config must still apply.
        model_mock = mock.Mock()
        model_mock.transcribe.return_value = ([_FakeSeg(" um shipped word")], None)
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "pp.json"
            base.write_text(json.dumps(
                {"fillers": ["um"], "replacements": {"shipped": "Shipped!"}}),
                encoding="utf-8")
            overlay = Path(d) / "user_dictionary.json"
            overlay.write_text(json.dumps({"fillers": 5}), encoding="utf-8")
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            with mock.patch("core.postprocess.DEFAULT_CONFIG_PATH", base), \
                 mock.patch("core.postprocess.USER_DICT_PATH", overlay), \
                 mock.patch("core.daemon._load_model", return_value=model_mock), \
                 mock.patch("core.daemon._is_model_cached", return_value=True), \
                 mock.patch("sys.stdin", io.StringIO(
                     json.dumps({"id": 1, "wav": str(wav)}) + "\n")), \
                 mock.patch("sys.stdout", io.StringIO()) as out_buf, \
                 contextlib.redirect_stderr(io.StringIO()):
                daemon.main()
        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        reply = next(m for m in msgs if m.get("id") == 1)
        self.assertEqual(reply["text"], "Shipped! word")

    def test_no_model_load_attempted_after_startup_crash(self):
        # A startup crash must exit before ever touching the (possibly slow /
        # network-bound) model loader.
        with mock.patch("core.daemon.load_settings", side_effect=ValueError("boom")), \
             mock.patch("core.daemon._load_model") as load_mock, \
             mock.patch("sys.stdin", io.StringIO("")), \
             mock.patch("sys.stdout", io.StringIO()), \
             self.assertRaises(SystemExit):
            daemon.main()
        load_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Transcription requests
# ---------------------------------------------------------------------------

class TranscriptionTests(unittest.TestCase):
    def test_response_contains_request_id(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            msgs = _run_daemon([
                {"id": 42, "wav": str(wav), "initial_prompt": "", "raw": False},
                {"cmd": "shutdown"},
            ])
        tx = next(m for m in msgs if "id" in m)
        self.assertEqual(tx["id"], 42)

    def test_normal_mode_returns_text(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            msgs = _run_daemon([
                {"id": 1, "wav": str(wav), "initial_prompt": "", "raw": False},
                {"cmd": "shutdown"},
            ])
        tx = next(m for m in msgs if "id" in m)
        self.assertIn("text", tx)
        self.assertNotIn("error", tx)

    def test_missing_wav_returns_error(self):
        msgs = _run_daemon([
            {"id": 7, "wav": "/no/such/file.wav", "initial_prompt": "", "raw": False},
            {"cmd": "shutdown"},
        ])
        tx = next(m for m in msgs if "id" in m)
        self.assertEqual(tx["id"], 7)
        self.assertIn("error", tx)

    def test_initial_prompt_passed_to_model(self):
        model_mock = mock.Mock()
        model_mock.transcribe.return_value = ([_FakeSeg(" Kalpit")], None)
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            _run_daemon([
                {"id": 1, "wav": str(wav), "initial_prompt": "Kalpit,Bengaluru", "raw": False},
                {"cmd": "shutdown"},
            ], model_mock=model_mock)
        _, kwargs = model_mock.transcribe.call_args
        self.assertEqual(kwargs.get("initial_prompt"), "Hello, welcome to my lecture. Kalpit,Bengaluru")

    def test_empty_initial_prompt_passes_primer(self):
        model_mock = mock.Mock()
        model_mock.transcribe.return_value = ([_FakeSeg(" hi")], None)
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            _run_daemon([
                {"id": 1, "wav": str(wav), "initial_prompt": "", "raw": False},
                {"cmd": "shutdown"},
            ], model_mock=model_mock)
        _, kwargs = model_mock.transcribe.call_args
        self.assertEqual(kwargs.get("initial_prompt"), "Hello, welcome to my lecture.")

    def test_raw_mode_returns_json_segment_lines(self):
        model_mock = mock.Mock()
        model_mock.transcribe.return_value = ([_FakeSeg(" hello", start=0.1, end=1.2)], None)
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            msgs = _run_daemon([
                {"id": 5, "wav": str(wav), "initial_prompt": "", "raw": True},
                {"cmd": "shutdown"},
            ], model_mock=model_mock)
        tx = next(m for m in msgs if "id" in m)
        seg = json.loads(tx["text"])
        self.assertIn("start", seg)
        self.assertIn("end", seg)
        self.assertIn("text", seg)
        self.assertIn("avg_logprob", seg)


# ---------------------------------------------------------------------------
# Control commands
# ---------------------------------------------------------------------------

class ControlTests(unittest.TestCase):
    def test_shutdown_causes_clean_exit(self):
        # If main() returns without exception, we're done.
        _run_daemon([{"cmd": "shutdown"}])

    def test_eof_causes_clean_exit(self):
        # Empty stdin → EOF → main() returns cleanly.
        out_buf = io.StringIO()
        with mock.patch("core.daemon._load_model",
                        return_value=mock.Mock(transcribe=lambda *a, **k: ([], None))), \
             mock.patch("core.daemon._is_model_cached", return_value=True), \
             mock.patch("sys.stdin", io.StringIO("")), \
             mock.patch("sys.stdout", out_buf):
            daemon.main()  # must not raise

    def test_invalid_json_is_ignored(self):
        # Truly malformed JSON and non-object JSON values are both skipped;
        # the next valid command still processes normally.
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            msgs = _run_daemon([
                "{broken json",          # malformed → JSONDecodeError
                '"just a string"',       # valid JSON but not a dict
                {"id": 9, "wav": str(wav), "initial_prompt": "", "raw": False},
                {"cmd": "shutdown"},
            ])
        ids = [m["id"] for m in msgs if "id" in m]
        self.assertIn(9, ids)

    def test_reload_triggers_new_loading_and_ready(self):
        calls = []

        def _fake_load(name):
            calls.append(name)
            m = mock.Mock()
            m.transcribe.return_value = ([], None)
            return m

        out_buf = io.StringIO()
        stdin = io.StringIO(
            json.dumps({"cmd": "reload", "model": "tiny.en"}) + "\n" +
            json.dumps({"cmd": "shutdown"}) + "\n"
        )
        with mock.patch("core.daemon._load_model", side_effect=_fake_load), \
             mock.patch("core.daemon._is_model_cached", return_value=True), \
             mock.patch("sys.stdin", stdin), \
             mock.patch("sys.stdout", out_buf):
            daemon.main()

        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        statuses = [m.get("status") for m in msgs]
        # Second loading/ready pair from the reload.
        self.assertEqual(statuses.count("loading"), 2)
        self.assertEqual(statuses.count("ready"), 2)
        self.assertIn("tiny.en", calls)

    def test_failed_reload_keeps_serving_with_old_model(self):
        """A reload that fails to load the new model must NOT brick the daemon:
        the previously-working model stays loaded and transcription still works."""
        good_model = mock.Mock()
        good_model.transcribe.return_value = ([_FakeSeg(" still works")], None)

        def _fake_load(name):
            if name == "small.en":
                return good_model
            raise RuntimeError("model not found: " + name)

        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            out_buf = io.StringIO()
            stdin = io.StringIO(
                json.dumps({"cmd": "reload", "model": "does-not-exist"}) + "\n" +
                json.dumps({"id": 1, "wav": str(wav), "initial_prompt": "", "raw": False}) + "\n" +
                json.dumps({"cmd": "shutdown"}) + "\n"
            )
            with mock.patch("core.daemon._load_model", side_effect=_fake_load), \
                 mock.patch("core.daemon._is_model_cached", return_value=True), \
                 mock.patch("sys.stdin", stdin), \
                 mock.patch("sys.stdout", out_buf):
                daemon.main()

        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        statuses = [m.get("status") for m in msgs]
        # The failed reload is reported as an error...
        self.assertIn("error", statuses)
        # ...but the transcription request after it still succeeds on the old model.
        tx = next(m for m in msgs if "id" in m)
        self.assertEqual(tx["id"], 1)
        self.assertIn("text", tx)
        self.assertNotIn("error", tx)

    def test_failed_reload_reports_error_then_ready_on_old_model(self):
        """A failed reload must emit `error` AND a follow-up `ready` for the OLD
        model, so the Swift client's `isReady` flips back true and keeps using the
        hot daemon instead of permanently falling back to the cold-start CLI."""
        def _fake_load(name):
            if name == "small.en":
                m = mock.Mock()
                m.transcribe.return_value = ([], None)
                return m
            raise RuntimeError("boom")

        out_buf = io.StringIO()
        stdin = io.StringIO(
            json.dumps({"cmd": "reload", "model": "broken"}) + "\n" +
            json.dumps({"cmd": "shutdown"}) + "\n"
        )
        with mock.patch("core.daemon._load_model", side_effect=_fake_load), \
             mock.patch("core.daemon._is_model_cached", return_value=True), \
             mock.patch("sys.stdin", stdin), \
             mock.patch("sys.stdout", out_buf):
            daemon.main()

        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        statuses = [m.get("status") for m in msgs]
        self.assertIn("error", statuses)
        # The error must be followed by a recovery `ready` naming the old model.
        err_idx = statuses.index("error")
        recovery = next(
            m for m in msgs[err_idx + 1:]
            if m.get("status") == "ready"
        )
        self.assertEqual(recovery["model"], "small.en")


# ---------------------------------------------------------------------------
# _transcribe unit tests (bypasses I/O completely)
# ---------------------------------------------------------------------------

class TranscribeUnitTests(unittest.TestCase):
    def _pp(self):
        from core.postprocess import load_config
        return load_config()

    def test_transcribe_returns_text(self):
        model = mock.Mock()
        model.transcribe.return_value = ([_FakeSeg(" moving to blr")], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            result = daemon._transcribe(model, wav, "", False, self._pp())
        self.assertIn("text", result)
        self.assertNotIn("error", result)

    def test_transcribe_applies_postprocessing(self):
        model = mock.Mock()
        model.transcribe.return_value = ([_FakeSeg(" um moving to blr")], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            result = daemon._transcribe(model, wav, "", False, self._pp())
        # "um" removed, "blr" → "Bengaluru"
        self.assertNotIn("um", result["text"].lower().split())
        self.assertIn("Bengaluru", result["text"])

    def test_transcribe_missing_wav_returns_error(self):
        model = mock.Mock()
        result = daemon._transcribe(model, "/no/such.wav", "", False, {})
        self.assertIn("error", result)
        self.assertNotIn("text", result)

    def test_silent_clip_returns_empty_text_without_calling_model(self):
        # The silence gate must answer BEFORE Whisper runs: a silent clip can
        # only hallucinate (prompt echo, word loops), so the model is never
        # invoked and nothing is inserted.
        model = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "quiet.wav")
            _make_wav(Path(wav), amplitude=0.0)
            result = daemon._transcribe(model, wav, "VivoType, menu", False, self._pp())
        self.assertEqual(result["text"], "")
        model.transcribe.assert_not_called()

    def test_silent_clip_in_raw_mode_still_calls_model(self):
        # Raw mode is the diagnostic window — the gate must not hide what the
        # model would have produced.
        model = mock.Mock()
        model.transcribe.return_value = ([_FakeSeg(" VivoType")], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "quiet.wav")
            _make_wav(Path(wav), amplitude=0.0)
            result = daemon._transcribe(model, wav, "", True, self._pp())
        model.transcribe.assert_called_once()
        self.assertIn("text", result)

    def test_no_speech_segments_yield_empty_text(self):
        # Silence makes Whisper echo the initial_prompt with a high no_speech_prob;
        # such segments must be dropped so nothing is inserted.
        model = mock.Mock()
        model.transcribe.return_value = ([_FakeSeg(" VivoType", no_speech_prob=0.95)], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            result = daemon._transcribe(model, wav, "VivoType", False, self._pp())
        self.assertEqual(result["text"], "")

    def test_prompt_echo_with_low_no_speech_prob_is_dropped(self):
        # The dangerous variant: Whisper echoes the whole vocabulary hint back
        # CONFIDENTLY (low no_speech_prob), sailing past speech_segments().
        # The transcript being exactly the prompt is the tell.
        model = mock.Mock()
        model.transcribe.return_value = (
            [_FakeSeg(" VivoType, menu", no_speech_prob=0.1)], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            result = daemon._transcribe(model, wav, "VivoType, menu", False, self._pp())
        self.assertEqual(result["text"], "")

    def test_filler_prefixed_prompt_echo_is_dropped(self):
        # Whisper often prefixes a filler before echoing the hint. The echo
        # guard runs after postprocess so "um …" still collapses to the prompt
        # and is dropped instead of being inserted.
        model = mock.Mock()
        model.transcribe.return_value = (
            [_FakeSeg(" um VivoType, menu", no_speech_prob=0.1)], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            result = daemon._transcribe(model, wav, "VivoType, menu", False, self._pp())
        self.assertEqual(result["text"], "")

    def test_filler_suffixed_prompt_echo_is_dropped(self):
        model = mock.Mock()
        model.transcribe.return_value = (
            [_FakeSeg(" VivoType, menu um", no_speech_prob=0.1)], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            result = daemon._transcribe(model, wav, "VivoType, menu", False, self._pp())
        self.assertEqual(result["text"], "")

    def test_doubled_prompt_echo_is_dropped(self):
        # Loop variant: the prompt echoed twice back-to-back must not slip
        # past the guard (token-multiple check) — collapse_repetitions only
        # fires at 3+ repeats, so the echo guard has to catch 2x itself.
        model = mock.Mock()
        model.transcribe.return_value = (
            [_FakeSeg(" VivoType, menu VivoType, menu", no_speech_prob=0.1)], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            result = daemon._transcribe(model, wav, "VivoType, menu", False, self._pp())
        self.assertEqual(result["text"], "")

    def test_primer_only_echo_is_dropped(self):
        # Primer prepended in core must be dropped on silence even when the
        # full prompt was primer + vocabulary.
        model = mock.Mock()
        model.transcribe.return_value = (
            [_FakeSeg(" Hello, welcome to my lecture.", no_speech_prob=0.1)], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            result = daemon._transcribe(model, wav, "VivoType, menu", False, self._pp())
        self.assertEqual(result["text"], "")

    def test_doubled_primer_echo_is_dropped(self):
        model = mock.Mock()
        model.transcribe.return_value = (
            [_FakeSeg(" Hello, welcome to my lecture. Hello, welcome to my lecture.",
                      no_speech_prob=0.1)], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            result = daemon._transcribe(model, wav, "VivoType, menu", False, self._pp())
        self.assertEqual(result["text"], "")

    def test_postprocess_failure_returns_raw_asr(self):
        # A broken dictionary rule must not drop the transcript.
        model = mock.Mock()
        model.transcribe.return_value = (
            [_FakeSeg(" hello world", no_speech_prob=0.05)], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            with mock.patch("core.daemon.postprocess", side_effect=RuntimeError("bad regex")):
                result = daemon._transcribe(model, wav, "", False, self._pp())
        self.assertEqual(result["text"], "hello world")

    def test_real_speech_containing_prompt_terms_is_kept(self):
        # Dictation that merely CONTAINS prompt vocabulary must not be eaten.
        model = mock.Mock()
        model.transcribe.return_value = (
            [_FakeSeg(" Open the menu in VivoType please", no_speech_prob=0.05)], None)
        with tempfile.TemporaryDirectory() as d:
            wav = str(Path(d) / "x.wav")
            _make_wav(Path(wav))
            result = daemon._transcribe(model, wav, "VivoType, menu", False, self._pp())
        self.assertIn("menu", result["text"])
        self.assertIn("VivoType", result["text"])


class ModelLoadFailureIntegrationTests(unittest.TestCase):
    """F1 end-to-end: daemon.main() through the REAL core.asr loader (only the
    mlx modules are faked). A warm-up failure must surface as an NDJSON `error`
    event — never a false `ready` — and a failed reload must still roll back."""

    def test_boot_load_failure_emits_error_and_exits(self):
        from core.tests.test_asr import _fake_mlx_modules

        def boom(repo, dtype):
            raise OSError("download failed")

        out_buf = io.StringIO()
        with mock.patch.dict(sys.modules, _fake_mlx_modules(boom)), \
             mock.patch("core.daemon._is_model_cached", return_value=True), \
             mock.patch("sys.stdin", io.StringIO("")), \
             mock.patch("sys.stdout", out_buf), \
             self.assertRaises(SystemExit):
            daemon.main()

        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        statuses = [m.get("status") for m in msgs]
        self.assertIn("error", statuses)
        self.assertNotIn("ready", statuses)  # the F1 lie: ready after a failed load
        err = next(m for m in msgs if m.get("status") == "error")
        self.assertIn("download failed", err["error"])

    def test_failed_reload_via_real_loader_rolls_back_and_serves(self):
        from core.tests.test_asr import _fake_mlx_modules

        def selective(repo, dtype):
            if "broken" in repo:
                raise OSError("bad model")

        def fake_transcribe(audio, **opts):
            return {"segments": [{"start": 0.0, "end": 1.0, "text": " still works",
                                  "avg_logprob": -0.2, "no_speech_prob": 0.0}]}

        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            out_buf = io.StringIO()
            stdin = io.StringIO(
                json.dumps({"cmd": "reload", "model": "broken.en"}) + "\n" +
                json.dumps({"id": 1, "wav": str(wav), "initial_prompt": "", "raw": False}) + "\n" +
                json.dumps({"cmd": "shutdown"}) + "\n"
            )
            with mock.patch.dict(sys.modules,
                                 _fake_mlx_modules(selective, transcribe_fn=fake_transcribe)), \
                 mock.patch("core.daemon._is_model_cached", return_value=True), \
                 mock.patch("sys.stdin", stdin), \
                 mock.patch("sys.stdout", out_buf):
                daemon.main()

        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        statuses = [m.get("status") for m in msgs]
        boot_model = next(m for m in msgs if m.get("status") == "ready")["model"]
        # error for the failed reload, then recovery ready on the OLD model...
        self.assertIn("error", statuses)
        err_idx = statuses.index("error")
        recovery = next(m for m in msgs[err_idx + 1:] if m.get("status") == "ready")
        self.assertEqual(recovery["model"], boot_model)
        # ...and the old (real MLXModel) model still transcribes.
        tx = next(m for m in msgs if "id" in m)
        self.assertEqual(tx["text"], "still works")


class ReloadEdgeCaseTests(unittest.TestCase):
    """A-F2 / A-F3 / A-F8 on the reload control path."""

    def _run(self, commands, load_side_effect=None, extra_patches=()):
        loads = []

        def _fake_load(name):
            loads.append(name)
            if load_side_effect:
                load_side_effect(name)
            m = mock.Mock()
            m.transcribe.return_value = ([_FakeSeg(" blr")], None)
            return m

        stdin = io.StringIO("\n".join(json.dumps(c) for c in commands) + "\n")
        with contextlib.ExitStack() as stack:
            for p in extra_patches:
                stack.enter_context(p)
            stack.enter_context(mock.patch("core.daemon._load_model", side_effect=_fake_load))
            stack.enter_context(mock.patch("core.daemon._is_model_cached", return_value=True))
            stack.enter_context(mock.patch("core.daemon.load_settings",
                                           return_value={"model": "small.en"}))
            stack.enter_context(mock.patch("sys.stdin", stdin))
            out_buf = stack.enter_context(mock.patch("sys.stdout", io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            daemon.main()
        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        return msgs, loads

    def test_reload_to_current_model_still_emits_ready(self):
        # A-F2: the client flips isReady false on sending reload and waits
        # for ready; a same-model reload used to emit nothing at all.
        msgs, loads = self._run([{"cmd": "reload", "model": "small.en"},
                                 {"cmd": "shutdown"}])
        readies = [m for m in msgs if m.get("status") == "ready"]
        self.assertEqual(len(readies), 2)  # boot + reload
        self.assertEqual(readies[-1]["model"], "small.en")
        self.assertEqual(loads, ["small.en"])  # no pointless reload

    def test_reload_without_model_field_emits_ready(self):
        msgs, loads = self._run([{"cmd": "reload"}, {"cmd": "shutdown"}])
        self.assertEqual([m.get("status") for m in msgs].count("ready"), 2)
        self.assertEqual(loads, ["small.en"])

    def test_reload_with_non_string_model_reports_error_then_ready(self):
        msgs, loads = self._run([{"cmd": "reload", "model": 5},
                                 {"cmd": "shutdown"}])
        statuses = [m.get("status") for m in msgs]
        self.assertIn("error", statuses)
        self.assertEqual(statuses[-1], "ready")
        self.assertEqual(msgs[-1]["model"], "small.en")
        self.assertEqual(loads, ["small.en"])

    def test_config_raise_during_model_reload_keeps_daemon_alive(self):
        # A-F3: the reload path's load_config had no guard, so a wrong-typed
        # user dictionary killed the daemon on a model switch.
        calls = {"n": 0}

        def lc(*a, **k):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise TypeError("'int' object is not iterable")
            return {"fillers": [], "replacements": {"blr": "Bengaluru"},
                    "profiles": {}}

        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            msgs, loads = self._run(
                [{"cmd": "reload", "model": "tiny.en"},
                 {"id": 1, "wav": str(wav)},
                 {"cmd": "shutdown"}],
                extra_patches=[mock.patch("core.daemon.load_config", side_effect=lc),
                               mock.patch("core.daemon.config_mtime", return_value=1.0)])
        readies = [m for m in msgs if m.get("status") == "ready"]
        self.assertEqual(readies[-1]["model"], "tiny.en")
        reply = next(m for m in msgs if m.get("id") == 1)
        self.assertEqual(reply["text"], "Bengaluru")  # last-good rules kept


class ReloadGpuCacheTests(unittest.TestCase):
    """F9: a successful model switch must release the old model's GPU memory;
    a rolled-back (failed) switch must NOT evict the cache it's still using."""

    def test_successful_reload_clears_gpu_cache(self):
        calls = []
        model_mock = mock.Mock()
        model_mock.transcribe.return_value = ([], None)

        stdin = io.StringIO(
            json.dumps({"cmd": "reload", "model": "tiny.en"}) + "\n" +
            json.dumps({"cmd": "shutdown"}) + "\n"
        )
        with mock.patch("core.daemon._load_model", return_value=model_mock), \
             mock.patch("core.daemon._is_model_cached", return_value=True), \
             mock.patch("core.daemon.asr.clear_gpu_cache",
                        side_effect=lambda: calls.append(True)), \
             mock.patch("sys.stdin", stdin), \
             mock.patch("sys.stdout", io.StringIO()):
            daemon.main()

        self.assertEqual(calls, [True])

    def test_failed_reload_does_not_clear_gpu_cache(self):
        calls = []
        good_model = mock.Mock()
        good_model.transcribe.return_value = ([], None)

        def _fake_load(name):
            # Boot loads whatever model_name settings resolve to (real
            # load_settings() isn't mocked here) — only the reload target
            # ("broken") must fail, so boot always succeeds regardless of name.
            if name == "broken":
                raise RuntimeError("boom")
            return good_model

        stdin = io.StringIO(
            json.dumps({"cmd": "reload", "model": "broken"}) + "\n" +
            json.dumps({"cmd": "shutdown"}) + "\n"
        )
        with mock.patch("core.daemon._load_model", side_effect=_fake_load), \
             mock.patch("core.daemon._is_model_cached", return_value=True), \
             mock.patch("core.daemon.asr.clear_gpu_cache",
                        side_effect=lambda: calls.append(True)), \
             mock.patch("sys.stdin", stdin), \
             mock.patch("sys.stdout", io.StringIO()):
            daemon.main()

        self.assertEqual(calls, [])


class ConfigReloadTests(unittest.TestCase):
    """A live edit to the dictionary/filler config must take effect WITHOUT a
    model reload — otherwise promote.py's "rules are already active" is a lie."""

    def test_dictionary_edit_picked_up_between_requests(self):
        model_mock = mock.Mock()
        model_mock.transcribe.return_value = ([_FakeSeg(" blr")], None)

        cfg_before = {"fillers": [], "replacements": {}}
        cfg_after = {"fillers": [], "replacements": {"blr": "Bengaluru"}}

        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            req = json.dumps({"id": 1, "wav": str(wav), "initial_prompt": "", "raw": False})
            with mock.patch("core.daemon._load_model", return_value=model_mock), \
                 mock.patch("core.daemon._is_model_cached", return_value=True), \
                 mock.patch("core.daemon.load_config", side_effect=[cfg_before, cfg_after]), \
                 mock.patch("core.daemon.config_mtime", side_effect=[1.0, 1.0, 2.0]), \
                 mock.patch("sys.stdin", io.StringIO(req + "\n" + req + "\n"
                                                     + json.dumps({"cmd": "shutdown"}) + "\n")), \
                 mock.patch("sys.stdout", io.StringIO()) as out_buf:
                daemon.main()

        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        texts = [m["text"] for m in msgs if "id" in m]
        self.assertEqual(texts[0], "blr")          # before the edit
        self.assertEqual(texts[1], "Bengaluru")    # after the live edit


class ProfileThreadingTests(unittest.TestCase):
    """The optional 'profile' request field must select post-processing rules:
    omitted/non-string/unknown all behave exactly as 'default' (the gated IPC
    surface), and the reply shape never changes."""

    CFG = {
        "fillers": ["um"],
        "replacements": {},
        "profiles": {
            "code": {"convert_currency": False, "remove_fillers": False},
        },
    }

    def _texts_for(self, *profile_fields):
        """One request per argument; each argument is the raw 'profile' field
        value to send, or _OMITTED to build the request without the field."""
        model_mock = mock.Mock()
        model_mock.transcribe.return_value = ([_FakeSeg(" that is $10k um")], None)

        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            lines = []
            for i, field in enumerate(profile_fields, start=1):
                req = {"id": i, "wav": str(wav), "initial_prompt": "", "raw": False}
                if field is not _OMITTED:
                    req["profile"] = field
                lines.append(json.dumps(req))
            lines.append(json.dumps({"cmd": "shutdown"}))
            out_buf = io.StringIO()
            with mock.patch("core.daemon._load_model", return_value=model_mock), \
                 mock.patch("core.daemon._is_model_cached", return_value=True), \
                 mock.patch("core.daemon.load_config", return_value=self.CFG), \
                 mock.patch("core.daemon.config_mtime", return_value=1.0), \
                 mock.patch("sys.stdin", io.StringIO("\n".join(lines) + "\n")), \
                 mock.patch("sys.stdout", out_buf):
                daemon.main()

        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        self.assertEqual(len([m for m in msgs if "id" in m]), len(profile_fields))
        return [m["text"] for m in msgs if "id" in m]

    def test_profile_selects_rules(self):
        texts = self._texts_for("default", "code")
        # Default: currency converted, filler stripped.
        self.assertEqual(texts[0], "that is ₹10 lakh")
        # Code context: both toggles off — dollars and "um" survive untouched.
        self.assertEqual(texts[1], "that is $10k um")

    def test_omitted_field_equals_explicit_default(self):
        texts = self._texts_for(_OMITTED, "default")
        self.assertEqual(texts[0], texts[1])

    def test_non_string_and_empty_fields_coerce_to_default(self):
        texts = self._texts_for("default", 5, "")
        self.assertEqual(texts[0], texts[1])
        self.assertEqual(texts[0], texts[2])

    def test_unknown_profile_behaves_as_default(self):
        # The real postprocess path warns once to stderr; behavior must not
        # change and the reply shape stays {"id","text"}.
        with contextlib.redirect_stderr(io.StringIO()):
            texts = self._texts_for("default", "no-such-profile")
        self.assertEqual(texts[0], texts[1])


class ReloadFailureResilienceTests(unittest.TestCase):
    """D4 hardening: a config reload that RAISES mid-request-loop (a wrong-typed
    field slips past load_config's decode guards — e.g. {"fillers": 5} raises
    TypeError) must not kill the daemon. Keep serving last-good rules; warn
    once per distinct mtime, not once per request."""

    def test_raising_reload_keeps_last_good_config_and_stays_alive(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            model_mock = mock.Mock()
            model_mock.transcribe.return_value = ([_FakeSeg(" blr")], None)
            cfg_good = {"fillers": [], "replacements": {"blr": "Bengaluru"},
                        "profiles": {}}
            calls = {"n": 0}

            def raising_reload():
                # Boot + request 1 get the good config; every later reload
                # attempt raises exactly like the real wrong-typed file does.
                calls["n"] += 1
                if calls["n"] >= 2:
                    raise TypeError("'int' object is not iterable")
                return dict(cfg_good)

            reqs = [json.dumps({"id": i, "wav": str(wav), "initial_prompt": "",
                                "raw": False}) for i in (1, 2, 3)]
            err = io.StringIO()
            # mtime timeline: boot baseline 7.0; req1 sees 7.0 (no reload); req2
            # sees 9.0 -> reload raises; req3 sees 9.0 again -> retry, warn-once.
            with mock.patch("core.daemon._load_model", return_value=model_mock), \
                 mock.patch("core.daemon._is_model_cached", return_value=True), \
                 mock.patch("core.daemon.load_config", side_effect=raising_reload), \
                 mock.patch("core.daemon.config_mtime",
                            side_effect=[7.0, 7.0, 9.0, 9.0, 9.0]), \
                 mock.patch("sys.stdin", io.StringIO(
                     "\n".join(reqs) + "\n" + json.dumps({"cmd": "shutdown"}) + "\n")), \
                 mock.patch("sys.stdout", io.StringIO()) as out_buf, \
                 contextlib.redirect_stderr(err):
                daemon.main()

            msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]

        texts = [m["text"] for m in msgs if "id" in m]
        self.assertEqual([m.get("error") for m in msgs if "id" in m],
                         [None, None, None])      # no error replies anywhere
        self.assertEqual(len(texts), 3)           # daemon alive throughout
        self.assertEqual(texts[0], "Bengaluru")   # good rules before the edit
        self.assertEqual(texts[1], "Bengaluru")   # last-good kept after raise
        self.assertEqual(texts[2], "Bengaluru")   # still serving on retry
        self.assertEqual(err.getvalue().count("config reload failed"), 1)
        self.assertEqual(calls["n"], 3)           # boot + two reload attempts

    def test_wrong_typed_config_file_raises_through_real_loader(self):
        # End-to-end proof of the premise above: a REAL load_config against a
        # {"fillers": 5} file raises TypeError (decode guards don't catch it),
        # and the daemon's reload guard absorbs it mid-loop instead of dying.
        model_mock = mock.Mock()
        model_mock.transcribe.return_value = ([_FakeSeg(" blr")], None)
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "pp.json"
            base.write_text(json.dumps(
                {"fillers": [], "replacements": {"blr": "Bengaluru"}}),
                encoding="utf-8")
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            reqs = [json.dumps({"id": i, "wav": str(wav),
                                "initial_prompt": "", "raw": False})
                    for i in (1, 2)]
            err = io.StringIO()
            state = {"call": 0}

            def fake_mtime(*a, **k):
                # boot=7.0; req1=7.0 (no reload); req2: corrupt the file to a
                # wrong-typed field FIRST, then report the new mtime so the
                # loop attempts a reload against the real (raising) loader.
                state["call"] += 1
                if state["call"] == 3:
                    base.write_text(json.dumps({"fillers": 5}), encoding="utf-8")
                    mtime = base.stat().st_mtime
                    os.utime(base, (mtime + 10, mtime + 10))
                return base.stat().st_mtime

            with mock.patch("core.postprocess.DEFAULT_CONFIG_PATH", base), \
                 mock.patch("core.postprocess.USER_DICT_PATH",
                            Path(d) / "no-overlay.json"), \
                 mock.patch("core.daemon._load_model", return_value=model_mock), \
                 mock.patch("core.daemon._is_model_cached", return_value=True), \
                 mock.patch("core.daemon.config_mtime", side_effect=fake_mtime), \
                 mock.patch("sys.stdin", io.StringIO(
                     "\n".join(reqs) + "\n"
                     + json.dumps({"cmd": "shutdown"}) + "\n")), \
                 mock.patch("sys.stdout", io.StringIO()) as out_buf, \
                 contextlib.redirect_stderr(err):
                daemon.main()

        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        texts = [m["text"] for m in msgs if "id" in m]
        self.assertEqual(texts, ["Bengaluru", "Bengaluru"])  # alive, last-good
        self.assertIn("config reload failed", err.getvalue())


class RequestRobustnessTests(unittest.TestCase):
    """A-F4 / A-F8: odd requests get a reply for their id; the daemon never
    dies on one bad request."""

    def _empty_wav(self, d):
        p = Path(d) / "empty.wav"
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
        return p

    def test_empty_wav_is_silence_not_error(self):
        # An error reply counts toward the client's hang-restart budget; a
        # zero-frame clip is just a very short silence.
        with tempfile.TemporaryDirectory() as d:
            wav = self._empty_wav(d)
            model_mock = mock.Mock()
            msgs = _run_daemon([{"id": 3, "wav": str(wav)},
                                {"id": 4, "wav": str(wav), "raw": True},
                                {"cmd": "shutdown"}], model_mock=model_mock)
        replies = [m for m in msgs if "id" in m]
        self.assertEqual(replies, [{"id": 3, "text": "", "command": None},
                                   {"id": 4, "text": "", "command": None}])
        model_mock.transcribe.assert_not_called()

    def test_non_string_initial_prompt_does_not_kill_daemon(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            msgs = _run_daemon([{"id": 1, "wav": str(wav), "initial_prompt": 5},
                                {"id": 2, "wav": str(wav), "initial_prompt": ["x"]},
                                {"id": 3, "wav": str(wav)},
                                {"cmd": "shutdown"}])
        replies = {m["id"]: m for m in msgs if "id" in m}
        self.assertEqual(sorted(replies), [1, 2, 3])
        # A junk prompt is ignored (primer only), the dictation still lands.
        self.assertEqual(replies[1]["text"], "hello world")
        self.assertEqual(replies[3]["text"], "hello world")

    def test_non_string_wav_gets_error_reply(self):
        msgs = _run_daemon([{"id": 7, "wav": 123},
                            {"id": 8, "wav": None},
                            {"cmd": "shutdown"}])
        replies = [m for m in msgs if "id" in m]
        self.assertEqual([r["id"] for r in replies], [7, 8])
        self.assertTrue(all("error" in r for r in replies))

    def test_unexpected_raise_is_isolated_to_its_request(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            real = daemon._transcribe
            calls = {"n": 0}

            def flaky(*a, **k):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise AttributeError("boom")
                return real(*a, **k)

            err = io.StringIO()
            with mock.patch("core.daemon._transcribe", side_effect=flaky), \
                 contextlib.redirect_stderr(err):
                msgs = _run_daemon([{"id": 1, "wav": str(wav)},
                                    {"id": 2, "wav": str(wav)},
                                    {"cmd": "shutdown"}])
        replies = {m["id"]: m for m in msgs if "id" in m}
        self.assertIn("boom", replies[1]["error"])
        self.assertNotIn("command", replies[1])  # error replies carry no command
        self.assertEqual(replies[2]["text"], "hello world")


class VoiceCommandTests(unittest.TestCase):
    """Feature 4: literal command phrases. Scratch returns the structural
    signal; transforms apply AFTER postprocess; every normal/silent/raw reply
    carries "command" (null); error replies never do."""

    def _msgs(self, segs_text, wav=None, raw=False, load_config_ret=None,
              postprocess_side_effect=None, initial_prompt="", voice_commands=True):
        model_mock = mock.Mock()
        model_mock.transcribe.return_value = (
            [_FakeSeg(t) for t in segs_text], None)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.wav"
            if wav is None:
                _make_wav(path)
            else:
                wav(path)
            req = json.dumps({"id": 7, "wav": str(path),
                              "initial_prompt": initial_prompt,
                              "raw": raw, "voice_commands": voice_commands})
            out_buf = io.StringIO()
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch(
                    "core.daemon._load_model", return_value=model_mock))
                stack.enter_context(mock.patch(
                    "core.daemon._is_model_cached", return_value=True))
                if load_config_ret is not None:
                    stack.enter_context(mock.patch(
                        "core.daemon.load_config", return_value=load_config_ret))
                if postprocess_side_effect is not None:
                    stack.enter_context(mock.patch(
                        "core.daemon.postprocess",
                        side_effect=postprocess_side_effect))
                stack.enter_context(mock.patch(
                    "sys.stdin",
                    io.StringIO(req + "\n"
                                + json.dumps({"cmd": "shutdown"}) + "\n")))
                stack.enter_context(mock.patch("sys.stdout", out_buf))
                daemon.main()
        lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
        return [json.loads(l) for l in lines]

    def test_commands_off_types_every_phrase_as_words(self):
        # The default (Settings off, or no field at all): nothing is a command.
        for enabled in (False, None, "true", 1):
            msgs = self._msgs([" scratch that"], voice_commands=enabled)
            reply = next(m for m in msgs if "id" in m)
            self.assertIsNone(reply["command"], enabled)
            self.assertIn("scratch that", reply["text"].lower(), enabled)
        msgs = self._msgs([" first point new line second point"], voice_commands=False)
        reply = next(m for m in msgs if "id" in m)
        self.assertIn("new line", reply["text"].lower())
        self.assertNotIn("\n", reply["text"])

    def test_scratch_utterance_signals_structural_command(self):
        msgs = self._msgs([" scratch that"])
        reply = next(m for m in msgs if "id" in m)
        self.assertEqual(reply["text"], "")
        self.assertEqual(reply["command"], "scratch_that")

    def test_trailing_scratch_types_literally(self):
        # Destructive commands match standalone only (see core/commands.py):
        # a sentence ending in the phrase is dictation, not a command.
        msgs = self._msgs([" hello world scratch that"])
        reply = next(m for m in msgs if "id" in m)
        self.assertEqual(reply["text"], "hello world scratch that")
        self.assertIsNone(reply["command"])

    def test_normal_reply_carries_null_command(self):
        msgs = self._msgs([" hello world"])
        reply = next(m for m in msgs if "id" in m)
        self.assertEqual(reply["text"], "hello world")
        self.assertIn("command", reply)          # uniform schema: key present
        self.assertIsNone(reply["command"])

    def test_silence_gate_wins_before_detection(self):
        # A silent clip returns pre-detection; a hallucinated scratch can't
        # fire on silence. Shape pinned: {"text":"","command":null}.
        def silence(path):
            _make_wav(path, amplitude=0.0)
        msgs = self._msgs([], wav=silence)
        reply = next(m for m in msgs if "id" in m)
        self.assertEqual(reply["text"], "")
        self.assertIsNone(reply["command"])

    def test_raw_mode_bypasses_detection_uniform_schema(self):
        msgs = self._msgs([" scratch that"], raw=True)
        reply = next(m for m in msgs if "id" in m)
        self.assertIn("scratch that", reply["text"])   # raw segment JSON line
        self.assertIsNone(reply["command"])

    def test_error_replies_carry_no_command_key(self):
        model_mock = mock.Mock()
        model_mock.transcribe.side_effect = RuntimeError("boom")
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            _make_wav(wav)
            req = json.dumps({"id": 1, "wav": str(wav), "initial_prompt": "",
                              "raw": False})
            out_buf = io.StringIO()
            with mock.patch("core.daemon._load_model", return_value=model_mock), \
                 mock.patch("core.daemon._is_model_cached", return_value=True), \
                 mock.patch("sys.stdin", io.StringIO(req + "\n")), \
                 mock.patch("sys.stdout", out_buf):
                daemon.main()
        msgs = [json.loads(l) for l in out_buf.getvalue().splitlines() if l.strip()]
        reply = next(m for m in msgs if "id" in m)
        self.assertIn("error", reply)
        self.assertNotIn("command", reply)

    def test_detection_runs_before_postprocess(self):
        # The discriminator for ordering (QA-designed): a promoted rule that
        # rewrites "that". Detection-first matches the phrase on RAW text and
        # the remainder never contains it; postprocess-first would corrupt
        # "cap that" into "Capitol X" and inject it literally.
        cfg = {"fillers": [], "replacements": {"cap": "Capitol", "that": "X"},
               "profiles": {}}
        msgs = self._msgs([" hello world cap that"], load_config_ret=cfg)
        reply = next(m for m in msgs if "id" in m)
        self.assertEqual(reply["text"], "Hello world")
        self.assertIsNone(reply["command"])

    def test_transform_after_cleanup_with_fillers(self):
        cfg = {"fillers": ["um"], "replacements": {}, "profiles": {}}
        msgs = self._msgs([" um hello world all caps that"],
                          load_config_ret=cfg)
        reply = next(m for m in msgs if "id" in m)
        self.assertEqual(reply["text"], "HELLO WORLD")

    def test_filler_prefixed_scratch_detected(self):
        cfg = {"fillers": ["um"], "replacements": {}, "profiles": {}}
        msgs = self._msgs([" um scratch that"], load_config_ret=cfg)
        reply = next(m for m in msgs if "id" in m)
        self.assertEqual(reply["text"], "")
        self.assertEqual(reply["command"], "scratch_that")

    def test_postprocess_failure_skips_transform(self):
        msgs = self._msgs([" hello all caps that"],
                          postprocess_side_effect=RuntimeError("bad regex"))
        reply = next(m for m in msgs if "id" in m)
        self.assertEqual(reply["text"], "hello")     # raw remainder, untransformed
        self.assertIsNone(reply["command"])
    def test_scratch_phrase_across_segment_join(self):
        # Whisper may split the phrase across segments; join happens before
        # detection, so ["scratch"],["that"] is still the standalone command.
        msgs = self._msgs(["scratch", "that"])
        reply = next(m for m in msgs if "id" in m)
        self.assertEqual(reply["command"], "scratch_that")

    def test_keep_raw_path_still_echo_guards(self):
        # Pins 6c67ae4: a postprocess failure must not skip the prompt-echo
        # guard — echoed prompts are dropped even when cleanup failed.
        msgs = self._msgs(["VivoType menu"],
                          postprocess_side_effect=RuntimeError("bad regex"),
                          initial_prompt="VivoType, menu")
        reply = next(m for m in msgs if "id" in m)
        self.assertEqual(reply["text"], "")
        self.assertIsNone(reply["command"])



if __name__ == "__main__":
    unittest.main()
