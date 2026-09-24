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
    /// Active rules, for the "Your rules" section: a dictionary rule is
    /// (from, to); a learned name is (name, name).
    private struct ActiveRule { let isName: Bool; let from: String; let to: String }
    private var activeRules: [ActiveRule] = []
    private var isProcessing = false
    /// Set when `--list-json` itself failed, so an unreadable queue is never
    /// shown as "No pending corrections".
    private var listError: String?

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
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 460),
                         styleMask: [.titled, .closable], backing: .buffered, defer: false)
        w.title = "VivoType — Review Corrections"
        w.isReleasedWhenClosed = false
        w.delegate = self

        let content = NSView(frame: NSRect(x: 0, y: 0, width: 480, height: 460))

        headerLabel.frame = NSRect(x: 16, y: 428, width: 448, height: 18)
        headerLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        content.addSubview(headerLabel)

        let scroll = NSScrollView(frame: NSRect(x: 12, y: 52, width: 456, height: 368))
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
            let rules = self.runRulesList()
            DispatchQueue.main.async {
                switch list {
                case .success(let rows): self.corrections = rows; self.listError = nil
                case .failure(let error): self.corrections = []; self.listError = error.detail
                }
                self.activeRules = rules
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
        if let listError = listError {
            headerLabel.stringValue = "⚠ Couldn't load corrections"
            headerLabel.toolTip = listError
            return
        }
        headerLabel.toolTip = nil
        headerLabel.stringValue = corrections.isEmpty
            ? "No pending corrections 🎉"
            : "\(corrections.count) pending · most frequent first"
        for (index, correction) in corrections.enumerated() {
            rowsStack.addArrangedSubview(makeRow(index: index, correction: correction))
        }
        guard !activeRules.isEmpty else { return }
        if let last = rowsStack.arrangedSubviews.last { rowsStack.setCustomSpacing(18, after: last) }
        let section = NSTextField(labelWithString: "Your rules (\(activeRules.count)) · VivoType applies these as you dictate")
        section.font = .systemFont(ofSize: 12, weight: .semibold)
        section.textColor = .secondaryLabelColor
        rowsStack.addArrangedSubview(section)
        for (index, rule) in activeRules.enumerated() {
            rowsStack.addArrangedSubview(makeRuleRow(index: index, rule: rule))
        }
    }

    private func makeRuleRow(index: Int, rule: ActiveRule) -> NSView {
        let row = NSView()
        row.translatesAutoresizingMaskIntoConstraints = false
        row.heightAnchor.constraint(equalToConstant: 40).isActive = true
        row.widthAnchor.constraint(equalToConstant: 432).isActive = true

        let title = NSTextField(labelWithString: rule.isName ? rule.to : "\(rule.from)  →  \(rule.to)")
        title.frame = NSRect(x: 4, y: 20, width: 330, height: 16)
        title.font = .systemFont(ofSize: 13)
        title.lineBreakMode = .byTruncatingTail
        row.addSubview(title)

        let subtitle = NSTextField(labelWithString: rule.isName ? "learned name" : "dictionary rule")
        subtitle.frame = NSRect(x: 4, y: 3, width: 330, height: 14)
        subtitle.font = .systemFont(ofSize: 11)
        subtitle.textColor = .secondaryLabelColor
        row.addSubview(subtitle)

        let delete = rowButton("Delete", #selector(deleteRule(_:)), index, x: 366, width: 70)
        delete.frame.origin.y = 6
        row.addSubview(delete)
        return row
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

        let risky = correction["risky"] as? Bool ?? false
        let riskReason = correction["risk_reason"] as? String ?? ""
        let refused = correction["refused"] as? Bool ?? false

        let title = NSTextField(labelWithString: "\(from)  →  \(to)")
        title.frame = NSRect(x: 4, y: 24, width: 236, height: 16)
        title.font = .systemFont(ofSize: 13)
        row.addSubview(title)

        let subtitleText = refused ? "a bare symbol · can't become a rule"
                         : risky ? "⚠ \(where_) · seen \(count)× · risky"
                                 : "\(where_) · seen \(count)×"
        let subtitle = NSTextField(labelWithString: subtitleText)
        subtitle.frame = NSRect(x: 4, y: 6, width: 236, height: 14)
        subtitle.font = .systemFont(ofSize: 11)
        subtitle.textColor = risky ? .systemOrange : .secondaryLabelColor
        if risky { subtitle.toolTip = riskReason; title.toolTip = riskReason }
        row.addSubview(subtitle)

        let promote = rowButton("Promote", #selector(promoteRow(_:)), index, x: 246, width: 74)
        promote.isEnabled = !refused
        row.addSubview(promote)
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

    @objc private func deleteRule(_ sender: NSButton) {
        guard sender.tag < activeRules.count, !isProcessing else { return }
        let rule = activeRules[sender.tag]
        let alert = NSAlert()
        alert.messageText = rule.isName ? "Forget the name “\(rule.to)”?"
                                        : "Delete “\(rule.from) → \(rule.to)”?"
        alert.informativeText = rule.isName
            ? "VivoType forgets that it learned “\(rule.to)”. If it is also one of your contacts, it stays in your contacts."
            : "VivoType will stop changing “\(rule.from)” as you dictate."
        alert.addButton(withTitle: "Delete")
        alert.addButton(withTitle: "Cancel")
        let run: (NSApplication.ModalResponse) -> Void = { [weak self] response in
            guard response == .alertFirstButtonReturn, let self = self else { return }
            self.beginProcessing()
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                let result = self?.apply(action: rule.isName ? "delete-name" : "delete-rule",
                                         from: rule.from, to: rule.to)
                DispatchQueue.main.async {
                    self?.endProcessing()
                    self?.presentIfFailed(result)
                    // A name learned before VivoType recorded which names it
                    // added: it can't tell a learned name from a contact, so
                    // it keeps the name rather than risk deleting a contact.
                    if rule.isName, (result?["ok"] as? Bool) == true,
                       (result?["removed_from_names"] as? Bool) == false {
                        self?.presentNotice("“\(rule.to)” is still in your names list",
                                            "It may be one of your contacts, so VivoType kept it "
                                            + "and can still match words to it.")
                    }
                }
            }
        }
        if let win = window { alert.beginSheetModal(for: win, completionHandler: run) }
        else { run(alert.runModal()) }
    }

    @objc private func skipRow(_ sender: NSButton) {
        guard sender.tag < corrections.count else { return }
        corrections.remove(at: sender.tag)  // local only — stays in the log
        rebuildRows()
    }

    @objc private func promoteAll() {
        guard !isProcessing else { return }
        // Bulk promotion never takes a risky rule (common-word rewrite or a
        // single sighting) — those stay listed for a deliberate per-row click.
        let allCorrections = corrections.filter {
            ($0["risky"] as? Bool) != true && ($0["refused"] as? Bool) != true
        }
        if allCorrections.isEmpty {
            if !corrections.isEmpty, corrections.allSatisfy({ ($0["refused"] as? Bool) == true }) {
                presentNotice("Nothing to promote",
                              "The remaining corrections turn a word into a bare symbol and can't become "
                              + "rules. Discard them to clear the list.")
            } else if !corrections.isEmpty {
                presentNotice("Only risky corrections are pending",
                              "Each remaining correction is flagged ⚠ — promoting it could rewrite "
                              + "ordinary words. Hover a row to see why, and promote individually "
                              + "if you're sure.")
            }
            return
        }
        beginProcessing()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            for c in allCorrections {
                let result = self.apply(action: "promote", from: c["from"] as? String ?? "", to: c["to"] as? String ?? "")
                // Stop on the first failure that will repeat for every row (a
                // corrupt target file, the helper not running) and surface it
                // once. A row-specific refusal (already handled elsewhere,
                // refused pair) just moves on to the next row.
                let status = result?["status"] as? String
                if (result?["ok"] as? Bool) == false,
                   status == "write_failed" || status == "process_failed" {
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

    private struct HelperFailure: Error { let detail: String }

    private func runList() -> Result<[[String: Any]], HelperFailure> {
        let result = runPromote(["--list-json"])
        guard result.status == 0, let data = result.stdout.data(using: .utf8),
              let array = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
        else { return .failure(HelperFailure(detail: Self.failureDetail(result))) }
        return .success(array)
    }

    /// The active rules (`--list-rules-json`). An unreadable dictionary shows
    /// no rules section; the pending list reports its own failures.
    private func runRulesList() -> [ActiveRule] {
        let result = runPromote(["--list-rules-json"])
        guard result.status == 0, let data = result.stdout.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [] }
        let rules = (obj["rules"] as? [[String: Any]] ?? []).compactMap { r -> ActiveRule? in
            guard let from = r["from"] as? String, let to = r["to"] as? String else { return nil }
            return ActiveRule(isName: false, from: from, to: to)
        }
        let names = (obj["names"] as? [String] ?? []).map { ActiveRule(isName: true, from: $0, to: $0) }
        return rules + names
    }

    /// Run one promote action and return promote.py's parsed JSON result, so the
    /// caller can react to a failure (e.g. a corrupt target file) instead of
    /// silently swallowing it. A helper that crashed or printed no JSON comes
    /// back as `{"ok": false, "status": "process_failed"}`, never as nil —
    /// nil used to read as success.
    @discardableResult
    private func apply(action: String, from: String, to: String) -> [String: Any]? {
        guard !from.isEmpty, !to.isEmpty else { return nil }
        // The words go on stdin, never argv: any local process can read
        // another's command line.
        guard let input = try? JSONSerialization.data(withJSONObject: ["from": from, "to": to])
        else { return ["ok": false, "status": "process_failed"] }
        let result = runPromote(["--apply", "--stdin-json", "--action", action], input: input)
        guard result.status == 0, let data = result.stdout.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return ["ok": false, "status": "process_failed", "error": Self.failureDetail(result)]
        }
        return obj
    }

    private func runPromote(_ args: [String], input: Data? = nil) -> (stdout: String, stderr: String, status: Int32) {
        return runProcess(pythonPath, [promotePath] + args, timeout: 60, input: input)
    }

    /// The last line of the helper's stderr (a Python exception message), or
    /// its exit status when it printed nothing.
    private static func failureDetail(_ result: (stdout: String, stderr: String, status: Int32)) -> String {
        let lastLine = result.stderr.split(whereSeparator: \.isNewline).last.map(String.init)
        return lastLine ?? "The review helper exited with status \(result.status)."
    }

    // MARK: failure handling

    /// If promote.py reported `{"ok": false}` (e.g. it refused to overwrite a
    /// corrupt dictionary/lexicon — see promote.py `_load_json_object`), show a
    /// friendly recovery dialog instead of failing silently.
    private func presentIfFailed(_ result: [String: Any]?) {
        guard let result = result, (result["ok"] as? Bool) == false else { return }
        switch result["status"] as? String {
        case "write_failed":
            break  // the corrupt-file recovery dialog below
        case "not_found":
            return  // already promoted or discarded elsewhere; the reload shows it
        case "changed_since":
            return presentNotice("That rule changed",
                                 "It was edited since this list loaded, so it wasn't deleted. "
                                 + "The list now shows the current rule.")
        case "refused_punctuation":
            return presentNotice("That correction is a bare symbol",
                                 "A rule like “comma → ,” would change that word everywhere, so it can't become a rule. "
                                 + "Discard it to clear it from the list.")
        default:
            return presentNotice("Couldn't apply that correction",
                                 result["error"] as? String ?? "The review helper failed.")
        }
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

    private func presentNotice(_ title: String, _ detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        if let win = window { alert.beginSheetModal(for: win) }
        else { alert.runModal() }
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
