#!/usr/bin/env python3
"""Lightweight WAV I/O using only stdlib `wave` + `numpy` (no soundfile/librosa).

Public API:
    load_wav(path) -> np.ndarray   # float32, mono, 16 kHz
    write_wav(path, samples, samplerate)
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Union

import numpy as np

TARGET_SR = 16_000


def load_wav(path: Union[str, Path]) -> np.ndarray:
    """Read any WAV file; return a mono float32 array resampled to 16 kHz.

    Handles: 8/16/24/32-bit PCM, mono or multi-channel, any sample rate.
    """
    path = Path(path)
    with wave.open(str(path), "rb") as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        if framerate <= 0:
            raise ValueError(f"Malformed sample rate: {framerate}")
        nframes = wf.getnframes()
        if nframes == 0:
            raise ValueError("Empty WAV file")
        raw = wf.readframes(nframes)

    if sampwidth == 1:
        # WAV 8-bit is unsigned (0..255, silence=128)
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        data = (data - 128.0) / 128.0
    elif sampwidth == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 3:
        # 24-bit PCM has no numpy dtype, so rebuild each signed sample from its
        # 3 little-endian bytes (b0=LSB, b2=MSB) and sign-extend the top bit.
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        ints = np.where(ints >= 0x800000, ints - 0x1000000, ints)
        data = ints.astype(np.float32) / 8_388_608.0
    elif sampwidth == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2_147_483_648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sampwidth} bytes")

    if nchannels > 1:
        data = data.reshape(-1, nchannels).mean(axis=1)

    if framerate != TARGET_SR:
        data = _resample(data, framerate, TARGET_SR)

    return np.ascontiguousarray(data, dtype=np.float32)


def write_wav(path: Union[str, Path], samples: np.ndarray, samplerate: int = TARGET_SR) -> None:
    """Write a float32 mono array as a 16-bit PCM WAV file."""
    path = Path(path)
    pcm = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(pcm.tobytes())


def _resample(data: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    """Linear interpolation resample — accurate enough for speech at 16 kHz."""
    if from_sr == to_sr:
        return data
    n_out = int(round(len(data) * to_sr / from_sr))
    if n_out <= 0 or len(data) < 2:
        return np.asarray(data, dtype=np.float32)
    # Interpolate over sample indices so both endpoints are preserved exactly.
    # (Mapping onto [0,1) with endpoint=False instead clamped the final output
    # samples to data[-1], leaving a dead flat tail when upsampling.)
    x_old = np.arange(len(data))
    x_new = np.linspace(0.0, len(data) - 1, n_out)
    return np.interp(x_new, x_old, data).astype(np.float32)
