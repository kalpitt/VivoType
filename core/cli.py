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
    from core.config import load_settings
    from core.audioio import load_wav
    from core import asr
    from core.asr import speech_segments
except ImportError:
    from postprocess import load_config, postprocess
    from config import load_settings
    from audioio import load_wav
    import asr
    from asr import speech_segments


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


def transcribe(audio, model_name: str):
    """Run MLX Whisper on the Apple Silicon GPU and return the segment list."""
    model = asr.load_model(model_name)
    segments, _info = model.transcribe(audio)
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
    except Exception as exc:  # surface a clean message to stderr, never stdout
        _eprint(f"Error: could not read audio '{audio_path}': {exc}")
        return 1

    model_name = args.model or load_settings().get("model", "small.en")
    try:
        segments = transcribe(audio, model_name)
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
        text = " ".join(seg.text.strip() for seg in speech_segments(segments)).strip()
        if not args.no_clean:
            try:
                text = postprocess(text, load_config(args.config))
            except Exception as exc:
                _eprint(f"Error: post-processing failed: {exc}")
                return 1
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
