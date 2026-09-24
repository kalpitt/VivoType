"""Tests for core/record.py helpers (no microphone required)."""

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from core import record


class ManifestPathTests(unittest.TestCase):
    def test_relative_when_under_data_dir(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            labels = data / "labels.csv"
            wav = data / "raw" / "hello-20260101.wav"
            wav.parent.mkdir()
            wav.write_bytes(b"")
            self.assertEqual(
                record._manifest_filename(wav, labels),
                "raw/hello-20260101.wav",
            )

    def test_absolute_when_outdir_outside_data_dir(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "data"
            other = Path(d) / "elsewhere"
            data.mkdir()
            other.mkdir()
            labels = data / "labels.csv"
            wav = other / "clip.wav"
            wav.write_bytes(b"")
            self.assertEqual(
                record._manifest_filename(wav, labels),
                str(wav.resolve()),
            )


class RecordWriteTests(unittest.TestCase):
    def test_script_path_write_and_manifest(self):
        # Exercise the dual-import write_wav path and labels.csv row without a mic.
        # main() preflights `import sounddevice` for a friendly error before it
        # ever calls _record, so mocking _record alone is not enough: on a host
        # without the library (every Linux CI runner) main() returns 1 early.
        # Stubbing the module keeps this test's "no microphone required" promise
        # true on every platform, so CI exercises the same path a Mac does.
        fake_audio = np.zeros((1600, 1), dtype=np.int16)
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            outdir = data / "raw"
            labels = data / "labels.csv"
            with mock.patch.dict(sys.modules, {"sounddevice": mock.Mock()}), \
                 mock.patch.object(record, "_record", return_value=(fake_audio, 0.1)), \
                 mock.patch.object(record, "_labels_csv", return_value=labels), \
                 mock.patch.object(record, "_default_raw_dir", return_value=outdir):
                rc = record.main(["--label", "hello world", "--duration", "0.1"])
            self.assertEqual(rc, 0)
            wavs = list(outdir.glob("hello-world-*.wav"))
            self.assertEqual(len(wavs), 1)
            with labels.open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], "hello world")
            self.assertTrue(rows[0]["filename"].startswith("raw/"))

    def test_stereo_recording_is_downmixed_to_mono(self):
        # A-F10: --channels 2 wrote interleaved L/R samples under a mono
        # header — a clip of double length at half speed. The saved file
        # must be true mono with one sample per frame.
        import wave
        n = 1600
        left = np.full(n, 1000, dtype=np.int16)
        right = np.full(n, 3000, dtype=np.int16)
        stereo = np.column_stack([left, right])
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            outdir = data / "raw"
            labels = data / "labels.csv"
            with mock.patch.dict(sys.modules, {"sounddevice": mock.Mock()}), \
                 mock.patch.object(record, "_record", return_value=(stereo, 0.1)), \
                 mock.patch.object(record, "_labels_csv", return_value=labels), \
                 mock.patch.object(record, "_default_raw_dir", return_value=outdir):
                rc = record.main(["--label", "st", "--duration", "0.1",
                                  "--channels", "2"])
            self.assertEqual(rc, 0)
            wav_path = next(outdir.glob("st-*.wav"))
            with wave.open(str(wav_path), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getnframes(), n)
                pcm = np.frombuffer(wf.readframes(n), dtype=np.int16)
            with labels.open(encoding="utf-8") as fh:
                row = next(csv.DictReader(fh))
        np.testing.assert_allclose(pcm, 2000, atol=1)
        self.assertEqual(row["channels"], "1")  # describes the saved file


if __name__ == "__main__":
    unittest.main()
