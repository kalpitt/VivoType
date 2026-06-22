"""Tests for core/daemon.py — no model download required (mock patching)."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

import core.daemon as daemon


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


def _make_wav(path: Path, samplerate: int = 16000, duration: float = 0.1) -> None:
    """Write a minimal silent WAV for path-existence checks."""
    pcm = np.zeros(int(samplerate * duration), dtype=np.int16)
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
        self.assertEqual(kwargs.get("initial_prompt"), "Kalpit,Bengaluru")

    def test_empty_initial_prompt_not_passed(self):
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
        self.assertNotIn("initial_prompt", kwargs)

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


if __name__ == "__main__":
    unittest.main()
