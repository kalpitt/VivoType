# [ADR-0007] Adopt MLX (mlx-whisper) over faster-whisper/CTranslate2

**Status:** Accepted  
**Date:** 2026-06-19  

## Context
The original transcription engine (ADR-0002/0003/0004 era) was `faster-whisper`, which statically links the `CTranslate2` C++ runtime and executes on the **CPU**. Two problems followed from that choice:

- **Performance.** On Apple Silicon the CPU path left the M-series GPU / Neural Engine idle. Warm transcribes were slower than they needed to be for a press-and-release dictation flow.
- **Packaging friction.** `CTranslate2` ships heavy `.so`/`.dylib` binaries that Gatekeeper quarantines when embedded in an app bundle — the entire motivation for the "build the `.venv` on first launch" workaround in ADR-0003.

In **Phase 9 (completed)** the engine was swapped to [**mlx-whisper**](https://github.com/ml-explore/mlx-examples/tree/main/whisper), Apple's MLX port of Whisper, which runs on the M-series GPU / Neural Engine. That swap shipped without its own ADR, leaving ADR-0002/0003/0004 describing an engine the code no longer uses — a violation of ADR-0001's rule that architectural changes must be recorded. This ADR closes that gap.

## Decision
We use **`mlx-whisper`** as the sole transcription backend, defaulting to the `small.en` model.

- A single backend module, `core/asr.py`, is shared by both the persistent daemon (ADR-0002) and the one-shot CLI. It maps a Whisper size (e.g. `small.en`) to its `mlx-community/whisper-<name>-mlx` Hugging Face repo, warms the model on construction by priming mlx-whisper's process-wide `ModelHolder` cache (float16), and returns backend-agnostic Whisper segments (`start`/`end`/`text`/`avg_logprob`).
- `requirements.txt` replaces `faster-whisper` with `mlx-whisper`; `faster-whisper` and `ctranslate2` are fully removed.
- Model weights are cached in a writable location via `core/paths.py::models_dir()` (`$VIVOTYPE_MODELS_DIR` → `$VIVOTYPE_APP_SUPPORT/models` → `~/Library/Application Support/VivoType/models`), with `HF_HOME` pointed there so nothing lands in the read-only bundle (preserving ADR-0003's boundary).

## Consequences
- **Hardware acceleration:** dictation runs on `Device(gpu, 0)`; warm transcribes are ~0.9 s.
- **Pure-Python, pip-installable engine:** `mlx-whisper` has no statically linked C++ blob to quarantine, so the first-launch `.venv` build (ADR-0003) becomes simpler in spirit — though the local-build approach is retained because it is still the cleanest way to ship a notarization-free, self-contained app.
- **Apple-Silicon only:** MLX requires Apple Silicon. There is no Linux/Intel GPU path. CI therefore runs the Python suite on Linux with `mlx-whisper` mocked (it has no Linux wheel) and builds the app on a macOS runner.
- **The NDJSON IPC protocol (ADR-0002) is unchanged** — only the engine behind it changed.

## 🤖 Agent Directives
- **DO NOT** reintroduce `faster-whisper`, `ctranslate2`, `whisper.cpp`, or any CPU-bound transcription backend. The engine is MLX.
- **DO NOT** add a non-MLX inference path "for portability" — VivoType is an Apple-Silicon macOS app by design.
- **DO** route all transcription (daemon and CLI) through `core/asr.py` so callers stay backend-agnostic.
- **DO** keep model weights under the writable `models_dir()` (via `HF_HOME`), never inside `VivoType.app/Contents/Resources/`.

## Relationship to earlier ADRs
- **Supersedes** ADR-0003's *premise* (the quarantined `CTranslate2` binary). The "build `.venv` on first launch" decision itself still stands; only the engine that motivated it has changed.
- **Amends** ADR-0002 and ADR-0004, which referenced `faster-whisper` as the engine. The daemon (0002) and native Swift resampling (0004) decisions are unchanged; the engine they feed is now MLX.
