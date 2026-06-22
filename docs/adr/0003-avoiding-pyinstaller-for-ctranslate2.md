# [ADR-0003] Avoiding PyInstaller for CTranslate2

**Status:** Accepted (premise partly superseded by [ADR-0007](0007-adopt-mlx-whisper.md))  
**Date:** 2026-06-15  

> **Engine note (2026-06-19):** The specific trigger for this decision — `faster-whisper`'s statically linked `CTranslate2` binary being quarantined by Gatekeeper — no longer applies; the engine is now **MLX (`mlx-whisper`)**, a pure-Python wheel (see [ADR-0007](0007-adopt-mlx-whisper.md)). The **decision still stands**: we continue to build the `.venv` on first launch in Application Support rather than bundling/notarizing binaries. Treat the `CTranslate2`-specific reasoning below as historical context, and the Agent Directives as still in force.

## Context
We needed to distribute VivoType as a self-contained, drag-and-drop macOS `.app` bundle so users don't have to use the terminal to install Python dependencies.
The standard industry solution for packaging Python applications is `PyInstaller` or `py2app`. 
However, VivoType relies on `faster-whisper`, which statically links `CTranslate2` (a heavy C++ machine learning binary). When PyInstaller bundles these `.so`/`.dylib` files into the macOS app bundle, Apple's Gatekeeper flags them as quarantined, unsigned executable code. This causes the app to instantly crash on launch unless the developer has a paid Apple Developer Account and navigates an extremely complex hardened runtime and notarization process.

## Decision
We bypass PyInstaller entirely using a "Smart Hybrid" bundling approach. 
The `.app` bundle only contains the Swift executable and immutable Python source code in `Contents/Resources/`. 
On the very first launch, the Swift app detects if a `.venv` exists. If not, it shows a native loading window and dynamically executes `setup_core.sh` to build a fresh `.venv` in the user's writable `~/Library/Application Support/VivoType/` directory.

## Consequences
- **Zero Notarization Nightmare:** Because the dependencies are built locally on the user's machine via `pip`, macOS trusts the binaries. No paid Apple Developer account is required.
- **App Size:** The downloaded `.app` is tiny (a few megabytes) because it doesn't include the massive Python runtime.
- **First-Run Latency:** The user has to wait ~30-60 seconds on their very first launch while the environment downloads and installs.

## 🤖 Agent Directives
- **DO NOT** attempt to use `PyInstaller`, `py2app`, or `cx_Freeze` to bundle this application.
- **DO NOT** embed pre-built `.venv` folders or ML `.dylib` files inside the `VivoType.app` bundle.
- **DO** respect the architectural boundary: Immutable code lives in `Contents/Resources/`, mutable state (`.venv`, models, logs) lives in `Application Support`.
