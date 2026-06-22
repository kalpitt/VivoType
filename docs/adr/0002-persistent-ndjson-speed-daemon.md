# [ADR-0002] Persistent NDJSON Speed Daemon

**Status:** Accepted  
**Date:** 2026-06-15  

> **Engine note (2026-06-19):** This ADR references `faster-whisper`/`CTranslate2` as the model engine. Since Phase 9 the engine is **MLX (`mlx-whisper`)** — see [ADR-0007](0007-adopt-mlx-whisper.md). The daemon + NDJSON IPC decision below is unchanged; only the engine kept warm in RAM is different.

## Context
VivoType needs to perform dictation instantly when the user presses and releases a hotkey.
Originally, the Swift macOS app spawned a new Python CLI process (`python core/cli.py input.wav`) every time the user triggered dictation.
However, loading large machine learning models like `faster-whisper` and `CTranslate2` into memory takes 2–4 seconds on Apple Silicon. This "cold start" latency made the dictation experience feel sluggish and unusable for quick, iterative typing.

## Decision
We shifted from a per-invocation CLI to a persistent background Python daemon (`core/daemon.py`). 
The daemon loads the Whisper model into RAM once upon app launch. The Swift client then communicates with the Python daemon using standard Unix pipes (`stdin` and `stdout`), sending file paths and receiving transcribed text formatted as NDJSON (Newline Delimited JSON).

## Consequences
- **Instant Transcription:** Dictation now begins transcribing within milliseconds of hotkey release.
- **Resource Usage:** The Python process constantly consumes a few hundred megabytes of RAM while the app is running.
- **Complexity:** We had to implement robust IPC (Inter-Process Communication) and broken-pipe handling in Swift.

## 🤖 Agent Directives
- **DO NOT** revert to spawning a new Python process for each dictation event. The model must remain hot in memory.
- **DO NOT** introduce HTTP servers (like Flask/FastAPI) or Unix Domain Sockets for IPC. Standard `stdin/stdout` pipes are lightweight, secure, and avoid firewall/port conflicts on macOS.
- **DO** ensure all output from the daemon to Swift is strictly formatted as NDJSON so the Swift parser does not crash.
