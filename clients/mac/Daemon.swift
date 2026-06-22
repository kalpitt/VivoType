// VivoType — persistent Python daemon client (ADR-0002). Spawns `python -m
// core.daemon` once, keeps the Whisper model warm, and exchanges NDJSON over
// stdin/stdout. On crash, callers receive nil and fall back to the one-shot CLI.

import Foundation

// MARK: - Daemon client

/// Status updates emitted by the Python daemon during its lifecycle.
enum DaemonStatus {
    case loading(String?)  // nil = model-load in progress; String = model name being downloaded
    case ready(String)     // model is hot — daemon is accepting requests
    case error(String)     // daemon failed to start or exited unexpectedly
}

/// Manages the persistent `python -m core.daemon` process.
///
/// The app spawns it once at startup; it keeps the Whisper model warm in RAM
/// so dictations after the first are sub-second. On crash the callers receive
/// nil and the AppDelegate falls back to the one-shot CLI automatically.
final class DaemonClient {
    private var process: Process?
    private var inHandle: FileHandle?

    private var lineBuffer = Data()
    private var pendingCallbacks: [Int: (String?) -> Void] = [:]
    private var nextId = 0
    private var isReady = false
    private var didShutdown = false  // set/read on daemonQueue — makes shutdown() idempotent

    private let daemonQueue = DispatchQueue(label: "com.vivotype.daemon", qos: .userInitiated)

    /// Called on the **main thread** whenever the daemon emits a status change.
    var onStatusChange: ((DaemonStatus) -> Void)?

    // MARK: lifecycle

    func start(pythonPath: String, repoRoot: String) {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonPath)
        proc.arguments = ["-u", "-m", "core.daemon"]
        proc.currentDirectoryURL = URL(fileURLWithPath: repoRoot)
        proc.environment = ProcessInfo.processInfo.environment

        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
        proc.standardInput  = inPipe
        proc.standardOutput = outPipe
        proc.standardError  = errPipe

        do { try proc.run() } catch {
            warn("VivoType: daemon launch failed: \(error)")
            return
        }
        process = proc
        inHandle = inPipe.fileHandleForWriting

        // Drain stderr (discard) via readabilityHandler.
        errPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil }
        }

        // Read stdout via readabilityHandler — GCD dispatch-source-based I/O
        // that works correctly under NSApplication's run loop. A blocking
        // readData(ofLength:) loop on DispatchQueue.global does NOT deliver
        // pipe data while an AppKit run loop is active (macOS kernel/GCD
        // interaction), so readabilityHandler is required here.
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else {
                // EOF — daemon exited.
                handle.readabilityHandler = nil
                self?.daemonQueue.async { self?.handleEOF() }
                return
            }
            self?.daemonQueue.async {
                self?.lineBuffer.append(data)
                self?.processLines()
            }
        }
    }

    func shutdown() {
        daemonQueue.async { [weak self] in
            guard let self = self, !self.didShutdown else { return }
            self.didShutdown = true
            self.sendJSON(["cmd": "shutdown"])
            // Force-terminate after a grace period, serialized on daemonQueue so it
            // can't race the stdout read/EOF handlers (which also hop onto this queue).
            self.daemonQueue.asyncAfter(deadline: .now() + 2) { [weak self] in
                self?.process?.terminate()
            }
        }
    }

    // MARK: transcription

    /// Submit a WAV for transcription. `completion` is always called on the main thread.
    /// Returns nil immediately if the daemon is not yet ready (caller falls back to CLI).
    func transcribe(wav: URL, initialPrompt: String, completion: @escaping (String?) -> Void) {
        daemonQueue.async { [weak self] in
            guard let self = self, self.isReady else {
                DispatchQueue.main.async { completion(nil) }
                return
            }
            let id = self.nextId; self.nextId += 1
            self.pendingCallbacks[id] = completion
            self.sendJSON(["id": id, "wav": wav.path,
                           "initial_prompt": initialPrompt, "raw": false])
        }
    }

    /// Ask the daemon to reload with a different model (emits loading/ready status).
    func reload(model: String) {
        daemonQueue.async { [weak self] in
            self?.isReady = false
            self?.sendJSON(["cmd": "reload", "model": model])
        }
    }

    // MARK: private

    private func processLines() {
        // Called on daemonQueue.
        while let nlIdx = lineBuffer.firstIndex(of: 0x0A) {
            let lineSlice = lineBuffer[lineBuffer.startIndex..<nlIdx]
            lineBuffer.removeSubrange(lineBuffer.startIndex...nlIdx)
            guard !lineSlice.isEmpty else { continue }
            // Swift Data slices keep their original indices; JSONSerialization
            // (backed by NSData) requires startIndex == 0 or it silently returns
            // nil. Copy to a fresh Data object to guarantee a 0-based index.
            let lineData = Data(lineSlice)
            guard let obj = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any]
            else { continue }
            handleMessage(obj)
        }
    }

    private func handleMessage(_ obj: [String: Any]) {
        // Called on daemonQueue; UI callbacks are dispatched to main.
        if let statusStr = obj["status"] as? String {
            let status: DaemonStatus
            switch statusStr {
            case "loading":
                isReady = false
                status = .loading(nil)
            case "downloading":
                isReady = false
                status = .loading(obj["model"] as? String)
            case "ready":
                isReady = true
                status = .ready(obj["model"] as? String ?? "")
            default:  // "error"
                isReady = false
                status = .error(obj["error"] as? String ?? "unknown")
            }
            let cb = onStatusChange
            DispatchQueue.main.async { cb?(status) }

        } else if let id = obj["id"] as? Int,
                  let cb = pendingCallbacks.removeValue(forKey: id) {
            let text = obj["text"] as? String
            DispatchQueue.main.async { cb(text) }
        }
    }

    private func handleEOF() {
        // Called on daemonQueue when the read loop terminates (clean exit or crash).
        inHandle = nil
        isReady = false
        let callbacks = pendingCallbacks
        pendingCallbacks.removeAll()
        let cb = onStatusChange
        DispatchQueue.main.async {
            for c in callbacks.values { c(nil) }
            cb?(.error("daemon terminated"))
        }
    }

    private func sendJSON(_ obj: [String: Any]) {
        guard let handle = inHandle,
              var data = try? JSONSerialization.data(withJSONObject: obj) else { return }
        data.append(0x0A)
        handle.write(data)
    }
}
