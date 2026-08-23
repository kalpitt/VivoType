#!/usr/bin/env python3
"""Record a labeled voice sample for VivoType personalization.

Records from the default microphone and saves a mono, 16 kHz, 16-bit WAV into
the writable data dir's raw/ folder (the format the ASR CLI expects), then
appends a row to labels.csv mapping the file to the spoken label/prompt.

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

try:  # package import (python -m / installed layout)
    from core.audioio import write_wav
    from core.paths import data_dir
except ImportError:  # script import (python core/record.py → sys.path[0] is core/)
    from audioio import write_wav
    from paths import data_dir

SAMPLE_RATE = 16000
CHANNELS = 1


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


def _slugify(label: str) -> str:
    """Turn a free-text label into a safe, short filename stem."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return (slug[:40] or "sample")


def _labels_csv() -> Path:
    return data_dir() / "labels.csv"


def _default_raw_dir() -> Path:
    return data_dir() / "raw"


def _manifest_filename(wav_path: Path, labels_csv: Path) -> str:
    """Path stored in labels.csv — relative to the data dir when possible.

    Basename-only rows break when --outdir is not the default raw/ folder;
    prefer a path relative to labels.csv's parent, else an absolute path.
    """
    try:
        return str(wav_path.resolve().relative_to(labels_csv.parent.resolve()))
    except ValueError:
        return str(wav_path.resolve())


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


def _append_manifest(row: dict, labels_csv: Path) -> None:
    """Append a row to labels.csv, writing the header if the file is new."""
    fields = ["filename", "label", "samplerate", "channels", "duration_sec", "recorded_at"]
    labels_csv.parent.mkdir(parents=True, exist_ok=True)
    is_new = not labels_csv.exists()
    with labels_csv.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vivotype-record",
        description="Record a labeled mic sample into the writable data dir's raw/.",
    )
    parser.add_argument("--label", help="Text prompt you will read aloud (the label).")
    parser.add_argument("--duration", type=float, default=None,
                        help="Fixed seconds to record. Omit to stop with Enter.")
    parser.add_argument("--samplerate", type=int, default=SAMPLE_RATE)
    parser.add_argument("--channels", type=int, default=CHANNELS)
    parser.add_argument("--outdir", default=None,
                        help="Where to save the WAV (default: <data_dir>/raw).")
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

    outdir = Path(args.outdir) if args.outdir else _default_raw_dir()
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = outdir / f"{_slugify(label)}-{stamp}.wav"
    labels_csv = _labels_csv()

    try:
        write_wav(path, audio.astype("float32") / 32768.0, args.samplerate)
    except Exception as exc:
        _eprint(f"Error: could not write '{path}': {exc}")
        return 1

    _append_manifest({
        "filename": _manifest_filename(path, labels_csv),
        "label": label,
        "samplerate": args.samplerate,
        "channels": args.channels,
        "duration_sec": round(seconds, 3),
        "recorded_at": stamp,
    }, labels_csv)

    print(f"Saved {path} ({seconds:.2f}s) — labeled: {label!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
