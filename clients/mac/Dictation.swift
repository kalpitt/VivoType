// VivoType — dictation controller: mic capture (resampled natively to 16 kHz mono
// 16-bit PCM via AVAudioConverter, so the Python backend needs no audio libs),
// text injection into the focused app, and clipboard-based correction learning.

import Foundation
import AVFoundation
import AppKit
import Carbon.HIToolbox
import CoreGraphics
import ApplicationServices

// MARK: - Dictation controller

final class Dictation {
    private let engine = AVAudioEngine()
    private var audioFile: AVAudioFile?
    private var audioConverter: AVAudioConverter?
    private var tempURL: URL?
    private var isRecording = false
    private var isBusy = false

    // Target WAV spec delivered to the daemon: mono, 16 kHz, 16-bit PCM.
    // Swift resamples natively via AVAudioConverter so the Python backend
    // no longer needs soundfile or librosa.
    private static let kTargetSampleRate: Double = 16_000

    private let pythonPath: String
    private let cliPath: String
    private let learnPath: String

    /// Injected by AppDelegate; routes transcription through the daemon with
    /// a CLI fallback. When nil, Dictation falls back to its own runCLI().
    var onTranscribe: ((URL, @escaping (String?) -> Void) -> Void)?

    /// Called on the main thread when capture state changes (drives the icon).
    var onState: ((VivoTypeState) -> Void)?

    /// Called on the main thread with a 0...1 input level while recording.
    var onLevel: ((Float) -> Void)?

    /// Called on the main thread when a clipboard correction is logged (count >= 1).
    var onCaptured: ((Int) -> Void)?

    /// Called on the main thread when injection was blocked (secure field, or
    /// Accessibility denied) and the text was placed on the clipboard instead.
    var onInjectBlocked: ((String) -> Void)?

    /// Called on the main thread when capture was cancelled because the input
    /// device changed mid-dictation and the captured audio was discarded.
    var onCaptureLost: ((String) -> Void)?

    /// Called on the main thread after text was successfully injected (typed or
    /// pasted). Used by first-run practice to detect a successful guided rep.
    var onInjected: ((String) -> Void)?

    /// Whether the "Pop" sound plays on a captured correction (Settings toggle).
    var soundEnabled = true

    // Correction-learning: remember the last insertion so we can detect when the
    // user copies a corrected version of it from the clipboard.
    private var lastInjected: String?
    private var lastInjectedAt = Date(timeIntervalSince1970: 0)
    private var lastSeenChangeCount = NSPasteboard.general.changeCount
    private var clipboardTimer: Timer?
    private var configObserver: NSObjectProtocol?

    // MARK: scratch-that history
    // Bounded stack of keyboard-injected spans ("scratch that" deletes the
    // newest). Only successful typeUnicode landings are recorded — clipboard
    // parks (AX denied / secure field) and browser pastes never become
    // deletable history, so a command can never fire Delete at text VivoType
    // didn't type itself. Depth 3 + 120 s TTL mirror the learning window.
    private struct ScratchRecord {
        let text: String
        /// Grapheme-cluster count — Cocoa text views delete one cluster per
        /// Backspace press, so this (not utf16.count) is the correct budget:
        /// Devanagari combining marks are several UTF-16 units but ONE cluster,
        /// and a unit-based count would over-delete into the user's own words.
        let deletePresses: Int
        let bundleID: String?
        let date: Date
    }
    private var scratchHistory: [ScratchRecord] = []  // newest last
    private static let scratchHistoryLimit = 3
    private static let scratchMaxPresses = 2_000
    private static let scratchChunkSize = 256

    // Focused apps where synthetic keystrokes are unreliable -> use paste.
    private static let browserBundleIDs: Set<String> = [
        "com.apple.Safari", "com.google.Chrome", "com.google.Chrome.canary",
        "org.mozilla.firefox", "com.microsoft.edgemac", "com.brave.Browser",
        "company.thebrowser.Browser", "com.operasoftware.Opera", "com.vivaldi.Vivaldi",
    ]

    init(pythonPath: String, cliPath: String, learnPath: String) {
        self.pythonPath = pythonPath
        self.cliPath = cliPath
        self.learnPath = learnPath
        // Recover gracefully if the input device changes mid-capture (e.g. the
        // user unplugs AirPods or a USB mic while holding the hotkey).
        configObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in self?.handleConfigChange() }
    }

    deinit {
        if let token = configObserver { NotificationCenter.default.removeObserver(token) }
    }

    // MARK: recording

    func startRecording() {
        guard !isRecording, !isBusy else { return }

        let input = engine.inputNode
        let hwFormat = input.outputFormat(forBus: 0)
        // No usable input device (none attached, or a USB mic just unplugged):
        // the format is 0 Hz / 0 channels and installTap would raise an
        // Objective-C exception Swift cannot catch — the app would crash.
        guard hwFormat.sampleRate > 0, hwFormat.channelCount > 0 else {
            warn("VivoType: no usable microphone (input format \(hwFormat)).")
            onCaptureLost?("No microphone found — check your input device")
            onState?(.error)
            return
        }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("vivotype-\(UUID().uuidString).wav")

        // Build a converter: hardware format → mono 16 kHz Float32.
        // The daemon's audioio.py reads the resulting 16-bit PCM WAV with stdlib
        // wave; no soundfile/librosa needed. Falls back to writing at hw format
        // (daemon still handles it via linear-resample in audioio).
        let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Dictation.kTargetSampleRate,
            channels: 1,
            interleaved: false
        )
        let converter: AVAudioConverter? = targetFormat.flatMap {
            AVAudioConverter(from: hwFormat, to: $0)
        }
        audioConverter = converter

        let fileSettings: [String: Any]
        if converter != nil {
            fileSettings = [
                AVFormatIDKey:            kAudioFormatLinearPCM,
                AVSampleRateKey:          Dictation.kTargetSampleRate,
                AVNumberOfChannelsKey:    UInt32(1),
                AVLinearPCMBitDepthKey:   16,
                AVLinearPCMIsBigEndianKey: false,
                AVLinearPCMIsFloatKey:    false,
            ]
        } else {
            fileSettings = hwFormat.settings  // fallback: Python will resample
        }

        do {
            audioFile = try AVAudioFile(forWriting: url, settings: fileSettings)
        } catch {
            warn("VivoType: could not open temp audio file: \(error)")
            onState?(.error)
            return
        }
        tempURL = url

        let ratio = Dictation.kTargetSampleRate / hwFormat.sampleRate

        input.installTap(onBus: 0, bufferSize: 4096, format: hwFormat) { [weak self] buffer, _ in
            guard let self = self else { return }
            if let conv = self.audioConverter, let tgt = targetFormat {
                let outCount = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 1)
                if let outBuf = AVAudioPCMBuffer(pcmFormat: tgt, frameCapacity: outCount) {
                    var provided = false
                    _ = conv.convert(to: outBuf, error: nil) { _, status in
                        if provided { status.pointee = .noDataNow; return nil }
                        provided = true; status.pointee = .haveData; return buffer
                    }
                    try? self.audioFile?.write(from: outBuf)
                }
            } else {
                try? self.audioFile?.write(from: buffer)
            }
            self.reportLevel(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
            isRecording = true
            onState?(.recording)
        } catch {
            input.removeTap(onBus: 0)
            audioFile = nil
            audioConverter = nil
            warn("VivoType: could not start audio engine: \(error)")
            onState?(.error)
        }
    }

    private func reportLevel(_ buffer: AVAudioPCMBuffer) {
        guard let channel = buffer.floatChannelData?[0] else { return }
        let count = Int(buffer.frameLength)
        if count == 0 { return }
        var sum: Float = 0
        for i in 0..<count {
            let sample = channel[i]
            sum += sample * sample
        }
        let rms = (sum / Float(count)).squareRoot()
        let db = 20 * log10(max(rms, Float(1e-7)))
        let level = min(Float(1), max(Float(0), (db + 50) / 50))  // ~-50 dBFS..0 -> 0..1
        DispatchQueue.main.async { self.onLevel?(level) }
    }

    func stopAndTranscribe() {
        guard isRecording else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        isRecording = false
        audioConverter = nil
        let framesWritten = audioFile?.length ?? 0
        audioFile = nil  // closes the file
        guard let url = tempURL else { return }
        tempURL = nil
        guard framesWritten > 0 else {
            try? FileManager.default.removeItem(at: url)
            warn("VivoType: empty recording (no audio frames) — nothing to transcribe.")
            onState?(.idle)
            return
        }
        isBusy = true
        onState?(.transcribing)

        // finish is always called on the main thread (daemon client guarantees this).
        let finish: (String?) -> Void = { [weak self] text in
            guard let self = self else { return }
            try? FileManager.default.removeItem(at: url)
            if let text = text, !text.isEmpty {
                self.inject(text)
            } else {
                warn("VivoType: empty transcript (nothing inserted).")
            }
            self.isBusy = false
            self.onState?(.idle)
        }

        if let onTranscribe = onTranscribe {
            onTranscribe(url, finish)
        } else {
            // Built-in fallback: one-shot CLI (used when no daemon is wired up).
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                guard let self = self else { return }
                let text = self.runCLI(on: url)
                DispatchQueue.main.async { finish(text) }
            }
        }
    }

    /// Stop and discard the current capture without transcribing (used when the
    /// audio configuration changes out from under us).
    private func cancelRecording() {
        guard isRecording else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        isRecording = false
        audioConverter = nil
        audioFile = nil
        if let url = tempURL {
            try? FileManager.default.removeItem(at: url)
            tempURL = nil
        }
    }

    /// Whether a capture is currently running (e.g. hands-free needs to confirm
    /// startRecording() actually began and wasn't blocked by an in-flight job).
    var isCapturing: Bool { isRecording }

    /// Public: stop and discard the current capture without transcribing — used by
    /// hands-free cancel and to drop a too-short tap. Safe to call when idle.
    func cancel() {
        guard isRecording else { return }
        cancelRecording()
        onState?(.idle)
    }

    private func handleConfigChange() {
        guard isRecording else { return }
        warn("VivoType: audio configuration changed — recording cancelled.")
        cancelRecording()
        onState?(.error)
        // Not gated on toastEnabled (same reasoning as onInjectBlocked): losing
        // spoken words silently is precisely the failure this surfaces.
        onCaptureLost?("Mic changed — that dictation was lost. Try again")
    }

    // MARK: voice-editing commands

    /// Best-effort read of the focused element via Accessibility. Returns nil
    /// when the app doesn't expose AX (canvas tools, some terminals) —
    /// executeCommand then proceeds on its remaining guards; that residual is
    /// stated inline at guard 7 below.
    private func focusedElement() -> AXUIElement? {
        let systemWide = AXUIElementCreateSystemWide()
        var focused: CFTypeRef?
        let focusError = AXUIElementCopyAttributeValue(systemWide,
                                                       kAXFocusedUIElementAttribute as CFString,
                                                       &focused)
        guard focusError == .success, focused != nil else { return nil }
        // CF types don't support conditional downcasts; the API guarantees
        // kAXFocusedUIElementAttribute yields an AXUIElement.
        return focused as! AXUIElement
    }

    /// Best-effort read of the focused element's text value, or nil when the
    /// app doesn't expose it.
    private func focusedElementValue() -> String? {
        guard let element = focusedElement() else { return nil }
        var value: CFTypeRef?
        let valueError = AXUIElementCopyAttributeValue(element,
                                                       kAXValueAttribute as CFString,
                                                       &value)
        guard valueError == .success else { return nil }
        return value as? String
    }

    /// Executes a structural voice-edit command signaled by the daemon
    /// (currently only "scratch_that"). Destructive by nature, so every guard
    /// failure shows a toast and sends ZERO key events — it fails toward
    /// doing nothing.
    func executeCommand(_ name: String) {
        guard name == "scratch_that" else { return }

        func refuse(_ reason: String) {
            warn("VivoType: scratch refused — \(reason)")
            onInjectBlocked?(reason)
        }

        // 1. History exists and is fresh (TTL mirrors correction learning).
        guard let record = scratchHistory.last else {
            return refuse("Nothing recent to delete")
        }
        guard Date().timeIntervalSince(record.date) < 120 else {
            _ = scratchHistory.popLast()
            return refuse("That dictation is too old to delete")
        }
        // 2. A password field must never receive Delete keyevents even though
        //    the original dictation landed somewhere else entirely.
        guard !IsSecureEventInputEnabled() else {
            return refuse("Can't delete inside a password field")
        }
        // 3. Same app as when the text landed (app-switch race). Strict
        //    compare: a nil bundle on either side refuses.
        guard let frontBundle = NSWorkspace.shared.frontmostApplication?.bundleIdentifier,
              frontBundle == record.bundleID
        else {
            return refuse("Switched apps since dictating — not deleting")
        }
        // 4. v1 scope: browsers excluded — focus outside an editable field can
        //    turn synthetic Delete into page navigation in embedded webviews.
        guard !Dictation.browserBundleIDs.contains(frontBundle) else {
            return refuse("Editing commands aren't available in this app yet.")
        }
        // 5. Deleting needs Accessibility just like typing did.
        guard AXIsProcessTrusted() else {
            return refuse("Accessibility needed — can't delete")
        }
        // 6. Bound the blast radius of a hallucinated phrase.
        guard record.deletePresses <= Self.scratchMaxPresses else {
            return refuse("That dictation is too long to delete")
        }
        // 7. Best-effort content check: if the app exposes its text via
        //    Accessibility, our span must still be present where we left it.
        //    Documented residuals: (a) apps WITHOUT AX text attributes
        //    (canvas tools, some terminals) proceed on guards 1-6; (b) this
        //    checks presence anywhere in the element, not caret position.
        //    We also capture the focused ELEMENT's identity so the mid-burst
        //    re-checks below can catch a same-app field switch, which a
        //    bundle check alone cannot see.
        let startElement = focusedElement()
        if let value = focusedElementValue(), !value.contains(record.text) {
            return refuse("Couldn't verify the dictated text — not deleting")
        }
        if let value = focusedElementValue(), !value.contains(record.text) {
            return refuse("Couldn't verify the dictated text — not deleting")
        }

        // Consume the record BEFORE any keyevents: a ⌘C during the burst must
        // not teach the just-deleted span as a "correction", and successive
        // scratches target the next-older entry. This span leaves the
        // correction-learning window too.
        scratchHistory.removeLast()
        if lastInjected == record.text {
            lastInjected = nil
            lastInjectedAt = Date(timeIntervalSince1970: 0)
        }

        // Chunked deletion with focus re-verified between chunks: ⌘-tabbing
        // mid-burst stops the stream after at most one chunk lands elsewhere,
        // and so does clicking into a DIFFERENT field of the same app (the
        // focused element's identity changes — a bundle check can't see it).
        let source = CGEventSource(stateID: .combinedSessionState)
        var sent = 0
        while sent < record.deletePresses {
            let currentElement = focusedElement()
            guard NSWorkspace.shared.frontmostApplication?.bundleIdentifier == frontBundle,
                  !IsSecureEventInputEnabled(),
                  startElement == nil || currentElement == nil
                      || CFEqual(startElement, currentElement)
            else { return refuse("Focus moved mid-delete — stopped early") }
            let chunkEnd = min(sent + Self.scratchChunkSize, record.deletePresses)
            while sent < chunkEnd {
                guard let down = CGEvent(keyboardEventSource: source,
                                         virtualKey: CGKeyCode(kVK_Delete), keyDown: true),
                      let up = CGEvent(keyboardEventSource: source,
                                       virtualKey: CGKeyCode(kVK_Delete), keyDown: false)
                else { return refuse("Could not synthesize the delete keys") }
                down.post(tap: .cghidEventTap)
                up.post(tap: .cghidEventTap)
                sent += 1
            }
        }
        warn("VivoType: scratched \(record.deletePresses) characters.")
    }

    // MARK: core/ CLI bridge

    private func runCLI(on url: URL) -> String? {
        // Generous timeout: the very first run may download the model.
        let result = runProcess(pythonPath, [cliPath, url.path], timeout: 300)
        if result.status != 0 {
            let detail = result.stderr.isEmpty ? "(no detail)" : result.stderr
            warn("VivoType: CLI exited \(result.status): \(detail)")
            return nil
        }
        return cliTranscript(result.stdout)
    }

    // MARK: text injection

    private func inject(_ text: String) {
        lastInjected = text
        lastInjectedAt = Date()

        // Checked FIRST, before Accessibility: a secure field must get the
        // concealed, self-clearing parking below even when Accessibility is
        // off, and must never stay behind as `lastInjected` text.
        // A password field (or any app holding secure event input) swallows
        // synthetic keystrokes AND ⌘V silently — the text would just vanish.
        // Park it on the clipboard instead so nothing is lost, and say so.
        if IsSecureEventInputEnabled() {
            // Secure input strongly implies a password: it must never enter the
            // correction-learning pipeline (argv of a child process + a
            // plaintext log on disk), so forget it as "last injected" text.
            lastInjected = nil
            lastInjectedAt = Date(timeIntervalSince1970: 0)
            let pasteboard = NSPasteboard.general
            pasteboard.clearContents()
            // Concealed-type marker: well-behaved clipboard managers skip it.
            pasteboard.setString("", forType: NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType"))
            pasteboard.setString(text, forType: .string)
            lastSeenChangeCount = pasteboard.changeCount  // our write, not a correction
            let parkedCount = pasteboard.changeCount
            // Don't leave likely-secret text on the clipboard forever: clear it
            // after 60 s unless the user has copied something else since.
            DispatchQueue.main.asyncAfter(deadline: .now() + 60) { [weak self] in
                let pb = NSPasteboard.general
                guard pb.changeCount == parkedCount else { return }
                pb.clearContents()
                self?.lastSeenChangeCount = pb.changeCount
            }
            warn("VivoType: secure input is active — text copied to the clipboard instead.")
            onInjectBlocked?("Secure field — text copied, press ⌘V to paste")
            return
        }

        // Typing and synthetic ⌘V both need Accessibility. Without it, park the
        // transcript as a normal pasteboard string so the user can ⌘V themselves —
        // no fragile workarounds. Toast (via onInjectBlocked) is the only UX;
        // do not set .error (finish() would immediately overwrite it with .idle).
        guard AXIsProcessTrusted() else {
            let pasteboard = NSPasteboard.general
            pasteboard.clearContents()
            pasteboard.setString(text, forType: .string)
            lastSeenChangeCount = pasteboard.changeCount  // our write, not a correction
            warn("VivoType: Accessibility missing — text copied to the clipboard.")
            onInjectBlocked?("Accessibility needed — text copied, press ⌘V to paste")
            return
        }

        let frontBundle = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        if Dictation.browserBundleIDs.contains(frontBundle ?? "") {
            pasteViaClipboard(text)
        } else if typeUnicode(text), !text.isEmpty {
            // Only a fully typed landing becomes scratchable history.
            scratchHistory.append(ScratchRecord(text: text,
                                                deletePresses: text.count,
                                                bundleID: frontBundle,
                                                date: Date()))
            if scratchHistory.count > Self.scratchHistoryLimit {
                scratchHistory.removeFirst()
            }
        }
        onInjected?(text)
    }

    /// Types text via synthetic keyboard events. Returns false if event
    /// creation failed mid-way (partial landing — callers must not record it
    /// as scratchable history). Delete events elsewhere deliberately do NOT
    /// reuse this pattern: they carry no unicode payload.
    @discardableResult
    private func typeUnicode(_ text: String) -> Bool {
        let source = CGEventSource(stateID: .combinedSessionState)
        let chunkSize = 16  // a single long unicode event is dropped/garbled by some apps
        // Chunks end on Character (grapheme) boundaries: a fixed 16-unit cut
        // split emoji surrogate pairs and combining sequences across two
        // events, which apps render as garbage. One Character longer than the
        // limit (rare ZWJ emoji) is sent alone rather than split.
        var chunks: [[UInt16]] = []
        var current: [UInt16] = []
        for character in text {
            let units = Array(String(character).utf16)
            if !current.isEmpty && current.count + units.count > chunkSize {
                chunks.append(current)
                current = []
            }
            current.append(contentsOf: units)
        }
        if !current.isEmpty { chunks.append(current) }
        for chunk in chunks {
            guard let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true),
                  let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false)
            else { return false }
            chunk.withUnsafeBufferPointer { ptr in
                down.keyboardSetUnicodeString(stringLength: ptr.count, unicodeString: ptr.baseAddress)
                up.keyboardSetUnicodeString(stringLength: ptr.count, unicodeString: ptr.baseAddress)
            }
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
        }
        return true
    }

    private func pasteViaClipboard(_ text: String) {
        let pasteboard = NSPasteboard.general
        // Snapshot every item and type (images, files, rich text) so they come
        // back after the paste; falls back to the plain string when a faithful
        // copy isn't possible (see snapshotPasteboard).
        let previousItems = snapshotPasteboard(pasteboard)
        let previousString = previousItems == nil ? pasteboard.string(forType: .string) : nil
        pasteboard.clearContents()
        // Transient marker: well-behaved clipboard managers skip this write.
        pasteboard.setString("", forType: NSPasteboard.PasteboardType("org.nspasteboard.TransientType"))
        pasteboard.setString(text, forType: .string)
        // Our own clipboard write — don't mistake it for a user correction.
        lastSeenChangeCount = pasteboard.changeCount
        let targetCount = pasteboard.changeCount

        let source = CGEventSource(stateID: .combinedSessionState)
        let vKey: CGKeyCode = 9  // 'v'
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: vKey, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: vKey, keyDown: false)
        else { return }
        down.flags = .maskCommand
        up.flags = .maskCommand
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)

        // Restore the user's previous clipboard once the paste has landed.
        // (Known residual, unchanged: a target slower than 0.4 s to read the
        // paste would get the restored clipboard instead.)
        if previousItems?.isEmpty ?? (previousString == nil) { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            let pb = NSPasteboard.general
            guard pb.changeCount == targetCount else { return }  // user copied something new; don't clobber it
            pb.clearContents()
            if let items = previousItems {
                pb.writeObjects(items)
            } else if let string = previousString {
                pb.setString(string, forType: .string)
            }
            self?.lastSeenChangeCount = pb.changeCount  // our restore — ignore it too
        }
    }

    /// Past this total size the snapshot is skipped: every representation is
    /// read synchronously on the main thread, and a huge multi-format image
    /// would stall injection.
    private static let clipboardSnapshotLimit = 16 * 1_048_576

    /// A faithful copy of every clipboard item and type, or nil when one can't
    /// be made cheaply: a representation that won't materialise (a lazy
    /// provider that fails, a file promise) or too much data. The caller then
    /// keeps only the plain string, as before — never a half-restored clipboard.
    private func snapshotPasteboard(_ pasteboard: NSPasteboard) -> [NSPasteboardItem]? {
        var copies: [NSPasteboardItem] = []
        var total = 0
        for item in pasteboard.pasteboardItems ?? [] {
            let copy = NSPasteboardItem()
            for type in item.types {
                if type.rawValue.lowercased().contains("promise") { return nil }
                guard let data = item.data(forType: type) else { return nil }
                total += data.count
                if total > Self.clipboardSnapshotLimit { return nil }
                copy.setData(data, forType: type)
            }
            copies.append(copy)
        }
        return copies
    }

    // MARK: correction learning

    func startClipboardMonitor() {
        lastSeenChangeCount = NSPasteboard.general.changeCount
        clipboardTimer = Timer.scheduledTimer(withTimeInterval: 0.6, repeats: true) { [weak self] _ in
            self?.checkClipboard()
        }
    }

    private func checkClipboard() {
        let pasteboard = NSPasteboard.general
        let changeCount = pasteboard.changeCount
        if changeCount == lastSeenChangeCount { return }
        lastSeenChangeCount = changeCount

        guard let clip = pasteboard.string(forType: .string),
              let original = lastInjected,
              Date().timeIntervalSince(lastInjectedAt) < 120
        else { return }
        if clip == original { return }                  // copied verbatim, no edit
        if similarity(original, clip) < 0.6 { return }  // unrelated copy

        lastInjected = nil  // capture at most one correction per insertion
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self = self else { return }
            let count = self.runLearn(original: original, corrected: clip)
            if count > 0 {
                DispatchQueue.main.async {
                    if self.soundEnabled { NSSound(named: "Pop")?.play() }
                    self.onCaptured?(count)
                }
            }
        }
    }

    /// The correction offer found this dictation in its text field and now
    /// owns learning from it: forget it here so the clipboard path can't log
    /// the same utterance too. False when the clipboard path already claimed
    /// it, or a newer dictation replaced it.
    func claimLastInjection(_ text: String) -> Bool {
        guard lastInjected == text else { return false }
        lastInjected = nil
        lastInjectedAt = Date(timeIntervalSince1970: 0)
        return true
    }

    // Word-overlap (Jaccard) similarity — Python does the precise diff.
    private func similarity(_ a: String, _ b: String) -> Double {
        let wa = Set(a.lowercased().split { !$0.isLetter && !$0.isNumber })
        let wb = Set(b.lowercased().split { !$0.isLetter && !$0.isNumber })
        if wa.isEmpty || wb.isEmpty { return 0 }
        return Double(wa.intersection(wb).count) / Double(wa.union(wb).count)
    }

    private func runLearn(original: String, corrected: String) -> Int {
        guard let payload = try? JSONSerialization.data(
            withJSONObject: ["original": original, "corrected": corrected]) else { return 0 }
        let result = runProcess(pythonPath, [learnPath], timeout: 60, input: payload)
        return Int(result.stdout.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
    }
}
