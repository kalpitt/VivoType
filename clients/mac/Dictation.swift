// VivoType — dictation controller: mic capture (resampled natively to 16 kHz mono
// 16-bit PCM via AVAudioConverter, so the Python backend needs no audio libs),
// text injection into the focused app, and clipboard-based correction learning.

import Foundation
import AVFoundation
import AppKit
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

    /// Whether the "Pop" sound plays on a captured correction (Settings toggle).
    var soundEnabled = true

    // Correction-learning: remember the last insertion so we can detect when the
    // user copies a corrected version of it from the clipboard.
    private var lastInjected: String?
    private var lastInjectedAt = Date(timeIntervalSince1970: 0)
    private var lastSeenChangeCount = NSPasteboard.general.changeCount
    private var clipboardTimer: Timer?
    private var configObserver: NSObjectProtocol?

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
        audioFile = nil  // closes the file
        guard let url = tempURL else { return }
        tempURL = nil
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
        return result.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: text injection

    private func inject(_ text: String) {
        lastInjected = text
        lastInjectedAt = Date()
        
        // Both injection methods require Accessibility permissions.
        guard AXIsProcessTrusted() else {
            warn("VivoType: Cannot inject text. Accessibility permissions are missing.")
            onState?(.error)
            return
        }

        let frontBundle = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
        if Dictation.browserBundleIDs.contains(frontBundle) {
            pasteViaClipboard(text)
        } else {
            typeUnicode(text)
        }
    }

    private func typeUnicode(_ text: String) {
        let source = CGEventSource(stateID: .combinedSessionState)
        let scalars = Array(text.utf16)
        let chunkSize = 16  // a single long unicode event is dropped/garbled by some apps
        var index = 0
        while index < scalars.count {
            let chunk = Array(scalars[index..<min(index + chunkSize, scalars.count)])
            guard let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true),
                  let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false)
            else { return }
            chunk.withUnsafeBufferPointer { ptr in
                down.keyboardSetUnicodeString(stringLength: ptr.count, unicodeString: ptr.baseAddress)
                up.keyboardSetUnicodeString(stringLength: ptr.count, unicodeString: ptr.baseAddress)
            }
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
            index += chunkSize
        }
    }

    private func pasteViaClipboard(_ text: String) {
        let pasteboard = NSPasteboard.general
        let previous = pasteboard.string(forType: .string)  // to restore afterward
        pasteboard.clearContents()
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
        guard let previous = previous else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            let pb = NSPasteboard.general
            guard pb.changeCount == targetCount else { return }  // user copied something new; don't clobber it
            pb.clearContents()
            pb.setString(previous, forType: .string)
            self?.lastSeenChangeCount = pb.changeCount  // our restore — ignore it too
        }
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

    // Word-overlap (Jaccard) similarity — Python does the precise diff.
    private func similarity(_ a: String, _ b: String) -> Double {
        let wa = Set(a.lowercased().split { !$0.isLetter && !$0.isNumber })
        let wb = Set(b.lowercased().split { !$0.isLetter && !$0.isNumber })
        if wa.isEmpty || wb.isEmpty { return 0 }
        return Double(wa.intersection(wb).count) / Double(wa.union(wb).count)
    }

    private func runLearn(original: String, corrected: String) -> Int {
        let result = runProcess(pythonPath, [learnPath, "--original", original, "--corrected", corrected],
                                timeout: 60)
        return Int(result.stdout.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
    }
}
