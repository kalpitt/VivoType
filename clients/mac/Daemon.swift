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
    private var didAutoRestart = false  // one respawn per client lifetime (crash-loop guard)
    private var pythonPath = ""
    private var repoRoot = ""

    private let daemonQueue = DispatchQueue(label: "com.vivotype.daemon", qos: .userInitiated)

    /// A hung transcription (MLX stall, pathological input) must not leave the
    /// app in "Transcribing…" forever: after this long the pending callback is
    /// failed over to the CLI fallback and a late daemon reply is ignored.
    private static let requestTimeout: TimeInterval = 30

    /// Called on the **main thread** whenever the daemon emits a status change.
    var onStatusChange: ((DaemonStatus) -> Void)?

    /// Called on the **main thread** when a transcription request fails (the
    /// daemon replied `{"id":N,"error":...}` or the request timed out). The
    /// request's completion still receives nil afterwards, so the CLI fallback
    /// runs as before — this hook exists so failures are visible, not silent.
    var onTranscribeError: ((String) -> Void)?

    // MARK: lifecycle

    func start(pythonPath: String, repoRoot: String) {
        self.pythonPath = pythonPath
        self.repoRoot = repoRoot
        spawn()
    }

    /// Launch the daemon process. Called from start() and again (at most once)
    /// from handleEOF when the daemon dies unexpectedly.
    private func spawn() {
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
            // Report failure so the app leaves the loading state and the CLI
            // fallback takes over — matters especially for a failed respawn.
            let cb = onStatusChange
            DispatchQueue.main.async { cb?(.error("daemon launch failed")) }
            return
        }
        process = proc
        inHandle = inPipe.fileHandleForWriting

        // Drain stderr into a log file so Python tracebacks survive a crash —
        // "daemon terminated" with no diagnostics is undebuggable in the field.
        let logHandle = Self.openDaemonLog()
        errPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                try? logHandle?.close()
                return
            }
            try? logHandle?.write(contentsOf: data)
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

    /// Open `Logs/daemon.log` for appending (created if missing), rotating it
    /// away first once it grows past ~1 MB. Appending — not truncating — keeps
    /// the traceback from a crash readable after the auto-restart spawns a
    /// fresh daemon.
    private static func openDaemonLog() -> FileHandle? {
        let url = vivotypeLogsURL().appendingPathComponent("daemon.log")
        let fm = FileManager.default
        if let size = (try? fm.attributesOfItem(atPath: url.path)[.size]) as? Int,
           size > 1_048_576 {
            try? fm.removeItem(at: url)
        }
        if !fm.fileExists(atPath: url.path) {
            fm.createFile(atPath: url.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: url) else { return nil }
        _ = try? handle.seekToEnd()
        return handle
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
            // Watchdog: if the daemon hasn't answered by then, fail this request
            // over to the CLI. A reply arriving later finds no pending callback
            // and is dropped harmlessly (handleMessage's removeValue).
            self.daemonQueue.asyncAfter(deadline: .now() + Self.requestTimeout) { [weak self] in
                guard let self = self,
                      let cb = self.pendingCallbacks.removeValue(forKey: id) else { return }
                warn("VivoType: daemon request \(id) timed out after \(Int(Self.requestTimeout))s")
                let ecb = self.onTranscribeError
                DispatchQueue.main.async {
                    ecb?("transcription timed out")
                    cb(nil)
                }
            }
        }
    }

    /// Ask the daemon to reload with a different model (emits loading/ready status).
    func reload(model: String) {
        daemonQueue.async { [weak self] in
            guard let self = self else { return }
            self.isReady = false
            guard self.inHandle != nil, self.process?.isRunning == true else {
                // No live process to answer (dead, mid-crash, or never
                // started) — waiting for a status reply here would wedge the
                // caller in "Loading model…" forever (F3). Fail immediately;
                // the CLI fallback keeps dictation usable in the meantime.
                let cb = self.onStatusChange
                DispatchQueue.main.async { cb?(.error("daemon not running")) }
                return
            }
            self.sendJSON(["cmd": "reload", "model": model])
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
            if let text = obj["text"] as? String {
                DispatchQueue.main.async { cb(text) }
            } else {
                // Per-request failure ({"id":N,"error":...}) — previously this
                // collapsed to nil and the message was lost.
                let message = obj["error"] as? String ?? "unknown daemon error"
                warn("VivoType: daemon transcription error: \(message)")
                let ecb = onTranscribeError
                DispatchQueue.main.async {
                    ecb?(message)
                    cb(nil)  // nil → caller's one-shot CLI fallback still runs
                }
            }
        }
    }

    private func handleEOF() {
        // Called on daemonQueue when the read loop terminates (clean exit or crash).
        inHandle = nil
        isReady = false
        let callbacks = pendingCallbacks
        pendingCallbacks.removeAll()
        let cb = onStatusChange
        // In-flight requests fail over to the one-shot CLI either way.
        DispatchQueue.main.async { for c in callbacks.values { c(nil) } }

        // Unexpected death (not a requested shutdown): respawn ONCE so the
        // model reloads and dictation stays warm; the traceback is already in
        // daemon.log. A second death gives up — the CLI fallback keeps
        // dictation functional, just cold — so a crash-looping daemon can't
        // spin forever.
        if !didShutdown && !didAutoRestart {
            didAutoRestart = true
            lineBuffer.removeAll()
            warn("VivoType: daemon died unexpectedly — restarting once (see daemon.log)")
            DispatchQueue.main.async { cb?(.loading(nil)) }
            spawn()
            return
        }
        DispatchQueue.main.async { cb?(.error("daemon terminated")) }
    }

    private func sendJSON(_ obj: [String: Any]) {
        guard let handle = inHandle,
              var data = try? JSONSerialization.data(withJSONObject: obj) else { return }
        data.append(0x0A)
        do {
            // `write(contentsOf:)` — unlike the classic `FileHandle.write(_:)` —
            // throws a catchable Swift error on a write failure (e.g. the
            // daemon just died and the pipe is broken) instead of raising an
            // uncatchable NSException that would crash the app.
            try handle.write(contentsOf: data)
        } catch {
            warn("VivoType: daemon pipe write failed: \(error)")
            // Stop using this handle; the stdout EOF handler (already in
            // flight or about to fire) drives the respawn/fallback from here.
            inHandle = nil
        }
    }
}
