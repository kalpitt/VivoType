// VivoType — "Review Corrections" window. A native front-end to core/promote.py;
// it never reimplements the promotion logic — it shells out to promote.py for
// both reading (--list-json) and acting (--apply) so behaviour matches the CLI.

import Foundation
import AppKit

// MARK: - review corrections panel

/// A native window front-end to core/promote.py. It never reimplements the
/// promotion logic: it calls `promote.py --list-json` to read the pending
/// corrections and `promote.py --apply ...` to act, so behaviour is identical to
/// the CLI and both read the same corrections.jsonl.
final class ReviewController: NSObject, NSWindowDelegate {
    private let pythonPath: String
    private let promotePath: String
    private var window: NSWindow?
    // Balances the foreground (.regular) claim while this window is open.
    private var heldForeground = false
    private let headerLabel = NSTextField(labelWithString: "")
    private let rowsStack = NSStackView()
    private var corrections: [[String: Any]] = []
    private var isProcessing = false

    init(pythonPath: String, promotePath: String) {
        self.pythonPath = pythonPath
        self.promotePath = promotePath
        super.init()
    }

    func show() {
        if window == nil { buildWindow() }
        if !heldForeground { heldForeground = true; ActivationCoordinator.shared.begin() }
        else { ActivationCoordinator.shared.refocus(window) }
        reload()
        window?.center()
        window?.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        if heldForeground { heldForeground = false; ActivationCoordinator.shared.end() }
    }

    private func buildWindow() {
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 380),
                         styleMask: [.titled, .closable], backing: .buffered, defer: false)
        w.title = "VivoType — Review Corrections"
        w.isReleasedWhenClosed = false
        w.delegate = self

        let content = NSView(frame: NSRect(x: 0, y: 0, width: 480, height: 380))

        headerLabel.frame = NSRect(x: 16, y: 348, width: 448, height: 18)
        headerLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        content.addSubview(headerLabel)

        let scroll = NSScrollView(frame: NSRect(x: 12, y: 52, width: 456, height: 288))
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = false
        scroll.autohidesScrollers = true

        rowsStack.orientation = .vertical
        rowsStack.alignment = .leading
        rowsStack.spacing = 8
        rowsStack.edgeInsets = NSEdgeInsets(top: 8, left: 8, bottom: 8, right: 8)
        rowsStack.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = rowsStack
        NSLayoutConstraint.activate([
            rowsStack.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            rowsStack.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            rowsStack.widthAnchor.constraint(equalTo: scroll.widthAnchor),
        ])
        content.addSubview(scroll)

        let promoteAll = NSButton(title: "Promote all", target: self, action: #selector(promoteAll))
        promoteAll.frame = NSRect(x: 12, y: 12, width: 110, height: 30)
        promoteAll.bezelStyle = .rounded
        content.addSubview(promoteAll)

        let done = NSButton(title: "Done", target: self, action: #selector(closeWindow))
        done.frame = NSRect(x: 388, y: 12, width: 80, height: 30)
        done.bezelStyle = .rounded
        done.keyEquivalent = "\r"
        content.addSubview(done)

        w.contentView = content
        window = w
    }

    // MARK: data

    private func reload() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let list = self.runList()
            DispatchQueue.main.async {
                self.corrections = list
                self.rebuildRows()
            }
        }
    }

    private func beginProcessing() {
        isProcessing = true
        headerLabel.stringValue = "Applying…"
    }

    private func endProcessing() {
        isProcessing = false
        reload()
    }

    private func rebuildRows() {
        rowsStack.arrangedSubviews.forEach { $0.removeFromSuperview() }
        if corrections.isEmpty {
            headerLabel.stringValue = "No pending corrections 🎉"
            return
        }
        headerLabel.stringValue = "\(corrections.count) pending · most frequent first"
        for (index, correction) in corrections.enumerated() {
            rowsStack.addArrangedSubview(makeRow(index: index, correction: correction))
        }
    }

    private func makeRow(index: Int, correction: [String: Any]) -> NSView {
        let from = correction["from"] as? String ?? ""
        let to = correction["to"] as? String ?? ""
        let count = correction["count"] as? Int ?? 1
        let target = correction["target"] as? String ?? "dictionary"
        let where_ = target == "lexicon" ? "names lexicon" : "dictionary"

        let row = NSView()
        row.translatesAutoresizingMaskIntoConstraints = false
        row.heightAnchor.constraint(equalToConstant: 46).isActive = true
        row.widthAnchor.constraint(equalToConstant: 432).isActive = true

        let title = NSTextField(labelWithString: "\(from)  →  \(to)")
        title.frame = NSRect(x: 4, y: 24, width: 236, height: 16)
        title.font = .systemFont(ofSize: 13)
        row.addSubview(title)

        let subtitle = NSTextField(labelWithString: "\(where_) · seen \(count)×")
        subtitle.frame = NSRect(x: 4, y: 6, width: 236, height: 14)
        subtitle.font = .systemFont(ofSize: 11)
        subtitle.textColor = .secondaryLabelColor
        row.addSubview(subtitle)

        row.addSubview(rowButton("Promote", #selector(promoteRow(_:)), index, x: 246, width: 74))
        row.addSubview(rowButton("Skip", #selector(skipRow(_:)), index, x: 322, width: 52))
        row.addSubview(rowButton("Discard", #selector(discardRow(_:)), index, x: 376, width: 60))
        return row
    }

    private func rowButton(_ title: String, _ action: Selector, _ tag: Int, x: CGFloat, width: CGFloat) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.frame = NSRect(x: x, y: 9, width: width, height: 28)
        button.bezelStyle = .rounded
        button.controlSize = .small
        button.font = .systemFont(ofSize: 11)
        button.tag = tag
        return button
    }

    // MARK: actions

    @objc private func promoteRow(_ sender: NSButton) {
        guard sender.tag < corrections.count, !isProcessing else { return }
        let c = corrections[sender.tag]
        let from = c["from"] as? String ?? "", to = c["to"] as? String ?? ""
        beginProcessing()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = self?.apply(action: "promote", from: from, to: to)
            DispatchQueue.main.async {
                self?.endProcessing()
                self?.presentIfFailed(result)
            }
        }
    }

    @objc private func discardRow(_ sender: NSButton) {
        guard sender.tag < corrections.count, !isProcessing else { return }
        let c = corrections[sender.tag]
        let from = c["from"] as? String ?? "", to = c["to"] as? String ?? ""
        beginProcessing()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = self?.apply(action: "discard", from: from, to: to)
            DispatchQueue.main.async {
                self?.endProcessing()
                self?.presentIfFailed(result)
            }
        }
    }

    @objc private func skipRow(_ sender: NSButton) {
        guard sender.tag < corrections.count else { return }
        corrections.remove(at: sender.tag)  // local only — stays in the log
        rebuildRows()
    }

    @objc private func promoteAll() {
        guard !isProcessing else { return }
        let allCorrections = corrections
        beginProcessing()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            for c in allCorrections {
                let result = self.apply(action: "promote", from: c["from"] as? String ?? "", to: c["to"] as? String ?? "")
                // Stop on the first write failure (e.g. a corrupt target) and
                // surface it once, rather than retrying every remaining row.
                if let result = result, (result["ok"] as? Bool) == false {
                    DispatchQueue.main.async { self.endProcessing(); self.presentIfFailed(result) }
                    return
                }
            }
            DispatchQueue.main.async { self.endProcessing() }
        }
    }

    @objc private func closeWindow() {
        window?.close()
    }

    // MARK: promote.py bridge

    private func runList() -> [[String: Any]] {
        let output = runPromote(["--list-json"])
        guard let data = output.data(using: .utf8),
              let array = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
        else { return [] }
        return array
    }

    /// Run one promote action and return promote.py's parsed JSON result, so the
    /// caller can react to a failure (e.g. a corrupt target file) instead of
    /// silently swallowing it. Returns nil only if the output wasn't valid JSON.
    @discardableResult
    private func apply(action: String, from: String, to: String) -> [String: Any]? {
        guard !from.isEmpty, !to.isEmpty else { return nil }
        let output = runPromote(["--apply", "--from", from, "--to", to, "--action", action])
        guard let data = output.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return obj
    }

    private func runPromote(_ args: [String]) -> String {
        return runProcess(pythonPath, [promotePath] + args, timeout: 60).stdout
    }

    // MARK: failure handling

    /// If promote.py reported `{"ok": false}` (e.g. it refused to overwrite a
    /// corrupt dictionary/lexicon — see promote.py `_load_json_object`), show a
    /// friendly recovery dialog instead of failing silently.
    private func presentIfFailed(_ result: [String: Any]?) {
        guard let result = result, (result["ok"] as? Bool) == false else { return }
        let detail = result["error"] as? String
            ?? "VivoType couldn't read one of your saved-words files."
        let path = firstQuotedPath(in: detail)

        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Couldn't read your dictionary"
        alert.informativeText =
            "\(detail)\n\nOpen the file to fix it by hand, or reset it — VivoType keeps "
            + "a copy of the old one next to it so nothing is lost."
        alert.addButton(withTitle: "Open in Finder")   // .alertFirstButtonReturn
        alert.addButton(withTitle: "Reset File…")       // .alertSecondButtonReturn
        alert.addButton(withTitle: "Cancel")            // .alertThirdButtonReturn
        // Disable the file-specific buttons if we couldn't recover a path.
        if path == nil {
            alert.buttons[0].isEnabled = false
            alert.buttons[1].isEnabled = false
        }
        if let win = window { alert.beginSheetModal(for: win) { [weak self] resp in self?.handle(resp, path: path) } }
        else { handle(alert.runModal(), path: path) }
    }

    private func handle(_ response: NSApplication.ModalResponse, path: String?) {
        guard let path = path else { return }
        switch response {
        case .alertFirstButtonReturn:
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        case .alertSecondButtonReturn:
            confirmAndResetFile(at: path)
        default:
            break
        }
    }

    /// Move the unreadable file aside (…<name>.corrupt-<timestamp>) after an
    /// explicit confirmation, so promote.py can recreate a clean one next time.
    /// We never delete: the old bytes are preserved for manual recovery.
    private func confirmAndResetFile(at path: String) {
        let confirm = NSAlert()
        confirm.alertStyle = .warning
        confirm.messageText = "Reset this file?"
        confirm.informativeText =
            "VivoType will move the unreadable file aside (renamed with “.corrupt”) and "
            + "start a fresh one. Your old file is kept so you can recover entries later."
        confirm.addButton(withTitle: "Reset")
        confirm.addButton(withTitle: "Cancel")
        guard confirm.runModal() == .alertFirstButtonReturn else { return }

        let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
        let backup = "\(path).corrupt-\(stamp)"
        do {
            try FileManager.default.moveItem(atPath: path, toPath: backup)
            reload()  // pending list re-reads; promote.py will recreate the file cleanly
        } catch {
            let fail = NSAlert()
            fail.messageText = "Couldn't reset the file"
            fail.informativeText = "\(error.localizedDescription)\n\nYou can move or delete it yourself:\n\(path)"
            fail.runModal()
        }
    }

    /// Extract the first single-quoted path from a promote.py error string, e.g.
    /// "Refusing to modify '/Users/…/user_dictionary.json': …" -> that path.
    private func firstQuotedPath(in message: String) -> String? {
        guard let start = message.firstIndex(of: "'") else { return nil }
        let afterStart = message.index(after: start)
        guard let end = message[afterStart...].firstIndex(of: "'") else { return nil }
        let candidate = String(message[afterStart..<end])
        return candidate.hasPrefix("/") ? candidate : nil
    }
}
