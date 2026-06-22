// VivoType — macOS menu-bar dictation app (entry point + app delegate).
//
// Push-to-talk: hold Right-Option -> capture mic -> Python core daemon -> inject
// text into the focused app. Learns from corrections via the clipboard (see
// Dictation). Packaged as a background .app (LSUIElement / .accessory) so macOS
// permissions attach to VivoType.app, not the terminal.
//
// The codebase is split into focused files compiled together by build_app.sh:
//   Support.swift            — process/IO helpers, paths, VivoTypeState
//   Dictation.swift          — mic capture, injection, correction learning
//   Daemon.swift             — persistent Python daemon client (NDJSON)
//   Settings.swift           — config.json model
//   UI/HUD.swift             — recording pill + capture toast
//   UI/ReviewController.swift, UI/SettingsController.swift,
//   UI/SetupWindowController.swift — native windows (concurrent onboarding)
//   App.swift                — this file: AppDelegate + @main

import Foundation
import AVFoundation
import AppKit
import CoreGraphics
import ApplicationServices

// MARK: - menu-bar app

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var pill: RecordingPill?
    private var toast: Toast?
    private var dictation: Dictation?
    private var reviewController: ReviewController?
    private var settingsController: SettingsController?
    private var settings = Settings()
    private var pythonPath: String?
    private var cliPath: String?
    private var promotePath: String?
    private var configPath: String?
    private var hotkeyMonitor: Any?       // global: fires when another app is frontmost
    private var localHotkeyMonitor: Any?  // local: fires when a VivoType window is key
    private var isPaused = false
    private var currentStatus = "Ready"
    private var daemonClient: DaemonClient?
    private var isDaemonLoading = false
    private var setupController: SetupWindowController?
    private var isSetupRunning = false   // guards against a double-spawned setup_core.sh
    private var currentState: VivoTypeState = .idle

    // Hands-free (toggle) dictation: double-tap the hotkey to start a continuous
    // recording (no holding); double-tap or tap once again to stop + insert. Hold
    // remains push-to-talk. Only the existing hotkey is used — no new global combo
    // — so we never collide with other apps' shortcuts.
    private var isHandsFree = false
    private var hotkeyPressTime: Date?            // when the current press began (hold detection)
    private var lastTapTime: Date?               // release time of a pending first tap
    private var tapResetWork: DispatchWorkItem?  // clears lastTapTime when the double-tap window lapses
    private var ignoreNextRelease = false        // swallow the release that paired with a mode switch
    private var handsFreeMaxTimer: Timer?        // safety auto-stop so it never records forever
    private let tapMaxDuration: TimeInterval = 0.25     // a press shorter than this counts as a tap
    private let doubleTapWindow: TimeInterval = 0.35    // two taps within this = a double-tap
    private let handsFreeMaxDuration: TimeInterval = 60 // hard cap on a single hands-free clip

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        // Pin the writable data home for every Python child (daemon, CLI, learn,
        // promote) so they all agree on where mutable state lives — never the
        // read-only app bundle. Children inherit this via the process environment.
        setenv("VIVOTYPE_APP_SUPPORT", vivotypeAppSupportURL().path, 1)
        // Keep the downloaded Whisper model inside App Support too, so VivoType is
        // fully self-contained: uninstalling and deleting VivoType removes
        // everything. huggingface_hub / mlx-whisper honour HF_HOME.
        let modelCache = vivotypeAppSupportURL().appendingPathComponent("models")
        try? FileManager.default.createDirectory(at: modelCache, withIntermediateDirectories: true)
        setenv("HF_HOME", modelCache.path, 1)
        setupStatusItem()
        pill = RecordingPill()
        toast = Toast()
        loadSettings()

        // First-run gate: if the App Support .venv is missing, build it behind an
        // onboarding window before starting the backend. Otherwise launch normally.
        if FileManager.default.fileExists(atPath: vivotypeVenvPython().path) {
            finishLaunch()
        } else {
            currentStatus = "Setting up…"
            rebuildMenu()
            beginFirstRunSetup()
        }
    }

    /// Normal startup once the .venv exists: wire the backend, hotkey, daemon,
    /// then guide the user through permissions (only if not already granted).
    private func finishLaunch() {
        guard setupBackend() else {
            currentStatus = "⚠ backend not found"
            rebuildMenu()
            return
        }
        // These never prompt: install them up-front so dictation is armed the
        // moment permissions land. The model also warms in parallel while the
        // user works through the permissions checklist.
        installHotkey()
        dictation?.startClipboardMonitor()
        startDaemon()
        presentPermissionsIfNeeded()
    }

    // MARK: first-run setup (concurrent onboarding)

    /// Open the unified Setup window (State 1: permissions) and IMMEDIATELY kick
    /// off setup_core.sh on a background queue, so the .venv + model download runs
    /// concurrently while the user grants Microphone/Accessibility. The window
    /// stays fully responsive — the script never blocks the main thread.
    private func beginFirstRunSetup() {
        let controller = SetupWindowController()
        controller.waitsForModel = true   // hold Success until the model is live
        controller.onRunSetup = { [weak self] in self?.runSetupScript() }
        // setup_core.sh exited 0 → wire the backend + daemon NOW so the model
        // downloads/loads on the onboarding screen, not after the handoff.
        controller.onSetupSucceeded = { [weak self] in self?.startBackendForOnboarding() }
        controller.onRetryModel = { [weak self] in self?.restartDaemonForOnboarding() }
        controller.onOpenVivoType = { [weak self] in self?.completeOnboardingHandoff() }
        setupController = controller
        controller.show()
        runSetupScript()
    }

    /// First-run: the .venv now exists (setup_core.sh exited 0). Wire the backend,
    /// hotkey, clipboard monitor, and daemon so the model loads while the Setup
    /// window shows the download screen. The daemon's `ready`/`error` status flows
    /// back to the Setup window (see startDaemon) to advance/abort onboarding.
    private func startBackendForOnboarding() {
        // Fires only after both permissions cleared, so Accessibility is trusted —
        // record this build so a future reinstall's stale-grant case is detected.
        recordAccessibilityTrust()
        guard setupBackend() else {
            setupController?.modelDidFail("VivoType's backend is missing from the app. Please reinstall.")
            return
        }
        installHotkey()
        dictation?.startClipboardMonitor()
        startDaemon()
    }

    /// Retry a failed model load during onboarding: drop the dead daemon and start
    /// a fresh one (it re-downloads/loads the model).
    private func restartDaemonForOnboarding() {
        daemonClient?.shutdown()
        daemonClient = nil
        startDaemon()
    }

    /// Execute scripts/setup_core.sh from the bundle, passing the App Support dir
    /// as an explicit argument. Runs off the main thread so the UI keeps spinning;
    /// the exit code is reported back to the Setup window's state machine.
    private func runSetupScript() {
        // Never run two setup_core.sh processes at once — concurrent writers would
        // corrupt the shared .venv and truncate each other's setup.log. A Retry
        // can only fire after the prior run reported back, but guard regardless.
        guard !isSetupRunning else { return }
        guard let resources = vivotypeResourcesURL() else {
            setupController?.setupDidFinish(
                exitCode: -1,
                failureMessage: "VivoType's setup files are missing from the app bundle. Please reinstall VivoType.")
            return
        }
        isSetupRunning = true
        let script = resources.appendingPathComponent("scripts/setup_core.sh").path
        let appSupport = vivotypeAppSupportURL().path
        let logFile = vivotypeLogsURL().appendingPathComponent("setup.log").path

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/bin/bash")
            proc.arguments = [script, appSupport]
            // Capture combined output to a log file for debugging first-run issues.
            FileManager.default.createFile(atPath: logFile, contents: nil)
            if let handle = FileHandle(forWritingAtPath: logFile) {
                proc.standardOutput = handle
                proc.standardError = handle
            }

            var status: Int32 = -1
            do {
                try proc.run()
                proc.waitUntilExit()
                status = proc.terminationStatus
            } catch {
                warn("VivoType: failed to launch setup script: \(error)")
                status = -1
            }

            DispatchQueue.main.async {
                guard let self = self else { return }
                self.isSetupRunning = false
                // Keep user-facing copy short and path-free (the full log lives at
                // logFile for support). Exit 42 is the one case worth naming.
                _ = logFile
                let message: String?
                switch status {
                case 0:
                    message = nil
                case 42:
                    message = "Python 3 is required. Please install Python 3.11 or newer, then tap Retry."
                default:
                    message = "Setup couldn't finish. Check your internet connection and tap Retry."
                }
                self.setupController?.setupDidFinish(exitCode: status, failureMessage: message)
            }
        }
    }

    /// State 3 handoff: the user clicked "Open VivoType". The backend + daemon were
    /// already started during onboarding (startBackendForOnboarding), and the model
    /// is loaded (Success only shows after daemon `ready`), so just dismiss the
    /// window and gently flash the menu-bar item so the user sees where VivoType
    /// lives. We highlight the button rather than performClick() it: the latter
    /// opens the menu, which the activation policy reverting to .accessory one
    /// runloop later (ActivationCoordinator.end) immediately dismisses — the
    /// "menu flashes open for a split second" bug.
    private func completeOnboardingHandoff() {
        setupController = nil
        flashMenuBarIcon(blinks: 2, onDuration: 1.0, gap: 0.3)
    }

    /// Briefly highlight the menu-bar button a few times so the user's eye lands
    /// on where VivoType lives. We highlight rather than performClick() — the
    /// latter opens the menu, which the activation policy reverting to .accessory
    /// dismisses a runloop later (the "menu flashes open" bug).
    private func flashMenuBarIcon(blinks: Int, onDuration: TimeInterval, gap: TimeInterval) {
        guard let button = statusItem?.button else { return }
        let cycle = onDuration + gap
        for i in 0..<max(0, blinks) {
            let start = Double(i) * cycle
            DispatchQueue.main.asyncAfter(deadline: .now() + start) { [weak button] in button?.highlight(true) }
            DispatchQueue.main.asyncAfter(deadline: .now() + start + onDuration) { [weak button] in button?.highlight(false) }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        handsFreeMaxTimer?.invalidate()
        handsFreeMaxTimer = nil
        daemonClient?.shutdown()
    }

    // MARK: status item + menu

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        setState(.idle)
    }

    private func setState(_ state: VivoTypeState) {
        currentState = state
        let symbol: String
        let tint: NSColor?
        // Idle uses the VivoType brand-wave template for visual identity; all other
        // states keep the SF Symbols. The brand wave is never tinted, so macOS
        // auto-renders it white/black to match the menu bar like every other icon.
        var customImage: NSImage? = nil
        switch state {
        case .idle:         customImage = brandWaveTemplate; symbol = "mic"; tint = nil; currentStatus = "Ready"
        case .recording:    symbol = "waveform";                tint = nil;                   currentStatus = "Recording…"
        case .transcribing: symbol = "waveform";                tint = nil;                   currentStatus = "Transcribing…"
        case .error:        symbol = "exclamationmark.triangle"; tint = .systemOrange;        currentStatus = "Error"
        case .loading:      symbol = "ellipsis";                tint = nil;                   // currentStatus set by startDaemon/reload
        }
        if let button = statusItem?.button {
            if let image = customImage ?? NSImage(systemSymbolName: symbol, accessibilityDescription: "VivoType") {
                image.isTemplate = true
                button.image = image
                button.title = ""
            } else {
                button.image = nil
                button.title = "🎙"
            }
            button.contentTintColor = tint
        }
        rebuildMenu()

        switch state {
        case .recording:
            pill?.setMode(handsFree: isHandsFree)  // distinct badge for hands-free
            pill?.show()
        default:
            pill?.hide()
        }
    }

    /// Cached VivoType brand-wave menu-bar template (idle state). Loaded from the
    /// bundled PNG by URL — this app has no asset catalog, so `NSImage(named:)`
    /// can't find a loose `Resources/*.png`. Prefers the @2x pixels and is sized
    /// to ~17pt so it stays crisp on Retina. `isTemplate` lets macOS auto-tint it
    /// (white on a dark bar, black on a light bar) — we never set a tint on it.
    /// Falls back to the `mic` SF Symbol if the asset fails to load.
    private lazy var brandWaveTemplate: NSImage? = {
        let bundle = Bundle.main
        let url = bundle.url(forResource: "MenuBarIcon@2x", withExtension: "png")
            ?? bundle.url(forResource: "MenuBarIcon", withExtension: "png")
        guard let url = url, let image = NSImage(contentsOf: url) else { return nil }
        let h: CGFloat = 17
        image.size = NSSize(width: h * (image.size.width / max(image.size.height, 1)), height: h)
        image.isTemplate = true
        return image
    }()

    private func rebuildMenu() {
        let menu = NSMenu()

        let header = NSMenuItem(title: "VivoType — \(currentStatus)", action: nil, keyEquivalent: "")
        header.isEnabled = false
        header.image = statusSymbol()  // colored dot reflecting the live state
        menu.addItem(header)
        menu.addItem(.separator())

        menu.addItem(NSMenuItem(title: isPaused ? "Resume listening" : "Pause listening",
                                action: #selector(togglePause), keyEquivalent: ""))

        // Hands-free toggle (also via double-tapping the hotkey). Checked + "Stop"
        // while active; disabled when paused or the model is still loading.
        let handsFree = NSMenuItem(
            title: isHandsFree ? "Stop hands-free dictation" : "Start hands-free dictation",
            action: #selector(toggleHandsFree), keyEquivalent: "")
        handsFree.state = isHandsFree ? .on : .off
        handsFree.isEnabled = isHandsFree || (!isPaused && !isDaemonLoading)
        menu.addItem(handsFree)

        menu.addItem(NSMenuItem(title: "Review corrections…",
                                action: #selector(reviewCorrections), keyEquivalent: ""))

        // Model picker — checkmark on the active model; switching reloads the daemon.
        let modelItem = NSMenuItem(title: "Model", action: nil, keyEquivalent: "")
        let modelMenu = NSMenu()
        for name in ["small.en", "tiny.en"] {
            let item = NSMenuItem(title: name, action: #selector(selectModel(_:)), keyEquivalent: "")
            item.target = self
            item.state = (settings.model == name) ? .on : .off
            modelMenu.addItem(item)
        }
        modelItem.submenu = modelMenu
        menu.addItem(modelItem)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Settings…", action: #selector(openSettings), keyEquivalent: ","))
        menu.addItem(.separator())
        if let version = vivotypeVersion() {
            let versionItem = NSMenuItem(title: "VivoType \(version)", action: nil, keyEquivalent: "")
            versionItem.isEnabled = false
            menu.addItem(versionItem)
        }
        menu.addItem(NSMenuItem(title: "Quit VivoType", action: #selector(quit), keyEquivalent: "q"))

        for item in menu.items where item.action != nil { item.target = self }
        statusItem?.menu = menu
    }

    /// A small status dot for the menu header, tinted by the live app state.
    private func statusSymbol() -> NSImage? {
        let color: NSColor
        switch currentState {
        case .idle:         color = .systemGreen
        case .recording:    color = .systemRed
        case .transcribing: color = .systemBlue
        case .loading:      color = .systemOrange
        case .error:        color = .systemOrange
        }
        let config = NSImage.SymbolConfiguration(pointSize: 9, weight: .bold)
            .applying(.init(paletteColors: [color]))
        return NSImage(systemSymbolName: "circle.fill", accessibilityDescription: nil)?
            .withSymbolConfiguration(config)
    }

    // MARK: backend + permissions + hotkey

    private func setupBackend() -> Bool {
        let env = ProcessInfo.processInfo.environment
        // Immutable Python source comes from the app bundle's Resources; the
        // interpreter comes from the .venv in Application Support. Env-var
        // overrides remain for development / CLI testing.
        let resources = vivotypeResourcesURL()
        let python = env["VIVOTYPE_PYTHON"] ?? vivotypeVenvPython().path
        let cli = env["VIVOTYPE_CLI"] ?? resources.map { $0.appendingPathComponent("core/cli.py").path }
        let learn = env["VIVOTYPE_LEARN"] ?? resources.map { $0.appendingPathComponent("core/learn.py").path } ?? ""
        let promote = env["VIVOTYPE_PROMOTE"] ?? resources.map { $0.appendingPathComponent("core/promote.py").path } ?? ""

        guard let cli = cli,
              FileManager.default.fileExists(atPath: python),
              FileManager.default.fileExists(atPath: cli)
        else {
            warn("VivoType: backend not found. The .venv may not be set up yet, or the bundle is missing Resources/core.")
            return false
        }
        pythonPath = python
        cliPath    = cli
        promotePath = promote
        let controller = Dictation(pythonPath: python, cliPath: cli, learnPath: learn)
        controller.onState = { [weak self] state in self?.setState(state) }
        controller.onLevel = { [weak self] level in 
            self?.pill?.update(level: level)
            if self?.currentState == .recording {
                self?.updateStatusBarWaveform(level: CGFloat(level))
            }
        }
        controller.onCaptured = { [weak self] count in
            guard let self = self, self.settings.toastEnabled else { return }
            let message = count > 1 ? "✓ \(count) corrections captured" : "✓ Correction captured"
            self.toast?.show(message)
        }
        controller.soundEnabled = settings.soundEnabled

        // Route transcription through the daemon; fall back to one-shot CLI
        // if the daemon is still loading, crashed, or hasn't started yet.
        controller.onTranscribe = { [weak self] url, completion in
            guard let self = self else { completion(nil); return }
            if let daemon = self.daemonClient {
                let prompt = self.buildInitialPrompt()
                daemon.transcribe(wav: url, initialPrompt: prompt) { [weak self] text in
                    if let text = text {
                        completion(text)
                    } else {
                        self?.runCliFallback(url: url, completion: completion)
                    }
                }
            } else {
                self.runCliFallback(url: url, completion: completion)
            }
        }

        dictation = controller
        return true
    }

    private func runCliFallback(url: URL, completion: @escaping (String?) -> Void) {
        guard let python = pythonPath, let cli = cliPath else { completion(nil); return }
        DispatchQueue.global(qos: .userInitiated).async {
            let result = runProcess(python, [cli, url.path], timeout: 300)
            let text = result.status == 0
                ? result.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
                : nil
            DispatchQueue.main.async { completion(text) }
        }
    }

    private func loadSettings() {
        let env = ProcessInfo.processInfo.environment
        // Settings are mutable, so they live in the writable App Support dir, not
        // the read-only bundle. Seed from the bundle's default config on first run.
        let fm = FileManager.default
        let appSupportConfig = vivotypeAppSupportURL().appendingPathComponent("config.json")
        if !fm.fileExists(atPath: appSupportConfig.path),
           let bundled = vivotypeResourcesURL()?.appendingPathComponent("core/config.json"),
           fm.fileExists(atPath: bundled.path) {
            try? fm.copyItem(at: bundled, to: appSupportConfig)
        }
        let path = env["VIVOTYPE_CONFIG"] ?? appSupportConfig.path
        configPath = path
        settings = Settings.load(from: path)
        applySettings()
    }

    private func applySettings() {
        dictation?.soundEnabled = settings.soundEnabled
        // The hotkey keycode and toast flag are read live from `settings`.
    }

    private func startDaemon() {
        // The daemon runs as `python -m core.daemon`, so its working directory
        // must be the bundle's Resources (where the `core` package lives), while
        // the interpreter is the App Support .venv python.
        guard let python = pythonPath, let root = vivotypeResourcesURL()?.path else {
            isDaemonLoading = false
            return
        }
        let daemon = DaemonClient()
        daemon.onStatusChange = { [weak self] status in
            guard let self = self else { return }
            switch status {
            case .loading(let downloading):
                self.isDaemonLoading = true
                self.currentStatus = downloading != nil ? "Downloading model…" : "Loading model…"
                self.setState(.loading)
            case .ready:
                self.isDaemonLoading = false
                self.setState(.idle)
                // Onboarding (if still showing) advances to Success only now —
                // the menu-bar icon is live and dictation is genuinely ready.
                self.setupController?.modelDidBecomeReady()
            case .error(let msg):
                self.isDaemonLoading = false
                warn("VivoType: daemon — \(msg). One-shot CLI fallback active.")
                self.setState(.idle)  // still usable via CLI
                self.setupController?.modelDidFail(
                    "Couldn't load the model. Check your internet connection and tap Retry.")
            }
        }
        daemonClient = daemon
        isDaemonLoading = true
        currentStatus = "Loading model…"
        setState(.loading)
        daemon.start(pythonPath: python, repoRoot: root)
    }

    /// Build a comma-delimited hint string from the user's active dictionary/lexicon
    /// for the daemon's `initial_prompt` (biases Whisper toward personal vocabulary).
    private func buildInitialPrompt() -> String {
        // Read the live personalization data from the writable App Support dir —
        // the same files the Python backend reads/writes (see core/paths.py).
        let root = vivotypeAppSupportURL().appendingPathComponent("data").path
        var terms: [String] = []

        let dictPath = root + "/user_dictionary.json"
        if let data = FileManager.default.contents(atPath: dictPath),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let repls = obj["replacements"] as? [String: String] {
            terms.append(contentsOf: repls.values.sorted())
        }

        let lexPath = root + "/lexicon/contacts.json"
        if let data = FileManager.default.contents(atPath: lexPath),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let names = obj["names"] as? [String] {
            terms.append(contentsOf: names)
        }

        var seen = Set<String>()
        var parts: [String] = []
        var total = 0
        for term in terms {
            guard !seen.contains(term) else { continue }
            seen.insert(term)
            let add = term.count + (parts.isEmpty ? 0 : 2)  // account for ", "
            guard total + add <= 200 else { break }
            total += add
            parts.append(term)
        }
        return parts.joined(separator: ", ")
    }

    /// Phase 10: instead of letting macOS throw raw Microphone/Accessibility
    /// prompts, present a guided checklist that explains why each is needed and
    /// requests them on the user's command. Returning users (both already
    /// granted) are never interrupted.
    private func presentPermissionsIfNeeded() {
        let micGranted = AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
        let axGranted = AXIsProcessTrusted()
        // Remember the build that Accessibility was last granted to, so a future
        // reinstall (which silently invalidates the old grant) can be detected.
        if axGranted { recordAccessibilityTrust() }
        guard !(micGranted && axGranted) else { return }

        // Reinstall recovery: macOS ties the Accessibility grant to the app's code
        // signature, so after reinstalling/updating, the OLD entry lingers in
        // System Settings (shown as enabled) while AXIsProcessTrusted() is false —
        // dictation silently can't type. Guide the user to remove the stale entry
        // and relaunch instead of dropping them into the normal grant flow, which
        // looks like it should already be on.
        if !axGranted && isLikelyReinstall() {
            presentReinstallGuidance()
            return
        }

        // Returning user: the .venv already exists and the backend is wired, so
        // reuse the Setup window purely for the permissions → success flow with
        // setup pre-marked complete (skips the download screen). "Open VivoType"
        // here just dismisses — no second finishLaunch.
        presentStandardPermissions()
    }

    // MARK: reinstall-aware Accessibility recovery

    private static let axEverTrustedKey = "axEverTrusted"
    private static let axTrustedBuildKey = "axTrustedBuild"

    /// A short identifier for this build — the bundled VERSION file (a git short
    /// hash written by build_app.sh), falling back to CFBundleVersion, then "dev".
    private func currentBuildID() -> String {
        if let url = vivotypeResourcesURL()?.appendingPathComponent("VERSION"),
           let text = try? String(contentsOf: url, encoding: .utf8) {
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { return trimmed }
        }
        return (Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String) ?? "dev"
    }

    /// Record that Accessibility is trusted for the current build.
    private func recordAccessibilityTrust() {
        let defaults = UserDefaults.standard
        defaults.set(true, forKey: Self.axEverTrustedKey)
        defaults.set(currentBuildID(), forKey: Self.axTrustedBuildKey)
    }

    /// True when Accessibility was granted to a DIFFERENT build before but is now
    /// untrusted — the signature of a reinstall/update with a stale TCC entry.
    private func isLikelyReinstall() -> Bool {
        let defaults = UserDefaults.standard
        guard defaults.bool(forKey: Self.axEverTrustedKey) else { return false }
        let trustedBuild = defaults.string(forKey: Self.axTrustedBuildKey) ?? ""
        return !trustedBuild.isEmpty && trustedBuild != currentBuildID()
    }

    /// In-app panel guiding the user to remove the stale Accessibility entry and
    /// relaunch. Pure guidance — VivoType never alters TCC/permissions itself; it
    /// only opens System Settings and offers to relaunch.
    private func presentReinstallGuidance() {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "VivoType was updated — re-enable Accessibility"
        alert.informativeText = """
        Because VivoType was reinstalled, macOS still lists the previous version under \
        Accessibility, so it can't type for you yet.

        1. Open Accessibility settings below.
        2. Select VivoType and click the “–” button to remove it.
        3. Click “+”, then choose VivoType again (or just relaunch).

        Your voice and text always stay on your Mac.
        """
        alert.addButton(withTitle: "Open Accessibility Settings")  // .alertFirstButtonReturn
        alert.addButton(withTitle: "Relaunch VivoType")            // .alertSecondButtonReturn
        alert.addButton(withTitle: "Later")                        // .alertThirdButtonReturn

        switch alert.runModal() {
        case .alertFirstButtonReturn:
            if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
                NSWorkspace.shared.open(url)
            }
            // Still show the normal permissions checklist so the badge flips live
            // once they re-add VivoType.
            presentStandardPermissions()
        case .alertSecondButtonReturn:
            relaunchApp()
        default:
            presentStandardPermissions()
        }
    }

    /// The normal permissions → success Setup window (returning-user flow).
    private func presentStandardPermissions() {
        let controller = SetupWindowController()
        controller.onOpenVivoType = { [weak self] in self?.setupController = nil }
        setupController = controller
        controller.setupDidFinish(exitCode: 0, failureMessage: nil)
        controller.show()
    }

    /// Relaunch VivoType: spawn a fresh instance, then terminate this one. A new
    /// process re-reads the (now corrected) Accessibility state on launch.
    private func relaunchApp() {
        let bundleURL = Bundle.main.bundleURL
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        task.arguments = ["-n", bundleURL.path]
        try? task.run()
        NSApp.terminate(nil)
    }

    private func installHotkey() {
        // Global monitors observe events destined for *other* apps, so they don't
        // fire while one of VivoType's own windows (Settings/Permissions/Review) is
        // key. We therefore register a local monitor as well, sharing one handler,
        // so push-to-talk works regardless of which app is frontmost.
        hotkeyMonitor = NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            self?.handleHotkey(event)
        }
        localHotkeyMonitor = NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            self?.handleHotkey(event)
            return event  // don't consume it — let normal key handling proceed
        }
    }

    /// Shared push-to-talk handler for both the global and local flagsChanged monitors.
    private func handleHotkey(_ event: NSEvent) {
        guard !self.isPaused, !self.isDaemonLoading,
              event.keyCode == self.settings.hotkeyKeycode else { return }
        // A modifier key is "pressed" when its flag is now set, else released.
        let flags = event.modifierFlags
        let pressed: Bool
        switch event.keyCode {
        case 54, 55: pressed = flags.contains(.command)
        case 58, 61: pressed = flags.contains(.option)
        case 59, 62: pressed = flags.contains(.control)
        case 56, 60: pressed = flags.contains(.shift)
        default:     pressed = !flags.isEmpty
        }
        if pressed { self.handleHotkeyPress() }
        else { self.handleHotkeyRelease() }
    }

    /// Hotkey went DOWN. Routes between: stop hands-free, enter hands-free (2nd tap
    /// of a double-tap), or begin a push-to-talk recording.
    private func handleHotkeyPress() {
        // Already dictating hands-free → the next press stops and inserts.
        if isHandsFree {
            ignoreNextRelease = true
            stopHandsFree(transcribe: true)
            return
        }
        // Second tap of a double-tap (a prior quick tap is still pending) → start
        // hands-free instead of a momentary recording.
        if let last = lastTapTime, Date().timeIntervalSince(last) <= doubleTapWindow {
            lastTapTime = nil
            tapResetWork?.cancel()
            ignoreNextRelease = true
            startHandsFree()
            return
        }
        // Otherwise it's a normal push-to-talk press: start capturing now so the
        // first syllable isn't clipped; the release decides hold vs. tap.
        hotkeyPressTime = Date()
        dictation?.startRecording()
    }

    /// Hotkey came UP. A real hold = push-to-talk (transcribe). A very short press
    /// is a tap: discard its tiny clip and arm the double-tap window.
    private func handleHotkeyRelease() {
        if ignoreNextRelease { ignoreNextRelease = false; return }
        let held = hotkeyPressTime.map { Date().timeIntervalSince($0) } ?? .infinity
        hotkeyPressTime = nil
        if held >= tapMaxDuration {
            dictation?.stopAndTranscribe()
        } else {
            dictation?.cancel()   // too short to be speech; drop it
            lastTapTime = Date()
            let work = DispatchWorkItem { [weak self] in self?.lastTapTime = nil }
            tapResetWork = work
            DispatchQueue.main.asyncAfter(deadline: .now() + doubleTapWindow, execute: work)
        }
    }

    // MARK: hands-free dictation

    private func startHandsFree() {
        guard !isHandsFree, !isPaused, !isDaemonLoading else { return }
        // Set the flag BEFORE recording so setState(.recording) styles the pill as
        // the hands-free badge; revert if capture didn't actually begin.
        isHandsFree = true
        dictation?.startRecording()
        guard dictation?.isCapturing == true else { isHandsFree = false; setState(.idle); return }
        currentStatus = "Hands-free…"        // overrides the .recording label below
        handsFreeMaxTimer?.invalidate()
        handsFreeMaxTimer = Timer.scheduledTimer(withTimeInterval: handsFreeMaxDuration,
                                                 repeats: false) { [weak self] _ in
            self?.stopHandsFree(transcribe: true)
        }
        rebuildMenu()
    }

    private func stopHandsFree(transcribe: Bool) {
        guard isHandsFree else { return }
        isHandsFree = false
        handsFreeMaxTimer?.invalidate()
        handsFreeMaxTimer = nil
        if transcribe { dictation?.stopAndTranscribe() } else { dictation?.cancel() }
        currentStatus = isPaused ? "Paused" : "Ready"
        rebuildMenu()
    }

    @objc private func toggleHandsFree() {
        if isHandsFree { stopHandsFree(transcribe: true) }
        else { startHandsFree() }
    }

    // MARK: menu actions

    @objc private func togglePause() {
        isPaused.toggle()
        // Pausing while dictating hands-free: insert what was captured, then pause.
        if isPaused && isHandsFree { stopHandsFree(transcribe: true) }
        currentStatus = isPaused ? "Paused" : "Ready"
        rebuildMenu()
    }

    /// Switch the active transcription model from the menu's Model submenu, then
    /// reload the daemon so the change takes effect immediately.
    @objc private func selectModel(_ sender: NSMenuItem) {
        let model = sender.title
        guard model != settings.model else { return }
        settings.model = model
        if let path = configPath { settings.save(to: path) }
        if let daemon = daemonClient {
            isDaemonLoading = true
            currentStatus = "Loading model…"
            setState(.loading)
            daemon.reload(model: model)
        }
        rebuildMenu()
    }

    private func updateStatusBarWaveform(level: CGFloat) {
        let size = NSSize(width: 18, height: 18)
        let waveformImage = NSImage(size: size, flipped: false) { rect in
            guard let _ = NSGraphicsContext.current?.cgContext else { return false }
            
            let barWidth: CGFloat = 2.0
            let spacing: CGFloat = 2.0
            let minHeight: CGFloat = 3.0
            let maxHeight: CGFloat = 14.0
            
            // Gaussian-like coefficients for the 5 bars (creates a natural voice peak)
            let coefficients: [CGFloat] = [0.3, 0.7, 1.0, 0.7, 0.3]
            
            NSColor.black.setFill() // Draw black template mask; macOS automatically tints it
            
            for i in 0..<5 {
                let coef = coefficients[i]
                let barHeight = minHeight + (maxHeight - minHeight) * level * coef
                let x = CGFloat(i) * (barWidth + spacing)
                let y = (rect.height - barHeight) / 2.0
                
                let barRect = NSRect(x: x, y: y, width: barWidth, height: barHeight)
                let path = NSBezierPath(roundedRect: barRect, xRadius: barWidth / 2.0, yRadius: barWidth / 2.0)
                path.fill()
            }
            return true
        }
        
        waveformImage.isTemplate = true
        statusItem?.button?.image = waveformImage
    }

    @objc private func reviewCorrections() {
        guard let python = pythonPath, let promote = promotePath, !promote.isEmpty else {
            showPlaceholder("Review corrections",
                            "Backend not found. Run ./scripts/setup_core.sh and launch VivoType from the repo.")
            return
        }
        if reviewController == nil {
            reviewController = ReviewController(pythonPath: python, promotePath: promote)
        }
        reviewController?.show()
    }

    @objc private func openSettings() {
        guard let configPath = configPath, !configPath.isEmpty else {
            showPlaceholder("Settings", "Could not locate core/config.json (run from the repo).")
            return
        }
        if settingsController == nil {
            settingsController = SettingsController(settings: settings, configPath: configPath) { [weak self] updated in
                guard let self = self else { return }
                let modelChanged = updated.model != self.settings.model
                self.settings = updated
                self.dictation?.soundEnabled = updated.soundEnabled
                // hotkey keycode + toast flag are read live from `self.settings`.
                if modelChanged, let daemon = self.daemonClient {
                    self.isDaemonLoading = true
                    self.currentStatus = "Loading model…"
                    self.setState(.loading)
                    daemon.reload(model: updated.model)
                }
            }
        }
        settingsController?.show()
    }

    @objc private func quit() {
        daemonClient?.shutdown()
        NSApp.terminate(nil)
    }

    private func showPlaceholder(_ title: String, _ message: String) {
        // Surface the alert as a real foreground app, then revert to the menu-bar
        // agent once it's dismissed (runModal blocks until then).
        ActivationCoordinator.shared.begin()
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.runModal()
        ActivationCoordinator.shared.end()
    }
}

// MARK: - entry point

/// Process entry point. Replaces the former top-level code in main.swift; with
/// the monolith split into multiple files, a file named `main.swift` no longer
/// exists, so `@main` provides the single executable entry instead.
@main
struct VivoTypeMain {
    static func main() {
        let delegate = AppDelegate()
        let app = NSApplication.shared
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}
