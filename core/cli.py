#!/usr/bin/env python3
"""VivoType core ASR CLI.

Transcribes an audio file to UTF-8 text on stdout using Apple MLX (mlx-whisper).

Usage:
    python core/cli.py path/to/audio.wav          # prints transcript text
    python core/cli.py path/to/audio.wav --raw    # prints per-segment JSON lines

Contract (see CLAUDE.md "Architecture Contract"):
    - Input: a path to an audio file. The expected format is mono, 16-bit PCM,
      16 kHz WAV. If the input differs, it is silently converted/resampled.
    - Normal mode: prints only the final joined transcript text to stdout.
    - --raw mode: prints one JSON object per segment to stdout, each with
      "start", "end", "text", and "avg_logprob" fields.
    - Errors: a human-readable message is written to stderr and the process
      exits with status 1. Nothing is ever written to stdout on error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from pathlib import Path

# Keep output channels clean: silence benign third-party chatter that would
# otherwise clutter stderr. These are cosmetic warnings, not errors.
#  - Hugging Face Hub's "unauthenticated requests" notice during model download.
#  - NumPy RuntimeWarnings from the mel-filter step on very quiet audio.
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# Post-processing lives in a sibling module; import it whether this file is run
# directly (python core/cli.py) or as a module (python -m core.cli).
try:
    from core.postprocess import load_config, postprocess
    from core.commands import apply_transform, detect_command
    from core.config import load_settings
    from core.audioio import EmptyAudioError, has_speech, load_wav
    from core import asr
    from core.asr import compose_initial_prompt, is_prompt_echo, speech_segments
except ImportError:
    from postprocess import load_config, postprocess
    from commands import apply_transform, detect_command
    from config import load_settings
    from audioio import EmptyAudioError, has_speech, load_wav
    import asr
    from asr import compose_initial_prompt, is_prompt_echo, speech_segments


def _eprint(message: str) -> None:
    """Write a human-readable message to stderr (never stdout)."""
    print(message, file=sys.stderr)


def load_audio(path: Path):
    """Load an audio file as a mono float32 array at 16 kHz.

    Validates the format and silently converts/resamples anything that does
    not match the expected spec (mono, 16 kHz). Returns samples as a float32
    numpy array normalized to [-1.0, 1.0], which is what mlx-whisper wants.
    """
    return load_wav(path)


def transcribe(audio, model_name: str, initial_prompt: str = ""):
    """Run MLX Whisper on the Apple Silicon GPU and return the segment list."""
    model = asr.load_model(model_name)
    kwargs: dict = {}
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    segments, _info = model.transcribe(audio, **kwargs)
    return list(segments)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vivotype",
        description="Transcribe a WAV file to text (fully local, no cloud).",
    )
    parser.add_argument("audio", help="Path to the input audio (WAV) file.")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Output one JSON object per segment (start, end, text, avg_logprob).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Whisper model name (e.g. small.en, tiny.en). Defaults to the "
             "configured model in core/config.json (else small.en). Use tiny.en "
             "for speed.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a post-processing JSON config (default: bundled config).",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Disable Indic post-processing (filler removal, dictionary, currency).",
    )
    parser.add_argument(
        "--voice-commands",
        action="store_true",
        help="Act on spoken commands (\"scratch that\", \"new line\", ...). "
             "Off by default: every phrase is typed as words. Mirrors the "
             "daemon's 'voice_commands' request field.",
    )
    parser.add_argument(
        "--profile",
        default="default",
        help="Post-processing profile name from postprocess_config.json "
             "'profiles' (per-app contexts). Unknown names fall back to the "
             "default rules. Mirrors the daemon's 'profile' request field.",
    )
    parser.add_argument(
        "--initial-prompt",
        default="",
        help="Vocabulary hint passed to Whisper (same as the daemon's "
             "initial_prompt). Also used to drop prompt-echo hallucinations.",
    )
    args = parser.parse_args(argv)

    audio_path = Path(args.audio)
    if not audio_path.exists():
        _eprint(f"Error: file not found: {audio_path}")
        return 1
    if not audio_path.is_file():
        _eprint(f"Error: not a file: {audio_path}")
        return 1

    try:
        audio = load_audio(audio_path)
    except EmptyAudioError:
        # A zero-frame recording is silence, not a failure (mirrors the daemon).
        print("")
        return 0
    except Exception as exc:  # surface a clean message to stderr, never stdout
        _eprint(f"Error: could not read audio '{audio_path}': {exc}")
        return 1

    # Silence gate: Whisper hallucinates on silence (prompt echoes, word
    # loops), so a silent clip prints nothing and exits cleanly. Raw mode
    # skips the gate so it stays a true diagnostic window into the model.
    if not args.raw and not has_speech(audio):
        print("")
        return 0

    model_name = args.model or load_settings().get("model", "small.en")
    prompt = compose_initial_prompt(args.initial_prompt)
    try:
        segments = transcribe(audio, model_name, initial_prompt=prompt)
    except Exception as exc:
        _eprint(f"Error: transcription failed: {exc}")
        return 1

    if args.raw:
        for seg in segments:
            line = json.dumps(
                {
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": seg.text,
                    "avg_logprob": seg.avg_logprob,
                },
                ensure_ascii=False,
            )
            print(line)
    else:
        # Drop non-speech segments (silence echoes the initial_prompt) first.
        text = " ".join(
            t for seg in speech_segments(segments) if (t := seg.text.strip())
        ).strip()
        # Command detection mirrors the daemon exactly: raw ASR before
        # cleanup, transforms after. One structural difference — the one-shot
        # CLI has no channel to ask Swift to delete previous dictation, so a
        # standalone "scratch that" degrades to a no-op (nothing typed)
        # instead of landing in the document as literal words.
        raw_asr = text
        pp_config = None
        if not args.no_clean:
            try:
                pp_config = load_config(args.config)
            except Exception as exc:
                _eprint(f"Warning: post-processing config failed ({exc}); returning raw ASR text")
        signal, remaining, transform = None, text, "none"
        if pp_config is not None and args.voice_commands:
            try:
                signal, remaining, transform = detect_command(
                    remaining, leading_fillers=pp_config.get("fillers"))
            except Exception as exc:
                _eprint(f"Warning: command detection failed ({exc}); treating as dictation")
        if signal == "scratch_that":
            _eprint("Warning: 'scratch that' over the standalone CLI cannot "
                    "delete earlier dictation; ignored.")
            print("")
            return 0
        text = remaining
        if pp_config is not None:
            try:
                text = postprocess(remaining, pp_config, profile=args.profile)
            except Exception as exc:
                # Match the daemon: keep the transcript, don't fail the CLI
                # fallback path on a bad dictionary rule.
                _eprint(f"Warning: post-processing failed ({exc}); returning raw ASR text")
                text = remaining
            else:
                try:
                    text = apply_transform(text, transform)
                except Exception as exc:
                    _eprint(f"Warning: command transform failed ({exc}); returning cleaned text")
        # Echo guard after cleanup so filler-prefixed echoes still drop
        # (same order the daemon reliability path aims for).
        if is_prompt_echo(text, prompt):
            text = ""
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
