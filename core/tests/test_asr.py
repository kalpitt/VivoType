"""Tests for core/asr.py — segment helpers and model warm-up failure paths
(no real mlx import; the warm-up tests inject fake mlx modules)."""

import sys
import time
import types
import unittest
from unittest import mock

from core import asr
from core.asr import _Segment, speech_segments


def _fake_mlx_modules(get_model, transcribe_fn=None, download_fn=None):
    """Build sys.modules entries faking mlx / mlx_whisper / huggingface_hub.

    `get_model(repo, dtype)` backs ModelHolder.get_model (the warm-up path);
    `transcribe_fn` (optional) backs mlx_whisper.transcribe() for tests that
    exercise a real MLXModel end-to-end; `download_fn` (optional) backs
    huggingface_hub.snapshot_download (load_model's bounded download step —
    default: instant no-op so no test ever touches the network). Shared with
    test_daemon.py.
    """
    fake_mx = types.ModuleType("mlx.core")
    fake_mx.float16 = "float16"
    fake_mlx = types.ModuleType("mlx")
    fake_mlx.core = fake_mx

    fake_transcribe_mod = types.ModuleType("mlx_whisper.transcribe")
    holder = type("ModelHolder", (), {"get_model": staticmethod(get_model)})
    fake_transcribe_mod.ModelHolder = holder

    fake_whisper = types.ModuleType("mlx_whisper")
    # `from mlx_whisper.transcribe import ModelHolder` resolves the submodule
    # via sys.modules, so the package attribute is free to be the callable that
    # `mlx_whisper.transcribe(audio, ...)` invokes — mirroring the real package.
    fake_whisper.transcribe = transcribe_fn if transcribe_fn else fake_transcribe_mod

    fake_hub = types.ModuleType("huggingface_hub")
    fake_hub.snapshot_download = download_fn if download_fn else (lambda *a, **k: None)

    return {
        "mlx": fake_mlx,
        "mlx.core": fake_mx,
        "mlx_whisper": fake_whisper,
        "mlx_whisper.transcribe": fake_transcribe_mod,
        "huggingface_hub": fake_hub,
    }


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


class WarmupFailureTests(unittest.TestCase):
    """A real model-load failure must raise from load_model(). Silently
    swallowing it made the daemon report `ready` for a model that could never
    transcribe (offline first run, corrupt cache, unknown model)."""

    def test_load_failure_raises_with_model_name(self):
        def boom(repo, dtype):
            raise OSError("no network")

        with mock.patch.dict(sys.modules, _fake_mlx_modules(boom)):
            with self.assertRaises(RuntimeError) as ctx:
                asr.load_model("small.en")
        # The message must be self-explanatory in the daemon's NDJSON error.
        self.assertIn("small.en", str(ctx.exception))
        self.assertIn("no network", str(ctx.exception))

    def test_successful_warmup_primes_model_holder(self):
        calls = []

        def ok(repo, dtype):
            calls.append((repo, dtype))

        with mock.patch.dict(sys.modules, _fake_mlx_modules(ok)):
            model = asr.load_model("small.en")
        self.assertEqual(model.model_name, "small.en")
        self.assertEqual(calls, [("mlx-community/whisper-small.en-mlx", "float16")])

    def test_missing_internals_falls_back_to_lazy_loading(self):
        # If mlx-whisper refactors ModelHolder away, warm-up is skipped (lazy
        # load on first transcribe) — an internals change is not a load failure.
        mods = _fake_mlx_modules(lambda *a: None)
        bare = types.ModuleType("mlx_whisper.transcribe")  # no ModelHolder attr
        mods["mlx_whisper.transcribe"] = bare
        mods["mlx_whisper"].transcribe = bare
        with mock.patch.dict(sys.modules, mods):
            model = asr.load_model("small.en")  # must not raise
        self.assertEqual(model.model_name, "small.en")


class TranscribeOptionsTests(unittest.TestCase):
    """Decode options that keep hallucinations down must actually reach
    mlx_whisper.transcribe()."""

    def _captured_opts(self):
        captured = {}

        def fake_transcribe(audio, **opts):
            captured.update(opts)
            return {"segments": []}

        with mock.patch.dict(sys.modules,
                             _fake_mlx_modules(lambda *a: None,
                                               transcribe_fn=fake_transcribe)):
            model = asr.load_model("small.en")
            model.transcribe([0.0] * 160)
        return captured

    def test_condition_on_previous_text_disabled(self):
        # Feeding each segment back as context is what turns one misheard word
        # into a "guilty guilty guilty" loop — must be off for dictation clips.
        self.assertIs(self._captured_opts().get("condition_on_previous_text"), False)

    def test_verbose_stays_off(self):
        # stdout is the daemon's NDJSON channel.
        self.assertIs(self._captured_opts().get("verbose"), False)


class LoadModelTimeoutTests(unittest.TestCase):
    """F7: a stalled DOWNLOAD must not hang the daemon forever. Only the
    network-bound download is time-bounded on a worker thread — the Metal
    warm-up must stay on the calling thread (see thread-affinity test)."""

    def test_stalled_download_raises_timeout_error(self):
        def slow_download(*args, **kwargs):
            time.sleep(0.5)

        mods = _fake_mlx_modules(lambda *a: None, download_fn=slow_download)
        with mock.patch.dict(sys.modules, mods):
            with self.assertRaises(TimeoutError) as ctx:
                asr.load_model("small.en", timeout=0.05)
        # The message must be self-explanatory in the daemon's NDJSON error.
        self.assertIn("small.en", str(ctx.exception))
        self.assertIn("timed out", str(ctx.exception))

    def test_fast_load_returns_before_timeout(self):
        with mock.patch.dict(sys.modules, _fake_mlx_modules(lambda *a: None)):
            model = asr.load_model("small.en", timeout=5)
        self.assertEqual(model.model_name, "small.en")

    def test_download_failure_falls_through_to_load_error(self):
        # A fast download failure (offline, no cache) must surface as the
        # load's own clear RuntimeError, not be swallowed into a false success.
        def offline(*args, **kwargs):
            raise OSError("offline")

        def boom(repo, dtype):
            raise OSError("no cached weights")

        mods = _fake_mlx_modules(boom, download_fn=offline)
        with mock.patch.dict(sys.modules, mods):
            with self.assertRaises(RuntimeError) as ctx:
                asr.load_model("small.en", timeout=5)
        self.assertIn("no cached weights", str(ctx.exception))


class LoadModelThreadAffinityTests(unittest.TestCase):
    """MLX GPU streams are thread-local: a model warmed on a worker thread
    makes transcribe() on the calling thread fail with 'There is no
    Stream(gpu, N) in current thread' (field-verified on Apple Silicon).
    The warm-up must therefore run on the caller's thread."""

    def test_warmup_runs_on_calling_thread(self):
        import threading
        warmup_threads = []

        def record(repo, dtype):
            warmup_threads.append(threading.get_ident())

        with mock.patch.dict(sys.modules, _fake_mlx_modules(record)):
            asr.load_model("small.en", timeout=5)
        self.assertEqual(warmup_threads, [threading.get_ident()])


class ClearGpuCacheTests(unittest.TestCase):
    """F9: switching models must release the old model's GPU memory."""

    def test_calls_mlx_clear_cache(self):
        calls = []
        fake_mx = types.ModuleType("mlx.core")
        fake_mx.clear_cache = lambda: calls.append(True)
        fake_mlx = types.ModuleType("mlx")
        fake_mlx.core = fake_mx

        with mock.patch.dict(sys.modules, {"mlx": fake_mlx, "mlx.core": fake_mx}):
            asr.clear_gpu_cache()
        self.assertEqual(calls, [True])

    def test_missing_clear_cache_is_swallowed(self):
        # Simulates an mlx release that doesn't expose clear_cache() — must
        # not raise (best-effort cleanup).
        fake_mx = types.ModuleType("mlx.core")  # no clear_cache attribute
        fake_mlx = types.ModuleType("mlx")
        fake_mlx.core = fake_mx

        with mock.patch.dict(sys.modules, {"mlx": fake_mlx, "mlx.core": fake_mx}):
            asr.clear_gpu_cache()  # must not raise


if __name__ == "__main__":
    unittest.main()
