"""Tests for core/asr.py — segment helpers and model warm-up failure paths
(no real mlx import; the warm-up tests inject fake mlx modules)."""

import contextlib
import io
import os
import sys
import time
import types
import unittest
from unittest import mock

from core import asr
from core.asr import _Segment, speech_segments


def _cached_download(repo_id, **kwargs):
    """Fake snapshot_download for a model that is already cached: returns the
    local snapshot directory, as the real one does."""
    return f"/fake-hf-cache/{repo_id}/snapshots/abc123"


def _fake_mlx_modules(get_model, transcribe_fn=None, download_fn=None):
    """Build sys.modules entries faking mlx / mlx_whisper / huggingface_hub.

    `get_model(repo, dtype)` backs ModelHolder.get_model (the warm-up path);
    `transcribe_fn` (optional) backs mlx_whisper.transcribe() for tests that
    exercise a real MLXModel end-to-end; `download_fn` (optional) backs
    huggingface_hub.snapshot_download (load_model's bounded download step —
    default: an instant "already cached" answer returning a fake local path,
    so no test ever touches the network). Shared with test_daemon.py.
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
    fake_hub.snapshot_download = download_fn if download_fn else _cached_download

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
        # The warm-up is keyed by the LOCAL snapshot path, never the HF repo id
        # (a repo id makes mlx-whisper call snapshot_download online).
        self.assertEqual(
            calls,
            [("/fake-hf-cache/mlx-community/whisper-small.en-mlx/snapshots/abc123",
              "float16")])

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

    def test_verbose_is_none(self):
        # stdout is the daemon's NDJSON channel. verbose=False is NOT quiet in
        # mlx-whisper: it still prints "Detected language: X" for multilingual
        # models (and enables a tqdm bar). Only None silences every print.
        self.assertIn("verbose", self._captured_opts())
        self.assertIsNone(self._captured_opts()["verbose"])

    def test_mlx_prints_never_reach_stdout(self):
        # Belt and braces for A-F11: whatever mlx-whisper prints during
        # warm-up or transcribe goes to stderr, never the NDJSON/CLI stdout.
        def chatty_warmup(repo, dtype):
            print("warming up")

        def chatty_transcribe(audio, **opts):
            print("Detected language: Hindi")
            return {"segments": []}

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(sys.modules,
                             _fake_mlx_modules(chatty_warmup,
                                               transcribe_fn=chatty_transcribe)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            model = asr.load_model("small.en")
            model.transcribe([0.0] * 160)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("Detected language: Hindi", err.getvalue())


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

    def test_download_failure_raises_without_touching_mlx(self):
        # A fast download failure (offline, no cache) must surface as a clear
        # RuntimeError. It must NOT fall through to mlx-whisper with the repo
        # id: mlx-whisper's loader would then retry the download online itself.
        def offline(*args, **kwargs):
            raise OSError("offline")

        warmups = []
        mods = _fake_mlx_modules(lambda repo, dtype: warmups.append(repo),
                                 download_fn=offline)
        with mock.patch.dict(sys.modules, mods):
            with self.assertRaises(RuntimeError) as ctx:
                asr.load_model("small.en", timeout=5)
        self.assertIn("small.en", str(ctx.exception))
        self.assertIn("offline", str(ctx.exception))
        self.assertEqual(warmups, [])

    def test_download_worker_is_daemon_thread(self):
        # A-F7: after a timeout the stalled download keeps running; it must be
        # a daemon thread so it cannot block interpreter exit (a non-daemon
        # ThreadPoolExecutor worker is joined at exit, hanging the process).
        import threading
        release = threading.Event()
        seen = []

        def stalled(*args, **kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not cached")
            seen.append(threading.current_thread())
            release.wait(5)

        mods = _fake_mlx_modules(lambda *a: None, download_fn=stalled)
        try:
            with mock.patch.dict(sys.modules, mods):
                with self.assertRaises(TimeoutError):
                    asr.load_model("small.en", timeout=0.05)
            self.assertEqual(len(seen), 1)
            self.assertTrue(seen[0].daemon)
        finally:
            release.set()

    def test_progressing_download_is_not_killed_by_stall_timeout(self):
        # A-F7: the timeout bounds a STALL (no bytes for `timeout` s), not the
        # total download time, so a slow-but-moving first download completes.
        base = type("tqdm", (), {
            "__init__": lambda self, *a, **k: None,
            "update": lambda self, n=1: None,
        })
        fake_utils = types.ModuleType("huggingface_hub.utils")
        fake_utils.tqdm = base

        def slow_but_moving(repo_id, **kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not cached")
            bar = kwargs["tqdm_class"](total=10)
            for _ in range(10):          # 0.3 s total, never 0.1 s idle
                time.sleep(0.03)
                bar.update(1)
            return f"/fake-hf-cache/{repo_id}"

        mods = _fake_mlx_modules(lambda *a: None, download_fn=slow_but_moving)
        mods["huggingface_hub.utils"] = fake_utils
        mods["huggingface_hub"].utils = fake_utils
        with mock.patch.dict(sys.modules, mods):
            model = asr.load_model("small.en", timeout=0.1)
        self.assertEqual(model.model_path,
                         "/fake-hf-cache/mlx-community/whisper-small.en-mlx")


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


class OfflineModelLoadTests(unittest.TestCase):
    """A-F1 (local-only): once a model is cached, loading and transcribing
    must never go online. mlx-whisper's own loader calls
    snapshot_download(repo_id=...) WITHOUT local_files_only whenever it is
    handed something that is not an existing path — i.e. an HF repo id — so
    VivoType must hand it the resolved local snapshot directory instead."""

    def _recording_hub(self, cached: bool, snapshot_dir: str):
        calls = []

        def snapshot_download(repo_id, **kwargs):
            calls.append((repo_id, kwargs.get("local_files_only", False)))
            if kwargs.get("local_files_only") and not cached:
                raise FileNotFoundError("not in cache")
            return snapshot_dir

        return calls, snapshot_download

    def _mlx_like_loader(self, snapshot_download, seen):
        # Mirrors mlx_whisper/load_models.py: a non-existent path is treated
        # as an HF repo id and fetched ONLINE (no local_files_only).
        def get_model(path_or_hf_repo, dtype):
            seen.append(path_or_hf_repo)
            if not os.path.exists(path_or_hf_repo):
                snapshot_download(repo_id=path_or_hf_repo)
        return get_model

    def _run(self, cached: bool):
        import tempfile
        with tempfile.TemporaryDirectory() as snap:
            # A real cached snapshot always holds its weights file.
            open(os.path.join(snap, "weights.safetensors"), "wb").close()
            calls, dl = self._recording_hub(cached, snap)
            warmups, transcribes = [], []

            def fake_transcribe(audio, **opts):
                path = opts["path_or_hf_repo"]
                transcribes.append(path)
                if not os.path.exists(path):
                    dl(repo_id=path)
                return {"segments": []}

            mods = _fake_mlx_modules(self._mlx_like_loader(dl, warmups),
                                     transcribe_fn=fake_transcribe,
                                     download_fn=dl)
            with mock.patch.dict(sys.modules, mods):
                model = asr.load_model("small.en", timeout=5)
                model.transcribe([0.0] * 160)
                model.transcribe([0.0] * 160)
        return snap, calls, warmups, transcribes

    def test_cached_model_never_calls_hub_online(self):
        snap, calls, _, _ = self._run(cached=True)
        online = [c for c in calls if not c[1]]
        self.assertEqual(online, [], f"online hub calls: {online}")

    def test_warmup_and_transcribe_use_same_local_path(self):
        # Same identifier in both places, so mlx-whisper's ModelHolder cache
        # hits and the first dictation doesn't reload the weights.
        snap, _, warmups, transcribes = self._run(cached=True)
        self.assertEqual(warmups, [snap])
        self.assertEqual(transcribes, [snap, snap])

    def test_first_download_is_the_only_online_call(self):
        snap, calls, warmups, transcribes = self._run(cached=False)
        online = [c for c in calls if not c[1]]
        self.assertEqual(online, [("mlx-community/whisper-small.en-mlx", False)])
        self.assertEqual(warmups, [snap])
        self.assertEqual(transcribes, [snap, snap])

    def test_mlxmodel_built_directly_resolves_local_path_offline(self):
        # Constructing MLXModel without a path (any future caller) must
        # resolve the cache offline too, never pass the repo id to mlx.
        import tempfile
        with tempfile.TemporaryDirectory() as snap:
            # A real cached snapshot always holds its weights file.
            open(os.path.join(snap, "weights.safetensors"), "wb").close()
            calls, dl = self._recording_hub(True, snap)
            warmups = []
            mods = _fake_mlx_modules(self._mlx_like_loader(dl, warmups),
                                     download_fn=dl)
            with mock.patch.dict(sys.modules, mods):
                model = asr.MLXModel("small.en")
        self.assertEqual(model.model_path, snap)
        self.assertEqual(warmups, [snap])
        self.assertEqual([c for c in calls if not c[1]], [])

    def test_mlxmodel_not_cached_raises_instead_of_going_online(self):
        calls, dl = self._recording_hub(False, "/unused")
        warmups = []
        mods = _fake_mlx_modules(self._mlx_like_loader(dl, warmups),
                                 download_fn=dl)
        with mock.patch.dict(sys.modules, mods):
            with self.assertRaises(RuntimeError) as ctx:
                asr.MLXModel("small.en")
        self.assertIn("small.en", str(ctx.exception))
        self.assertEqual(warmups, [])
        self.assertEqual([c for c in calls if not c[1]], [])


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


class PromptPrimerAndEchoTests(unittest.TestCase):
    def test_primer_constant(self):
        self.assertEqual(asr.PRIMER, "Hello, welcome to my lecture.")

    def test_compose_initial_prompt_with_hint(self):
        self.assertEqual(
            asr.compose_initial_prompt("Kalpit,Bengaluru"),
            "Hello, welcome to my lecture. Kalpit,Bengaluru",
        )

    def test_compose_initial_prompt_empty(self):
        self.assertEqual(asr.compose_initial_prompt(""), asr.PRIMER)
        self.assertEqual(asr.compose_initial_prompt(None), asr.PRIMER)
        self.assertEqual(asr.compose_initial_prompt("   "), asr.PRIMER)

    def test_compose_initial_prompt_already_primed(self):
        already = "Hello, welcome to my lecture. Kalpit"
        self.assertEqual(asr.compose_initial_prompt(already), already)

    def test_is_prompt_echo_primer_only(self):
        prompt = asr.compose_initial_prompt("VivoType, menu")
        self.assertTrue(asr.is_prompt_echo("Hello, welcome to my lecture.", prompt))
        self.assertTrue(asr.is_prompt_echo("hello welcome to my lecture", prompt))

    def test_is_prompt_echo_primer_repeated(self):
        prompt = asr.compose_initial_prompt("VivoType, menu")
        self.assertTrue(
            asr.is_prompt_echo(
                "Hello, welcome to my lecture. Hello, welcome to my lecture.", prompt
            )
        )

    def test_is_prompt_echo_vocab_only(self):
        prompt = asr.compose_initial_prompt("VivoType, menu")
        self.assertTrue(asr.is_prompt_echo("VivoType, menu", prompt))
        self.assertTrue(asr.is_prompt_echo("VivoType, menu VivoType, menu", prompt))

    def test_is_prompt_echo_full_composed(self):
        prompt = asr.compose_initial_prompt("VivoType, menu")
        self.assertTrue(asr.is_prompt_echo(prompt, prompt))

    def test_is_prompt_echo_real_speech_kept(self):
        prompt = asr.compose_initial_prompt("VivoType, menu")
        self.assertFalse(
            asr.is_prompt_echo("Open the menu in VivoType please", prompt)
        )
        self.assertFalse(
            asr.is_prompt_echo("Hello, welcome to my lecture. Today we discuss biology.", prompt)
        )
        self.assertFalse(asr.is_prompt_echo("Hello", prompt))
        self.assertFalse(asr.is_prompt_echo("VivoType", prompt))

    def test_is_prompt_echo_empty(self):
        self.assertFalse(asr.is_prompt_echo("", "Hello, welcome to my lecture."))
        self.assertTrue(asr.is_prompt_echo("Hello, welcome to my lecture.", ""))
        self.assertFalse(asr.is_prompt_echo("Hello", ""))


class IncompleteSnapshotTests(unittest.TestCase):
    """A download killed mid-way leaves config.json but no weights in the
    snapshot folder. That must not count as cached, or the app never shows
    "downloading" and fails to load on every launch."""

    def _hub(self, snapshot, calls):
        def download(repo_id, **kwargs):
            calls.append(kwargs.get("local_files_only", False))
            if not kwargs.get("local_files_only"):
                (snapshot / "weights.safetensors").write_bytes(b"w")  # the repair
            return str(snapshot)
        return _fake_mlx_modules(lambda *a: None, download_fn=download)

    def test_snapshot_without_weights_is_not_cached_and_is_repaired(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            snapshot = Path(d) / "snapshots" / "abc"
            snapshot.mkdir(parents=True)
            (snapshot / "config.json").write_text("{}")
            calls = []
            with mock.patch.dict(sys.modules, self._hub(snapshot, calls)):
                self.assertFalse(asr.is_model_cached("small.en"))
                with self.assertRaises(RuntimeError):
                    asr.local_model_path("small.en")
                self.assertEqual(asr._ensure_downloaded("small.en"), str(snapshot))
                self.assertIn(False, calls)  # went online to fill the gap
                self.assertTrue(asr.is_model_cached("small.en"))


class TelemetryTests(unittest.TestCase):
    def test_hugging_face_telemetry_is_off(self):
        # Local-only: the one-time model download must not also send
        # Hugging Face usage telemetry. setdefault, so a user's own
        # environment still wins.
        import subprocess
        code = "import os, core.asr; print(os.environ.get('HF_HUB_DISABLE_TELEMETRY'))"
        env = {k: v for k, v in os.environ.items() if k != "HF_HUB_DISABLE_TELEMETRY"}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, check=True).stdout.strip()
        self.assertEqual(out, "1")


if __name__ == "__main__":
    unittest.main()
