// VivoType — persistent Python daemon client (ADR-0002). Spawns `python -m
// core.daemon` once, keeps the Whisper model warm, and exchanges NDJSON over
// stdin/stdout. On crash, callers receive nil and fall back to the one-shot CLI.

import Foundation
import Darwin  // kill()/SIGKILL for the hang-recovery watchdog

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

    /// Consecutive per-request failures (timeouts OR per-request errors) with
    /// no SUCCESSFUL transcription in between — the hang-detection signal
    /// (distinct from a hard crash). A daemon that stays alive but replies
    /// with nothing but errors is just as broken as one that never replies,
    /// so both count. Reset by any genuine model answer: an actual `text`
    /// reply or a structural "scratch_that" reply (both prove the daemon is
    /// healthy); an error reply does not.
    private var consecutiveFailures = 0
    /// How many times this client has force-restarted a *hung* (not crashed)
    /// daemon, for its ENTIRE lifetime — capped independently of
    /// didAutoRestart so a hang and a crash can't consume each other's
    /// budget. Deliberately never reset by a success: the point is to bound
    /// total restarts even against a daemon that hangs intermittently
    /// (hang, recover, hang, recover...), not just one that hangs forever.
    private var hangRestartCount = 0
    /// True between restartAfterHang()'s kill and the resulting handleEOF —
    /// tells handleEOF this death was deliberate so it respawns without
    /// consuming didAutoRestart (the crash-restart budget). Also used to
    /// ignore any further status messages from the process being killed.
    private var expectingHangRestart = false
    /// True after hang-budget exhaustion: the next EOF must not crash-respawn
    /// a daemon we've already given up on (CLI fallback stays active until
    /// the user picks "Restart speech engine").
    private var abandonProcess = false
    /// 2 consecutive failures (not 1 — a single one is ambiguous and a
    /// respawn costs a 2-4s model reload) means the daemon is alive but wedged.
    private static let hangTimeoutThreshold = 2
    /// Bounded so a systemically-hanging model/daemon can't restart forever;
    /// after this, the CLI fallback stays active until a manual "Restart
    /// speech engine".
    private static let maxHangRestarts = 3
    /// Grace period between a SIGTERM and the SIGKILL backstop — shared by
    /// the ordinary shutdown() path and the hang-recovery kill sequence.
    private static let killGracePeriod: TimeInterval = 2

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

    /// Called on the **main thread** when the daemon signals a structural
    /// voice-editing command (currently only "scratch_that" — see
    /// core/commands.py). Delivered AFTER that request's completion callback,
    /// so Dictation's normal empty-text cleanup runs first. The reply also
    /// resets the hang counters: a structural response is a healthy model
    /// answer, not a daemon failure.
    var onCommand: ((String) -> Void)?

    // MARK: lifecycle

    func start(pythonPath: String, repoRoot: String) {
        self.pythonPath = pythonPath
        self.repoRoot = repoRoot
        spawn()
    }

    /// Launch the daemon process. Called from start(), and again from
    /// handleEOF() — once per crash (didAutoRestart, one-shot) and up to
    /// maxHangRestarts times per hang-recovery cycle (independently budgeted).
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
            // Grace for a clean JSON shutdown, then the same SIGTERM→SIGKILL
            // backstop hang recovery uses — a wedged MLX call ignores SIGTERM.
            let oldProc = self.process
            self.daemonQueue.asyncAfter(deadline: .now() + Self.killGracePeriod) {
                Self.scheduleForceKill(oldProc)
            }
        }
    }

    /// SIGTERM now; SIGKILL after killGracePeriod if still alive.
    /// `oldProc` is captured independently of any DaemonClient so quit/restart
    /// during the grace window cannot leave an orphan wedged Python process.
    private static func scheduleForceKill(_ oldProc: Process?) {
        oldProc?.terminate()
        let grace = DaemonClient.killGracePeriod
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + grace) {
            guard oldProc?.isRunning == true else { return }
            kill(oldProc!.processIdentifier, SIGKILL)
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
    /// `profile` selects per-app post-processing rules (core/postprocess_config.json
    /// "profiles"); unknown names degrade to default rules daemon-side.
    func transcribe(wav: URL, initialPrompt: String, profile: String = "default",
                    completion: @escaping (String?) -> Void) {
        daemonQueue.async { [weak self] in
            guard let self = self, self.isReady else {
                DispatchQueue.main.async { completion(nil) }
                return
            }
            let id = self.nextId; self.nextId += 1
            self.pendingCallbacks[id] = completion
            self.sendJSON(["id": id, "wav": wav.path,
                           "initial_prompt": initialPrompt, "raw": false,
                           "profile": profile])
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
                self.noteFailure()
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
            // Once we've decided to kill this process (restartAfterHang set
            // expectingHangRestart), ignore anything further it says — it may
            // still emit a straggling status line before the SIGTERM/SIGKILL
            // actually lands, and accepting it (e.g. a stale "ready") would
            // flip isReady true for a process that's already being torn down.
            // The real status arrives after handleEOF respawns a fresh one.
            guard !expectingHangRestart else { return }
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
            if obj["command"] as? String == "scratch_that" {
                // Structural voice-edit command (exact match only — anything
                // else falls through to normal text handling). Empty text via
                // cb runs Dictation's standard finish/cleanup; the command is
                // then delivered for execution. removeValue above is the
                // single ticket, so neither path can run twice.
                consecutiveFailures = 0
                let commandCallback = onCommand
                DispatchQueue.main.async {
                    cb("")
                    commandCallback?("scratch_that")
                }
            } else if let text = obj["text"] as? String {
                // Only an actual transcription proves the daemon is healthy —
                // an error reply does not (see the else branch: a daemon that
                // stays alive but errors on every request is still failing).
                consecutiveFailures = 0
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
                // A daemon that's alive and replies promptly but always with
                // an error (e.g. a corrupted model) is just as broken as one
                // that never replies — count it the same way a timeout is.
                noteFailure()
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

        // A deliberate hang-kill (restartAfterHang) drives its own respawn,
        // separate from the crash-restart budget below — a hang and a crash
        // are different failure signatures and shouldn't consume each other's
        // one-shot guard.
        if expectingHangRestart {
            expectingHangRestart = false
            // If shutdown() was requested (quit / restartBackend) while the
            // hang-kill's SIGTERM-to-SIGKILL window was still in flight, honor
            // it — don't spawn a fresh daemon during/after intentional exit.
            guard !didShutdown else { return }
            warn("VivoType: hung daemon respawned (attempt \(hangRestartCount)/\(Self.maxHangRestarts)).")
            respawnFreshDaemon(statusCallback: cb)
            return
        }

        // Hang budget exhausted: we already force-killed and told the UI —
        // do not crash-respawn a daemon we've abandoned.
        if abandonProcess {
            abandonProcess = false
            return
        }

        // Unexpected death (not a requested shutdown): respawn ONCE so the
        // model reloads and dictation stays warm; the traceback is already in
        // daemon.log. A second death gives up — the CLI fallback keeps
        // dictation functional, just cold — so a crash-looping daemon can't
        // spin forever.
        if !didShutdown && !didAutoRestart {
            didAutoRestart = true
            // The new process shares nothing with the one that just crashed —
            // stale hang-detection state from its lifetime must not carry over
            // and prematurely force-kill an otherwise-healthy fresh daemon.
            consecutiveFailures = 0
            warn("VivoType: daemon died unexpectedly — restarting once (see daemon.log)")
            respawnFreshDaemon(statusCallback: cb)
            return
        }
        DispatchQueue.main.async { cb?(.error("daemon terminated")) }
    }

    /// Shared final step for both respawn paths above: clear the read
    /// buffer, tell the UI a fresh model load is starting, then spawn().
    private func respawnFreshDaemon(statusCallback cb: ((DaemonStatus) -> Void)?) {
        lineBuffer.removeAll()
        DispatchQueue.main.async { cb?(.loading(nil)) }
        spawn()
    }

    /// Register one failed request (timeout or per-request error). Two in a
    /// row with no success in between means the process itself is wedged —
    /// distinct from a hard crash, which handleEOF detects separately.
    private func noteFailure() {
        // Called on daemonQueue.
        consecutiveFailures += 1
        if consecutiveFailures >= Self.hangTimeoutThreshold
            && !didShutdown && !expectingHangRestart {
            restartAfterHang()
        }
    }

    /// The daemon process is alive but has stopped answering requests (two
    /// consecutive failures). Force-kill it and let the resulting stdout EOF
    /// drive the respawn via handleEOF — reusing that single spawn point
    /// avoids the stale-EOF race restartBackend() (App.swift) already guards
    /// against, since the old client's callbacks stay wired throughout.
    private func restartAfterHang() {
        // Called on daemonQueue.
        guard hangRestartCount < Self.maxHangRestarts else {
            // Giving up doesn't mean the daemon became healthy — it's still
            // wedged. Kill it so GPU/RAM are released; abandonProcess stops
            // handleEOF from crash-respawning. CLI fallback stays active.
            warn("VivoType: daemon hung \(hangRestartCount)x — giving up auto-restart; CLI fallback stays active.")
            isReady = false
            abandonProcess = true
            let oldProc = process
            Self.scheduleForceKill(oldProc)
            let cb = onStatusChange
            DispatchQueue.main.async { cb?(.error("daemon repeatedly hung — restart manually")) }
            return
        }
        hangRestartCount += 1
        consecutiveFailures = 0
        expectingHangRestart = true
        isReady = false  // new requests short-circuit to CLI until the respawn is ready
        warn("VivoType: daemon appears hung — force-restarting (attempt \(hangRestartCount)/\(Self.maxHangRestarts)).")
        Self.scheduleForceKill(process)
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
