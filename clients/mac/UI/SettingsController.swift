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
import UniformTypeIdentifiers

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
    private let hudSwitch = NSSwitch()
    private let contactsSwitch = NSSwitch()

    /// Live "Idle" / "Downloading model…" readout for the Privacy card, refreshed
    /// on a timer while the window is open.
    private let networkLabel = NSTextField(labelWithString: "Idle")
    private var networkTimer: Timer?

    /// Set by AppDelegate: reports whether the one-time model download is running
    /// (the app's only network activity).
    var isDownloading: (() -> Bool)?

    /// Set by AppDelegate: resolves the writable App Support directory that holds
    /// config.json and data/ — the files Backup & Restore reads and writes.
    var appSupportURL: (() -> URL)?

    /// Set by AppDelegate: called after a successful import so the running app
    /// picks up the restored settings without a relaunch.
    var onImported: (() -> Void)?

    /// Set by AppDelegate: profile names defined in postprocess_config.json's
    /// "profiles" object ("default" excluded). The card offers one popup entry
    /// per name; definitions themselves are edited in that JSON file (v1 scope).
    var definedProfileNames: (() -> [String])?

    /// Rows of the Contexts card live here so a mapping change can rebuild them
    /// without rebuilding the window (the card frame grows to fit).
    private let contextsRowsStack = NSStackView()
    /// The window's root content stack — kept for re-fitting the frame when
    /// the Contexts card grows or shrinks.
    private var contentStack: NSStackView?

    /// Model ids in popup order — mirrors `modelPopup`'s items exactly. Normally
    /// just `ModelCatalog.all`, but a config.json carrying a model we don't offer
    /// (e.g. `medium.en` chosen via the CLI) gets an extra trailing entry so the
    /// popup can represent it. Without that, selecting index 0 as a fallback would
    /// silently rewrite the user's model the next time any control changed.
    private var modelIds: [String] = []

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

    /// Replace the controller's settings snapshot (e.g. after a menu Model change)
    /// and refresh controls if the window already exists.
    func reload(_ settings: Settings) {
        self.settings = settings
        if window != nil { syncControls() }
    }

    func show() {
        if window == nil { buildWindow() }
        syncControls()
        if !heldForeground { heldForeground = true; ActivationCoordinator.shared.begin() }
        else { ActivationCoordinator.shared.refocus(window) }
        window?.center()
        window?.makeKeyAndOrderFront(nil)
        startNetworkTimer()
    }

    func windowWillClose(_ notification: Notification) {
        if heldForeground { heldForeground = false; ActivationCoordinator.shared.end() }
        networkTimer?.invalidate()
        networkTimer = nil
    }

    // MARK: privacy readout

    /// Poll the download flag once a second while the window is visible. A timer
    /// (rather than a push) keeps AppDelegate free of any Settings-window state;
    /// it is invalidated in `windowWillClose` so nothing ticks in the background.
    private func startNetworkTimer() {
        networkTimer?.invalidate()
        refreshNetworkLabel()
        let timer = Timer(timeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.refreshNetworkLabel()
        }
        RunLoop.main.add(timer, forMode: .common)
        networkTimer = timer
    }

    private func refreshNetworkLabel() {
        let downloading = isDownloading?() ?? false
        networkLabel.stringValue = downloading ? "Downloading model…" : "Idle"
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
        rebuildModelPopup()
        modelPopup.target = self; modelPopup.action = #selector(changed)
        soundSwitch.target = self; soundSwitch.action = #selector(changed)
        toastSwitch.target = self; toastSwitch.action = #selector(changed)
        hudSwitch.target = self; hudSwitch.action = #selector(changed)

        networkLabel.font = .systemFont(ofSize: 12)
        networkLabel.textColor = .secondaryLabelColor

        let localLabel = NSTextField(labelWithString: "Local")
        localLabel.font = .systemFont(ofSize: 12)
        localLabel.textColor = .secondaryLabelColor

        let exportButton = NSButton(title: "Export…", target: self, action: #selector(exportBundle))
        exportButton.bezelStyle = .rounded
        let importButton = NSButton(title: "Import…", target: self, action: #selector(importBundle))
        importButton.bezelStyle = .rounded
        let backupButtons = NSStackView(views: [exportButton, importButton])
        backupButtons.orientation = .horizontal
        backupButtons.spacing = 8

        // Rows → cards → sections.
        let dictation = makeCard([
            makeRow(symbol: "command", title: "Push-to-talk key",
                    desc: "Hold this key to dictate", control: hotkeyPopup),
            makeRow(symbol: "waveform", title: "Model",
                    desc: "Higher accuracy uses more memory", control: modelPopup),
            makeRow(symbol: "eye.slash", title: "Hide recording HUD",
                    desc: "Use sounds instead of the on-screen pill", control: hudSwitch),
        ])
        let contexts = makeContextsCard()
        rebuildContextRows()
        let notifications = makeCard([
            makeRow(symbol: "speaker.wave.2.fill", title: "Pop sound",
                    desc: "Play a sound when a correction is captured", control: soundSwitch),
            makeRow(symbol: "bell.fill", title: "Capture toast",
                    desc: "Show a chip when a correction is learned", control: toastSwitch),
        ])
        let privacy = makeCard([
            makeRow(symbol: "lock.shield.fill", title: "On-device processing",
                    desc: "Your voice and text never leave this Mac.", control: localLabel),
            makeRow(symbol: "network", title: "Network activity",
                    desc: "Only used once, to download the speech model.", control: networkLabel),
        ])
        let backup = makeCard([
            // Descriptions here are deliberately short: the two-button stack eats
            // most of the row, and makeRow clips text rather than wrapping it.
            makeRow(symbol: "arrow.up.arrow.down.circle.fill", title: "Backup file",
                    desc: "Settings + dictionary", control: backupButtons),
            makeRow(symbol: "person.crop.circle", title: "Include contact names",
                    desc: "Off by default — these are real names",
                    control: contactsSwitch),
        ])

        let outer = NSStackView(views: [
            sectionLabel("Dictation"), dictation,
            sectionLabel("Contexts"), contexts,
            sectionLabel("Notifications"), notifications,
            sectionLabel("Privacy"), privacy,
            sectionLabel("Backup & Restore"), backup,
            makeFooter(),
        ])
        outer.orientation = .vertical
        outer.alignment = .leading
        outer.spacing = 8
        outer.setCustomSpacing(18, after: dictation)
        outer.setCustomSpacing(18, after: contexts)
        outer.setCustomSpacing(18, after: notifications)
        outer.setCustomSpacing(18, after: privacy)
        outer.setCustomSpacing(18, after: backup)
        outer.translatesAutoresizingMaskIntoConstraints = false

        contentStack = outer
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

        let note = NSTextField(labelWithString: "Model changes apply immediately (daemon reloads).")
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

    // MARK: contexts (per-app post-processing profiles)

    /// Card shell for the Contexts rows; the rows themselves live in
    /// `contextsRowsStack` so they can be rebuilt as mappings change.
    private func makeContextsCard() -> NSView {
        let card = NSView()
        card.translatesAutoresizingMaskIntoConstraints = false
        card.wantsLayer = true
        card.layer?.cornerRadius = 10
        card.layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        card.layer?.borderWidth = 1
        card.layer?.borderColor = NSColor.separatorColor.cgColor

        contextsRowsStack.orientation = .vertical
        contextsRowsStack.alignment = .leading
        contextsRowsStack.spacing = 0
        contextsRowsStack.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(contextsRowsStack)
        NSLayoutConstraint.activate([
            card.widthAnchor.constraint(equalToConstant: contentWidth),
            contextsRowsStack.topAnchor.constraint(equalTo: card.topAnchor),
            contextsRowsStack.bottomAnchor.constraint(equalTo: card.bottomAnchor),
            contextsRowsStack.leadingAnchor.constraint(equalTo: card.leadingAnchor),
            contextsRowsStack.trailingAnchor.constraint(equalTo: card.trailingAnchor),
        ])
        return card
    }

    /// Refill the Contexts card: one row per stored mapping, then the
    /// "Add frontmost app" row. Called on build, on reload(settings), and
    /// after every mapping change.
    private func rebuildContextRows() {
        let views = contextsRowsStack.views
        views.forEach { $0.removeFromSuperview() }

        var rows: [NSView] = []
        for (bundleID, profileName) in settings.appProfiles.sorted(by: { $0.key < $1.key }) {
            rows.append(makeContextRow(bundleID: bundleID, profileName: profileName))
        }
        let addButton = NSButton(title: "Add…", target: self, action: #selector(addFrontmostApp))
        addButton.bezelStyle = .rounded
        rows.append(makeRow(symbol: "plus.app.fill", title: "Add frontmost app",
                            desc: "Dictate in this app with a chosen context",
                            control: addButton))

        var arranged: [NSView] = []
        for (i, row) in rows.enumerated() {
            if i > 0 { arranged.append(makeHairline()) }
            arranged.append(row)
        }
        arranged.forEach(contextsRowsStack.addArrangedSubview)
        resizeWindowToFit()
    }

    private func makeContextRow(bundleID: String, profileName: String) -> NSView {
        let popup = NSPopUpButton(frame: .zero, pullsDown: false)
        popup.addItems(withTitles: ["Default rules"])
        popup.lastItem?.representedObject = "default"
        for name in definedProfileNames?() ?? [] where name != "default" {
            popup.addItem(withTitle: name)
            popup.lastItem?.representedObject = name
        }
        let idx = popup.indexOfItem(withRepresentedObject: profileName)
        if idx < 0 && profileName != "default" {
            // Stored mapping points at a profile deleted from the JSON — show
            // that honestly instead of silently displaying "Default rules".
            popup.addItem(withTitle: profileName + " (missing)")
            popup.lastItem?.representedObject = profileName
        }
        popup.selectItem(at: max(0, popup.indexOfItem(withRepresentedObject: profileName)))
        popup.target = self
        popup.action = #selector(contextPopupChanged(_:))
        // Controls have no representedObject; carry the mapping key here.
        popup.identifier = NSUserInterfaceItemIdentifier(bundleID)

        let remove = NSButton(title: "–", target: self,
                              action: #selector(removeContextMapping(_:)))
        remove.bezelStyle = .rounded
        remove.identifier = NSUserInterfaceItemIdentifier(bundleID)
        remove.toolTip = "Remove this app's context"

        let controls = NSStackView(views: [popup, remove])
        controls.orientation = .horizontal
        controls.spacing = 6

        // Display name first (running app, then installed bundle metadata);
        // the raw bundle ID is a last-resort fallback for uninstalled apps.
        return makeRow(symbol: "app.badge.fill", title: displayName(forBundleID: bundleID),
                       desc: "Dictates with the selected context", control: controls)
    }

    /// Human-readable app name for a stored bundle ID.
    private func displayName(forBundleID bundleID: String) -> String {
        if let running = NSRunningApplication.runningApplications(
            withBundleIdentifier: bundleID).first, let name = running.localizedName {
            return name
        }
        if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID),
           let bundle = Bundle(url: url) {
            let name = bundle.localizedInfoDictionary?["CFBundleDisplayName"] as? String
                ?? bundle.infoDictionary?["CFBundleDisplayName"] as? String
                ?? bundle.localizedInfoDictionary?["CFBundleName"] as? String
                ?? bundle.infoDictionary?["CFBundleName"] as? String
            if let name = name { return name }
        }
        return bundleID
    }

    /// Save + live-apply + re-render after any mapping change.
    private func persistContexts() {
        settings.save(to: configPath)
        onApply(settings)
        rebuildContextRows()
    }

    @objc private func contextPopupChanged(_ sender: NSPopUpButton) {
        guard let bundleID = sender.identifier?.rawValue,
              let item = sender.selectedItem,
              let profileName = item.representedObject as? String
        else { return }
        settings.appProfiles[bundleID] = profileName
        persistContexts()
    }

    @objc private func removeContextMapping(_ sender: NSButton) {
        guard let bundleID = sender.identifier?.rawValue else { return }
        settings.appProfiles.removeValue(forKey: bundleID)
        persistContexts()
    }

    @objc private func addFrontmostApp() {
        // VivoType itself is never a dictation target.
        guard let app = NSWorkspace.shared.frontmostApplication,
              app.bundleIdentifier != Bundle.main.bundleIdentifier,
              let bundleID = app.bundleIdentifier
        else { return }
        if settings.appProfiles[bundleID] != nil { return }  // already mapped; edit its popup instead
        settings.appProfiles[bundleID] = "default"
        persistContexts()
    }

    /// The Contexts card changes height as mappings are added/removed — re-fit
    /// the window using the same math as buildWindow so nothing clips or leaves
    /// a growing gap above the footer.
    private func resizeWindowToFit() {
        guard let window = window, let outer = contentStack else { return }
        let fitting = outer.fittingSize
        window.setContentSize(NSSize(width: contentWidth + 48, height: fitting.height + 48))
    }

    // MARK: state

    /// Fill the popup from the catalog, plus the active model if it isn't one we
    /// offer, so `modelIds[selectedIndex]` always round-trips the stored value.
    private func rebuildModelPopup() {
        modelIds = ModelCatalog.all.map { $0.id }
        var titles = ModelCatalog.all.map { $0.label }
        if !modelIds.contains(settings.model) {
            modelIds.append(settings.model)
            titles.append(ModelCatalog.label(for: settings.model))  // raw id
        }
        modelPopup.removeAllItems()
        modelPopup.addItems(withTitles: titles)
    }

    private func syncControls() {
        hotkeyPopup.selectItem(at: hotkeyOptions.firstIndex { $0.code == settings.hotkeyKeycode } ?? 0)
        // Select by model id, not by title — titles are display labels now.
        if !modelIds.contains(settings.model) { rebuildModelPopup() }
        modelPopup.selectItem(at: modelIds.firstIndex(of: settings.model) ?? 0)
        soundSwitch.state = settings.soundEnabled ? .on : .off
        toastSwitch.state = settings.toastEnabled ? .on : .off
        hudSwitch.state = settings.hudEnabled ? .off : .on   // switch reads "Hide recording HUD"
        rebuildContextRows()
    }

    @objc private func changed() {
        let option = hotkeyOptions[max(0, hotkeyPopup.indexOfSelectedItem)]
        settings.hotkeyLabel = option.label
        settings.hotkeyKeycode = option.code
        let modelIndex = modelPopup.indexOfSelectedItem
        if modelIds.indices.contains(modelIndex) { settings.model = modelIds[modelIndex] }
        settings.soundEnabled = (soundSwitch.state == .on)
        settings.toastEnabled = (toastSwitch.state == .on)
        settings.hudEnabled = (hudSwitch.state == .off)      // on = hide the HUD
        settings.save(to: configPath)
        onApply(settings)
    }

    @objc private func closeWindow() {
        window?.close()
    }

    // MARK: backup & restore

    /// The three real per-user files, as (bundle key, absolute URL) pairs.
    /// `config.json` comes from `configPath` (VIVOTYPE_CONFIG can relocate it);
    /// the personalization files live under App Support's `data/`. The tracked
    /// `core/postprocess_config.json` is deliberately NOT here — it's the shipped
    /// default and carries no user data.
    private func backupFiles() -> [(key: String, url: URL)] {
        var files: [(String, URL)] = [("config", URL(fileURLWithPath: configPath))]
        guard let root = appSupportURL?() else { return files }
        files.append(("user_dictionary", root.appendingPathComponent("data/user_dictionary.json")))
        files.append(("contacts_lexicon", root.appendingPathComponent("data/lexicon/contacts.json")))
        return files
    }

    private func readJSONObject(at url: URL) -> Any? {
        guard let data = FileManager.default.contents(atPath: url.path),
              let obj = try? JSONSerialization.jsonObject(with: data),
              obj is [String: Any]
        else { return nil }
        return obj
    }

    /// Assemble the backup payload. Kept free of panels/alerts so the file
    /// contract (which keys, which files, version) is verifiable on its own.
    func makeBackupBundle(includeContacts: Bool) -> [String: Any] {
        var bundle: [String: Any] = ["version": 1]
        for (key, url) in backupFiles() {
            if key == "contacts_lexicon" && !includeContacts { continue }
            if let obj = readJSONObject(at: url) { bundle[key] = obj }
        }
        return bundle
    }

    /// Write a validated bundle back over the real files. Returns the keys that
    /// failed, empty on full success. Panel-free for the same reason as above.
    @discardableResult
    func restore(bundle: [String: Any]) -> [String] {
        var failed: [String] = []
        for (key, url) in backupFiles() where bundle[key] != nil {
            guard let obj = bundle[key], JSONSerialization.isValidJSONObject(obj),
                  let out = try? JSONSerialization.data(withJSONObject: obj,
                                                        options: [.prettyPrinted, .sortedKeys])
            else { failed.append(key); continue }
            try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                     withIntermediateDirectories: true)
            if (try? out.write(to: url, options: .atomic)) == nil { failed.append(key) }
        }
        return failed
    }

    @objc private func exportBundle() {
        let includeContacts = (contactsSwitch.state == .on)
        // A backup with real names in it is personal data leaving the machine's
        // managed folder — make that an explicit, separate decision.
        if includeContacts {
            let alert = NSAlert()
            alert.messageText = "Include your contact names in this backup?"
            alert.informativeText = "The backup file will contain the real names VivoType "
                + "learned for dictation. Anyone who opens the file can read them. "
                + "Store it somewhere you trust."
            alert.alertStyle = .warning
            alert.addButton(withTitle: "Include Names")
            alert.addButton(withTitle: "Cancel")
            guard alert.runModal() == .alertFirstButtonReturn else { return }
        }

        let bundle = makeBackupBundle(includeContacts: includeContacts)

        let panel = NSSavePanel()
        panel.nameFieldStringValue = "VivoType-backup.json"
        panel.allowedContentTypes = [.json]
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let target = panel.url else { return }
        guard let data = try? JSONSerialization.data(withJSONObject: bundle,
                                                     options: [.prettyPrinted, .sortedKeys]),
              (try? data.write(to: target, options: .atomic)) != nil
        else {
            presentError("Export failed", "Couldn't write the backup file to that location.")
            return
        }
    }

    @objc private func importBundle() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.json]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        guard panel.runModal() == .OK, let source = panel.url else { return }

        guard let data = FileManager.default.contents(atPath: source.path),
              let bundle = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            presentError("Import failed", "That file isn't a valid VivoType backup.")
            return
        }
        guard let version = bundle["version"] as? Int, version == 1 else {
            presentError("Import failed",
                         "This backup was made by a different version of VivoType.")
            return
        }

        // Overwriting the dictionary is not undoable — confirm before touching disk.
        let present = backupFiles().filter { bundle[$0.key] != nil }
        guard !present.isEmpty else {
            presentError("Nothing to import", "That backup file contains no VivoType data.")
            return
        }
        let alert = NSAlert()
        alert.messageText = "Replace your current settings with this backup?"
        alert.informativeText = "This overwrites \(present.count) file(s) and can't be undone."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Replace")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        let failed = restore(bundle: bundle)
        if !failed.isEmpty {
            presentError("Import incomplete",
                         "Couldn't restore: \(failed.joined(separator: ", ")).")
        }

        // Re-read from disk so the window and the running app both reflect the
        // restored config immediately.
        settings = Settings.load(from: configPath)
        syncControls()
        onApply(settings)
        onImported?()
    }

    private func presentError(_ title: String, _ detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.alertStyle = .warning
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}
