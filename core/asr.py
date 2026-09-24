#!/usr/bin/env python3
"""VivoType ASR backend — Apple MLX (mlx-whisper) transcription.

Plain English: this is the one place that turns recorded audio into text using
Apple's on-device GPU/Neural Engine, instead of running Whisper on the CPU.

Single source of truth for turning a 16 kHz mono float32 audio array into
Whisper segments. Both the persistent daemon (core/daemon.py) and the one-shot
CLI (core/cli.py) go through here, so model resolution, the model cache
location, and the returned segment shape stay identical across entry points.

Models are pulled from the ``mlx-community/whisper-<name>-mlx`` repos on the
Hugging Face Hub and cached under VivoType's writable models dir (via ``HF_HOME``),
so the read-only ``.app`` bundle is never written to (see ADR-0003).
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import re
import sys
import threading
import time

try:  # sibling module; works whether imported as a package or a loose script
    from core.paths import models_dir
except ImportError:
    from paths import models_dir

# Point the Hugging Face cache at VivoType's writable models dir BEFORE
# huggingface_hub is imported anywhere, so downloaded weights land outside the
# read-only app bundle (ADR-0003). An explicit HF_HOME already in the
# environment always wins.
os.environ.setdefault("HF_HOME", str(models_dir()))
# Local-only: the one-time model download must not also report usage
# telemetry to Hugging Face.
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# mlx-whisper publishes one repo per Whisper size, e.g.
#   small.en  ->  mlx-community/whisper-small.en-mlx
_REPO_TEMPLATE = "mlx-community/whisper-{name}-mlx"


def repo_for(model_name: str) -> str:
    """Map a Whisper size (e.g. ``small.en``) to its mlx-community HF repo id."""
    return _REPO_TEMPLATE.format(name=model_name)


def clear_gpu_cache() -> None:
    """Release MLX's cached-but-unused Metal GPU buffers back to the OS.

    mlx-whisper's ModelHolder (see MLXModel.__init__) replaces its one cached
    model in place when a different repo is requested, so the old weights are
    already unreferenced by the time a reload finishes — but MLX's own
    allocator keeps freed GPU buffers around for reuse rather than returning
    them immediately. Without this, switching models repeatedly (Settings ->
    Model submenu) grows resident GPU memory monotonically.
    """
    try:
        import mlx.core as mx
        mx.clear_cache()
    except Exception:
        pass  # best-effort; older/newer mlx releases may not expose this


# The files mlx-whisper loads a model's weights from (either one).
_WEIGHT_FILES = ("weights.safetensors", "weights.npz")


def _cached_snapshot(repo: str) -> str:
    """The cached snapshot directory for `repo`, strictly offline. Raises when
    it is missing OR incomplete: a download killed mid-way leaves the folder
    with config.json but no weights, and treating that as cached would fail
    every load forever instead of letting the download finish."""
    from huggingface_hub import snapshot_download
    path = snapshot_download(repo, local_files_only=True)
    folder = Path(path)
    if folder.is_dir() and not any((folder / name).is_file() for name in _WEIGHT_FILES):
        raise FileNotFoundError(f"incomplete snapshot (no weights file) at {path}")
    return path


def local_model_path(model_name: str) -> str:
    """Return the cached model's local snapshot directory, strictly offline.

    Raises RuntimeError when the model is not cached. mlx-whisper must always
    be handed this path, never the HF repo id: its loader treats anything that
    is not an existing path as a repo id and calls ``snapshot_download``
    WITHOUT ``local_files_only`` (a network round-trip to huggingface.co on
    every load, which breaks the local-only rule).
    """
    repo = repo_for(model_name)
    try:
        return _cached_snapshot(repo)
    except Exception as exc:
        raise RuntimeError(
            f"failed to load Whisper model '{model_name}' ({repo}): "
            f"not in the local model cache ({exc})"
        ) from exc


def is_model_cached(model_name: str) -> bool:
    """Return True if the MLX model is already in the local cache (no download)."""
    try:
        _cached_snapshot(repo_for(model_name))
        return True
    except Exception:
        return False


# Whisper reports a per-segment probability that the audio is NOT speech. With an
# initial_prompt, silence makes the model echo the prompt back (e.g. a contact
# name) as a confident "segment", so the usual logprob check doesn't catch it —
# but no_speech_prob stays high. Drop anything at or above this.
NO_SPEECH_THRESHOLD = 0.6


class _Segment:
    """A lightweight Whisper segment with attribute access (start/end/text)."""

    __slots__ = ("start", "end", "text", "avg_logprob", "no_speech_prob")

    def __init__(self, start: float, end: float, text: str, avg_logprob: float,
                 no_speech_prob: float = 0.0):
        self.start = start
        self.end = end
        self.text = text
        self.avg_logprob = avg_logprob
        self.no_speech_prob = no_speech_prob


def speech_segments(segments, threshold: float = NO_SPEECH_THRESHOLD):
    """Keep only segments Whisper considers speech.

    Filters out silence/non-speech segments — the place initial_prompt
    hallucinations appear — so a clip where the user said nothing inserts nothing
    instead of echoing their personal vocabulary.
    """
    return [s for s in segments if getattr(s, "no_speech_prob", 0.0) < threshold]


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

PRIMER = "Hello, welcome to my lecture."
_PRIMER_TOKENS = tuple(_TOKEN_RE.findall(PRIMER.lower()))


def compose_initial_prompt(vocabulary_hint: str = "") -> str:
    """Compose the initial_prompt by prepending a punctuated primer.

    Whisper mirrors the punctuation and style of initial_prompt. Prepending a
    punctuated conversational sentence primes Whisper to output proper
    capitalization, commas, and sentence breaks rather than unpunctuated text.
    When vocabulary_hint is provided, the primer is prepended to it.
    When vocabulary_hint is empty, the primer alone is returned so punctuation
    is still primed.
    """
    hint = (vocabulary_hint or "").strip()
    if not hint:
        return PRIMER
    if hint.startswith(PRIMER):
        return hint
    return f"{PRIMER} {hint}"


def _is_token_repetition(text_tokens: list[str], target_tokens: tuple[str, ...] | list[str]) -> bool:
    if not text_tokens or not target_tokens:
        return False
    if len(text_tokens) % len(target_tokens) != 0:
        return False
    repeats = len(text_tokens) // len(target_tokens)
    return text_tokens == list(target_tokens) * repeats


def is_prompt_echo(text: str, prompt: str) -> bool:
    """True when the transcript is nothing but the prompt (or primer) echoed back.

    On silence/noise Whisper sometimes returns its vocabulary hint verbatim
    with a LOW no_speech_prob, sailing past speech_segments(). Comparing
    normalized token sequences catches that:
    - the primer alone echoed (when prompt is primer + vocabulary)
    - the full prompt echoed (or repeated N times)
    - the vocabulary alone echoed (the remainder after the primer)

    Real dictation that merely *contains* prompt terms is left untouched.
    Shared by the daemon and the one-shot CLI so both paths drop the same
    hallucination.
    """
    if not text:
        return False
    text_tokens = _TOKEN_RE.findall(text.lower())
    if not text_tokens:
        return False

    if _is_token_repetition(text_tokens, _PRIMER_TOKENS):
        return True

    if not prompt:
        return False

    prompt_tokens = _TOKEN_RE.findall(prompt.lower())
    if not prompt_tokens:
        return False

    if _is_token_repetition(text_tokens, prompt_tokens):
        return True

    if (
        len(prompt_tokens) > len(_PRIMER_TOKENS)
        and prompt_tokens[:len(_PRIMER_TOKENS)] == list(_PRIMER_TOKENS)
    ):
        vocab_tokens = prompt_tokens[len(_PRIMER_TOKENS):]
        if _is_token_repetition(text_tokens, vocab_tokens):
            return True

    return False


class MLXModel:
    """A warm, reusable MLX Whisper model.

    Loading the weights once and reusing them keeps every dictation fast — the
    whole point of the persistent daemon (ADR-0002). mlx-whisper caches the
    loaded model on a process-wide ``ModelHolder`` (keyed by model path), so
    once it is warm, repeated transcribe() calls reuse weights already
    resident in GPU memory rather than reloading them from disk.

    ``model_path`` is the local snapshot directory; when omitted it is
    resolved from the cache offline (see local_model_path).
    """

    def __init__(self, model_name: str, model_path: str | None = None):
        self.model_name = model_name
        self.repo = repo_for(model_name)
        # The ONE identifier handed to mlx-whisper, for warm-up and transcribe
        # alike, so its ModelHolder cache hits (see local_model_path for why
        # it must be a local path and not self.repo).
        self.model_path = model_path or local_model_path(model_name)
        # Warm the model NOW so the daemon only reports "ready" once weights are
        # actually resident in memory (and the one-time download has happened).
        # We must prime mlx-whisper's own ModelHolder cache with the same
        # float16 dtype transcribe() uses by default (fp16=True); calling
        # load_model() directly would build a model the cache never sees, so the
        # first dictation would reload from disk and negate the warm-up.
        try:
            import mlx.core as mx
            from mlx_whisper.transcribe import ModelHolder
        except Exception:
            # mlx-whisper internals moved — fall back to lazy loading on the
            # first transcribe() call (correct, just not pre-warmed).
            return
        try:
            with contextlib.redirect_stdout(sys.stderr):
                ModelHolder.get_model(self.model_path, mx.float16)
        except Exception as exc:
            # A real load failure (no network on first run, partial/corrupt
            # download, unknown model) must propagate so the daemon emits an
            # NDJSON `error` event instead of a false `ready` — otherwise the
            # app advances to Success and every dictation silently fails.
            raise RuntimeError(
                f"failed to load Whisper model '{model_name}' ({self.repo}): {exc}"
            ) from exc

    def transcribe(self, audio, *, initial_prompt: str | None = None, **_ignored):
        """Transcribe a 16 kHz mono float32 array.

        Returns ``(segments, info)``: ``segments`` is a list of :class:`_Segment`
        and ``info`` is mlx-whisper's raw result dict. Extra keyword arguments
        (e.g. a legacy ``beam_size``) are ignored, since MLX uses
        temperature-fallback decoding rather than beam search.
        """
        import mlx_whisper

        opts: dict = {
            "path_or_hf_repo": self.model_path,
            # stdout is the daemon's NDJSON channel (and the CLI's typed text)
            # — mlx-whisper must never print to it. Only None is fully quiet:
            # verbose=False still prints "Detected language: X" for
            # multilingual models and turns on a tqdm progress bar.
            "verbose": None,
            # Don't feed each segment's text back as context for the next:
            # on noisy/marginal audio that feedback loop is what turns one
            # misheard word into "guilty guilty guilty …". Dictation clips are
            # single short utterances, so cross-segment context adds nothing.
            "condition_on_previous_text": False,
        }
        if initial_prompt:
            opts["initial_prompt"] = initial_prompt

        # Belt and braces: anything mlx-whisper prints goes to stderr.
        with contextlib.redirect_stdout(sys.stderr):
            result = mlx_whisper.transcribe(audio, **opts)
        segments = [
            _Segment(
                start=float(s.get("start", 0.0)),
                end=float(s.get("end", 0.0)),
                text=s.get("text", ""),
                avg_logprob=float(s.get("avg_logprob", 0.0)),
                no_speech_prob=float(s.get("no_speech_prob", 0.0)),
            )
            for s in result.get("segments", [])
        ]
        return segments, result


# A stalled connection during the one-time model download (~466 MB for
# small.en) must not hang the daemon forever with "Downloading model…" spinning
# with no way out. This bounds a STALL: the download fails once no bytes have
# arrived for this long. A slow-but-moving download is left to finish.
MODEL_LOAD_TIMEOUT = 180  # seconds without download progress


def _progress_tqdm_class(on_progress):
    """A huggingface_hub progress-bar class that calls on_progress() on every
    update, or None when the hub's tqdm is unavailable (the stall timer then
    simply never resets, i.e. it caps total time as before)."""
    try:
        from huggingface_hub.utils import tqdm as hf_tqdm
    except Exception:
        return None

    class _ProgressTqdm(hf_tqdm):
        def update(self, n=1):
            on_progress()
            return super().update(n)

    return _ProgressTqdm


def _ensure_downloaded(model_name: str, on_progress=None) -> str:
    """Fetch the model files if missing; return the local snapshot directory.

    Network-bound and GPU-free, so safe to run on a worker thread: it never
    touches Metal. The local (offline) check runs first so a cached model
    boots without any network.
    """
    from huggingface_hub import snapshot_download
    repo = repo_for(model_name)
    try:
        return _cached_snapshot(repo)  # already cached and complete
    except Exception:
        pass  # missing or incomplete: the online call fetches what's missing
    kwargs: dict = {}
    tqdm_class = _progress_tqdm_class(on_progress) if on_progress else None
    if tqdm_class is not None:
        kwargs["tqdm_class"] = tqdm_class
    return snapshot_download(repo, **kwargs)


def load_model(model_name: str, timeout: float = MODEL_LOAD_TIMEOUT) -> MLXModel:
    """Build (and warm) an MLX Whisper model for the given Whisper size.

    Only the download runs on a worker thread (failing after `timeout`
    seconds without progress); the Metal load/warm-up MUST happen on the
    calling thread. MLX GPU streams are thread-local — a model warmed on a
    worker thread makes transcribe() on the calling thread fail with "There
    is no Stream(gpu, N) in current thread". (Field-verified on Apple
    Silicon; the previous version warmed on the worker and broke every
    transcription.)
    """
    last_progress = [time.monotonic()]
    outcome: dict = {}
    done = threading.Event()

    def on_progress():
        last_progress[0] = time.monotonic()

    def worker():
        try:
            outcome["path"] = _ensure_downloaded(model_name, on_progress)
        except BaseException as exc:  # reported to the caller below
            outcome["error"] = exc
        finally:
            done.set()

    # A daemon thread: after a stall timeout the download may still be
    # blocked in a socket read, and a non-daemon worker would be joined at
    # interpreter exit, hanging the process that just reported the error.
    threading.Thread(target=worker, name=f"vivotype-download-{model_name}",
                     daemon=True).start()
    while not done.wait(min(1.0, timeout)):
        if time.monotonic() - last_progress[0] >= timeout:
            raise TimeoutError(
                f"downloading model '{model_name}' timed out: no progress for "
                f"{int(timeout)}s (stalled download or network issue)"
            )
    if "error" in outcome:
        # Offline with no cache, or the download failed. Never fall through
        # to mlx-whisper with the repo id: its loader would retry online.
        exc = outcome["error"]
        raise RuntimeError(
            f"failed to load Whisper model '{model_name}' "
            f"({repo_for(model_name)}): download failed ({exc})"
        ) from exc
    return MLXModel(model_name, model_path=outcome.get("path"))
