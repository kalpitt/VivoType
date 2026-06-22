# Architectural Decision Records (ADR)

This directory contains Architectural Decision Records (ADRs) for the VivoType project. 

> **For AI Agents:** Before proposing new dependencies, packaging methods, or major refactors, you MUST review this index. Do not propose solutions that contradict an existing `Accepted` ADR.

## Index

* **[ADR-0001: Record Architecture Decisions](0001-record-architecture-decisions.md)**  
  *We will use Markdown Architectural Decision Records (ADRs) with a custom "Agent Directives" section to safeguard decisions.*
* **[ADR-0002: Persistent NDJSON Speed Daemon](0002-persistent-ndjson-speed-daemon.md)**  
  *We use a long-lived Python daemon communicating over `stdin/stdout` using NDJSON to avoid the cold-start latency of loading ML models.*
* **[ADR-0003: Avoiding PyInstaller for CTranslate2](0003-avoiding-pyinstaller-for-ctranslate2.md)**  
  *We use a "Smart Hybrid" bundling approach where the `.venv` is built dynamically in `Application Support` to avoid Gatekeeper quarantine on embedded ML C++ binaries. (Premise partly superseded by ADR-0007 — the decision stands, the `CTranslate2` trigger is historical.)*
* **[ADR-0004: Native Swift Audio Resampling](0004-native-swift-audio-resampling.md)**  
  *We use Apple's native `AVAudioConverter` in Swift to resample mic audio to 16kHz mono to avoid bloating the Python backend with heavy C dependencies like `librosa` or `soundfile`.*
* **[ADR-0005: Text-Based Multi-File Swift Build](0005-text-based-multi-file-swift-build.md)**  
  *We compile the macOS client's multiple `.swift` files together with a single `swiftc` invocation driven by `build_app.sh` — no `.xcodeproj`, SwiftPM, or Xcode.app — with a `@main` struct as the entry point.*
* **[ADR-0006: Transient `.regular` Activation for Setup Windows](0006-transient-regular-activation.md)**  
  *VivoType stays a background `.accessory` menu-bar agent, switching to `.regular` only while a setup/permissions/settings window is open (reference-counted via `ActivationCoordinator`) so those windows reliably foreground on macOS 14+ without making VivoType a permanent Dock app.*
* **[ADR-0007: Adopt MLX (mlx-whisper) over faster-whisper/CTranslate2](0007-adopt-mlx-whisper.md)**  
  *We transcribe with `mlx-whisper` on the Apple-Silicon GPU / Neural Engine instead of the CPU-bound `faster-whisper`/`CTranslate2`, recording the Phase 9 engine swap. Amends the engine references in ADR-0002/0004 and partly supersedes the premise of ADR-0003.*
