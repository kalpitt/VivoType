#!/usr/bin/env python3
"""Compare ASR models on a recording against a known reference transcript.

Prints each model's transcript and Word Error Rate (WER) — the percentage of
words it got wrong — so you can see how much accuracy improves with a bigger
model, measured on YOUR own audio.

Usage:
    # point at a specific recording:
    python core/benchmark.py core/data/raw/my-clip.wav \
        --reference-file core/data/prompts/training-paragraph.txt

    # or just point at the folder and it uses your newest recording:
    python core/benchmark.py core/data/raw \
        --reference-file core/data/prompts/training-paragraph.txt

    # choose which models to compare (default: tiny.en vs small.en):
    python core/benchmark.py core/data/raw --models tiny.en,small.en \
        --reference-file core/data/prompts/training-paragraph.txt
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import warnings
from pathlib import Path

logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=RuntimeWarning)

try:  # works as "python core/benchmark.py" or "python -m core.benchmark"
    from core.cli import load_audio
    from core import asr
except ImportError:
    from cli import load_audio
    import asr


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


def normalize(text: str) -> str:
    """Lowercase and strip punctuation so comparison is about words, not commas."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word-level edit distance / reference length (Levenshtein over words)."""
    ref = normalize(reference).split()
    hyp = normalize(hypothesis).split()
    n, m = len(ref), len(hyp)
    if n == 0:
        return 1.0 if m else 0.0
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            cur = dp[j]
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = cur
    return dp[m] / n


def transcribe(audio, model_name: str) -> str:
    model = asr.load_model(model_name)
    segments, _info = model.transcribe(audio)
    return " ".join(seg.text.strip() for seg in segments).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vivotype-benchmark",
        description="Compare ASR models by accuracy/WER on your audio.",
    )
    parser.add_argument("audio", help="A .wav file, or a folder (uses the newest .wav).")
    parser.add_argument("--reference", help="The exact words that were spoken.")
    parser.add_argument("--reference-file", help="A text file with the spoken words.")
    parser.add_argument("--models", default="tiny.en,small.en",
                        help="Comma-separated model names (default: tiny.en,small.en).")
    args = parser.parse_args(argv)

    audio_path = Path(args.audio)
    if audio_path.is_dir():
        wavs = sorted(audio_path.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        if not wavs:
            _eprint(f"Error: no .wav files in {audio_path}")
            return 1
        audio_path = wavs[-1]
        print(f"Using newest recording: {audio_path.name}\n")
    elif not audio_path.is_file():
        _eprint(f"Error: file not found: {audio_path}")
        return 1

    if args.reference_file:
        reference = Path(args.reference_file).read_text(encoding="utf-8")
    elif args.reference:
        reference = args.reference
    else:
        _eprint("Error: provide --reference or --reference-file.")
        return 1

    try:
        audio = load_audio(audio_path)
    except Exception as exc:
        _eprint(f"Error: could not read audio: {exc}")
        return 1

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    print(f"Reference: {len(normalize(reference).split())} words\n")

    results = []
    for name in models:
        try:
            hypothesis = transcribe(audio, name)
        except Exception as exc:
            _eprint(f"Error with model '{name}': {exc}")
            continue
        rate = word_error_rate(reference, hypothesis)
        accuracy = max(0.0, 100.0 * (1.0 - rate))
        results.append((name, accuracy))
        print(f"── {name} ──")
        print(f"   accuracy: {accuracy:5.1f}%   (word error rate {rate * 100:.1f}%)")
        print(f"   heard: {hypothesis}\n")

    if len(results) >= 2:
        best = max(results, key=lambda r: r[1])
        print(f"Most accurate: {best[0]} ({best[1]:.1f}%).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
