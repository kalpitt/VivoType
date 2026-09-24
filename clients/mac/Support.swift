// VivoType — shared support: small process/IO helpers, the app state enum, and the
// path resolvers that keep immutable bundle code separate from mutable App
// Support state. No UI here; everything in this file is usable headlessly.

import Foundation
import Darwin  // kill()/SIGKILL for hang recovery and runProcess backstop

// MARK: - helpers

func warn(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

enum VivoTypeState {
    case idle, recording, transcribing, error, loading
}

/// Run a process, draining stdout+stderr concurrently so a chatty child (e.g. a
/// model-download progress bar) can never fill a pipe and deadlock. A watchdog
/// SIGTERMs a child that overruns `timeout`, then SIGKILLs after a short grace
/// so a wedged MLX/native call cannot hang the app forever.
///
/// `input`, when given, is written to the child's stdin and then closed —
/// the way to hand a child personal text (dictations, corrections) without
/// putting it on argv, where `ps` and endpoint-monitoring tools can read it.
/// Without it the child gets /dev/null, never the app's own stdin.
func runProcess(_ executable: String, _ args: [String],
                timeout: TimeInterval = 120,
                input: Data? = nil) -> (stdout: String, stderr: String, status: Int32) {
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: executable)
    proc.arguments = args
    let outPipe = Pipe()
    let errPipe = Pipe()
    let inPipe = input.map { _ in Pipe() }
    proc.standardInput = inPipe ?? FileHandle.nullDevice
    proc.standardOutput = outPipe
    proc.standardError = errPipe
    do {
        try proc.run()
    } catch {
        return ("", "launch failed: \(error)", -1)
    }
    if let input = input, let inPipe = inPipe {
        // Off this thread: a child that exits without reading must not block
        // us on a full pipe; a write to a dead child throws (EPIPE) rather
        // than raising, and the result below reports its exit status anyway.
        DispatchQueue.global(qos: .userInitiated).async {
            let handle = inPipe.fileHandleForWriting
            // A child that already exited would otherwise deliver SIGPIPE,
            // whose default action terminates the whole app.
            _ = fcntl(handle.fileDescriptor, F_SETNOSIGPIPE, 1)
            try? handle.write(contentsOf: input)
            try? handle.close()
        }
    }

    var outData = Data()
    var errData = Data()
    let group = DispatchGroup()
    let queue = DispatchQueue.global(qos: .userInitiated)
    group.enter()
    queue.async { outData = outPipe.fileHandleForReading.readDataToEndOfFile(); group.leave() }
    group.enter()
    queue.async { errData = errPipe.fileHandleForReading.readDataToEndOfFile(); group.leave() }

    let killGrace: TimeInterval = 2
    // Absolute ceiling: timeout + SIGTERM grace + small slack for pipe EOF after SIGKILL.
    let hardCeiling = timeout + killGrace + 5

    let termWatchdog = DispatchWorkItem {
        if proc.isRunning { proc.terminate() }
    }
    let killWatchdog = DispatchWorkItem {
        if proc.isRunning { kill(proc.processIdentifier, SIGKILL) }
    }
    queue.asyncAfter(deadline: .now() + timeout, execute: termWatchdog)
    queue.asyncAfter(deadline: .now() + timeout + killGrace, execute: killWatchdog)

    let pipeWait = group.wait(timeout: .now() + hardCeiling)
    if pipeWait == .timedOut, proc.isRunning {
        kill(proc.processIdentifier, SIGKILL)
    }
    if proc.isRunning {
        proc.waitUntilExit()
    } else {
        // Already exited — still reap status (returns immediately).
        proc.waitUntilExit()
    }
    termWatchdog.cancel()
    killWatchdog.cancel()

    return (String(data: outData, encoding: .utf8) ?? "",
            String(data: errData, encoding: .utf8) ?? "",
            proc.terminationStatus)
}

/// A CLI transcript from stdout: drop only the one newline `print()` adds.
/// Trimming all whitespace would erase a spoken "new line", which the core
/// returns as the text "\n".
func cliTranscript(_ stdout: String) -> String {
    stdout.hasSuffix("\n") ? String(stdout.dropLast()) : stdout
}

// MARK: - locate the repo (python + CLI)

func findRepoRoot() -> String? {
    let fm = FileManager.default
    let starts = [
        URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent(),
        URL(fileURLWithPath: fm.currentDirectoryPath),
    ]
    for start in starts {
        var dir = start.standardizedFileURL
        for _ in 0..<10 {
            if fm.fileExists(atPath: dir.appendingPathComponent("core/cli.py").path) {
                return dir.path
            }
            dir = dir.deletingLastPathComponent()
        }
    }
    return nil
}

// MARK: - bundle resources + writable app support

/// Directory holding the bundled, immutable Python source (core/, scripts/,
/// requirements.txt, VERSION). Inside VivoType.app this is Contents/Resources.
/// When running the bare executable from the repo during development, we fall
/// back to the repo root so `swiftc` builds keep working.
func vivotypeResourcesURL() -> URL? {
    if let res = Bundle.main.resourceURL,
       FileManager.default.fileExists(atPath: res.appendingPathComponent("core/cli.py").path) {
        return res
    }
    return findRepoRoot().map { URL(fileURLWithPath: $0) }
}

/// `~/Library/Application Support/VivoType/` — the single writable home for all
/// mutable runtime state (.venv, logs, user data). Created on first access.
/// Resolved via FileManager (never a hardcoded shell-expanded string) so it is
/// correct regardless of where VivoType.app lives.
func vivotypeAppSupportURL() -> URL {
    let fm = FileManager.default
    let base = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support")
    let dir = base.appendingPathComponent("VivoType", isDirectory: true)
    if !fm.fileExists(atPath: dir.path) {
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
    }
    return dir
}

/// Path to the Python interpreter inside the App Support virtual environment.
func vivotypeVenvPython() -> URL {
    vivotypeAppSupportURL().appendingPathComponent(".venv/bin/python")
}

/// The bundled build identifier (Contents/Resources/VERSION), or nil if absent.
func vivotypeVersion() -> String? {
    guard let res = vivotypeResourcesURL() else { return nil }
    let v = try? String(contentsOf: res.appendingPathComponent("VERSION"), encoding: .utf8)
    let trimmed = v?.trimmingCharacters(in: .whitespacesAndNewlines)
    return (trimmed?.isEmpty == false) ? trimmed : nil
}

/// `~/Library/Application Support/VivoType/logs/`, created on first access.
func vivotypeLogsURL() -> URL {
    let dir = vivotypeAppSupportURL().appendingPathComponent("logs", isDirectory: true)
    if !FileManager.default.fileExists(atPath: dir.path) {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }
    return dir
}
