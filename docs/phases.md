## Execution Plan & Definition of Done (DoD)

**Phase 0: Initial Project Setup**
- Initialize git. Add a sensible `.gitignore` covering `.venv/`, `__pycache__/`, `*.pyc`, `*.wav` (except test fixtures), and macOS system files (`.DS_Store`).
- Propose 3 project name options. *Constraint: Names should be pronounceable by both Indian and international users, evoke voice/speech, and work as a short GitHub repo slug.*
- **DoD:** User approves the project name. An MIT `LICENSE` file (copyright year 2025) and the initial README are generated.

**Phase 1: The Core ASR CLI Prototype**
- Integrate a local Whisper engine (pure Python, pip installable) as the first prototype using the `tiny.en` model. Document this choice in README.
- Create the strict CLI that takes a WAV file and prints transcripts to `stdout`. Implement a `--raw` debugging flag.
- *Raw Flag Spec:* `--raw` outputs raw Whisper segment objects as JSON to stdout, one JSON object per line, including `start`, `end`, `text`, and `avg_logprob` fields. Normal mode outputs only the final joined text.
- **DoD:** `python core/cli.py sample.wav` successfully prints transcribed text to stdout on an M4 Mac.

**Phase 2: Post-Processing & Indic Dictionary**
- Implement the cleanup and JSON dictionary replacement defined in `docs/indic-nlp.md`. Wire it to the CLI.
- **DoD:** `python core/cli.py sample_indian_accent.wav` prints text with filler words removed and "blr" replaced with "Bengaluru".

**Phase 3: macOS Hotkey & Text Insertion**
- Build the macOS background helper defined in `docs/mac-client.md`. 
- **DoD:** User presses the global hotkey, speaks, releases the hotkey, and text successfully appears in the macOS Notes app.

**Phase 4: Personalization Hooks**
- Create the `core/data/` layout and an audio recording script using `sounddevice` or `pyaudio`. Do not build a training loop.
- **DoD:** User can run a script that records their mic and saves a labeled `.wav` file into `core/data/raw/`.

**Phase 4.5: Accuracy & Learning Loop (completed)**
- Default model raised to `small.en` (measured 82.5% → 94% on the user's voice).
- Fuzzy name-matcher (`core/namematch.py`) against a private contacts lexicon.
- Headless correction learning: `core/learn.py` (clipboard diff → log + "Pop" sound) and `core/promote.py` (interactive `[y/N/d]` review → lexicon/dictionary).
- **DoD:** captured corrections can be reviewed and promoted into active rules.

**Phase 5: Native UI Implementation (completed)**
Move from a terminal-run binary to a proper **`.app` bundle** (menu-bar app, `LSUIElement`) built with `swiftc` + a generated `Info.plist` (no Xcode/SwiftPM). This makes macOS permissions attach to **VivoType.app** instead of Terminal. Build iteratively. The four agreed components are the Definition of Done:
1. **Menu-bar app** — status-bar icon with state (idle/recording/transcribing/error) and a dropdown (Pause, Review corrections, Settings, Quit).
2. **Transient recording pill** — a borderless HUD with a live level meter, shown while the hotkey is held, gone on release.
3. **Minimal "correction captured" toast** — a self-dismissing, non-focus-stealing chip (paired with the existing Pop sound; both toggleable).
4. **GUI "Review corrections" panel** — a visual front-end to the promotion flow (Promote / Skip / Discard); the `core/promote.py` CLI is kept as a fallback.
- **DoD:** VivoType runs as a menu-bar `.app`; permissions are granted to VivoType.app; all four components work; dictation + learning remain functional.

**Phase 6: Settings & Polish (completed)**

*Task 1 — Settings window.* A native menu-bar preferences window (open via the dropdown or `⌘,`) with: a push-to-talk **hotkey picker**, **model selection** (`small.en` ↔ `tiny.en`), and **Pop sound / capture toast** toggles. Settings persist to `config.json` (in `core/` for a dev checkout, or `~/Library/Application Support/VivoType/` once installed) and are read by **both** the app and the Python CLI.

*Task 2 — Principal-Engineer polish (4 review→plan→implement rounds).*
- **Correctness:** shared `runProcess` helper (concurrent stdout/stderr drain fixing the full-pipe deadlock, plus a watchdog timeout so a hung child can't wedge the app); chunked Unicode typing for long text; clipboard save/restore instead of clobbering.
- **Robustness:** mic/device-change recovery (cancels cleanly mid-record), bounded per-call subprocess timeouts, atomic config writes (Python + Swift).
- **Cleanup / open-source hygiene:** git-ignored **user-dictionary overlay** so promotions and personal terms merge over the shipped default instead of entering the tracked config; HUD panel de-duplication; docs.
- Also removed two harmful dictionary rules (`problem→prompt`, `plant→plan`).

- **DoD:** Settings persist across restarts and are respected by the backend; the polish pass is complete with 56 tests passing and a warning-free Swift build. ✅

**Deferred to a future phase (not part of Phase 6):** an app icon and CI.

**Phase 7: The Persistent Speed Daemon (completed)**

Eliminates the per-dictation 2–5 s cold-start by keeping the Whisper model warm in a long-lived Python process owned by the app.

*Component 1 & 3 — `core/audioio.py` + backend cleanup.* Lightweight WAV I/O using only stdlib `wave` + `numpy`: `load_wav` (8/16/32-bit, multi-channel, any sample rate → mono 16 kHz Float32) and `write_wav` (Float32 → 16-bit PCM WAV). Removed `soundfile` and `librosa` from `requirements.txt` and from `cli.py`/`record.py`. 13 new tests.

*Component 2 — `core/daemon.py`.* Persistent daemon: loads the model once, loops on stdin reading NDJSON requests, writes NDJSON responses to stdout. Boot status sequence: `loading` → `downloading` (if model not cached) → `ready`. Supports `reload` command (model switch without process restart), `shutdown`, and stdin-EOF clean exit. Feeds `initial_prompt` to `model.transcribe()` to bias Whisper toward the user's personal vocabulary. 17 new tests.

*Component 4 — `clients/mac/main.swift`.* Swift-side audio resampling via `AVAudioConverter`: records at hardware format, converts to mono 16 kHz 16-bit PCM WAV before writing — no Python audio libs in the hot path. `DaemonClient` class manages the daemon process lifecycle (NDJSON pipe, daemonQueue, pending-callback map, graceful shutdown + SIGTERM). New `VivoTypeState.loading` state gates the hotkey until the daemon is ready. `AppDelegate.buildInitialPrompt()` assembles user-dict + contacts-lexicon into a ≤200-char hint. Settings model changes trigger `DaemonClient.reload(model:)`.

- **DoD:** Daemon boots once; second+ dictations are sub-second; loading state shown during startup and model reload; `initial_prompt` biasing active; `soundfile`/`librosa` removed; one-shot CLI fallback remains functional; 86 tests passing; warning-free Swift build. ✅

**Deferred to a future phase:** app icon, CI.

**Phase 8: Self-Contained App Bundle (completed)**

Makes `VivoType.app` fully runnable from `/Applications` with no Git repo or manual terminal setup.

*`clients/mac/build_app.sh` (rewritten).* Bundles `core/`, `scripts/`, `requirements.txt`, and a plain `VERSION` file (git tag → short hash → `dev`) into `Contents/Resources/`. Excludes personal data (`corrections.jsonl`, `user_dictionary.json`, `lexicon/`, `labels.csv`, `raw/`), `tests/`, and `config.json` so no private data ships in the bundle.

*`scripts/setup_core.sh` (rewritten).* Now accepts a required `<app-support-dir>` argument (never guesses a path). Python detection scans `python3.20 … python3.11` then generic names to future-proof against new releases. Exit 42 when no 3.11+ interpreter is found; exit 0 on success; exit 1 on other failures.

*`core/paths.py` (new).* Single source of truth for writable runtime paths. Resolution order: `$VIVOTYPE_DATA_DIR` → `$VIVOTYPE_APP_SUPPORT/data` → bundled `core/data` if writable (dev checkout) → `~/Library/Application Support/VivoType/data`. Seeds bundled defaults into App Support on first use. `config.py`, `learn.py`, `promote.py`, `postprocess.py`, `namematch.py` all resolve their paths through it.

*`clients/mac/main.swift` (extended).* Exports `VIVOTYPE_APP_SUPPORT` and `HF_HOME` via `setenv` at launch so every Python child agrees on the writable home and model cache directory. On first launch (no `.venv`), shows `OnboardingController` (spinner, status label, retry button) and runs `setup_core.sh <app-support-dir>` async; exit 42 → "Python 3 is missing" message. Path helpers `vivotypeResourcesURL()` / `vivotypeAppSupportURL()` / `vivotypeVenvPython()` resolve immutable vs. mutable paths; `vivotypeVersion()` reads the bundled `VERSION` for the menu-bar version display. The legacy headless `build.sh` / `vivotype-dictate` path is removed.

- **DoD:** First-run onboarding creates the venv and installs deps; daemon spawns from the bundle's Python; model cache lives in App Support; zero network sockets at idle; 86 tests pass; permissions attach to VivoType.app (not Terminal). ✅

**Deferred to a future phase:** app icon, CI.

**Phase 9: MLX Hardware Acceleration (completed)**

Swaps the CPU transcription engine for Apple's MLX, so dictation runs on the M-series GPU / Neural Engine instead of the CPU.

*`core/asr.py` (new).* Single MLX transcription backend shared by the daemon and the one-shot CLI. Maps a Whisper size (e.g. `small.en`) to its `mlx-community/whisper-<name>-mlx` HF repo, warms the model on construction by priming mlx-whisper's process-wide `ModelHolder` cache (float16) so repeated dictations reuse resident GPU weights, and returns lightweight Whisper segments (`start`/`end`/`text`/`avg_logprob`) so callers stay backend-agnostic. Transcribes with `verbose=False`, keeping the daemon's stdout a clean NDJSON channel.

*`core/daemon.py`, `core/cli.py`, `core/benchmark.py`.* Route transcription through `core/asr.py`; the NDJSON IPC protocol (ADR-0002) is unchanged.

*`core/paths.py` (extended).* New `models_dir()` resolves the writable model-cache root (`$VIVOTYPE_MODELS_DIR` → `$VIVOTYPE_APP_SUPPORT/models` → `~/Library/Application Support/VivoType/models`); `core/asr.py` points `HF_HOME` there so weights never land in the read-only bundle (ADR-0003).

*`requirements.txt`.* `faster-whisper` → `mlx-whisper`.

- **DoD:** `faster-whisper`/`ctranslate2` fully removed; 86 tests pass; live dictation runs on `Device(gpu, 0)` with warm transcribes (~0.9 s) and model weights cached under App Support. ✅

**Deferred to a future phase:** app icon, CI.

> **Note on numbering:** from here on, some phases carry a parenthetical *(tracked as "Phase N" in the upgrade plan)*. These refer to a separate, later "upgrade plan" that restarted its own count; the canonical sequence is the one in this file. The parentheticals are kept only so cross-references in older notes still resolve.

**Phase 10: Swift Modularization (completed)** *(tracked as "Phase 8" in the upgrade plan)*

Breaks the ~69 KB `clients/mac/main.swift` monolith into focused, single-responsibility files compiled together by `swiftc` — no Xcode project or SwiftPM (see ADR-0005). Behaviour is unchanged; the split is purely structural.

*New source layout (`clients/mac/`).* `Support.swift` (process/IO helpers, `VivoTypeState`, bundle/App-Support path resolvers), `Dictation.swift` (mic capture + native 16 kHz resample, text injection, clipboard correction-learning), `Daemon.swift` (`DaemonStatus` + `DaemonClient` NDJSON bridge), `Settings.swift` (config.json model), `App.swift` (`AppDelegate` + the `@main` entry point), and a `UI/` folder: `HUD.swift` (recording pill + capture toast), `ReviewController.swift`, `SettingsController.swift`, `OnboardingController.swift`.

*`clients/mac/build_app.sh`.* The `swiftc -O` step now discovers every `*.swift` file under `clients/mac/` (excluding `build/`) and compiles them as one module, so new files are picked up automatically. With `main.swift` gone, the `@main` attribute in `App.swift` provides the single executable entry point.

- **DoD:** `main.swift` deleted and replaced by 9 modules; `build_app.sh` produces a working, signed `VivoType.app`; the rebuilt app launches, spawns the daemon, and the hotkey/UI behave exactly as before. ✅

**Phase 11: Seamless Permissions UX (completed)** *(tracked as "Phase 10" in the upgrade plan)*

Replaces the abrupt, out-of-context macOS Microphone/Accessibility prompts with a guided, premium first-run checklist.

*`clients/mac/UI/PermissionsController.swift` (new).* A "Welcome to VivoType" window with two cards (Microphone, Accessibility), each explaining *why* it's needed and that audio stays on-device, plus an in-context **Grant** button. A 0.7 s poll watches the live authorization state (there is no callback for Accessibility grants) and flips each row from its Grant button to a green **Enabled** badge the instant access is granted; a previously-denied permission shows **Open Settings** and deep-links into the right System Settings pane.

*`clients/mac/App.swift`.* `finishLaunch()` now installs the hotkey/clipboard monitor and warms the daemon up-front (so the model loads while the user reads the checklist), then calls `presentPermissionsIfNeeded()`. The checklist is shown only when Microphone or Accessibility is not yet granted, so returning users are never interrupted. The old `requestPermissions()` (raw prompts) is removed.

- **DoD:** a new user launches, sees the checklist, grants via the guided UI, and watches the statuses update live before dictating. ✅

**CI (completed).** GitHub Actions (`.github/workflows/test.yml`) runs the stdlib-unittest suite on Linux (Python 3.9 / 3.11 / 3.13, numpy-only — mlx-whisper has no Linux wheel and the tests mock it) and builds `VivoType.app` on a macOS runner via `build_app.sh`.

**Phase 12: Permissions Bug Fixes (completed)**

Two regressions introduced by the guided-checklist UX (Phase 11):

1. **Window focus lost after mic grant.** When the system Microphone dialog dismissed, the permissions window dropped behind other apps. Fixed by calling `ActivationCoordinator.shared.refocus(_:)` inside the `AVCaptureDevice.requestAccess` completion handler, which re-asserts focus without touching the ref count.

2. **Accessibility double-prompt stacking.** Clicking **Grant** a second time called both `AXIsProcessTrustedWithOptions(prompt:)` (which no longer re-shows the dialog) and `openPrivacySettings`, stacking two windows. Fixed with a `hasPromptedAX` `UserDefaults` flag that mirrors the system's own prompt-once behaviour: first click → native prompt only; subsequent clicks → jump straight to System Settings.

- **DoD:** mic grant leaves the permissions window in the foreground; the AX prompt is shown at most once per install; both fixes verified by build + manual test. ✅

**Phase 13: Transient `.regular` Activation for Setup Windows (completed)**

VivoType is a background `.accessory` menu-bar agent, but its setup/permissions/settings windows need to reliably foreground on macOS 14+. The deprecated `NSApp.activate(ignoringOtherApps:)` API is largely ignored by the OS in `.accessory` mode, causing windows to drop behind after system dialogs.

*`clients/mac/UI/ActivationCoordinator.swift` (new).* A ref-counted singleton (`begin()` / `end()` / `refocus(_:)`) that switches `NSApplication.ActivationPolicy` to `.regular` only while at least one setup window is open, then defers the revert one runloop turn on the last close to prevent Dock-icon flicker during the onboarding→permissions hand-off. Uses `NSApp.activate()` on macOS 14+ with a `activate(ignoringOtherApps:)` fallback.

Each window controller (`OnboardingController`, `PermissionsController`, `SettingsController`) holds a `heldForeground` guard so repeated `show()` calls can't unbalance the count; each is its window's `NSWindowDelegate` so the red close button correctly calls `end()`.

The transient recording HUD/toast is intentionally excluded — it must never steal focus.

Decision recorded in [ADR-0006](adr/0006-transient-regular-activation.md).

- **DoD:** setup/permissions/settings windows reliably foreground after system dialogs; no permanent Dock presence; `ActivationCoordinator` is the single owner of policy changes; ADR-0006 merged. ✅

**Phase 14: Native UI Redesign (completed)**

Applies a Figma design brief's layouts to the shipped app using native system colors (no brand hex values), so the UI auto-adapts to the user's accent color and light/dark appearance.

*`clients/mac/UI/BrandMark.swift` (new).* An accent-tinted rounded-square view with a `waveform` SF Symbol glyph and a top-down `CAGradientLayer`, used as a temporary app logo in first-run windows until a real AppIcon ships. Replace usages with `NSApp.applicationIconImage` when the icon lands.

*`clients/mac/UI/PermissionsController.swift` (updated).* Added the `BrandMark` header (64 pt), a shield-glyph + "100% on-device" privacy line, tightened permission-card geometry (title 200 pt wide, detail 205 pt wide) to prevent text wrapping under the Grant button, and shortened card copy to fit.

*`clients/mac/UI/OnboardingController.swift` (updated).* Added the `BrandMark` header (48 pt) and adjusted element positions for the taller window.

*`clients/mac/UI/SettingsController.swift` (rewritten).* Replaced hardcoded `NSRect` layout with Auto Layout + `NSStackView`. Layout: section headers (DICTATION, NOTIFICATIONS) → grouped cards with hairline separators → rows with a tinted SF Symbol chip (28×28, `controlAccentColor` background), title/description labels, and a trailing native control (`NSPopUpButton` for hotkey/model, `NSSwitch` for toggles). All colors are system semantic (`controlBackgroundColor`, `separatorColor`, `controlAccentColor`, `labelColor`).

*`clients/mac/App.swift` (updated).* `statusSymbol()` returns a small colored circle SF Symbol (green = ready, red = recording, blue = transcribing, orange = loading/error). `rebuildMenu()` sets the menu header image to this symbol. Added a **Model** submenu with checkmarks on the active model and a `selectModel(_:)` action that switches the model, saves settings, and reloads the daemon.

- **DoD:** onboarding, permissions, settings windows use the new layout; menu shows a live status dot and a Model submenu; all colors are system semantic; build passes; manual verification confirmed. ✅

**Phase 15: App Icon & Brand Assets (in progress)**

Ships VivoType's first real app icon (previously deferred since Phase 6) and lands a starter brand package.

*`clients/mac/Assets/VivoType.iconset/` (new).* The 10 macOS icon sizes (16–1024 px, @1x/@2x) generated from the dark "pebble + waveform" tile.

*`clients/mac/build_app.sh` (updated).* New step compiles the iconset into `Contents/Resources/AppIcon.icns` via `iconutil` (ships with macOS — no install), guarded so non-macOS/CI paths skip cleanly; added to the build-summary checks.

*`clients/mac/Info.plist` (updated).* Added `CFBundleIconFile = AppIcon` so Finder, the Dock, and notifications use the icon.

*`clients/mac/UI/BrandMark.swift` (updated).* Added `makeVivoTypeMark(size:)` — returns the real `NSApp.applicationIconImage`, falling back to the drawn `BrandMarkView` if the bundle has no icon.

*`OnboardingController.swift` / `PermissionsController.swift` (updated).* First-run windows now use `makeVivoTypeMark(...)` instead of the placeholder.

*`branding/` (new).* Brand basics (palette/type/usage), the branding review, the beginner roadmap, and "VivoType AI" wordmark lockups (transparent/light/dark).

- **DoD (pending macOS build):** `build_app.sh` produces `VivoType.app` whose icon appears in Finder/Dock; first-run windows show the real icon; warning-free Swift build; manual verification on an Apple-Silicon Mac. ⏳

**Deferred to a future phase:** Liquid Glass `.icon` (Icon Composer) for macOS 26; editable vector master; finalized wordmark in Inter.
