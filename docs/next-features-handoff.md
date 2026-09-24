# VivoType — Handoff: "Next" Features (Trust Surface, Per-App Profiles, Voice Commands)

> **STATUS 2026-08-23: ALL THREE FEATURES SHIPPED.** Feature 2 merged (#29),
> Feature 3 merged (#34), Feature 4 merged (#36). The sketches below are
> kept as historical spec; where the code deliberately deviates, an "As
> built" note at the top of each feature section is authoritative — read the
> code and tests as final truth. Kept per your call (1a) — archive when you
> say so.

*Written 2026-07-18 for a fresh session (any model tier) to implement without
further architectural judgment calls. Cross-references
`docs/competitive-landscape.md` for the "why"; this document carries the full
"how," verified directly against the code as of this date.*

## Before you start — non-negotiable rules

1. **Layer split** (per `AGENTS.md`): ASR/text-processing logic lives in
   `core/` (Python, stdlib 3.9+ compatible); macOS UI/permissions/injection
   lives in `clients/mac/` (Swift). Don't cross it.
2. **Test convention**: stdlib `unittest`, one `core/tests/test_<module>.py`
   per `core/<module>.py`, classes named `<Thing>Tests`, `from core import
   <module>`, `tempfile.TemporaryDirectory()` for any file-touching test.
   Follow the exact shape of existing files like `core/tests/test_postprocess.py`
   or `core/tests/test_config.py` — don't invent a different style.
3. **Governance gate — read this twice.** `AGENTS.md`'s "must ask first" list
   names **daemon IPC** and **text-injection paths** explicitly. Feature 3's
   new `profile` NDJSON field and Feature 4's new `command` NDJSON field +
   its backspace-synthesis injection code are exactly those things. **Before
   writing the `daemon.py`/`Daemon.swift` protocol changes in Feature 3, and
   before writing the protocol + injection changes in Feature 4, stop and
   state your plan to Kalpit, then wait for explicit approval** (a real
   "APPROVED" or equivalent, not a 1-2 character reply). This is true even
   though the roadmap item itself is pre-approved — the specific technical
   approach still needs sign-off per the repo's own contract. Feature 2 has
   no protocol changes and needs no such pause.
4. Work on a feature branch. Never push to or commit on `main` — the guard
   hook blocks it mechanically (note: as of 2026-07-18, the Edit/Write
   content-scanning half of that hook was found not to fire in at least one
   session type — don't rely on it as your only safety net; follow the rules
   above regardless of whether a hook catches a violation).
5. Build with `/build-sign` (`VIVOTYPE_SIGN_ID="VivoType Dev" ./clients/mac/build_app.sh`)
   so Accessibility/Microphone grants survive rebuilds. Run
   `./scripts/run_tests.sh` after every `core/` change.

Build order: **Feature 2 → Feature 3 → Feature 4** (2 has zero dependencies
and no gate; 3 is optional substrate for 4 but not required by it; 4 has the
most risk, ship it last).

---

## Feature 2 — Trust-surface polish pass

No daemon-IPC or text-injection changes — build straight through, no gate.

### What to build

**New "Privacy" card** in `clients/mac/UI/SettingsController.swift`, placed
after the existing "Notifications" card, using the file's existing
`makeCard`/`makeRow(symbol:title:desc:control:)`/`makeIconChip`/`sectionLabel`
builders and its fixed `contentWidth = 412`:
- Row A: `symbol: "lock.shield.fill"`, title "On-device processing", desc
  "Your voice and text never leave this Mac.", control a non-interactive
  `NSTextField(labelWithString: "Local")` (`.secondaryLabelColor`).
- Row B: `symbol: "network"`, title "Network activity", desc "Only used
  once, to download the speech model.", control a label that reads "Idle"
  normally and "Downloading model…" only during the one-time model
  download (the app's only real network event). Drive this from a new
  `AppDelegate` bool `isDownloadingModel`, set `true` inside the
  `.loading(let downloading)` case of `startDaemon()`'s `onStatusChange`
  closure (App.swift ~517) when `downloading != nil`, `false` on
  `.ready`/`.error`. Expose it to `SettingsController` via a closure
  (`var isDownloading: (() -> Bool)?`), refreshed on a 1s `Timer` while the
  Settings window is open (invalidate in `windowWillClose`).

**Model relabeling — single source of truth.** Add to `clients/mac/Settings.swift`:
```swift
struct ModelOption { let label: String; let id: String }
enum ModelCatalog {
    static let all: [ModelOption] = [
        ModelOption(label: "Fast (tiny)",       id: "tiny.en"),
        ModelOption(label: "Balanced (small)",  id: "small.en"),
        ModelOption(label: "Accurate (medium)", id: "medium.en"),
    ]
    static func label(for id: String) -> String { all.first { $0.id == id }?.label ?? id }
}
```
- `SettingsController.swift`: `modelPopup` currently does
  `modelPopup.addItems(withTitles: ["small.en", "tiny.en"])` (line ~69) —
  **note `medium.en` is currently missing here even though it's a valid
  model** — replace with `ModelCatalog.all.map { $0.label }`. In
  `syncControls()` select by matching `settings.model` to the right
  `ModelOption.id` and selecting that index. In `changed()`, set
  `settings.model = ModelCatalog.all[max(0, modelPopup.indexOfSelectedItem)].id`.
- `App.swift`'s Model submenu (in `rebuildMenu()`, currently
  `for name in ["small.en", "tiny.en"]` — line ~362, a second,
  independently-hardcoded 2-item list) — iterate `ModelCatalog.all` instead;
  set `item.title = option.label`, `item.state = (settings.model == option.id)
  ? .on : .off`, and **`item.representedObject = option.id`**.
- `selectModel(_:)` (App.swift ~857) currently does `let model = sender.title`
  — **this breaks once titles become display labels**. Change to
  `guard let model = sender.representedObject as? String else { return }`.
  Everything downstream (`settings.model = model`, `daemon.reload(model:)`)
  is unchanged.

**Pending-corrections badge.** Reuse the exact subprocess contract
`ReviewController.runList()` already uses (ReviewController.swift ~245-251):
shell `promotePath --list-json`, parse the JSON array, count = `array.count`.
In `AppDelegate`: cache `pendingCorrectionsCount`, add a
`refreshCorrectionsCount()` that runs the subprocess on a background queue
and updates the cached count + calls `rebuildMenu()` on completion. Trigger
it from **both**: (a) `NSMenuDelegate.menuWillOpen(_:)` on the status menu,
and (b) the existing `controller.onCaptured` closure (App.swift ~435).
Render as a suffix on the existing "Review corrections…" item (App.swift
~356): `"Review corrections… (\(count))"` when `count > 0`, else unchanged.
Don't touch the menu-bar icon itself.

**HUD-less "sound-only" toggle.**
- `core/config.py`'s `DEFAULTS` dict (~line 41-47) needs a new
  `"hud_enabled": True` entry. **This is required, not optional** — Python's
  `save_settings()` re-merges over `DEFAULTS` on every save (line ~68-69), so
  a Swift-only key with no Python-side default silently vanishes the next
  time anything saves through the Python path.
- `Settings.swift`: new `var hudEnabled = true` field with load/save wiring
  matching the existing `soundEnabled`/`toastEnabled` fields.
- New row in `SettingsController.swift`'s Dictation card: `symbol:
  "eye.slash"`, title "Hide recording HUD", desc "Use sounds instead of the
  on-screen pill", an `NSSwitch` (on = HUD hidden).
- In `AppDelegate.setState()` (App.swift ~283-292): when
  `!settings.hudEnabled`, skip the `pill?.setMode`/`pill?.show()`/
  `pill?.setProcessing()` calls in the `.recording`/`.transcribing`
  branches — the `default: pill?.hide()` branch stays harmless either way.
- Add a start/stop sound pair for when the pill is hidden, e.g.
  `NSSound(named: "Tink")` on entering `.recording`,
  `NSSound(named: "Purr")` on entering `.transcribing`, gated on
  `!settings.hudEnabled && settings.soundEnabled`. **Leave "Pop" reserved**
  for the existing correction-captured cue (Dictation.swift ~394) — don't
  reuse it here.
- **Decision, don't re-litigate:** HUD-less suppresses only the recording
  **pill**. It does NOT suppress the ⚠ warning toast (inject-blocked,
  transcription-error) — that toast carries information a silent failure
  would otherwise lose.

**Error-glyph fix — ship this always-on, independent of the HUD-less
toggle** (it's a plain correctness bug, not a new feature). In `setState()`
(App.swift ~255), the `.error` case unconditionally sets the orange
`exclamationmark.triangle` glyph (line ~267) with no check for whether the
user is still mid-dictation. Two real call sites can fire `.error` while the
hotkey is held: `Dictation.handleConfigChange()` (audio device changed
mid-capture) and `Dictation.startRecording()`'s catch block (engine failed
to start). Fix: in `setState`, when `state == .error` and
(`hotkeyPressTime != nil || isHandsFree`) is true, render the `.recording`
glyph (`symbol = "waveform"`, `tint = nil`) instead of the orange triangle,
and skip scheduling the 5s `errorEpoch` auto-recovery for that case (the
recording will resolve to a real state shortly on its own). Both
`hotkeyPressTime` and `isHandsFree` already exist as `AppDelegate` properties.

**Export/import bundle.** Single JSON file, versioned, 3 sub-keys:
```json
{
  "version": 1,
  "config": { /* full contents of config.json */ },
  "user_dictionary": { /* full contents of data/user_dictionary.json */ },
  "contacts_lexicon": { /* data/lexicon/contacts.json — ONLY if opted in */ }
}
```
Resolve the 3 real files under `vivotypeAppSupportURL()` (Swift already
duplicates these exact relative paths as literal strings in
`buildInitialPrompt()`, App.swift ~573/~580 — `config.json`,
`data/user_dictionary.json`, `data/lexicon/contacts.json`). **Naming trap to
avoid:** despite this feature's shorthand name mentioning
"postprocess_config.json", do **NOT** export the tracked
`core/postprocess_config.json` — that's the shipped default with no user
data. The real per-user file is `user_dictionary.json`. New "Backup &
Restore" card: "Export…"/"Import…" `NSButton`s + an "Include contact names"
`NSSwitch` (off by default). Export → `NSSavePanel`
(default name `VivoType-backup.json`). Import → `NSOpenPanel` (`.json`),
validate `version == 1`, write each present sub-key back atomically. Show
an explicit privacy warning next to the contacts switch and re-confirm via
`NSAlert` before an export that includes them — it contains real personal
names.

### Files to change
`clients/mac/Settings.swift`, `clients/mac/App.swift`,
`clients/mac/UI/SettingsController.swift`, `core/config.py` (`DEFAULTS`
addition). Tests: `core/tests/test_config.py` — new `hud_enabled` default
(`True` when missing) + round-trip through `save_settings`/`load_settings`.

---

## Feature 3 — Per-app profiles ("Contexts")

> **As built 2026-08-22 (PR #34) — read the sketch below as history.** Three
> deliberate deviations, all reviewer-vetted: (1) the profile resolves at
> **transcribe start** (the receiving app), not keypress — strictly better for
> hands-free; there is no `capturedBundleID` property. (2) CLI fallback parity
> (`--profile` on core/cli.py) was REQUIRED, not optional. (3) The daemon
> gained reload hardening: a raising config reload keeps last-good rules
> (warn-once per distinct mtime) instead of killing the process. Also added
> beyond this sketch: `Settings.swift` must round-trip `app_profiles` or every
> settings save wipes mappings; malformed profile entries are validated and
> skipped in `load_config`; explicit `profiles["default"]` is ignored.

**Governance gate: pause here before touching `daemon.py`'s request schema
or `Daemon.swift`'s `transcribe()` signature — the new `profile` field is a
daemon-IPC protocol change. State your plan, get explicit sign-off, then
proceed.**

### v1 scope boundary — read this before designing anything

**Post-processing rules only** (currency-conversion on/off, filler-removal
on/off, a `replacements` overlay). **No per-profile model switching, full
stop.** ADR-0002 requires exactly one warm Whisper model resident at a time
— the entire point of the persistent daemon is eliminating the 2-4s cold
reload. Switching models per-utterance based on frontmost app means either a
synchronous `daemon.reload(model:)` before every transcription when the app
differs from the last one (reintroducing that exact latency), or holding
multiple models resident simultaneously (a real re-architecture, far
outside this feature's scope). Do not go there.

### What to build

**Schema — extend `core/postprocess_config.json`** with a `profiles` object.
The current flat `fillers`/`replacements` become the implicit `"default"`
profile (fully backward-compatible — a file with no `profiles` key behaves
exactly as today):
```json
{
  "fillers": ["um", "uh", "umm", "uhh", "er", "erm"],
  "replacements": { "blr": "Bengaluru", "iit": "IIT" },
  "profiles": {
    "email": { "convert_currency": false, "remove_fillers": true,  "replacements": { "regards": "Regards" } },
    "code":  { "convert_currency": false, "remove_fillers": false, "replacements": {} }
  }
}
```
A named profile's toggles default to `true` when absent; its `replacements`
merge **over** the default profile's.

**`core/postprocess.py` changes:**
- `load_config()` additionally parses `profiles` (default `{}`), returned in
  the config dict.
- New `resolve_profile(config, name="default") -> dict` — for `"default"`
  or any unknown name, returns `{"fillers": config["fillers"],
  "replacements": config["replacements"], "convert_currency": True,
  "remove_fillers": True}`; for a named profile, start from the default and
  apply that profile's toggles + merged `replacements`.
- `postprocess(text, config=None, profile="default")` — after resolving,
  run `convert_currency` only if `resolved["convert_currency"]`, and
  `remove_fillers` only if `resolved["remove_fillers"]`. Every other step
  (`collapse_repetitions`, `_space_script_boundaries`, `apply_replacements`
  using the resolved replacements, `correct_names`, whitespace,
  `_restore_leading_capital`) stays unconditional. Default
  `profile="default"` means every existing call site and test is
  unaffected.

**App→profile mapping**: new `app_profiles` dict (bundle ID → profile
name) in `config.json`, added to `core/config.py::DEFAULTS` as `{}` (same
reason as Feature 2's `hud_enabled` — a Swift-only key vanishes otherwise).
An unmapped frontmost app falls back to `"default"`.

**Data flow** (capture at press time, not injection time — the user may
switch apps in between):
1. `AppDelegate.handleHotkeyPress()` and `startHandsFree()` (App.swift
   ~753, ~794) capture `NSWorkspace.shared.frontmostApplication?.bundleIdentifier`
   into a new `AppDelegate` property, e.g. `capturedBundleID: String?`.
2. Resolved to a profile name inside the existing `onTranscribe` closure
   (App.swift ~452-466, where `buildInitialPrompt()` is already called):
   `let profile = settings.appProfiles[capturedBundleID ?? ""] ?? "default"`.
3. `DaemonClient.transcribe(wav:initialPrompt:completion:)` gains a
   `profile: String = "default"` parameter, added to the NDJSON dict
   (mirrors the existing `"raw": false` literal).
4. `core/daemon.py` reads `profile = cmd.get("profile", "default")`
   (mirrors the existing `cmd.get("raw", False)` at line ~217), threads it
   into `_transcribe(model, wav, initial_prompt, raw, pp_config, profile)`,
   which calls `postprocess(text, pp_config, profile)`. Additive — an old
   client that omits `profile` still gets `"default"`, so this is
   backward-compatible.
- `Dictation.swift` does **not** change — profile resolution stays entirely
  in `AppDelegate`.

**Minimal "Contexts" Settings UI** — one row per existing `app_profiles`
entry (app display name + an `NSPopUpButton` listing profile names from
`postprocess_config.json`'s `profiles` keys plus `"default"`), and an "Add
current app" button that captures the frontmost non-VivoType bundle ID and
maps it to `"default"` for the user to change. Writes back to `config.json`
via the existing `Settings.save`. **Editing profile *definitions*
(the toggles/replacements themselves) is out of scope for the UI in v1** —
edit `postprocess_config.json` directly; it's already live-reloaded on
mtime change (`config_mtime()`, daemon.py ~220-223).

### Files to change
`core/postprocess.py`, `core/postprocess_config.json` (schema),
`core/daemon.py`, `clients/mac/Daemon.swift`, `clients/mac/App.swift`,
`clients/mac/Settings.swift`, `core/config.py`. Tests:
`core/tests/test_postprocess.py` — new `ProfileTests` class: default
profile behaves exactly as today; `convert_currency: false` leaves `$10k`
untouched; `remove_fillers: false` keeps "um"; a profile's `replacements`
override the default's; an unknown profile name falls back to default.
`core/tests/test_config.py` — `app_profiles` defaults to `{}` and
round-trips through save/load.

---

## Feature 4 — Bounded local voice-editing commands

> **As built 2026-08-23 (PR: voice-editing-commands) — deviations from the
> sketch below, all reviewer-vetted:** (1) Scratch matches **STANDALONE only**
> ("hello world scratch that" types literally) — a trailing false positive now
> DELETES text instead of inserting junk. Transform phrases still anchor at
> the end. (2) Detection runs BEFORE postprocess on raw ASR; transforms AFTER
> (whitespace normalization destroys an earlier-inserted "\n\n"). (3) Deletion
> budget is grapheme-cluster count (`text.count`), not utf16 units — Devanagari
> combining marks are several units but one cluster; a unit count would
> over-delete into the user's own words. (4) Injection history is a depth-3 /
> 120 s stack of keyboard-typed spans ONLY (parks/pastes excluded); executeCommand
> is a 7-guard ladder ending in best-effort AX content verification, chunked
> 256-press bursts with frontmost/secure-input re-verified between chunks,
> capped at 2000 presses. Documented residuals: apps without AX text attributes
> proceed without the content check; the check verifies presence, not caret
> position; CLI-fallback dictations have no command channel and type literally.

**Governance gate: pause here before touching `daemon.py`'s response
schema, `Daemon.swift`'s `handleMessage`, or `Dictation.swift`'s injection
code — this feature has BOTH a daemon-IPC protocol change (new `command`
field) AND a text-injection change (backspace synthesis). State your plan,
get explicit sign-off on both, then proceed.**

### Command set (v1 — literal phrases only, no fuzzy matching)

Two structural kinds, handled differently:

**Text-transform commands** (act on the just-dictated text; work in every
app including browsers, since only the text that gets injected changes):
- "new paragraph" / "new line" → append `"\n\n"` / `"\n"`.
- "all caps that" → uppercase the remaining content.
- "cap that" → capitalize the first letter of the remaining content.
- "make that a bullet list" → prefix each line with `"- "`.

**History command** (acts on previously-injected text — needs a structural
Swift action, not just returned text):
- "scratch that" / "delete that" → delete the last injected text.

### Architecture decision — don't relitigate this

Detection lives in a **new `core/commands.py`**, running on the raw ASR text
**before** `postprocess()` (so filler-removal/capitalization never mangles a
command phrase). **All text transforms happen in Python** — the daemon
returns fully-transformed text for the 4 text-transform commands (`command:
null` in the response); the new `command` field Swift receives carries
**only** the literal string `"scratch_that"` (the one action that can't be
expressed as returned text, because it must delete something already typed).

Protocol becomes: `{"id":N,"text":...,"command":"scratch_that"|null}` (or
the existing `{"id":N,"error":...}` shape, unchanged).

Detection reuses the longest-match-first alternation-regex shape already in
`postprocess.py::apply_replacements` — don't invent a different matching
strategy.

### "scratch that" scope — v1: non-browser (`typeUnicode`) path ONLY

Deletion is `lastInjected.utf16.count` synthesized backspace CGEvents
(`kVK_Delete`, virtual key 51), mirroring the CGEvent construction already
in `typeUnicode`. **Evaluated and rejected for the browser/paste path: ⌘Z
(select-all-undo).** In a browser, ⌘Z undoes the last *editor* action —
after any intervening keystroke, autocomplete, or form JS since the paste,
that could revert unrelated content the user never asked to lose. Too
risky; don't build it. In a browser, `scratch_that` is still detected
server-side, but Swift shows the existing `onInjectBlocked` toast
("Editing commands aren't available in this app yet.") instead of acting on
it.

### Code path

`core/commands.py` (new):
```python
def detect_command(raw: str) -> tuple[str | None, str, str]:
    """Return (command_signal, remaining_text, transform).
    command_signal is 'scratch_that' or None — the only value Swift acts on
    structurally. transform is one of 'none'|'upper'|'cap'|'bullet'|
    'newpara'|'newline'. Strips the recognized command phrase from raw."""

def apply_transform(text: str, transform: str) -> str:
    """Apply a text transform AFTER postprocess (clean casing/spacing)."""
```

`core/daemon.py::_transcribe` — after joining the ASR segments into `text`,
before the existing `postprocess` call:
```python
command, remaining, transform = commands.detect_command(text)
if command == "scratch_that":
    return {"text": "", "command": "scratch_that"}
text = postprocess(remaining, pp_config, profile)   # profile arg only if Feature 3 has landed
text = commands.apply_transform(text, transform)
return {"text": text, "command": None}
```
`raw` mode bypasses command detection entirely — it stays a pure diagnostic
window into the ASR model, unchanged from today.

`clients/mac/Daemon.swift`:
- New `var onCommand: ((String) -> Void)?` (main-thread callback).
- In `handleMessage`, add a case checked **before** the existing `text`
  branch: if `obj["command"] as? String == "scratch_that"`, remove the
  pending callback, then on main thread call `cb("")` (so
  `Dictation.stopAndTranscribe`'s normal cleanup runs — empty text means
  nothing gets injected) followed by `onCommand?("scratch_that")`. This
  reply also counts as "a reply" for Feature 1's hang-detection counters
  (resets them) — it's a legitimate response, not a timeout.

`clients/mac/App.swift`: in `startDaemon()`, wire
`daemon.onCommand = { [weak self] cmd in self?.dictation?.executeCommand(cmd) }`.

`clients/mac/Dictation.swift`: new `func executeCommand(_ name: String)` —
for `"scratch_that"`: guard `lastInjected` is non-nil and `AXIsProcessTrusted()`;
check the frontmost bundle ID against the existing `browserBundleIDs` set
(used today in `inject()` to choose paste-vs-type) — if it's a browser,
call `onInjectBlocked?("Editing commands aren't available in this app yet.")`
and return; otherwise synthesize `lastInjected!.utf16.count` backspace
keydown/keyup CGEvents (same source/post pattern as the existing
`typeUnicode`), then set `lastInjected = nil`.

### Open risk — flag it, don't silently resolve it

Injected `"\n"`/`"\n\n"` via `typeUnicode`'s CGEvent path may not produce an
actual Return keystroke in every target app (some apps only respond to a
synthesized Return key event, not a unicode newline character). Ship the
unicode-newline path in v1; if field use surfaces apps that ignore it, add
a synthesized Return keydown/keyup to `typeUnicode` for embedded newlines
as a follow-up — don't pre-build that complexity now.

### Files to change
`core/commands.py` (new), `core/daemon.py`, `clients/mac/Daemon.swift`,
`clients/mac/App.swift`, `clients/mac/Dictation.swift`. Tests:
`core/tests/test_commands.py` (new) — `CommandDetectionTests` (standalone
and trailing-phrase detection; non-command text yields `(None, text,
"none")`; both "scratch that" and "delete that" variants recognized) and
`TransformTests` (exact output for each of `upper`/`cap`/`bullet`/
`newpara`/`newline`). `core/tests/test_daemon.py` additions: an utterance
whose ASR text is "scratch that" yields
`{"id":N,"text":"","command":"scratch_that"}`; a normal utterance yields
`command: null` alongside the existing `text` behavior.

---

## Definition of done (all three features)

- `./scripts/run_tests.sh` green, including the new test files/cases above.
- `/build-sign` produces a warning-free build.
- Each feature's governance gate (Features 3 and 4) was actually paused on
  and approved — not assumed.
- Manual smoke test per feature (no Swift test target exists in this repo,
  so this is the real verification for UI/injection behavior): Feature 2 —
  open Settings, confirm the Privacy card, relabeled model picker
  (including the previously-missing `medium.en`), corrections badge, and
  HUD-less toggle all work; try an export then import round-trip. Feature
  3 — map a test app (e.g. TextEdit) to a "code" profile with
  `convert_currency: false`, dictate a dollar amount into it, confirm it's
  left unconverted, then confirm a different unmapped app still converts
  normally. Feature 4 — dictate "hello world scratch that" into TextEdit
  and confirm only "hello world " remains; try the same in Safari and
  confirm the blocked-toast appears instead of a partial/wrong deletion.
