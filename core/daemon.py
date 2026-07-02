#!/usr/bin/env python3
"""VivoType persistent transcription daemon.

Spawned once by the macOS app; keeps the MLX Whisper model warm in (GPU) memory
so every dictation is sub-second (no per-call reload).

Protocol — NDJSON over stdin/stdout:

  Boot (daemon → app):
    {"status":"loading"}
    {"status":"downloading","model":"<name>","progress":null}   # if not cached
    {"status":"ready","model":"<name>"}
    {"status":"error","error":"<message>"}

  Transcription request (app → daemon):
    {"id":<int>,"wav":"<path>","initial_prompt":"<str>","raw":<bool>}

  Transcription response (daemon → app):
    {"id":<int>,"text":"<str>"}
    {"id":<int>,"error":"<str>"}

  Control (app → daemon):
    {"cmd":"reload","model":"<name>"}   # switch model; daemon re-emits loading then
                                        # ready. If the new model fails to load it
                                        # emits error then ready for the OLD model —
                                        # the old model stays hot, never bricking.
    {"cmd":"shutdown"}                  # clean exit

The daemon also exits cleanly on stdin EOF (app died/crashed).
"""
from __future__ import annotations

import gc
import json
import logging
import sys
import warnings
from pathlib import Path

# Silence benign huggingface / numpy noise — app drains stderr safely.
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=RuntimeWarning)

try:
    from core.audioio import load_wav
    from core.postprocess import config_mtime, load_config, postprocess
    from core.config import load_settings
    from core import asr
    from core.asr import speech_segments
except ImportError:
    from audioio import load_wav
    from postprocess import config_mtime, load_config, postprocess
    from config import load_settings
    import asr
    from asr import speech_segments


def _emit(obj: dict) -> None:
    """Write one JSON line to stdout, flushed immediately."""
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _is_model_cached(model_name: str) -> bool:
    """Return True if the model files are already in the local cache."""
    return asr.is_model_cached(model_name)


def _load_model(model_name: str):
    """Load and warm the MLX Whisper model (runs on the Apple Silicon GPU)."""
    return asr.load_model(model_name)


def _transcribe(model, wav_path: str, initial_prompt: str, raw: bool,
                pp_config: dict) -> dict:
    """Transcribe one WAV; return {"text": ...} or {"error": ...}."""
    try:
        audio = load_wav(wav_path)
    except Exception as exc:
        return {"error": f"load_wav: {exc}"}

    try:
        kwargs: dict = {}
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        segments, _ = model.transcribe(audio, **kwargs)
        segs = list(segments)  # normalise to a list (mirrors the old API)
    except Exception as exc:
        return {"error": f"transcribe: {exc}"}
    finally:
        gc.collect()  # belt-and-suspenders; MLX frees its own GPU allocs

    if raw:
        lines = [
            json.dumps(
                {
                    "start": round(s.start, 3),
                    "end": round(s.end, 3),
                    "text": s.text,
                    "avg_logprob": s.avg_logprob,
                },
                ensure_ascii=False,
            )
            for s in segs
        ]
        return {"text": "\n".join(lines)}

    # Drop non-speech segments (silence echoes the initial_prompt) before joining.
    text = " ".join(s.text.strip() for s in speech_segments(segs)).strip()
    try:
        text = postprocess(text, pp_config)
    except Exception:
        pass  # post-processing failure is non-fatal; return raw ASR text
    return {"text": text}


def main() -> None:
    try:
        settings = load_settings()
        model_name: str = settings.get("model", "small.en")
        pp_config = load_config()
        pp_mtime = config_mtime()  # baseline for live dictionary/filler reloads
    except Exception as exc:
        # A corrupt config.json / postprocess config must not kill the daemon
        # with a bare traceback on stderr — emit a proper NDJSON error so the
        # Swift client sees *why* and falls back to the CLI, instead of just
        # "daemon terminated" with no cause (stderr is captured to daemon.log,
        # but the app shouldn't need to go spelunking for a config typo).
        _emit({"status": "error", "error": f"startup: {exc}"})
        sys.exit(1)

    _emit({"status": "loading"})

    if not _is_model_cached(model_name):
        _emit({"status": "downloading", "model": model_name, "progress": None})

    try:
        model = _load_model(model_name)
    except Exception as exc:
        _emit({"status": "error", "error": str(exc)})
        sys.exit(1)

    _emit({"status": "ready", "model": model_name})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue  # ignore malformed lines
        if not isinstance(cmd, dict):
            continue  # ignore non-object JSON values

        if cmd.get("cmd") == "shutdown":
            break

        if cmd.get("cmd") == "reload":
            new_model = cmd.get("model", model_name)
            if new_model != model_name:
                _emit({"status": "loading"})
                # Load the new model into a temporary first; only discard the
                # currently-working model once the swap is guaranteed to succeed.
                # A failed reload (bad model name, download failure, OOM) must
                # never brick the daemon — keep serving with the old model.
                try:
                    new = _load_model(new_model)
                except Exception as exc:
                    _emit({"status": "error", "error": str(exc)})
                    _emit({"status": "ready", "model": model_name})
                    continue
                model = new
                model_name = new_model
                gc.collect()  # reclaim the now-unreferenced old model
                asr.clear_gpu_cache()  # return its Metal buffers to the OS
                pp_config = load_config()
                pp_mtime = config_mtime()
                _emit({"status": "ready", "model": model_name})
            continue

        req_id = cmd.get("id")
        wav = cmd.get("wav", "")
        initial_prompt = cmd.get("initial_prompt", "") or ""
        raw = bool(cmd.get("raw", False))

        # Pick up dictionary/filler edits (e.g. a promoted term) without a restart.
        current_mtime = config_mtime()
        if current_mtime != pp_mtime:
            pp_config = load_config()
            pp_mtime = current_mtime

        if model is None:
            result = {"error": "ASR model is not loaded."}
        else:
            result = _transcribe(model, wav, initial_prompt, raw, pp_config)
        result["id"] = req_id
        _emit(result)
    # stdin EOF → clean exit (app died or sent shutdown)


if __name__ == "__main__":
    main()
