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
    {"id":<int>,"wav":"<path>","initial_prompt":"<str>","raw":<bool>,
     "profile":"<name>",          # optional; selects post-processing rules
                                  # from postprocess_config.json 'profiles'
                                  # (per-app contexts). Missing/unknown ->
                                  # default rules.
     "voice_commands":<bool>}     # optional; true enables spoken commands
                                  # ("scratch that", "new line", ...).
                                  # Missing/anything but true -> off: every
                                  # phrase is typed as words.

  Transcription response (daemon → app):
    {"id":<int>,"text":"<str>","command":"scratch_that"|null}
    {"id":<int>,"error":"<str>"}
    # "command" is present (null) on every normal reply; only the exact
    # string "scratch_that" ever appears — it asks the client to delete its
    # previously injected text (see core/commands.py). Error replies carry
    # no command key.

  Control (app → daemon):
    {"cmd":"reload","model":"<name>"}   # switch model; daemon re-emits loading then
                                        # ready. If the new model fails to load it
                                        # emits error then ready for the OLD model —
                                        # the old model stays hot, never bricking.
                                        # A reload to the model already loaded
                                        # (or with no model) emits just ready.
    {"cmd":"shutdown"}                  # clean exit

The daemon also exits cleanly on stdin EOF (app died/crashed).
"""
from __future__ import annotations

import gc
import json
import logging
import os
import sys
import warnings
from pathlib import Path

# Silence benign huggingface / numpy noise — app drains stderr safely.
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=RuntimeWarning)

try:
    from core.audioio import EmptyAudioError, has_speech, load_wav
    from core.postprocess import DEFAULT_CONFIG, config_mtime, load_config, postprocess
    from core.commands import apply_transform, detect_command
    from core.config import load_settings
    from core import asr
    from core.asr import compose_initial_prompt, is_prompt_echo, speech_segments
except ImportError:
    from audioio import EmptyAudioError, has_speech, load_wav
    from postprocess import DEFAULT_CONFIG, config_mtime, load_config, postprocess
    from commands import apply_transform, detect_command
    from config import load_settings
    import asr
    from asr import compose_initial_prompt, is_prompt_echo, speech_segments


def _emit(obj: dict) -> None:
    """Write one JSON line to stdout, flushed immediately."""
    print(json.dumps(obj, ensure_ascii=False), flush=True)


# A path that can never exist (a child of a character device), used to load the
# shipped post-processing rules WITHOUT the user's dictionary overlay.
_NO_OVERLAY = Path(os.devnull) / "no-user-dictionary.json"


def _fallback_config() -> dict:
    """Rules to serve with when the user's config raises on load.

    The shipped config without the personal overlay first; if even that
    raises, the built-in defaults. Dictation must keep working either way.
    """
    try:
        return load_config(user_path=_NO_OVERLAY)
    except Exception:
        return {
            "fillers": list(DEFAULT_CONFIG["fillers"]),
            "replacements": dict(DEFAULT_CONFIG["replacements"]),
            "profiles": {},
        }


def _is_model_cached(model_name: str) -> bool:
    """Return True if the model files are already in the local cache."""
    return asr.is_model_cached(model_name)


def _load_model(model_name: str):
    """Load and warm the MLX Whisper model (runs on the Apple Silicon GPU)."""
    return asr.load_model(model_name)


def _transcribe(model, wav_path: str, initial_prompt: str, raw: bool,
                pp_config: dict, profile: str = "default",
                voice_commands: bool = False) -> dict:
    """Transcribe one WAV; return {"text": ...} or {"error": ...}."""
    try:
        audio = load_wav(wav_path)
    except EmptyAudioError:
        # A zero-frame clip is silence, not a failure: an error reply would
        # count toward the client's hang-restart budget.
        return {"text": "", "command": None}
    except Exception as exc:
        return {"error": f"load_wav: {exc}"}

    # Silence gate: never hand a silent clip to Whisper — on silence it
    # hallucinates (echoes the initial_prompt, loops a word). Raw mode skips
    # the gate so it stays a true diagnostic window into the model.
    if not raw and not has_speech(audio):
        return {"text": "", "command": None}

    prompt = compose_initial_prompt(initial_prompt)
    try:
        kwargs: dict = {}
        if prompt:
            kwargs["initial_prompt"] = prompt
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
        return {"text": "\n".join(lines), "command": None}

    # Drop non-speech segments (silence echoes the initial_prompt) before joining.
    # Skip empty strips so a blank segment cannot invent a double space before
    # postprocess (or leave one when --no-clean / postprocess fails).
    text = " ".join(
        t for s in speech_segments(segs) if (t := s.text.strip())
    ).strip()

    # Command detection runs on RAW ASR text BEFORE cleanup: promoted-name
    # replacements or capitalization could rewrite a literal phrase otherwise
    # ("cap that" with a cap->Capitol rule). Configured leading fillers are
    # stripped for scratch matching — Whisper habitually prefixes "um", and a
    # filler must not turn a spoken command into typed text. A structural
    # scratch returns immediately — there is nothing to clean or inject. The
    # commands module is wrapped like every other stage: a raise must never
    # kill the daemon, it just degrades to plain dictation.
    # Voice commands are opt-in (Settings): off, every phrase is words.
    try:
        signal, remaining, transform = (
            detect_command(text, leading_fillers=pp_config.get("fillers"))
            if voice_commands else (None, text, "none"))
    except Exception as exc:
        print(
            f"VivoType: command detection failed ({exc}); treating as dictation",
            file=sys.stderr,
        )
        signal, remaining, transform = None, text, "none"
    if signal == "scratch_that":
        return {"text": "", "command": "scratch_that"}

    # Postprocess BEFORE the echo guard: Whisper often prefixes fillers
    # ("um VivoType, menu"), which would fail a raw-token echo check and then
    # become a clean prompt insertion after filler stripping.
    raw_asr = remaining
    try:
        text = postprocess(remaining, pp_config, profile=profile)
    except Exception as exc:
        # Keep the transcription — a bad regex / overlay must not drop speech.
        # Transform is skipped too: applying it to unclean raw text is worse
        # than returning it as dictated (keep-raw precedent).
        print(
            f"VivoType: postprocess failed ({exc}); returning raw ASR text",
            file=sys.stderr,
        )
        text = raw_asr
    else:
        try:
            text = apply_transform(text, transform)
        except Exception as exc:
            print(
                f"VivoType: command transform failed ({exc}); returning cleaned text",
                file=sys.stderr,
            )
    # The echo guard runs on BOTH paths — a failed cleanup must not turn into
    # a prompt-echo injection either.
    if is_prompt_echo(text, prompt):
        return {"text": "", "command": None}
    return {"text": text, "command": None}


def main() -> None:
    try:
        settings = load_settings()
        model_name: str = settings.get("model", "small.en")
        pp_mtime = config_mtime()  # baseline for live dictionary/filler reloads
        failed_reload_mtime = None  # last mtime whose reload raised (warn-once)
        try:
            pp_config = load_config()
        except Exception as exc:
            # A wrong-typed user dictionary (e.g. {"fillers": 5}) must not stop
            # dictation: serve the shipped rules; an edit to the file (new
            # mtime) is retried by the request loop below.
            failed_reload_mtime = pp_mtime
            print(
                f"VivoType: config load failed ({exc}); using the shipped rules.",
                file=sys.stderr,
            )
            pp_config = _fallback_config()
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
            if not isinstance(new_model, str) or not new_model:
                _emit({"status": "error",
                       "error": f"reload: invalid model name {new_model!r}"})
                _emit({"status": "ready", "model": model_name})
                continue
            if new_model == model_name:
                # Nothing to load, but the client flipped isReady off when it
                # sent the reload and waits for this line.
                _emit({"status": "ready", "model": model_name})
                continue
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
            # Same guard as the request path: a raising config keeps the
            # last-good rules and must not kill the daemon mid-reload.
            current_mtime = config_mtime()
            try:
                pp_config = load_config()
                pp_mtime = current_mtime
                failed_reload_mtime = None
            except Exception as exc:
                if current_mtime != failed_reload_mtime:
                    failed_reload_mtime = current_mtime
                    print(
                        f"VivoType: config reload failed ({exc}); keeping previous rules.",
                        file=sys.stderr,
                    )
            _emit({"status": "ready", "model": model_name})
            continue

        req_id = cmd.get("id")
        wav = cmd.get("wav", "")
        if not isinstance(wav, str):
            _emit({"error": f"invalid 'wav' field: {wav!r}", "id": req_id})
            continue
        # A wrong-typed prompt is only a vocabulary hint: drop it (the primer
        # still applies) rather than fail the dictation.
        initial_prompt = cmd.get("initial_prompt", "")
        if not isinstance(initial_prompt, str):
            initial_prompt = ""
        raw = bool(cmd.get("raw", False))
        # A non-string profile (or an empty one) must degrade to the default
        # rules, never skip cleanup: resolve_profile would treat junk as an
        # unknown name anyway, but normalizing here keeps the contract explicit.
        profile = cmd.get("profile", "default")
        if not isinstance(profile, str) or not profile:
            profile = "default"
        # Strictly true: a junk value must never turn commands on.
        voice_commands = cmd.get("voice_commands") is True

        # Pick up dictionary/filler/profile edits (e.g. a promoted term) without
        # a restart. A reload that RAISES (e.g. a wrong-typed field that slips
        # past load_config's own guards) must not kill the daemon mid-request:
        # keep serving the last-good rules and warn once per distinct mtime, so
        # a left-corrupt file doesn't spam stderr on every dictation.
        current_mtime = config_mtime()
        if current_mtime != pp_mtime:
            try:
                new_config = load_config()
                pp_config = new_config
                pp_mtime = current_mtime
                failed_reload_mtime = None  # re-arm warn-once for future failures
            except Exception as exc:
                if current_mtime != failed_reload_mtime:
                    failed_reload_mtime = current_mtime
                    print(
                        f"VivoType: config reload failed ({exc}); keeping previous rules.",
                        file=sys.stderr,
                    )

        if model is None:
            result = {"error": "ASR model is not loaded."}
        else:
            # One bad request must never take the daemon (and every later
            # dictation) down with it: an unexpected raise becomes an error
            # reply for this id only.
            try:
                result = _transcribe(model, wav, initial_prompt, raw, pp_config, profile,
                                     voice_commands)
            except Exception as exc:
                print(f"VivoType: request {req_id!r} failed ({exc!r})", file=sys.stderr)
                result = {"error": f"internal: {exc}"}
        result["id"] = req_id
        _emit(result)
    # stdin EOF → clean exit (app died or sent shutdown)


if __name__ == "__main__":
    main()
