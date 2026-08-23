"""Tests for core/audioio.py — no soundfile or librosa dependency."""
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from core.audioio import TARGET_SR, _resample, has_speech, load_wav, write_wav


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_raw_wav(path: Path, samples_f32: np.ndarray, samplerate: int,
                   nchannels: int = 1, sampwidth: int = 2) -> None:
    """Write a WAV without using soundfile — used only by these tests."""
    if sampwidth == 2:
        pcm = np.clip(samples_f32 * 32768.0, -32768, 32767).astype(np.int16)
    elif sampwidth == 1:
        pcm = np.clip(samples_f32 * 128.0 + 128.0, 0, 255).astype(np.uint8)
    elif sampwidth == 4:
        pcm = np.clip(samples_f32 * 2_147_483_648.0,
                      -2_147_483_648, 2_147_483_647).astype(np.int32)
    elif sampwidth == 3:
        # 24-bit little-endian: keep the low 3 bytes of each int32 sample.
        ints = np.clip(samples_f32 * 8_388_608.0,
                       -8_388_608, 8_388_607).astype("<i4")
        pcm = ints.view(np.uint8).reshape(-1, 4)[:, :3]
    else:
        raise ValueError(sampwidth)
    if nchannels > 1:
        pcm = np.column_stack([pcm] * nchannels)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(samplerate)
        wf.writeframes(pcm.tobytes())


def _silence(seconds: float = 0.5, sr: int = TARGET_SR) -> np.ndarray:
    return np.zeros(int(sr * seconds), dtype=np.float32)


# ---------------------------------------------------------------------------
# load_wav tests
# ---------------------------------------------------------------------------

class LoadWavTests(unittest.TestCase):
    def test_mono_16k_passthrough(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.wav"
            sig = _silence(0.25)
            _write_raw_wav(p, sig, TARGET_SR)
            out = load_wav(p)
        self.assertEqual(out.dtype, np.float32)
        self.assertEqual(out.ndim, 1)
        self.assertAlmostEqual(len(out) / TARGET_SR, 0.25, delta=0.01)

    def test_stereo_downmixed_to_mono(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.wav"
            sig = _silence(0.3)
            _write_raw_wav(p, sig, TARGET_SR, nchannels=2)
            out = load_wav(p)
        self.assertEqual(out.ndim, 1)

    def test_44k_resampled_to_16k(self):
        sr = 44100
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.wav"
            sig = _silence(0.5, sr=sr)
            _write_raw_wav(p, sig, sr)
            out = load_wav(p)
        expected = int(round(sr * 0.5 * TARGET_SR / sr))
        # allow ±5% rounding
        self.assertAlmostEqual(len(out), expected, delta=expected * 0.05)

    def test_stereo_44k_becomes_mono_16k(self):
        sr = 44100
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sh.wav"
            sig = _silence(0.5, sr=sr)
            _write_raw_wav(p, sig, sr, nchannels=2)
            out = load_wav(p)
        self.assertEqual(out.ndim, 1)
        self.assertTrue(7000 < len(out) < 9000, len(out))

    def test_8bit_readable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "8b.wav"
            sig = _silence(0.1)
            _write_raw_wav(p, sig, TARGET_SR, sampwidth=1)
            out = load_wav(p)
        self.assertEqual(out.dtype, np.float32)

    def test_24bit_readable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "24b.wav"
            sig = _silence(0.1)
            _write_raw_wav(p, sig, TARGET_SR, sampwidth=3)
            out = load_wav(p)
        self.assertEqual(out.dtype, np.float32)
        self.assertEqual(out.ndim, 1)

    def test_24bit_amplitude_preserved(self):
        # A half-scale tone must decode to ~0.5, proving sign/scale are correct.
        sr = TARGET_SR
        tone = 0.5 * np.sin(np.linspace(0, 2 * np.pi, sr, endpoint=False)).astype(np.float32)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "24tone.wav"
            _write_raw_wav(p, tone, sr, sampwidth=3)
            out = load_wav(p)
        np.testing.assert_allclose(tone, out, atol=1e-3)

    def test_32bit_readable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "32b.wav"
            sig = _silence(0.1)
            _write_raw_wav(p, sig, TARGET_SR, sampwidth=4)
            out = load_wav(p)
        self.assertEqual(out.dtype, np.float32)

    def test_empty_wav_raises(self):
        # A zero-length recording must raise (caller turns this into a clean
        # error), not return an empty array that breaks the model downstream.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "empty.wav"
            with wave.open(str(p), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(TARGET_SR)
                # no frames written
            with self.assertRaises(ValueError):
                load_wav(p)

    def test_contiguous_float32_output(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.wav"
            _write_raw_wav(p, _silence(), TARGET_SR)
            out = load_wav(p)
        self.assertTrue(out.flags["C_CONTIGUOUS"])
        self.assertEqual(out.dtype, np.float32)


# ---------------------------------------------------------------------------
# write_wav tests
# ---------------------------------------------------------------------------

class WriteWavTests(unittest.TestCase):
    def test_round_trip(self):
        sig = np.sin(np.linspace(0, 2 * np.pi, TARGET_SR, endpoint=False)).astype(np.float32)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rt.wav"
            write_wav(p, sig)
            back = load_wav(p)
        # 16-bit quantization error should be < 0.001
        np.testing.assert_allclose(sig, back, atol=3e-4)

    def test_output_is_16bit_pcm(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.wav"
            write_wav(p, _silence())
            with wave.open(str(p), "rb") as wf:
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getframerate(), TARGET_SR)

    def test_clipping_does_not_raise(self):
        loud = np.full(TARGET_SR, 2.0, dtype=np.float32)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "loud.wav"
            write_wav(p, loud)
            with wave.open(str(p), "rb") as wf:
                raw = np.frombuffer(wf.readframes(TARGET_SR), dtype=np.int16)
        self.assertTrue(np.all(raw == 32767))


# ---------------------------------------------------------------------------
# has_speech (silence gate) tests
# ---------------------------------------------------------------------------

class HasSpeechTests(unittest.TestCase):
    def test_pure_silence_is_not_speech(self):
        self.assertFalse(has_speech(np.zeros(TARGET_SR, dtype=np.float32)))

    def test_empty_array_is_not_speech(self):
        self.assertFalse(has_speech(np.zeros(0, dtype=np.float32)))

    def test_faint_mic_noise_is_not_speech(self):
        # Electrical noise floor well below any real utterance.
        rng = np.random.default_rng(42)
        noise = (0.0005 * rng.standard_normal(TARGET_SR)).astype(np.float32)
        self.assertFalse(has_speech(noise))

    def test_dc_offset_alone_is_not_speech(self):
        # A biased-but-silent mic: constant 0.1 offset, no signal.
        self.assertFalse(has_speech(np.full(TARGET_SR, 0.1, dtype=np.float32)))

    def test_speech_level_tone_is_speech(self):
        t = np.arange(TARGET_SR, dtype=np.float64) / TARGET_SR
        tone = (0.05 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        self.assertTrue(has_speech(tone))

    def test_short_burst_in_long_silence_is_speech(self):
        # One word in a 3 s clip must count — the gate is per-window, not global.
        clip = np.zeros(3 * TARGET_SR, dtype=np.float32)
        t = np.arange(TARGET_SR // 5, dtype=np.float64) / TARGET_SR
        clip[TARGET_SR:TARGET_SR + len(t)] = 0.05 * np.sin(2 * np.pi * 220 * t)
        self.assertTrue(has_speech(clip))

    def test_quiet_speech_still_passes(self):
        # Very quiet-but-real speech (~ -40 dBFS) must NOT be eaten.
        t = np.arange(TARGET_SR, dtype=np.float64) / TARGET_SR
        tone = (0.01 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        self.assertTrue(has_speech(tone))

    def test_threshold_boundary_pinned(self):
        # Pin the gate's edge (sine RMS = amplitude/√2 vs SILENCE_RMS=0.0025)
        # so a future threshold tweak can't silently start eating quiet
        # speakers or letting silence through.
        t = np.arange(TARGET_SR, dtype=np.float64) / TARGET_SR
        just_above = (0.0040 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        just_below = (0.0030 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        self.assertTrue(has_speech(just_above))
        self.assertFalse(has_speech(just_below))


# ---------------------------------------------------------------------------
# _resample tests
# ---------------------------------------------------------------------------

class ResampleTests(unittest.TestCase):
    def test_identity(self):
        sig = np.ones(100, dtype=np.float32)
        np.testing.assert_array_equal(_resample(sig, TARGET_SR, TARGET_SR), sig)

    def test_output_length_correct(self):
        sig = np.zeros(44100, dtype=np.float32)
        out = _resample(sig, 44100, TARGET_SR)
        self.assertEqual(len(out), TARGET_SR)  # 1 second at 16kHz

    def test_output_dtype_float32(self):
        sig = np.zeros(1000, dtype=np.float64)
        out = _resample(sig, 8000, TARGET_SR)
        self.assertEqual(out.dtype, np.float32)

    def test_upsample_preserves_endpoints_no_flat_tail(self):
        # Upsampling a strict ramp must stay strictly increasing and keep both
        # endpoints. The old endpoint=False interpolation clamped the final
        # sample(s) to the last value, leaving a dead flat tail.
        data = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
        out = _resample(data, 4, 8)  # 4 Hz -> 8 Hz, n_out = 8
        self.assertEqual(len(out), 8)
        self.assertAlmostEqual(float(out[0]), 0.0, places=5)
        self.assertAlmostEqual(float(out[-1]), 3.0, places=5)
        self.assertTrue(np.all(np.diff(out) > 0))  # no flat (clamped) tail


if __name__ == "__main__":
    unittest.main()
