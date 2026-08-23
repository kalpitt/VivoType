# VivoType 🎙️

**VivoType** is a fully local, open-source voice dictation tool for macOS — inspired by Wispr Flow, but with **100% on-device processing**. No cloud, no accounts, no audio ever leaving your Mac.

Hold a hotkey, speak, release — your words appear in whatever app you're focused on.

> **Status:** Beta — feature-complete. The project was built in phases (see [`docs/phases.md`](docs/phases.md)): local ASR, Indic post-processing, the native macOS client, MLX hardware acceleration, guided permissions, and the app icon have all landed. Recent additions: per-app post-processing contexts (different rules per destination app) and bounded voice-editing commands ("scratch that", "new paragraph", "all caps that").

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

**Requires:** an Apple Silicon Mac (M1/M2/M3/M4) running macOS 13 (Ventura) or later.
Intel Macs are not supported.

### Install VivoType (recommended — under 5 minutes, no coding required)

Pick **one** of these three ways to get `VivoType.app` onto your Mac. All three end
up in the same place: `VivoType.app` in your Applications folder.

**Option 1 — one line in Terminal (fastest, no security warning)**

Open the **Terminal** app (search for it with Spotlight: `Cmd+Space`, type
`Terminal`, press Return), paste this, and press Return:
```bash
curl -fsSL https://raw.githubusercontent.com/kalpitt/VivoType/main/install.sh | bash
```
*What this does, in plain English:* downloads the latest ready-to-run VivoType
build, checks it hasn't been tampered with, and installs it into your
Applications folder — no compiling, no Xcode.

**Option 2 — Homebrew** (if you already use [Homebrew](https://brew.sh))
```bash
brew tap kalpitt/vivotype
brew install --cask vivotype
```

**Option 3 — manual download** (no Terminal at all)
1. Go to the [Releases page](https://github.com/kalpitt/VivoType/releases) and
   download the newest `VivoType-vX.X.X.zip`.
2. Double-click the zip to unzip it — you get `VivoType.app`.
3. Drag `VivoType.app` into your **Applications** folder.
4. The first time you open it, **right-click the app → Open** (once). macOS
   shows a warning for any app not sold through the App Store — this is normal
   and only appears the first time.

> Options 1 and 2 remove that warning automatically, so if you'd rather skip
> the right-click step, use one of those.

### First launch

1. **Open VivoType** from `/Applications` (or Spotlight). A **"Welcome to
   VivoType"** window appears and sets up VivoType's private engine — this
   takes about a minute the first time and needs **Python 3.11+** already on
   your Mac (most current Macs have it; if not, VivoType tells you and offers
   a **Retry** button after you install it).
2. **Grant permissions** — a guided checklist walks you through the two
   permissions VivoType needs, one card each, with a **Grant** button and a
   green **Enabled** badge once done:
   - **Microphone** — so VivoType can hear you speak.
   - **Accessibility** — so VivoType can type your words into whatever app
     you're using. (macOS requires this for *any* app that types on your
     behalf — it's not specific to VivoType.)

   If a card doesn't flip to green after clicking Grant, open **System
   Settings → Privacy & Security**, find the permission in the list, and
   enable **VivoType** by hand.
3. **Dictate** — hold **`Right-Option`**, speak, release. Your words appear in
   whatever app has focus. The first dictation is slower (VivoType downloads a
   ~466 MB speech model once); every one after that is fast.

See [clients/mac/README.md](clients/mac/README.md) for the full permissions
reference and how text insertion works.

> **Uninstall:** delete `VivoType.app` and the
> `~/Library/Application Support/VivoType/` folder. That's the whole app — it
> never writes anywhere else on your Mac.

### Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Setup window says Python is missing | No Python 3.11+ interpreter found | Install it (`xcode-select --install` often provides it, or get Python 3 from [python.org](https://www.python.org/downloads/macos/)), then click **Retry** in VivoType. |
| "VivoType can't be opened / is damaged" | macOS quarantines apps downloaded outside Options 1/2 | Right-click the app → **Open** → **Open** again in the dialog. Only needed once, and only for the manual-download option. |
| Holding Right-Option does nothing | Accessibility (and sometimes Input Monitoring) not granted | System Settings → Privacy & Security → **Accessibility** (and **Input Monitoring**) → enable **VivoType** → quit and reopen VivoType. |
| No text appears after you speak | Microphone permission not granted, or the app you're dictating into wasn't focused | Check System Settings → Privacy & Security → **Microphone** → **VivoType** is on; click into the target app first, then hold the hotkey. |
| First dictation is very slow / seems stuck | The ~466 MB speech model is downloading on first use | Wait for it to finish (one-time, needs internet); every dictation after this is fast and fully offline. |

### What VivoType never does

- **Never sends your audio or text anywhere.** No cloud APIs, no analytics, no
  telemetry, no crash reporting that leaves your Mac.
- **No account, no sign-in, no network requirement to dictate.** The one-time
  speech-model download is the only network activity, and dictation works
  fully offline afterward.
- **Never reads or types outside what you dictate.** VivoType only sees the
  audio while you hold the hotkey and only types into the app you're focused on.
- **Everything it writes stays in one place:** `~/Library/Application
  Support/VivoType/` (its model, your personal dictionary, correction history,
  and logs). Deleting that folder plus the app removes VivoType completely.

### Build the app from source (for developers)

If you'd rather build `VivoType.app` yourself instead of downloading a release,
you need Xcode Command Line Tools (`xcode-select --install` if prompted):
```bash
./clients/mac/build_app.sh
```
This produces `clients/mac/build/VivoType.app` and prints a build summary. Drag
it into `/Applications` and continue from "First launch" above.

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
