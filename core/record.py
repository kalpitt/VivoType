#!/usr/bin/env python3
"""Record a labeled voice sample for VivoType personalization.

Records from the default microphone and saves a mono, 16 kHz, 16-bit WAV into
core/data/raw/ (the format the ASR CLI expects), then appends a row to
core/data/labels.csv mapping the file to the spoken label/prompt.

This ONLY collects data — there is no training loop here.

Usage:
    python core/record.py --label "the quick brown fox"     # press Enter to stop
    python core/record.py --label "hello" --duration 3      # fixed 3-second clip
    python core/record.py                                   # prompts for a label
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import re
import sys
from pathlib import Path

# Default data locations, relative to this file (core/).
DATA_DIR = Path(__file__).with_name("data")
RAW_DIR = DATA_DIR / "raw"
LABELS_CSV = DATA_DIR / "labels.csv"

SAMPLE_RATE = 16000
CHANNELS = 1


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


def _slugify(label: str) -> str:
    """Turn a free-text label into a safe, short filename stem."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return (slug[:40] or "sample")


def _record(samplerate: int, channels: int, duration: float | None):
    """Capture audio as an int16 numpy array. Returns (array, seconds)."""
    import numpy as np
    import sounddevice as sd

    if duration is not None:
        print(f"Recording {duration:g}s…", file=sys.stderr)
        audio = sd.rec(
            int(duration * samplerate),
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
        )
        sd.wait()
    else:
        input("Press Enter to START recording… ")
        frames = []
        stream = sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            callback=lambda indata, frames_, time_, status: frames.append(indata.copy()),
        )
        with stream:
            input("Recording… press Enter to STOP. ")
        if not frames:
            raise RuntimeError("no audio captured")
        audio = np.concatenate(frames, axis=0)

    seconds = len(audio) / float(samplerate)
    return audio, seconds


def _append_manifest(row: dict) -> None:
    """Append a row to labels.csv, writing the header if the file is new."""
    fields = ["filename", "label", "samplerate", "channels", "duration_sec", "recorded_at"]
    is_new = not LABELS_CSV.exists()
    with LABELS_CSV.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vivotype-record",
        description="Record a labeled mic sample into core/data/raw/.",
    )
    parser.add_argument("--label", help="Text prompt you will read aloud (the label).")
    parser.add_argument("--duration", type=float, default=None,
                        help="Fixed seconds to record. Omit to stop with Enter.")
    parser.add_argument("--samplerate", type=int, default=SAMPLE_RATE)
    parser.add_argument("--channels", type=int, default=CHANNELS)
    parser.add_argument("--outdir", default=str(RAW_DIR),
                        help="Where to save the WAV (default: core/data/raw).")
    args = parser.parse_args(argv)

    label = args.label or input("Label (what you'll say): ").strip()
    if not label:
        _eprint("Error: a non-empty --label is required.")
        return 1

    try:
        import sounddevice  # noqa: F401  (checked here for a friendly message)
    except (ImportError, OSError) as exc:
        _eprint(f"Error: microphone library unavailable ({exc}). "
                "Install it with: pip install sounddevice")
        return 1

    try:
        audio, seconds = _record(args.samplerate, args.channels, args.duration)
    except Exception as exc:
        _eprint(f"Error: recording failed: {exc}")
        return 1

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = outdir / f"{_slugify(label)}-{stamp}.wav"

    try:
        from core.audioio import write_wav
        write_wav(path, audio.astype("float32") / 32768.0, args.samplerate)
    except Exception as exc:
        _eprint(f"Error: could not write '{path}': {exc}")
        return 1

    _append_manifest({
        "filename": path.name,
        "label": label,
        "samplerate": args.samplerate,
        "channels": args.channels,
        "duration_sec": round(seconds, 3),
        "recorded_at": stamp,
    })

    print(f"Saved {path} ({seconds:.2f}s) — labeled: {label!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
