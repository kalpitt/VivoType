// VivoType — field watch: after a dictation lands, watch the text field it
// landed in and report the user's in-place edit of that span once they pause
// typing. Feeds the correction offer (CorrectionOffers.swift). Runs only when
// Settings "Suggest corrections after edits" is on.
//
// Every Accessibility read happens on a background queue with a short
// messaging timeout (an unresponsive app must never stall the hotkey or the
// UI) and is capped in size; all session state lives on the main thread.
// Secure fields are never read, and a field that can't be read safely is
// simply not watched.

import Foundation
import AppKit
import ApplicationServices
import Carbon.HIToolbox

/// One settled in-place edit of a dictated span.
struct SettledEdit {
    let session: Int       // which dictation this belongs to
    let original: String   // what VivoType typed
    let corrected: String  // what that span reads now
    /// Where the span sits on screen (Cocoa coordinates), when the app
    /// reports it. The offer card is placed just below it.
    let rect: NSRect?
}

final class FieldWatch {
    /// Main thread: the span changed and then stayed unchanged for
    /// `settleDelay`. `corrected == original` means the user put it back.
    var onSettledEdit: ((SettledEdit) -> Void)?
    /// Main thread: the span is changing right now (typing in progress).
    var onEditing: ((Int) -> Void)?
    /// Main thread: the span was located, so this dictation is now watched.
    var onAnchored: ((Int) -> Void)?

    private static let landingDelay: TimeInterval = 0.35  // let the keystrokes land
    private static let pollInterval: TimeInterval = 0.5
    private static let settleDelay: TimeInterval = 1.2
    /// Stop watching an untouched dictation after this long.
    private static let untouchedLifetime: TimeInterval = 120
    /// Hard stop even mid-edit, so a forgotten session can't poll forever.
    private static let maxLifetime: TimeInterval = 600
    /// Fields longer than this are not read at all.
    private static let maxFieldLength = 20_000
    /// UTF-16 units of context remembered on each side of the span.
    private static let anchorLength = 24
    private static let axTimeout: Float = 0.25

    private let axQueue = DispatchQueue(label: "com.vivotype.fieldwatch", qos: .utility)
    private var session: Session?
    /// The app the dictation landed in: switching away ends the watch.
    private var watchedPID: pid_t?
    private var timer: Timer?
    /// Bumped by begin() and cancel(): a result computed for an older
    /// generation is dropped when it reaches the main thread.
    private var generation = 0

    private final class Session {
        let id: Int
        let original: String
        let anchor: Anchor
        let startedAt = Date()
        var lastSpan: String
        /// Where the span started on the last good read: text typed or
        /// deleted before it moves it, and the nearest prefix match follows.
        var spanStart: Int
        var lastRect: NSRect?
        var lastChange = Date()
        /// Consecutive polls that couldn't find the anchors; polling backs off.
        var lostStreak = 0
        var tick = 0
        var reported: String?
        var readInFlight = false

        init(id: Int, original: String, anchor: Anchor) {
            self.id = id
            self.original = original
            self.anchor = anchor
            self.lastSpan = original
            self.spanStart = anchor.spanStart
        }
    }

    /// Where a dictated span sits in its field: the element, plus the text
    /// immediately before and after it at landing time.
    private struct Anchor {
        let element: AXUIElement
        let prefix: String
        let suffix: String
        let spanStart: Int  // UTF-16 offset when anchored
    }

    private enum Read {
        case span(String, start: Int, bounds: CGRect?)  // bounds: Accessibility coords
        case lost   // anchors not found right now (user moved text, mid-edit)
        case gone   // element no longer readable (closed, secure, too long)
    }

    // MARK: control (main thread)

    /// Start watching a dictation that just landed. Cancels any previous
    /// session. Returns the session id (whether or not anchoring succeeds).
    @discardableResult
    func begin(original: String) -> Int {
        cancel()
        let gen = generation
        guard !original.isEmpty, AXIsProcessTrusted(), !IsSecureEventInputEnabled() else { return gen }
        watchedPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.landingDelay) { [weak self] in
            guard let self = self, self.generation == gen, !IsSecureEventInputEnabled() else { return }
            self.axQueue.async {
                let anchor = Self.locate(original)
                DispatchQueue.main.async {
                    guard self.generation == gen, let anchor = anchor else { return }
                    self.session = Session(id: gen, original: original, anchor: anchor)
                    self.startTimer()
                    self.onAnchored?(gen)
                }
            }
        }
        return gen
    }

    /// Stop watching (new dictation, offer resolved, feature turned off).
    func cancel() {
        generation += 1
        session = nil
        timer?.invalidate()
        timer = nil
    }

    /// End only if `id` is still the current session.
    func end(session id: Int) {
        if session?.id == id { cancel() }
    }

    private func startTimer() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: Self.pollInterval, repeats: true) { [weak self] _ in
            self?.poll()
        }
        timer?.tolerance = 0.1
    }

    private func poll() {
        guard let s = session else { return cancel() }
        let age = Date().timeIntervalSince(s.startedAt)
        let untouched = s.reported == nil && s.lastSpan == s.original
        if age > Self.maxLifetime || (untouched && age > Self.untouchedLifetime) {
            return cancel()
        }
        // The user moved to another app: the dictated field is no longer theirs.
        if let pid = watchedPID, NSWorkspace.shared.frontmostApplication?.processIdentifier != pid {
            return cancel()
        }
        // Anchors keep failing (text moved, mid-rewrite): read every 4th tick.
        s.tick += 1
        if s.lostStreak > 4, s.tick % 4 != 0 { return }
        // The user is in a password field right now: read nothing this tick.
        guard !IsSecureEventInputEnabled(), !s.readInFlight else { return }
        s.readInFlight = true
        let anchor = s.anchor
        let original = s.original
        let near = s.spanStart
        // Screen bounds cost an extra (layout-forcing) call in browsers: ask
        // only when this read may settle an edit, which is when the card shows.
        let wantBounds = s.lastSpan != s.original && s.lastSpan != s.reported
            && Date().timeIntervalSince(s.lastChange) >= Self.settleDelay - Self.pollInterval
        axQueue.async { [weak self] in
            let read = Self.readSpan(anchor, near: near, original: original, wantBounds: wantBounds)
            DispatchQueue.main.async {
                guard let self = self, let current = self.session, current === s else { return }
                s.readInFlight = false
                self.observe(read, in: s)
            }
        }
    }

    private func observe(_ read: Read, in s: Session) {
        switch read {
        case .gone:
            cancel()
        case .lost:
            s.lostStreak += 1
            return
        case .span(let text, let start, let bounds):
            s.lostStreak = 0
            s.spanStart = start
            if let bounds = bounds { s.lastRect = Self.cocoaRect(bounds) }
            if text != s.lastSpan {
                s.lastSpan = text
                s.lastChange = Date()
                onEditing?(s.id)
                return
            }
            guard Date().timeIntervalSince(s.lastChange) >= Self.settleDelay,
                  text != s.reported else { return }
            if s.reported == nil && text == s.original { return }  // never edited
            s.reported = text
            onSettledEdit?(SettledEdit(session: s.id, original: s.original, corrected: text,
                                       rect: s.lastRect))
        }
    }

    // MARK: Accessibility (axQueue only)

    /// Find the just-typed span in the focused field: the occurrence that
    /// ends at the caret, else the field's only occurrence. Ambiguous or
    /// missing (the app autocorrected it, a canvas app without AX text)
    /// means nil: that dictation is simply not watched.
    private static func locate(_ original: String) -> Anchor? {
        let system = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(system, axTimeout)
        var focused: CFTypeRef?
        guard AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute as CFString,
                                            &focused) == .success,
              let focusedRef = focused, CFGetTypeID(focusedRef) == AXUIElementGetTypeID()
        else { return nil }
        let element = focusedRef as! AXUIElement  // type checked just above
        AXUIElementSetMessagingTimeout(element, axTimeout)
        guard isPlainEditableText(element), let value = readValue(element) else { return nil }

        let field = value as NSString
        let target = original as NSString
        var range = NSRange(location: NSNotFound, length: 0)
        if let caret = caretLocation(element), caret >= target.length, caret <= field.length {
            let candidate = NSRange(location: caret - target.length, length: target.length)
            if field.substring(with: candidate) == original { range = candidate }
        }
        if range.location == NSNotFound {
            let first = field.range(of: original)
            guard first.location != NSNotFound else { return nil }
            let after = first.location + 1
            let rest = NSRange(location: after, length: field.length - after)
            guard field.range(of: original, options: [], range: rest).location == NSNotFound
            else { return nil }  // appears twice: can't tell which one we typed
            range = first
        }
        let prefixStart = max(0, range.location - anchorLength)
        let end = range.location + range.length
        return Anchor(
            element: element,
            prefix: field.substring(with: NSRange(location: prefixStart,
                                                  length: range.location - prefixStart)),
            suffix: field.substring(with: NSRange(location: end,
                                                  length: min(anchorLength, field.length - end))),
            spanStart: range.location)
    }

    /// The text now sitting between the two anchors. The prefix occurrence
    /// nearest the span's last known start wins; the suffix is the first one
    /// after it. Empty anchors mean the start / end of the field.
    private static func readSpan(_ anchor: Anchor, near: Int, original: String,
                                 wantBounds: Bool) -> Read {
        guard isPlainEditableText(anchor.element), let value = readValue(anchor.element)
        else { return .gone }
        let field = value as NSString
        let prefix = anchor.prefix as NSString
        var spanStart = 0
        if prefix.length > 0 {
            var best: Int?
            var search = NSRange(location: 0, length: field.length)
            while true {
                let hit = field.range(of: anchor.prefix, options: [], range: search)
                if hit.location == NSNotFound { break }
                let end = hit.location + hit.length
                if best == nil || abs(end - near) < abs(best! - near) {
                    best = end
                }
                let next = hit.location + 1
                search = NSRange(location: next, length: field.length - next)
            }
            guard let found = best else { return .lost }
            spanStart = found
        }
        var spanEnd = field.length
        if !anchor.suffix.isEmpty {
            let rest = NSRange(location: spanStart, length: field.length - spanStart)
            let hit = field.range(of: anchor.suffix, options: [], range: rest)
            guard hit.location != NSNotFound else { return .lost }
            spanEnd = hit.location
        }
        // A span that grew far past the dictation is new writing, not a fix.
        guard spanEnd - spanStart <= (original as NSString).length * 3 + 64 else { return .lost }
        let span = field.substring(with: NSRange(location: spanStart, length: spanEnd - spanStart))
        // With no anchor on one side the span runs to the field's edge, so
        // writing on past the dictation lands inside it. A fix keeps the word
        // count within one (a split or a join); more is new writing.
        if anchor.prefix.isEmpty || anchor.suffix.isEmpty,
           abs(wordCount(span) - wordCount(original)) > 1 {
            return .lost
        }
        let bounds = wantBounds
            ? axBounds(anchor.element, NSRange(location: spanStart, length: spanEnd - spanStart))
            : nil
        return .span(span, start: spanStart, bounds: bounds)
    }

    /// Text fields and text areas only, and never a secure (password) field.
    private static func isPlainEditableText(_ element: AXUIElement) -> Bool {
        guard let role = stringAttribute(element, kAXRoleAttribute) else { return false }
        let editable: Set<String> = [kAXTextFieldRole, kAXTextAreaRole, kAXComboBoxRole]
        guard editable.contains(role) else { return false }
        return stringAttribute(element, kAXSubroleAttribute) != kAXSecureTextFieldSubrole
    }

    /// The field's text, or nil when unreadable or longer than the cap. The
    /// character count is checked first so an oversized document is never
    /// copied across.
    private static func readValue(_ element: AXUIElement) -> String? {
        var count: CFTypeRef?
        if AXUIElementCopyAttributeValue(element, kAXNumberOfCharactersAttribute as CFString,
                                         &count) == .success,
           let n = count as? Int, n > maxFieldLength {
            return nil
        }
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXValueAttribute as CFString,
                                            &value) == .success,
              let text = value as? String, (text as NSString).length <= maxFieldLength
        else { return nil }
        return text
    }

    /// The span's bounds in Accessibility coordinates (top-left origin), or
    /// nil when the app doesn't report them. axQueue only: no AppKit here.
    private static func axBounds(_ element: AXUIElement, _ range: NSRange) -> CGRect? {
        var cfRange = CFRange(location: range.location, length: max(range.length, 1))
        guard let axRange = AXValueCreate(.cfRange, &cfRange) else { return nil }
        var value: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(
                  element, kAXBoundsForRangeParameterizedAttribute as CFString,
                  axRange, &value) == .success,
              let axValue = value, CFGetTypeID(axValue) == AXValueGetTypeID()
        else { return nil }
        var rect = CGRect.zero
        guard AXValueGetValue(axValue as! AXValue, .cgRect, &rect),  // type checked above
              rect.width > 0, rect.height > 0
        else { return nil }
        return rect
    }

    /// Accessibility's top-left origin to Cocoa's bottom-left; nil when the
    /// rect is on no screen. Main thread (NSScreen).
    private static func cocoaRect(_ rect: CGRect) -> NSRect? {
        guard let primary = NSScreen.screens.first else { return nil }
        let flipped = NSRect(x: rect.minX, y: primary.frame.maxY - rect.maxY,
                             width: rect.width, height: rect.height)
        return NSScreen.screens.contains { $0.frame.intersects(flipped) } ? flipped : nil
    }

    private static func wordCount(_ text: String) -> Int {
        text.split(whereSeparator: { $0.isWhitespace }).count
    }

    private static func caretLocation(_ element: AXUIElement) -> Int? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXSelectedTextRangeAttribute as CFString,
                                            &value) == .success,
              let axValue = value, CFGetTypeID(axValue) == AXValueGetTypeID()
        else { return nil }
        var range = CFRange()
        guard AXValueGetValue(axValue as! AXValue, .cfRange, &range), range.length == 0
        else { return nil }
        return range.location
    }

    private static func stringAttribute(_ element: AXUIElement, _ name: String) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success
        else { return nil }
        return value as? String
    }
}
