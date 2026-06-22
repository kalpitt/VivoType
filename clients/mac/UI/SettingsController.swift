// VivoType — native preferences window. Writes core/config.json on every change
// and calls back so the running app applies settings live (model reload,
// hotkey, sound, toast).
//
// Layout: a single scrolling-free pane of grouped "section cards" (Dictation,
// Notifications), each row a tinted SF Symbol chip + label + secondary
// description + a trailing native control. All colors are system semantic
// (controlAccentColor, controlBackgroundColor, separatorColor, labelColor) so
// the window follows the user's accent and light/dark appearance.

import Foundation
import AppKit

// MARK: - settings window

final class SettingsController: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private let configPath: String
    private var settings: Settings
    private let onApply: (Settings) -> Void
    // Balances the foreground (.regular) claim while this window is open.
    private var heldForeground = false

    /// Inner content width (window is this + 2× the 24pt margin).
    private let contentWidth: CGFloat = 412

    private let hotkeyPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let modelPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let soundSwitch = NSSwitch()
    private let toastSwitch = NSSwitch()

    private let hotkeyOptions: [(label: String, code: UInt16)] = [
        ("Right Option", 61), ("Left Option", 58),
        ("Right Command", 54), ("Right Control", 62), ("Right Shift", 60),
    ]

    init(settings: Settings, configPath: String, onApply: @escaping (Settings) -> Void) {
        self.settings = settings
        self.configPath = configPath
        self.onApply = onApply
        super.init()
    }

    func show() {
        if window == nil { buildWindow() }
        syncControls()
        if !heldForeground { heldForeground = true; ActivationCoordinator.shared.begin() }
        else { ActivationCoordinator.shared.refocus(window) }
        window?.center()
        window?.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        if heldForeground { heldForeground = false; ActivationCoordinator.shared.end() }
    }

    // MARK: build

    private func buildWindow() {
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: contentWidth + 48, height: 400),
                         styleMask: [.titled, .closable], backing: .buffered, defer: false)
        w.title = "VivoType — Settings"
        w.isReleasedWhenClosed = false
        w.delegate = self

        // Controls.
        hotkeyPopup.addItems(withTitles: hotkeyOptions.map { $0.label })
        hotkeyPopup.target = self; hotkeyPopup.action = #selector(changed)
        modelPopup.addItems(withTitles: ["small.en", "tiny.en"])
        modelPopup.target = self; modelPopup.action = #selector(changed)
        soundSwitch.target = self; soundSwitch.action = #selector(changed)
        toastSwitch.target = self; toastSwitch.action = #selector(changed)

        // Rows → cards → sections.
        let dictation = makeCard([
            makeRow(symbol: "command", title: "Push-to-talk key",
                    desc: "Hold this key to dictate", control: hotkeyPopup),
            makeRow(symbol: "waveform", title: "Model",
                    desc: "Higher accuracy uses more memory", control: modelPopup),
        ])
        let notifications = makeCard([
            makeRow(symbol: "speaker.wave.2.fill", title: "Pop sound",
                    desc: "Play a sound when recording starts", control: soundSwitch),
            makeRow(symbol: "bell.fill", title: "Capture toast",
                    desc: "Show a chip when a correction is learned", control: toastSwitch),
        ])

        let outer = NSStackView(views: [
            sectionLabel("Dictation"), dictation,
            sectionLabel("Notifications"), notifications,
            makeFooter(),
        ])
        outer.orientation = .vertical
        outer.alignment = .leading
        outer.spacing = 8
        outer.setCustomSpacing(18, after: dictation)
        outer.setCustomSpacing(18, after: notifications)
        outer.translatesAutoresizingMaskIntoConstraints = false

        let container = NSView()
        container.addSubview(outer)
        NSLayoutConstraint.activate([
            outer.topAnchor.constraint(equalTo: container.topAnchor, constant: 24),
            outer.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 24),
            outer.widthAnchor.constraint(equalToConstant: contentWidth),
        ])
        w.contentView = container

        // Size the window to fit the assembled content (fixed-width rows make the
        // fitting height deterministic).
        let fitting = outer.fittingSize
        w.setContentSize(NSSize(width: contentWidth + 48, height: fitting.height + 48))
        window = w
    }

    // MARK: builders

    private func sectionLabel(_ text: String) -> NSView {
        let label = NSTextField(labelWithString: text.uppercased())
        label.font = .systemFont(ofSize: 11, weight: .semibold)
        label.textColor = .secondaryLabelColor
        label.translatesAutoresizingMaskIntoConstraints = false
        label.widthAnchor.constraint(equalToConstant: contentWidth).isActive = true
        return label
    }

    private func makeCard(_ rows: [NSView]) -> NSView {
        let card = NSView()
        card.translatesAutoresizingMaskIntoConstraints = false
        card.wantsLayer = true
        card.layer?.cornerRadius = 10
        card.layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        card.layer?.borderWidth = 1
        card.layer?.borderColor = NSColor.separatorColor.cgColor

        // Interleave hairline separators between rows.
        var arranged: [NSView] = []
        for (i, row) in rows.enumerated() {
            if i > 0 { arranged.append(makeHairline()) }
            arranged.append(row)
        }
        let stack = NSStackView(views: arranged)
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 0
        stack.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(stack)
        NSLayoutConstraint.activate([
            card.widthAnchor.constraint(equalToConstant: contentWidth),
            stack.topAnchor.constraint(equalTo: card.topAnchor),
            stack.bottomAnchor.constraint(equalTo: card.bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: card.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: card.trailingAnchor),
        ])
        return card
    }

    private func makeHairline() -> NSView {
        let line = NSView()
        line.translatesAutoresizingMaskIntoConstraints = false
        line.wantsLayer = true
        line.layer?.backgroundColor = NSColor.separatorColor.cgColor
        NSLayoutConstraint.activate([
            line.widthAnchor.constraint(equalToConstant: contentWidth),
            line.heightAnchor.constraint(equalToConstant: 1),
        ])
        return line
    }

    private func makeIconChip(_ symbol: String) -> NSView {
        let chip = NSView()
        chip.translatesAutoresizingMaskIntoConstraints = false
        chip.wantsLayer = true
        chip.layer?.cornerRadius = 7
        chip.layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.12).cgColor

        let icon = NSImageView()
        icon.translatesAutoresizingMaskIntoConstraints = false
        icon.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
        icon.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 13, weight: .medium)
        icon.contentTintColor = .controlAccentColor
        chip.addSubview(icon)
        NSLayoutConstraint.activate([
            chip.widthAnchor.constraint(equalToConstant: 28),
            chip.heightAnchor.constraint(equalToConstant: 28),
            icon.centerXAnchor.constraint(equalTo: chip.centerXAnchor),
            icon.centerYAnchor.constraint(equalTo: chip.centerYAnchor),
        ])
        return chip
    }

    private func makeRow(symbol: String, title: String, desc: String, control: NSView) -> NSView {
        let row = NSView()
        row.translatesAutoresizingMaskIntoConstraints = false

        let chip = makeIconChip(symbol)
        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.font = .systemFont(ofSize: 13, weight: .medium)
        titleLabel.textColor = .labelColor
        let descLabel = NSTextField(labelWithString: desc)
        descLabel.font = .systemFont(ofSize: 11)
        descLabel.textColor = .secondaryLabelColor

        let text = NSStackView(views: [titleLabel, descLabel])
        text.orientation = .vertical
        text.alignment = .leading
        text.spacing = 1
        text.translatesAutoresizingMaskIntoConstraints = false
        control.translatesAutoresizingMaskIntoConstraints = false

        row.addSubview(chip)
        row.addSubview(text)
        row.addSubview(control)
        NSLayoutConstraint.activate([
            row.widthAnchor.constraint(equalToConstant: contentWidth),
            row.heightAnchor.constraint(equalToConstant: 56),
            chip.leadingAnchor.constraint(equalTo: row.leadingAnchor, constant: 14),
            chip.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            text.leadingAnchor.constraint(equalTo: chip.trailingAnchor, constant: 12),
            text.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            control.trailingAnchor.constraint(equalTo: row.trailingAnchor, constant: -14),
            control.centerYAnchor.constraint(equalTo: row.centerYAnchor),
            text.trailingAnchor.constraint(lessThanOrEqualTo: control.leadingAnchor, constant: -8),
        ])
        return row
    }

    private func makeFooter() -> NSView {
        let footer = NSView()
        footer.translatesAutoresizingMaskIntoConstraints = false

        let note = NSTextField(labelWithString: "Model changes take effect on your next dictation.")
        note.font = .systemFont(ofSize: 11)
        note.textColor = .secondaryLabelColor
        note.translatesAutoresizingMaskIntoConstraints = false

        let done = NSButton(title: "Done", target: self, action: #selector(closeWindow))
        done.bezelStyle = .rounded
        done.keyEquivalent = "\r"
        done.translatesAutoresizingMaskIntoConstraints = false

        footer.addSubview(note)
        footer.addSubview(done)
        NSLayoutConstraint.activate([
            footer.widthAnchor.constraint(equalToConstant: contentWidth),
            footer.heightAnchor.constraint(equalToConstant: 32),
            note.leadingAnchor.constraint(equalTo: footer.leadingAnchor),
            note.centerYAnchor.constraint(equalTo: footer.centerYAnchor),
            done.trailingAnchor.constraint(equalTo: footer.trailingAnchor),
            done.centerYAnchor.constraint(equalTo: footer.centerYAnchor),
            done.widthAnchor.constraint(equalToConstant: 80),
        ])
        return footer
    }

    // MARK: state

    private func syncControls() {
        hotkeyPopup.selectItem(at: hotkeyOptions.firstIndex { $0.code == settings.hotkeyKeycode } ?? 0)
        modelPopup.selectItem(withTitle: settings.model)
        if modelPopup.indexOfSelectedItem < 0 { modelPopup.selectItem(at: 0) }
        soundSwitch.state = settings.soundEnabled ? .on : .off
        toastSwitch.state = settings.toastEnabled ? .on : .off
    }

    @objc private func changed() {
        let option = hotkeyOptions[max(0, hotkeyPopup.indexOfSelectedItem)]
        settings.hotkeyLabel = option.label
        settings.hotkeyKeycode = option.code
        settings.model = modelPopup.titleOfSelectedItem ?? settings.model
        settings.soundEnabled = (soundSwitch.state == .on)
        settings.toastEnabled = (toastSwitch.state == .on)
        settings.save(to: configPath)
        onApply(settings)
    }

    @objc private func closeWindow() {
        window?.close()
    }
}
