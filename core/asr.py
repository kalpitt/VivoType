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

import os

try:  # sibling module; works whether imported as a package or a loose script
    from core.paths import models_dir
except ImportError:
    from paths import models_dir

# Point the Hugging Face cache at VivoType's writable models dir BEFORE
# huggingface_hub is imported anywhere, so downloaded weights land outside the
# read-only app bundle (ADR-0003). An explicit HF_HOME already in the
# environment always wins.
os.environ.setdefault("HF_HOME", str(models_dir()))

# mlx-whisper publishes one repo per Whisper size, e.g.
#   small.en  ->  mlx-community/whisper-small.en-mlx
_REPO_TEMPLATE = "mlx-community/whisper-{name}-mlx"


def repo_for(model_name: str) -> str:
    """Map a Whisper size (e.g. ``small.en``) to its mlx-community HF repo id."""
    return _REPO_TEMPLATE.format(name=model_name)


def is_model_cached(model_name: str) -> bool:
    """Return True if the MLX model is already in the local cache (no download)."""
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(repo_for(model_name), local_files_only=True)
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


class MLXModel:
    """A warm, reusable MLX Whisper model.

    Loading the weights once and reusing them keeps every dictation fast — the
    whole point of the persistent daemon (ADR-0002). mlx-whisper caches the
    loaded model on a process-wide ``ModelHolder`` (keyed by repo), so once it
    is warm, repeated transcribe() calls reuse weights already resident in GPU
    memory rather than reloading them from disk.
    """

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.repo = repo_for(model_name)
        # Warm the model NOW so the daemon only reports "ready" once weights are
        # actually resident in memory (and the one-time download has happened).
        # We must prime mlx-whisper's own ModelHolder cache with the same
        # float16 dtype transcribe() uses by default (fp16=True); calling
        # load_model() directly would build a model the cache never sees, so the
        # first dictation would reload from disk and negate the warm-up.
        try:
            import mlx.core as mx
            from mlx_whisper.transcribe import ModelHolder
            ModelHolder.get_model(self.repo, mx.float16)
        except Exception:
            # Unexpected mlx-whisper internals — fall back to lazy loading on
            # the first transcribe() call (correct, just not pre-warmed).
            pass

    def transcribe(self, audio, *, initial_prompt: str | None = None, **_ignored):
        """Transcribe a 16 kHz mono float32 array.

        Returns ``(segments, info)``: ``segments`` is a list of :class:`_Segment`
        and ``info`` is mlx-whisper's raw result dict. Extra keyword arguments
        (e.g. a legacy ``beam_size``) are ignored, since MLX uses
        temperature-fallback decoding rather than beam search.
        """
        import mlx_whisper

        opts: dict = {
            "path_or_hf_repo": self.repo,
            # stdout is the daemon's NDJSON channel — mlx-whisper must never
            # print progress to it.
            "verbose": False,
        }
        if initial_prompt:
            opts["initial_prompt"] = initial_prompt

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


def load_model(model_name: str) -> MLXModel:
    """Build (and warm) an MLX Whisper model for the given Whisper size."""
    return MLXModel(model_name)
