# VivoType — macOS Dictation Helper

A small background helper: **hold a hotkey, speak, release, and your words are typed into whatever app you're using.** It records the mic, sends the audio to VivoType's local `core/` CLI, and injects the returned text.

## Build & Run (menu-bar app)

You need Xcode Command Line Tools (`xcode-select --install`) — present if `swiftc --version` works.

```bash
./clients/mac/build_app.sh           # -> clients/mac/build/VivoType.app
open clients/mac/build/VivoType.app     # or drag VivoType.app into /Applications first
```

The app is **self-contained** — you do **not** need to set up the Python backend by hand. On first launch VivoType shows a **“Welcome to VivoType”** window and builds its own engine under `~/Library/Application Support/VivoType/` (a `.venv`, the model, your data, logs). This needs **Python 3.11+**; if it's missing, VivoType shows an install hint and a **Retry** button.

## Source Layout

There is **no Xcode project** — `build_app.sh` compiles every `.swift` file under `clients/mac/` together as one module with a single `swiftc` call (see [ADR-0005](../../docs/adr/0005-text-based-multi-file-swift-build.md)), so adding a new file needs no build-script change. The code is split by responsibility:

| File | Responsibility |
|------|----------------|
| `App.swift` | `AppDelegate` lifecycle + the `@main` entry point |
| `Support.swift` | process/IO helpers, `VivoTypeState`, bundle/App-Support path resolvers |
| `Dictation.swift` | mic capture + native 16 kHz resample, text injection, correction learning |
| `Daemon.swift` | persistent Python daemon client (NDJSON over stdin/stdout) |
| `Settings.swift` | `config.json` model shared with the backend |
| `UI/ActivationCoordinator.swift` | ref-counted `.regular`↔`.accessory` policy manager for setup windows |
| `UI/BrandMark.swift` | accent-tinted rounded-square waveform logo used in first-run windows |
| `UI/HUD.swift` | recording pill + capture toast |
| `UI/PermissionsController.swift` | first-run permissions checklist |
| `UI/OnboardingController.swift` | first-run engine-setup window |
| `UI/SettingsController.swift`, `UI/ReviewController.swift` | settings + correction-review windows |

VivoType lives in the **menu bar** (a mic icon, no Dock icon). The icon shows state — idle / recording / transcribing / error — and clicking it gives Pause, Review corrections, Settings, the build version, and Quit. On first launch (before either permission is granted) VivoType shows a guided **Permissions** checklist that explains and requests Microphone and Accessibility access (see below). Then **hold `Right-Option`, speak, release** to insert text into the focused app.

## Required Permissions

VivoType needs two macOS permissions. On first launch a guided **"Welcome to VivoType"** checklist appears with a card for each — it explains *why* each is needed (and that your audio never leaves your Mac), offers a **Grant** button, and flips to a green **Enabled** badge the moment access is granted. Returning users who have already granted both are never interrupted by it.

If you ever need to change them by hand, approve **VivoType** itself (this is the whole point of the `.app` — permissions no longer attach to Terminal). Open **System Settings → Privacy & Security**:

| Permission | Why | Where |
|------------|-----|-------|
| **Microphone** | Record your voice | Privacy & Security → **Microphone** → enable VivoType |
| **Accessibility** | Detect the global hotkey and type/paste text into other apps | Privacy & Security → **Accessibility** → enable VivoType |

> If the hotkey doesn't respond, also enable VivoType under **Privacy & Security → Input Monitoring**, then relaunch the helper. After changing any permission, quit and restart the helper.

## How Text Is Inserted

- **Default:** synthetic keystrokes via the Accessibility API (types Unicode directly).
- **Clipboard paste (`Cmd+V`) fallback** is used automatically when either:
  1. Accessibility permission is **not** granted, or
  2. the focused app is a **web browser** (Safari, Chrome, Firefox, Edge, Arc, Brave, …), where synthetic keystrokes are unreliable.

## Learning From Your Corrections

When you fix VivoType's output, **copy the whole corrected text** (`Cmd+A`, `Cmd+C`). The helper notices the clipboard now holds an edited version of what it just typed, logs the differences locally to `~/Library/Application Support/VivoType/data/corrections.jsonl`, plays a soft **"Pop"**, and shows a brief **"✓ Correction captured"** toast. These are suggestions for later review — nothing is auto-applied, and the data never leaves your Mac.

Review them anytime via the menu-bar icon → **Review corrections…**, which gives a Promote / Skip / Discard list (the same logic as `python core/promote.py`, which stays as a CLI fallback).

## Settings

Open **Settings…** from the menu-bar icon (or **⌘,**) to change:
- **Push-to-talk key** (default Right Option)
- **Model** — `small.en` ↔ `tiny.en` (changing the model reloads the daemon; the icon shows a loading state briefly)
- **Pop sound** and **capture toast** on/off

Settings are saved to `~/Library/Application Support/VivoType/config.json` and read by the Python backend too, so the model choice stays consistent across the app and the daemon.

## Speed — the persistent daemon

On first launch the menu-bar icon shows a **loading state** (ellipsis) while the Python backend loads the Whisper model into RAM. Once ready (icon switches back to the mic), the hotkey is enabled. Every subsequent dictation reuses the warm model — no Python startup cost, no model reload — so transcription completes in well under a second on Apple Silicon.

If the daemon crashes, VivoType falls back silently to the one-shot CLI path (slightly slower, but still functional).

## Configuration

By default the app resolves everything automatically: immutable Python code from the app bundle (`Contents/Resources/`), and all writable state from `~/Library/Application Support/VivoType/`. These environment variables override that (mainly for development):

- `VIVOTYPE_APP_SUPPORT` — writable home for `.venv`, `models`, `data`, `logs`, `config.json` (default: `~/Library/Application Support/VivoType`)
- `VIVOTYPE_PYTHON` — path to the Python interpreter (default: `<app-support>/.venv/bin/python`)
- `VIVOTYPE_CLI` — path to the CLI (default: `<bundle>/Contents/Resources/core/cli.py`)

The push-to-talk key is currently `Right-Option` (key code 61).
