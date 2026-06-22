## Native macOS Glue (`clients/mac/`)
- **Primary Language:** Swift (or Swift + Obj-C).
- **Onboarding:** Before writing Swift code, you must verify if the user has Xcode Command Line Tools installed and provide step-by-step instructions for compiling the Swift code via terminal (e.g., using `swiftc`).
- **Flow:** On hotkey press → start mic capture. On hotkey release → write a temp WAV → send via the persistent NDJSON daemon (`core/daemon.py`) → read the transcript response → inject text. Falls back to a one-shot `core/cli.py` call if the daemon is unavailable.
- **Injection Preference:** Prefer Accessibility API typing (CGEvent keystrokes). Fall back to clipboard paste (`pbcopy` + `Cmd+V`) only if: (a) Accessibility permission is denied, OR (b) the target app is a web browser, where synthetic keyboard events are unreliable.
- **Permissions:** Explicitly document the exact macOS Accessibility and Microphone permissions needed. These are surfaced through a guided first-run **Permissions checklist** (`UI/PermissionsController.swift`) — explaining each permission and requesting it in-context with live status — rather than letting macOS throw raw prompts.
- **Source layout:** The client is split into focused `.swift` files (`App.swift`, `Dictation.swift`, `Daemon.swift`, `Support.swift`, `Settings.swift`, `UI/*.swift`) compiled together by one `swiftc` call — no Xcode project (see ADR-0005).
- **Fallback:** Only propose a `pyobjc`-based Python fallback AFTER the user explicitly reports a Swift build failure with an error message. Do not offer it preemptively.
