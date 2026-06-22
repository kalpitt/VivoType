# [ADR-0004] Native Swift Audio Resampling

**Status:** Accepted  
**Date:** 2026-06-15  

> **Engine note (2026-06-19):** This ADR references `faster-whisper` as the consumer of the resampled audio. Since Phase 9 the engine is **MLX (`mlx-whisper`)** — see [ADR-0007](0007-adopt-mlx-whisper.md). The native-Swift resampling decision below is unchanged; MLX expects the same 16 kHz mono 16-bit PCM input.

## Context
`faster-whisper` strictly expects audio to be provided as 16kHz, mono, 16-bit PCM WAV data.
When capturing audio from the Mac's microphone, the native hardware format is often 44.1kHz or 48kHz stereo.
In typical Python ML projects, developers use libraries like `librosa`, `soundfile`, or `ffmpeg-python` to resample and mix the audio down to the correct format before feeding it to the model. However, these libraries require massive C-level dependencies (like `libsndfile` or `ffmpeg`) which drastically bloat the installation size and increase the risk of cross-platform compilation failures.

## Decision
We handle all audio capture, resampling, and format conversion entirely in native Swift using Apple's highly optimized `AVFoundation` and `AVAudioConverter` APIs. 
The Swift client writes a perfectly formatted 16kHz mono WAV file to disk, and the Python backend simply reads it in natively.

## Consequences
- **Extremely Lightweight Backend:** The Python dependencies (`requirements.txt`) remain purely focused on transcription, avoiding heavy audio manipulation libraries.
- **Performance:** `AVAudioConverter` is hardware-optimized on macOS and performs the resampling with zero noticeable overhead.
- **Complexity:** The Swift audio capture code (`AudioCapture.swift` / `main.swift`) is slightly more complex, requiring careful buffer management.

## 🤖 Agent Directives
- **DO NOT** add `librosa`, `soundfile`, `ffmpeg`, or `pydub` to the Python `requirements.txt`.
- **DO** ensure any new audio manipulation happens in Swift prior to passing data to the Python backend.
- **DO** maintain the fallback resampling code in `core/audioio.py` ONLY for CLI testing purposes, using pure `numpy` and `wave` without heavy dependencies.
