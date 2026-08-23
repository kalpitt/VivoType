# VivoType Quickstart

Companion to the [README Getting Started](../README.md#getting-started). This page is the short path for installing, first launch, and developer CLI work.

**Requires:** Apple Silicon Mac (M1+) on macOS 13+, and **Python 3.11+** for the engine.

## Install the app (recommended)

Pick **one** way to get `VivoType.app` into `/Applications`:

| Option | Command / steps |
|--------|-----------------|
| **One-liner** | `curl -fsSL https://raw.githubusercontent.com/kalpitt/VivoType/main/install.sh \| bash` |
| **Homebrew** | `brew tap kalpitt/vivotype` then `brew install --cask vivotype` |
| **Manual** | Download the newest zip from [Releases](https://github.com/kalpitt/VivoType/releases), unzip, drag into Applications; first open via **right-click → Open** |

Options 1 and 2 skip the Gatekeeper right-click step. Full plain-English walkthrough: [README → Getting Started](../README.md#getting-started).

## First launch

1. **Open VivoType** from Applications. A **"Welcome to VivoType"** window sets up the private engine (~1 minute the first time).
2. **Grant permissions** — Microphone and Accessibility for **VivoType** (not Terminal). Each card turns green when enabled.
3. **Try it once** — after the model loads, hold **`Right-Option`** and say something into the practice field (same move you'll use in any app). You can skip if you prefer.
4. **Dictate anywhere** — hold **`Right-Option`**, speak, release. Words appear in the focused app. The first dictation may download the ~466 MB speech model once; after that, dictation is fully offline and fast.

> **Uninstall:** delete `VivoType.app` and `~/Library/Application Support/VivoType/`.

## Build from source (developers)

```bash
./clients/mac/build_app.sh
```

Produces `clients/mac/build/VivoType.app`. Drag into `/Applications` and continue from **First launch** above. Prefer [`/build-sign`](../.claude/skills/build-sign/SKILL.md) so Accessibility/Microphone grants survive rebuilds.

## CLI workflow (developers)

```bash
./scripts/setup_core.sh "$(pwd)"      # creates ./.venv, installs deps
source .venv/bin/activate             # always, before any Python command
python core/cli.py path/to/audio.wav  # prints the transcript
```

`setup_core.sh` requires **Python 3.11+** and exits with a clear message if it isn't found.

## Record your voice (personalization samples)

```bash
source .venv/bin/activate
python core/record.py --label "the quick brown fox jumps over the lazy dog"
```

Press **Return** to start, speak, press **Return** to stop. Clips go to
`core/data/raw/` (kept out of git).

## Measure accuracy on your voice

1. Record the standard paragraph:

```bash
source .venv/bin/activate
python core/record.py --label "$(cat core/data/prompts/training-paragraph.txt)"
```

2. Compare models:

```bash
python core/benchmark.py core/data/raw --reference-file core/data/prompts/training-paragraph.txt
```

Prints an accuracy % for `tiny.en` vs `small.en` on your own recordings.

## Run the tests

```bash
source .venv/bin/activate
./scripts/run_tests.sh
```

Should end with **`OK`** (the suite prints its own test count — trust that over any number written here).

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Setup window says Python is missing | Install Python 3.11+ ([python.org](https://www.python.org/downloads/macos/) or `xcode-select --install`), then click **Retry**. |
| "VivoType can't be opened / is damaged" | Manual-download only: right-click → **Open** once. Prefer the curl or Homebrew install. |
| Holding Right-Option does nothing | Grant **Accessibility** (and **Input Monitoring**) to **VivoType**, then quit & relaunch. |
| No text appears after speaking | Check **Microphone** for **VivoType**; click into the target app first, then hold the hotkey. |
| First dictation is very slow | One-time ~466 MB model download — wait; later dictations are offline and fast. |
| `command not found` / `no such file` (CLI) | Stay in the repo root and run `./scripts/setup_core.sh "$(pwd)"` first. |

Privacy and what VivoType never does: [README → What VivoType never does](../README.md#what-vivotype-never-does).
