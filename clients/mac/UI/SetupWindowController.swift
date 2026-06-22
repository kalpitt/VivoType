// VivoType — unified first-run Setup window (Concurrent Onboarding).
//
// Replaces the former two-step OnboardingController + PermissionsController.
// A single window drives a three-state flow while the Python environment and AI
// model are downloaded CONCURRENTLY in the background (see App.swift), so the
// user spends the download latency granting permissions instead of staring at a
// spinner:
//
//   State 1 — Permissions (mandatory): the flat brand logo, "Welcome to
//             VivoType", and the Microphone + Accessibility cards. There is NO
//             "Continue Anyway" escape hatch — the window advances itself the
//             instant BOTH AVCaptureDevice and AXIsProcessTrusted report true.
//             A quiet "Quit" link is the only way out, so a user who declines a
//             permission is never trapped.
//   State 2 — Download/Setup (optional): a spinner + "Downloading your private
//             AI model…", shown only if setup_core.sh hasn't already exited 0.
//   State 3 — Success: a green checkmark + "You're all set!" and an "Open
//             VivoType" button (the window's default button — Return triggers it)
//             that reveals the menu-bar item.
//
// Pure programmatic AppKit auto-layout — no Storyboards/XIBs/SwiftUI.

import Foundation
import AppKit
import AVFoundation
import ApplicationServices

// MARK: - one permission card (reused from the former PermissionsController)

/// A single rounded "card" row: tinted icon, title, explanation, and a trailing
/// accessory that flips between a "Grant"/"Open Settings" button and a green
/// "Enabled" badge depending on the live authorization state.
///
/// Card/border/chip fills are layer CGColors, so they are re-resolved on every
/// appearance change (`viewDidChangeEffectiveAppearance`) to stay correct when
/// the user toggles Dark Mode or drags the window across displays.
final class PermissionRowView: NSView {
    private let grantButton: NSButton
    private let badgeIcon = NSImageView()
    private let badgeLabel = NSTextField(labelWithString: "Enabled")
    private let chip = NSView()
    private let accent: NSColor
    private let rowTitle: String
    private var isGranted = false

    init(symbol: String, accent: NSColor, title: String, detail: String,
         target: AnyObject, action: Selector) {
        self.accent = accent
        self.rowTitle = title
        grantButton = NSButton(title: "Grant", target: target, action: action)
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false

        wantsLayer = true
        layer?.cornerRadius = 14
        layer?.borderWidth = 1

        chip.translatesAutoresizingMaskIntoConstraints = false
        chip.wantsLayer = true
        chip.layer?.cornerRadius = 22
        NSLayoutConstraint.activate([
            chip.widthAnchor.constraint(equalToConstant: 44),
            chip.heightAnchor.constraint(equalToConstant: 44)
        ])

        let icon = NSImageView()
        icon.translatesAutoresizingMaskIntoConstraints = false
        if let image = NSImage(systemSymbolName: symbol, accessibilityDescription: title) {
            icon.image = image
        }
        icon.contentTintColor = accent
        icon.imageScaling = .scaleProportionallyUpOrDown
        chip.addSubview(icon)
        NSLayoutConstraint.activate([
            icon.centerXAnchor.constraint(equalTo: chip.centerXAnchor),
            icon.centerYAnchor.constraint(equalTo: chip.centerYAnchor),
            icon.widthAnchor.constraint(equalToConstant: 24),
            icon.heightAnchor.constraint(equalToConstant: 24)
        ])

        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.translatesAutoresizingMaskIntoConstraints = false
        titleLabel.font = .systemFont(ofSize: 15, weight: .semibold)

        let detailLabel = NSTextField(wrappingLabelWithString: "")
        detailLabel.translatesAutoresizingMaskIntoConstraints = false
        detailLabel.isSelectable = false
        let style = NSMutableParagraphStyle()
        style.lineSpacing = 2
        detailLabel.attributedStringValue = NSAttributedString(
            string: detail,
            attributes: [.font: NSFont.systemFont(ofSize: 11.5), .foregroundColor: NSColor.secondaryLabelColor, .paragraphStyle: style]
        )

        let textStack = NSStackView(views: [titleLabel, detailLabel])
        textStack.orientation = .vertical
        textStack.alignment = .leading
        textStack.spacing = 4
        textStack.translatesAutoresizingMaskIntoConstraints = false
        textStack.setContentHuggingPriority(.defaultLow, for: .horizontal)

        grantButton.translatesAutoresizingMaskIntoConstraints = false
        grantButton.bezelStyle = .rounded
        grantButton.controlSize = .regular

        badgeIcon.translatesAutoresizingMaskIntoConstraints = false
        if let check = NSImage(systemSymbolName: "checkmark.circle.fill", accessibilityDescription: "Granted") {
            badgeIcon.image = check
        }
        badgeIcon.contentTintColor = .systemGreen
        badgeIcon.isHidden = true
        NSLayoutConstraint.activate([
            badgeIcon.widthAnchor.constraint(equalToConstant: 20),
            badgeIcon.heightAnchor.constraint(equalToConstant: 20)
        ])

        badgeLabel.translatesAutoresizingMaskIntoConstraints = false
        badgeLabel.font = .systemFont(ofSize: 12, weight: .semibold)
        badgeLabel.textColor = .systemGreen
        badgeLabel.isHidden = true

        let badgeStack = NSStackView(views: [badgeIcon, badgeLabel])
        badgeStack.orientation = .horizontal
        badgeStack.alignment = .centerY
        badgeStack.spacing = 4
        badgeStack.translatesAutoresizingMaskIntoConstraints = false

        let trailingView = NSView()
        trailingView.translatesAutoresizingMaskIntoConstraints = false
        trailingView.addSubview(grantButton)
        trailingView.addSubview(badgeStack)

        NSLayoutConstraint.activate([
            grantButton.centerXAnchor.constraint(equalTo: trailingView.centerXAnchor),
            grantButton.centerYAnchor.constraint(equalTo: trailingView.centerYAnchor),
            badgeStack.centerXAnchor.constraint(equalTo: trailingView.centerXAnchor),
            badgeStack.centerYAnchor.constraint(equalTo: trailingView.centerYAnchor),
            trailingView.widthAnchor.constraint(equalToConstant: 104)
        ])

        let mainStack = NSStackView(views: [chip, textStack, trailingView])
        mainStack.orientation = .horizontal
        mainStack.alignment = .centerY
        mainStack.spacing = 16
        mainStack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(mainStack)

        NSLayoutConstraint.activate([
            mainStack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            mainStack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),
            mainStack.topAnchor.constraint(equalTo: topAnchor, constant: 16),
            mainStack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -16)
        ])

        // Expose the whole card as one VoiceOver element with a spoken status.
        setAccessibilityElement(true)
        setAccessibilityRole(.group)
        applyColors()
        updateAccessibility(denied: false)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    /// Re-resolve every layer CGColor in the current appearance. Layer colors are
    /// frozen at assignment time, so without this Dark/Light toggles would leave
    /// stale fills until the next full redraw.
    private func applyColors() {
        let resolve = {
            self.layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
            self.layer?.borderColor = NSColor.separatorColor.cgColor
            self.chip.layer?.backgroundColor = self.accent.withAlphaComponent(0.15).cgColor
        }
        if #available(macOS 11.0, *) {
            effectiveAppearance.performAsCurrentDrawingAppearance(resolve)
        } else {
            resolve()
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        applyColors()
    }

    func setState(granted: Bool, denied: Bool) {
        isGranted = granted
        grantButton.isHidden = granted
        badgeIcon.isHidden = !granted
        badgeLabel.isHidden = !granted
        grantButton.title = denied ? "Open Settings" : "Grant"
        updateAccessibility(denied: denied)
    }

    private func updateAccessibility(denied: Bool) {
        let status: String
        if isGranted { status = "granted" }
        else if denied { status = "not granted, opens System Settings" }
        else { status = "not granted, Grant button" }
        setAccessibilityLabel("\(rowTitle) permission, \(status)")
    }
}

// MARK: - unified Setup window

final class SetupWindowController: NSObject, NSWindowDelegate {

    /// The three onboarding screens, swapped with a cross-fade inside one window.
    private enum Screen { case permissions, downloading, success }

    /// Single source of truth for the download screen's spinner/label/retry.
    private enum DownloadUI { case spinning, error(String) }

    // Window + a fixed-size container the three screens fade in/out of.
    private var window: NSWindow!
    private let container = NSView()
    private var current: Screen = .permissions

    // State 1 — permission rows + live polling.
    private var permissionsView: NSView!
    private var micRow: PermissionRowView!
    private var axRow: PermissionRowView!
    private var pollTimer: Timer?
    private var permissionsCleared = false   // both granted → advanced past State 1

    // State 2 — progress / error.
    private var downloadingView: NSView!
    private let spinner = NSProgressIndicator()
    private let progressLabel = NSTextField(labelWithString: "Downloading your private AI model…")
    private let reassuranceLabel = NSTextField(labelWithString: "")
    private var retryButton: NSButton!
    private var reassuranceTimer: Timer?

    // State 3 — success.
    private var successView: NSView!
    private var openButton: NSButton!

    // Background setup_core.sh result (written on the main thread).
    private var setupFinished = false
    private var setupExitCode: Int32 = -999
    private var setupFailureMessage: String?

    // Balances ActivationCoordinator begin()/end().
    private var heldForeground = false

    /// AppDelegate launches `scripts/setup_core.sh` on a background queue.
    /// Called once on show() and again on Retry.
    var onRunSetup: (() -> Void)?
    /// AppDelegate wires the backend and reveals the menu-bar item.
    var onOpenVivoType: (() -> Void)?
    /// First-run only: setup_core.sh exited 0, so wire the backend + daemon NOW
    /// (during onboarding) and start loading the model.
    var onSetupSucceeded: (() -> Void)?
    /// First-run only: re-attempt a failed model load (restart the daemon).
    var onRetryModel: (() -> Void)?

    /// When true (first run), Success waits until the model is actually loaded
    /// (daemon ready) — not merely until setup_core.sh exits — so the menu-bar
    /// icon is live before we tell the user "You're all set". Returning-user
    /// permission re-grants leave this false (the model is already loaded).
    var waitsForModel = false
    private var modelLoading = false

    override init() {
        super.init()
        buildWindow()
    }

    // MARK: live authorization state

    private var micGranted: Bool { AVCaptureDevice.authorizationStatus(for: .audio) == .authorized }
    private var micDenied: Bool {
        let s = AVCaptureDevice.authorizationStatus(for: .audio)
        return s == .denied || s == .restricted
    }
    private var axGranted: Bool { AXIsProcessTrusted() }

    // MARK: window construction

    private func buildWindow() {
        let width: CGFloat = 480, height: CGFloat = 560
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                          styleMask: [.titled, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "VivoType Setup"
        window.titleVisibility = .hidden            // don't draw the title over the logo
        window.titlebarAppearsTransparent = true
        window.isMovableByWindowBackground = false  // drag via the titlebar only
        window.isReleasedWhenClosed = false
        window.delegate = self

        let content = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        container.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(container)
        NSLayoutConstraint.activate([
            container.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            container.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            container.topAnchor.constraint(equalTo: content.topAnchor),
            container.bottomAnchor.constraint(equalTo: content.bottomAnchor)
        ])
        window.contentView = content

        permissionsView = buildPermissionsView()
        downloadingView = buildDownloadingView()
        successView = buildSuccessView()

        // Start on State 1.
        permissionsView.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(permissionsView)
        pin(permissionsView, to: container)
    }

    /// The flat brand logo, loaded by URL from the bundle (no asset catalog).
    /// Falls back to an SF Symbol if the PNG is missing (e.g. dev binary run
    /// outside the built .app).
    private func makeLogo(size: CGFloat) -> NSImageView {
        let iv = NSImageView()
        iv.translatesAutoresizingMaskIntoConstraints = false
        if let url = Bundle.main.url(forResource: "WelcomeLogo", withExtension: "png"),
           let image = NSImage(contentsOf: url) {
            iv.image = image
        } else if let fallback = NSImage(systemSymbolName: "waveform.circle.fill",
                                         accessibilityDescription: "VivoType") {
            let config = NSImage.SymbolConfiguration(pointSize: size, weight: .regular)
                .applying(.init(paletteColors: [.controlAccentColor]))
            iv.image = fallback.withSymbolConfiguration(config)
        }
        iv.imageScaling = .scaleProportionallyUpOrDown
        iv.setAccessibilityLabel("VivoType")
        NSLayoutConstraint.activate([
            iv.widthAnchor.constraint(equalToConstant: size),
            iv.heightAnchor.constraint(equalToConstant: size)
        ])
        return iv
    }

    /// A subtle card on the success screen pointing the user at the menu bar —
    /// VivoType has no Dock icon or main window, so first-timers don't know where
    /// it went. Shows the actual menu-bar glyph next to the explanation.
    private func makeMenuBarHint() -> NSView {
        let container = NSView()
        container.translatesAutoresizingMaskIntoConstraints = false
        container.wantsLayer = true
        container.layer?.cornerRadius = 10
        container.layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        container.layer?.borderWidth = 1
        container.layer?.borderColor = NSColor.separatorColor.cgColor

        let iconView = NSImageView()
        iconView.translatesAutoresizingMaskIntoConstraints = false
        let bundle = Bundle.main
        if let url = bundle.url(forResource: "MenuBarIcon@2x", withExtension: "png")
            ?? bundle.url(forResource: "MenuBarIcon", withExtension: "png"),
           let image = NSImage(contentsOf: url) {
            image.isTemplate = true
            iconView.image = image
        } else if let fallback = NSImage(systemSymbolName: "waveform",
                                         accessibilityDescription: "VivoType menu-bar icon") {
            iconView.image = fallback
        }
        iconView.contentTintColor = .labelColor
        iconView.imageScaling = .scaleProportionallyDown
        NSLayoutConstraint.activate([
            iconView.widthAnchor.constraint(equalToConstant: 18),
            iconView.heightAnchor.constraint(equalToConstant: 18)
        ])

        let label = NSTextField(wrappingLabelWithString:
            "VivoType lives in your menu bar, at the top-right. Look for this icon to "
            + "pause, review words, or quit.")
        label.font = .systemFont(ofSize: 11.5)
        label.textColor = .secondaryLabelColor
        label.isSelectable = false
        label.translatesAutoresizingMaskIntoConstraints = false
        label.preferredMaxLayoutWidth = 290

        let row = NSStackView(views: [iconView, label])
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 10
        row.edgeInsets = NSEdgeInsets(top: 10, left: 14, bottom: 10, right: 14)
        row.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(row)
        NSLayoutConstraint.activate([
            row.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            row.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            row.topAnchor.constraint(equalTo: container.topAnchor),
            row.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            container.widthAnchor.constraint(lessThanOrEqualToConstant: 360)
        ])
        container.setAccessibilityElement(true)
        container.setAccessibilityRole(.staticText)
        container.setAccessibilityLabel("VivoType lives in your menu bar at the top-right of the screen.")
        return container
    }

    private func privacyLine() -> NSAttributedString {
        let result = NSMutableAttributedString()
        if let shield = NSImage(systemSymbolName: "lock.shield", accessibilityDescription: nil) {
            let config = NSImage.SymbolConfiguration(pointSize: 11, weight: .regular)
                .applying(.init(paletteColors: [.secondaryLabelColor]))
            let attachment = NSTextAttachment()
            attachment.image = shield.withSymbolConfiguration(config)
            result.append(NSAttributedString(attachment: attachment))
            result.append(NSAttributedString(string: "  "))
        }
        result.append(NSAttributedString(
            string: "100% on-device · your voice never leaves your Mac",
            attributes: [.font: NSFont.systemFont(ofSize: 11.5),
                         .foregroundColor: NSColor.secondaryLabelColor]))
        return result
    }

    // MARK: State 1 — permissions

    private func buildPermissionsView() -> NSView {
        let view = NSView()

        let logo = makeLogo(size: 48)  // constrained to 48×48 per spec

        let titleLabel = NSTextField(labelWithString: "Welcome to VivoType")
        titleLabel.font = .systemFont(ofSize: 22, weight: .bold)
        titleLabel.alignment = .center
        titleLabel.translatesAutoresizingMaskIntoConstraints = false

        let onDevice = NSTextField(labelWithString: "")
        onDevice.attributedStringValue = privacyLine()
        onDevice.alignment = .center
        onDevice.isSelectable = false
        onDevice.translatesAutoresizingMaskIntoConstraints = false
        onDevice.setAccessibilityLabel("100 percent on-device. Your voice never leaves your Mac.")

        let subtitle = NSTextField(wrappingLabelWithString:
            "Grant these two permissions to get started. Everything runs locally on your Mac.")
        subtitle.font = .systemFont(ofSize: 13)
        subtitle.textColor = .secondaryLabelColor
        subtitle.alignment = .center
        subtitle.isSelectable = false
        subtitle.translatesAutoresizingMaskIntoConstraints = false

        micRow = PermissionRowView(
            symbol: "mic.fill", accent: .systemBlue,
            title: "Microphone",
            detail: "Hears you while you hold the dictation key — transcribed on-device, never uploaded.",
            target: self, action: #selector(grantMic))

        axRow = PermissionRowView(
            symbol: "accessibility", accent: .systemIndigo,
            title: "Accessibility",
            detail: "Types transcribed text into any app and detects your push-to-talk key.",
            target: self, action: #selector(grantAccessibility))

        let footnote = NSTextField(wrappingLabelWithString:
            "VivoType continues automatically once both are enabled.")
        footnote.font = .systemFont(ofSize: 11)
        footnote.alignment = .center
        footnote.textColor = .tertiaryLabelColor
        footnote.isSelectable = false
        footnote.translatesAutoresizingMaskIntoConstraints = false

        // Quiet escape hatch: the window has no close button, so a user who
        // declines a permission would otherwise be trapped. Quitting fully exits.
        let quitButton = NSButton(title: "Quit", target: self, action: #selector(quitTapped))
        quitButton.bezelStyle = .inline
        quitButton.isBordered = false
        quitButton.contentTintColor = .tertiaryLabelColor
        quitButton.font = .systemFont(ofSize: 11)
        quitButton.translatesAutoresizingMaskIntoConstraints = false
        quitButton.setAccessibilityLabel("Quit VivoType")

        let bottomStack = NSStackView(views: [footnote, quitButton])
        bottomStack.orientation = .vertical
        bottomStack.alignment = .centerX
        bottomStack.spacing = 8

        let topStack = NSStackView(views: [logo, titleLabel, onDevice, subtitle])
        topStack.orientation = .vertical
        topStack.alignment = .centerX
        topStack.spacing = 12
        topStack.setCustomSpacing(24, after: logo)
        topStack.setCustomSpacing(4, after: titleLabel)

        let mainStack = NSStackView(views: [topStack, micRow, axRow, bottomStack])
        mainStack.orientation = .vertical
        mainStack.alignment = .centerX
        mainStack.spacing = 20
        mainStack.setCustomSpacing(32, after: topStack)
        mainStack.setCustomSpacing(16, after: axRow)
        mainStack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(mainStack)

        NSLayoutConstraint.activate([
            mainStack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            mainStack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            mainStack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 40),
            mainStack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -40),
            micRow.widthAnchor.constraint(equalTo: mainStack.widthAnchor),
            axRow.widthAnchor.constraint(equalTo: mainStack.widthAnchor)
        ])
        return view
    }

    // MARK: State 2 — download/setup progress (+ error)

    private func buildDownloadingView() -> NSView {
        let view = NSView()

        let logo = makeLogo(size: 48)

        spinner.style = .spinning
        spinner.controlSize = .regular
        spinner.isIndeterminate = true
        spinner.translatesAutoresizingMaskIntoConstraints = false

        progressLabel.font = .systemFont(ofSize: 14, weight: .medium)
        progressLabel.alignment = .center
        progressLabel.isSelectable = false
        progressLabel.lineBreakMode = .byWordWrapping
        progressLabel.maximumNumberOfLines = 4
        progressLabel.translatesAutoresizingMaskIntoConstraints = false

        reassuranceLabel.font = .systemFont(ofSize: 11.5)
        reassuranceLabel.textColor = .secondaryLabelColor
        reassuranceLabel.alignment = .center
        reassuranceLabel.isSelectable = false
        reassuranceLabel.lineBreakMode = .byWordWrapping
        reassuranceLabel.maximumNumberOfLines = 2
        reassuranceLabel.isHidden = true
        reassuranceLabel.translatesAutoresizingMaskIntoConstraints = false

        retryButton = NSButton(title: "Retry", target: self, action: #selector(retryTapped))
        retryButton.bezelStyle = .rounded
        retryButton.controlSize = .large
        retryButton.isHidden = true
        retryButton.translatesAutoresizingMaskIntoConstraints = false

        let stack = NSStackView(views: [logo, spinner, progressLabel, reassuranceLabel, retryButton])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 16
        stack.setCustomSpacing(28, after: logo)
        stack.setCustomSpacing(8, after: progressLabel)
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 48),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -48)
        ])
        return view
    }

    // MARK: State 3 — success handoff

    private func buildSuccessView() -> NSView {
        let view = NSView()

        let check = NSImageView()
        check.translatesAutoresizingMaskIntoConstraints = false
        let config = NSImage.SymbolConfiguration(pointSize: 56, weight: .semibold)
            .applying(.init(paletteColors: [.white, .systemGreen]))
        check.image = NSImage(systemSymbolName: "checkmark.circle.fill",
                              accessibilityDescription: "All set")?
            .withSymbolConfiguration(config)
        check.imageScaling = .scaleProportionallyUpOrDown
        NSLayoutConstraint.activate([
            check.widthAnchor.constraint(equalToConstant: 64),
            check.heightAnchor.constraint(equalToConstant: 64)
        ])

        let titleLabel = NSTextField(labelWithString: "You're all set!")
        titleLabel.font = .systemFont(ofSize: 22, weight: .bold)
        titleLabel.alignment = .center
        titleLabel.translatesAutoresizingMaskIntoConstraints = false

        let subtitle = NSTextField(wrappingLabelWithString:
            "Hold the Right-Option key in any app to dictate.")
        subtitle.font = .systemFont(ofSize: 13)
        subtitle.textColor = .secondaryLabelColor
        subtitle.alignment = .center
        subtitle.isSelectable = false
        subtitle.translatesAutoresizingMaskIntoConstraints = false

        // Tell the user WHERE VivoType lives — it's a menu-bar app with no Dock
        // icon or window, so point at the top-right and show the exact glyph.
        let menuBarHint = makeMenuBarHint()

        openButton = NSButton(title: "Open VivoType", target: self, action: #selector(openTapped))
        openButton.bezelStyle = .rounded
        openButton.controlSize = .large
        openButton.keyEquivalent = "\r"   // makes this the window's default (accent) button
        openButton.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            openButton.heightAnchor.constraint(equalToConstant: 40),
            openButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 200)
        ])

        let stack = NSStackView(views: [check, titleLabel, subtitle, menuBarHint, openButton])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 16
        stack.setCustomSpacing(20, after: check)
        stack.setCustomSpacing(20, after: subtitle)
        stack.setCustomSpacing(28, after: menuBarHint)
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 48),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -48)
        ])
        return view
    }

    // MARK: lifecycle

    /// Show the window (State 1) and start polling for permission grants. The
    /// caller is expected to kick off setup_core.sh via `onRunSetup` afterward.
    func show() {
        if !heldForeground { heldForeground = true; ActivationCoordinator.shared.begin() }
        refresh()
        window.center()
        window.makeKeyAndOrderFront(nil)
        startPolling()
    }

    private func startPolling() {
        pollTimer?.invalidate()
        // No callback exists for Accessibility grants, so poll the live state on
        // the main runloop (does NOT block the main thread) to flip badges and
        // auto-advance the instant the user toggles both permissions on.
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.7, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    /// Update permission badges; auto-advance past State 1 once both are granted.
    private func refresh() {
        guard !permissionsCleared else { return }
        // Accessibility has no "denied" status (AXIsProcessTrusted is bool-only),
        // so once we've shown the native prompt, relabel the button to reflect
        // that further clicks open System Settings.
        let axPrompted = UserDefaults.standard.bool(forKey: "hasPromptedAX")
        micRow?.setState(granted: micGranted, denied: micDenied)
        axRow?.setState(granted: axGranted, denied: axPrompted && !axGranted)
        if micGranted && axGranted {
            permissionsCleared = true
            pollTimer?.invalidate()
            pollTimer = nil
            routeAfterPermissions()
        }
    }

    /// Both permissions are granted — decide State 2 vs. State 3.
    private func routeAfterPermissions() {
        if setupFinished {
            // Setup already done while the user worked through permissions.
            if setupExitCode == 0 {
                advanceAfterSetupSucceeded()
            } else {
                transition(to: .downloading)
                applyDownloadUI(.error(setupFailureMessage ?? defaultSetupError))
            }
        } else {
            transition(to: .downloading)          // spinner until the script exits
            applyDownloadUI(.spinning)
        }
    }

    /// setup_core.sh exited 0. On a returning-user re-grant the model is already
    /// loaded, so jump to Success. On first run, keep the download screen up and
    /// load the model — Success waits for `modelDidBecomeReady()`.
    private func advanceAfterSetupSucceeded() {
        if waitsForModel {
            beginModelLoad()
        } else {
            transition(to: .success)
        }
    }

    /// Stay on the download screen and ask AppDelegate to start the backend +
    /// daemon, which downloads/loads the model. Fires `onSetupSucceeded` once.
    private func beginModelLoad() {
        guard !modelLoading else { return }
        modelLoading = true
        if current != .downloading { transition(to: .downloading) }
        applyDownloadUI(.spinning)
        onSetupSucceeded?()
    }

    /// AppDelegate calls this when the daemon reports `ready` (model resident).
    func modelDidBecomeReady() {
        guard waitsForModel, modelLoading else { return }
        modelLoading = false
        transition(to: .success)
    }

    /// AppDelegate calls this if the daemon can't load the model (e.g. the
    /// download failed). Show the error + Retry, which restarts the daemon.
    func modelDidFail(_ message: String) {
        guard waitsForModel, modelLoading else { return }
        if current != .downloading { transition(to: .downloading) }
        applyDownloadUI(.error(message))
    }

    /// Called on the main thread by AppDelegate when setup_core.sh exits.
    func setupDidFinish(exitCode: Int32, failureMessage: String?) {
        setupFinished = true
        setupExitCode = exitCode
        setupFailureMessage = failureMessage

        // Only touch the UI once the user has cleared the permissions screen;
        // otherwise routeAfterPermissions() applies the right state later.
        guard permissionsCleared else { return }
        if exitCode == 0 {
            advanceAfterSetupSucceeded()
        } else {
            if current != .downloading { transition(to: .downloading) }
            applyDownloadUI(.error(failureMessage ?? defaultSetupError))
        }
    }

    private var defaultSetupError: String {
        "Setup couldn't finish. Check your internet connection and tap Retry."
    }

    // MARK: download screen state (single owner)

    /// The ONLY place that mutates the spinner, progress label, reassurance line,
    /// and Retry button — so there is no cross-caller ordering hazard.
    private func applyDownloadUI(_ state: DownloadUI) {
        reassuranceTimer?.invalidate()
        reassuranceTimer = nil
        reassuranceLabel.isHidden = true
        switch state {
        case .spinning:
            spinner.isHidden = false
            spinner.startAnimation(nil)
            progressLabel.textColor = .labelColor
            progressLabel.stringValue = "Downloading your private AI model…"
            retryButton.isHidden = true
            // Reassure on a slow connection without implying anything is wrong.
            reassuranceTimer = Timer.scheduledTimer(withTimeInterval: 25, repeats: false) { [weak self] _ in
                self?.reassuranceLabel.stringValue =
                    "This one-time download can take a few minutes on a slow connection."
                self?.reassuranceLabel.isHidden = false
            }
        case .error(let message):
            spinner.stopAnimation(nil)
            spinner.isHidden = true
            progressLabel.textColor = .systemRed
            progressLabel.stringValue = message
            retryButton.isHidden = false
            ActivationCoordinator.shared.refocus(window)
            announce(message)
        }
    }

    // MARK: screen transitions (cross-fade)

    private func view(for screen: Screen) -> NSView {
        switch screen {
        case .permissions: return permissionsView
        case .downloading: return downloadingView
        case .success:     return successView
        }
    }

    private func transition(to screen: Screen) {
        guard screen != current else { return }
        let outgoing = view(for: current)
        let incoming = view(for: screen)
        current = screen

        incoming.alphaValue = 0
        incoming.translatesAutoresizingMaskIntoConstraints = false
        if incoming.superview == nil {
            container.addSubview(incoming)
            pin(incoming, to: container)
        }
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.25
            ctx.allowsImplicitAnimation = true
            outgoing.animator().alphaValue = 0
            incoming.animator().alphaValue = 1
        }, completionHandler: { [weak self] in
            guard let self = self else { return }
            if outgoing !== self.view(for: self.current) {
                outgoing.removeFromSuperview()
                outgoing.alphaValue = 1   // reset for any future reuse
            }
            // Put keyboard focus on the success screen's default button so Return
            // works immediately and the accent styling renders.
            if self.current == .success {
                self.window.recalculateKeyViewLoop()
                self.window.makeFirstResponder(self.openButton)
            }
        })

        announceScreen(screen)
    }

    private func pin(_ subview: NSView, to parent: NSView) {
        NSLayoutConstraint.activate([
            subview.leadingAnchor.constraint(equalTo: parent.leadingAnchor),
            subview.trailingAnchor.constraint(equalTo: parent.trailingAnchor),
            subview.topAnchor.constraint(equalTo: parent.topAnchor),
            subview.bottomAnchor.constraint(equalTo: parent.bottomAnchor)
        ])
    }

    // MARK: VoiceOver announcements

    private func announceScreen(_ screen: Screen) {
        switch screen {
        case .permissions: break  // announced implicitly when the window opens
        case .downloading: announce("Permissions granted. Downloading your private AI model.")
        case .success:     announce("You're all set. VivoType is ready. Press Return to open it.")
        }
    }

    private func announce(_ message: String) {
        NSAccessibility.post(element: NSApp as Any,
                             notification: .announcementRequested,
                             userInfo: [.announcement: message,
                                        .priority: NSAccessibilityPriorityLevel.high.rawValue])
    }

    // MARK: grant actions (ported from PermissionsController)

    @objc private func grantMic() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] _ in
                DispatchQueue.main.async {
                    ActivationCoordinator.shared.refocus(self?.window)
                    self?.refresh()
                }
            }
        default:
            openPrivacySettings("Privacy_Microphone")
        }
    }

    @objc private func grantAccessibility() {
        guard !axGranted else { return }
        // The system shows AXIsProcessTrustedWithOptions' prompt only once per
        // app, so: first click → native prompt; later clicks → jump to Settings.
        let promptedKey = "hasPromptedAX"
        if UserDefaults.standard.bool(forKey: promptedKey) {
            openPrivacySettings("Privacy_Accessibility")
        } else {
            UserDefaults.standard.set(true, forKey: promptedKey)
            let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue()
            let options = [key: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
        }
    }

    private func openPrivacySettings(_ anchor: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)") {
            NSWorkspace.shared.open(url)
        }
    }

    // MARK: buttons

    @objc private func retryTapped() {
        // Retry the right phase: a failed model load restarts the daemon; a failed
        // setup re-runs setup_core.sh.
        if modelLoading {
            applyDownloadUI(.spinning)
            onRetryModel?()
            return
        }
        setupFinished = false
        setupExitCode = -999
        setupFailureMessage = nil
        applyDownloadUI(.spinning)   // disables/hides Retry until the next report
        onRunSetup?()
    }

    @objc private func openTapped() {
        close()
        onOpenVivoType?()
    }

    @objc private func quitTapped() {
        NSApp.terminate(nil)
    }

    func close() {
        pollTimer?.invalidate()
        pollTimer = nil
        reassuranceTimer?.invalidate()
        reassuranceTimer = nil
        spinner.stopAnimation(nil)
        window.orderOut(nil)
        if heldForeground { heldForeground = false; ActivationCoordinator.shared.end() }
    }

    // MARK: NSWindowDelegate

    // The window has no close button (no .closable) during setup, so this only
    // fires on programmatic close(); cleanup is already handled there.
    func windowWillClose(_ notification: Notification) {
        pollTimer?.invalidate()
        pollTimer = nil
        reassuranceTimer?.invalidate()
        reassuranceTimer = nil
    }
}
