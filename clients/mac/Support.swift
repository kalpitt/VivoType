// VivoType — shared support: small process/IO helpers, the app state enum, and the
// path resolvers that keep immutable bundle code separate from mutable App
// Support state. No UI here; everything in this file is usable headlessly.

import Foundation

// MARK: - helpers

func warn(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

enum VivoTypeState {
    case idle, recording, transcribing, error, loading
}

/// Run a process, draining stdout+stderr concurrently so a chatty child (e.g. a
/// model-download progress bar) can never fill a pipe and deadlock. A watchdog
/// terminates a child that overruns `timeout`, so the app never wedges.
func runProcess(_ executable: String, _ args: [String],
                timeout: TimeInterval = 120) -> (stdout: String, stderr: String, status: Int32) {
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: executable)
    proc.arguments = args
    let outPipe = Pipe()
    let errPipe = Pipe()
    proc.standardOutput = outPipe
    proc.standardError = errPipe
    do {
        try proc.run()
    } catch {
        return ("", "launch failed: \(error)", -1)
    }

    var outData = Data()
    var errData = Data()
    let group = DispatchGroup()
    let queue = DispatchQueue.global(qos: .userInitiated)
    group.enter()
    queue.async { outData = outPipe.fileHandleForReading.readDataToEndOfFile(); group.leave() }
    group.enter()
    queue.async { errData = errPipe.fileHandleForReading.readDataToEndOfFile(); group.leave() }

    let watchdog = DispatchWorkItem { if proc.isRunning { proc.terminate() } }
    queue.asyncAfter(deadline: .now() + timeout, execute: watchdog)

    group.wait()            // both pipes read to EOF (child closed them)
    proc.waitUntilExit()
    watchdog.cancel()

    return (String(data: outData, encoding: .utf8) ?? "",
            String(data: errData, encoding: .utf8) ?? "",
            proc.terminationStatus)
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
