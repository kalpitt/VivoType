# VivoType 🎙️

**VivoType** is a fully local, open-source voice dictation tool for macOS — inspired by Wispr Flow, but with **100% on-device processing**. No cloud, no accounts, no audio ever leaving your Mac.

Hold a hotkey, speak, release — your words appear in whatever app you're focused on.

> **Status:** Beta — feature-complete. The project was built in phases (see [`docs/phases.md`](docs/phases.md)): local ASR, Indic post-processing, the native macOS client, MLX hardware acceleration, guided permissions, and the app icon have all landed.

## Why VivoType?

- **Private by design** — speech recognition runs locally; nothing is uploaded.
- **India-aware** — built-in post-processing handles Indian names, tech jargon, and ₹/lakh/crore conversions (see [`docs/indic-nlp.md`](docs/indic-nlp.md)).
- **Open source** — MIT licensed, hackable, yours.

## Project Layout

```
core/         # Python ASR backend + Indic post-processing (CLI only)
core/paths.py # Resolves writable runtime paths (App Support, or repo in dev)
core/data/    # Default/seed data; per-user data lives in App Support at runtime
core/tests/   # Unit tests (stdlib unittest)
clients/mac/  # Native macOS glue: hotkey, mic capture, text injection
docs/         # Specs and the phased execution plan
scripts/      # Setup + test helpers
```

When run as the installed app, all mutable state (the `.venv`, the downloaded
model, your dictionary, corrections, and logs) lives under
`~/Library/Application Support/VivoType/` — never inside the app bundle. In a
dev checkout the CLI uses `core/data/` directly.

## Speech Engine

VivoType transcribes with [**mlx-whisper**](https://github.com/ml-explore/mlx-examples/tree/main/whisper), Apple's MLX port of Whisper, defaulting to the **`small.en`** model.

- **Pure Python, pip-installable** — no manual ffmpeg or compiler setup needed.
- **Hardware-accelerated on Apple Silicon** — runs on the M-series GPU / Neural Engine via MLX, not the CPU, so dictation is fast and light. Nothing is ever sent to the cloud.
- **Why `small.en`:** on a real dictation test it scored ~94% word accuracy vs ~82% for `tiny.en` — roughly a third of the errors. It's a larger (~466 MB) download but still low-latency for short dictation phrases.
- **Need maximum speed / low resources?** Use `--model tiny.en`. Other Whisper models (e.g. `medium.en`, or multilingual `small`) also work via `--model`.

Models download automatically on first use and are cached locally. Use `python core/benchmark.py` to compare models on your own voice.

## Getting Started

### Use the app (recommended — no terminal needed)

VivoType ships as a self-contained `VivoType.app`. Everything it needs is inside the
bundle; on first launch it sets up its private engine under
`~/Library/Application Support/VivoType/` (a `.venv`, the downloaded model, your
dictionary, and logs). Nothing is written back into the app.

**1. Build the app** (needs Xcode Command Line Tools — run `xcode-select --install` if prompted)
```bash
./clients/mac/build_app.sh
```
This produces `clients/mac/build/VivoType.app` and prints a build summary.

**2. Install and launch** — drag `VivoType.app` into `/Applications`, then open it.
On first launch a **“Welcome to VivoType”** window appears while it builds its
engine. This needs **Python 3.11+**; if it's missing, VivoType tells you to run
`xcode-select --install` (or install Python 3) and offers a **Retry** button.

**3. Dictate** — hold **`Right-Option`**, speak, release. Your words appear in
the focused app. On first launch a guided **Permissions** checklist walks you
through granting Microphone and Accessibility access, with live status as each
is granted. See [clients/mac/README.md](clients/mac/README.md) for details.

> VivoType is fully self-contained: to remove it, delete `VivoType.app` and the
> `~/Library/Application Support/VivoType/` folder.

### Use the CLI (for developers)

**1. Set up the backend** — pass the directory where the `.venv` should live.
Use the repo root for local development:
```bash
./scripts/setup_core.sh "$(pwd)"      # creates ./.venv and installs dependencies
```
The script needs **Python 3.11+** and exits with a clear message if it isn't found.

**2. Activate the environment** (in each new terminal)
```bash
source .venv/bin/activate              # prompt now starts with (.venv)
```

**3. Transcribe an audio file**
```bash
python core/cli.py sample.wav          # prints the transcribed text
python core/cli.py sample.wav --raw    # per-segment JSON (timings + confidence)
```

### Record your voice (personalization)

Collect labeled voice samples (saved to `core/data/raw/`, kept private):

```bash
python core/record.py --label "the quick brown fox"
```

### Running the tests

```bash
./scripts/run_tests.sh
```

## License

[MIT](LICENSE) © 2025–2026 Kalpit Tiwari
