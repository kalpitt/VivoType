# [ADR-0006] Transient `.regular` Activation for Setup Windows

**Status:** Accepted  
**Date:** 2026-06-16  

## Context
VivoType ships as a background menu-bar agent: `LSUIElement` / `NSApplication.ActivationPolicy.accessory`. This is deliberate and correct for a dictation tool — it injects transcribed text into whatever app is frontmost, so it must *never* steal focus or appear in the Dock during normal use (see `App.swift`, ADR-0002).

However, the app also presents genuine foreground windows: the first-run onboarding (`.venv` build), the guided permissions checklist, Settings, and Review Corrections. On macOS 14+ an `.accessory` app cannot reliably activate its own windows — cooperative activation and focus-stealing prevention mean `NSApp.activate(ignoringOtherApps:)` (now deprecated) is largely ignored. The visible symptom: after the system Microphone permission dialog dismissed, the permissions window dropped behind other apps. The same OS behaviour blocked any reliable programmatic fix from within the `.accessory` policy.

A naive "make VivoType a normal windowed app" was rejected: a permanent Dock app would fight the core dictation behaviour (it must yield focus to the app being typed into) and would not even fix the activation problem.

## Decision
We keep `.accessory` as the resting policy and switch to `.regular` **only while a foreground window is open**, reverting to `.accessory` when the last one closes. This is centralized in a single reference-counted `ActivationCoordinator` (`clients/mac/UI/ActivationCoordinator.swift`):

- `begin()` — increment count; set `.regular` if needed; activate. Called as each setup/preferences window is shown.
- `refocus(_:)` — re-assert focus on an already-open window (e.g. after a system dialog dismisses) without touching the count.
- `end()` — decrement count; when it reaches zero, revert to `.accessory`. The revert is **deferred one runloop turn** so the onboarding→permissions hand-off (close-then-immediately-open) does not flicker the Dock icon.

Each window controller holds a `heldForeground` flag so repeated `show()` calls cannot unbalance the count. Activation uses the non-deprecated `NSApp.activate()` on macOS 14+, with an `NSApp.activate(ignoringOtherApps:)` fallback for older systems. The transient recording HUD/pill is intentionally **excluded** — it must never take focus.

## Consequences
- **Easier:** setup/permissions/settings/review windows reliably come to and stay at the front on macOS 14–26; the deprecated focus-stealing API is gone from all window code; one place owns the policy.
- **Harder:** every foreground window must route show/close through the coordinator (and be its window's delegate to catch the red close button); a new foreground window that forgets this will mis-balance the count.
- **Neutral:** a Dock icon and default app menu appear *briefly* during setup, then disappear. Returning users with permissions already granted never trigger the coordinator, so behaviour is unchanged for them.

## 🤖 Agent Directives
- **DO NOT** change the app's resting `ActivationPolicy` away from `.accessory`, and do not give VivoType a permanent Dock presence — it is a background dictation agent by design.
- **DO** route any new user-facing foreground window through `ActivationCoordinator` (`begin()`/`end()`, with `refocus(_:)` after system dialogs), guarded by a per-controller `heldForeground` flag, and set the window's `delegate` so `windowWillClose` fires `end()`.
- **DO NOT** route the recording pill, toast, or any non-interactive HUD through the coordinator — they must not take focus.
- **DO NOT** reintroduce `NSApp.activate(ignoringOtherApps:)` in window code; use `ActivationCoordinator` (which encapsulates the macOS-14+ `NSApp.activate()` call).
