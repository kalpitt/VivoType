// VivoType — correction offers: after the user fixes a dictated word in
// place, show "Always write “to”?" for 20 s. Remember writes the pair straight
// into the active rules (with Undo); silence or Not now files it in Review
// (corrections.jsonl) and changes nothing. Hovering the card pauses it. Runs only when Settings "Suggest corrections after
// edits" is on. All state lives on the main thread; the Python helpers run on
// a background queue.
//
// Two independent tracks share the one chip:
//   watch track — the current dictation: watching → offering → (expire | ✓).
//   learn track — a ✓ in flight or done: learning → learned (Undo) → gone.
// A new dictation resets only the watch track, so a ✓ already being written
// always ends with its Undo on screen (never a rule nobody can take back).

import Foundation
import AppKit

final class CorrectionOffers {
    /// Main thread: a pair was filed in Review — refresh the badge.
    var onQueueChanged: (() -> Void)?
    /// Main thread: a short message for the toast.
    var onMessage: ((String) -> Void)?
    /// Main thread: the field watch found the dictation. Returns false when
    /// the clipboard path already claimed it, so one utterance logs once.
    var claimInjection: ((String) -> Bool)?
    /// Main thread: a ✓ was written (the app plays the capture sound).
    var onLearned: (() -> Void)?
    /// Whether a word is a common English word (the chip marks it ⚠).
    var isCommonWord: ((String) -> Bool)?

    var enabled = false {
        didSet { if !enabled { resetAll() } }
    }

    private static let offerLifetime: TimeInterval = 20
    /// How long "Got it" stays up with its Undo.
    private static let learnedLifetime: TimeInterval = 8
    /// After the pointer leaves the card, it stays this much longer.
    private static let afterHover: TimeInterval = 5
    /// More pairs than this is a rewrite, not a fix: no chip, just Review.
    private static let maxOfferedPairs = 3

    private enum WatchPhase { case idle, watching, offering }
    private enum LearnPhase { case none, learning, learned, undoing }
    private typealias Pair = (from: String, to: String)

    private let pythonPath: String
    private let learnPath: String
    private let promotePath: String
    private let watch = FieldWatch()
    private let panel = CorrectionOfferPanel()
    private let work = DispatchQueue(label: "com.vivotype.offers", qos: .utility)
    /// The pointer is on the card: timers are paused and edits don't hide it.
    private var hovering = false

    // watch track
    private var watchPhase = WatchPhase.idle
    private var session = -1
    private var original: String?
    private var edit: SettledEdit?
    private var pairs: [Pair] = []
    private var watchTimer: Timer?
    /// Bumped on every edit event and watch reset: a pair lookup started
    /// before the latest edit is dropped when it returns.
    private var fetchSeq = 0

    // learn track
    private var learnPhase = LearnPhase.none
    private var learnedEdit: SettledEdit?
    private var learnedPairs: [Pair] = []       // everything the ✓ was for
    private var undoTokens: [(pair: Pair, token: String)] = []  // what it wrote
    private var learnTimer: Timer?
    /// Bumped only when the feature is turned off: a ✓ result for an older
    /// generation is dropped.
    private var learnGen = 0

    init(pythonPath: String, learnPath: String, promotePath: String) {
        self.pythonPath = pythonPath
        self.learnPath = learnPath
        self.promotePath = promotePath
        watch.onAnchored = { [weak self] id in self?.anchored(id) }
        watch.onEditing = { [weak self] id in self?.editing(id) }
        watch.onSettledEdit = { [weak self] edit in self?.settled(edit) }
        panel.onTick = { [weak self] in self?.tick() }
        panel.onUndo = { [weak self] in self?.undo() }
        panel.onDismiss = { [weak self] in self?.expire() }  // Not now = silence, now
        panel.onHover = { [weak self] inside in self?.hover(inside) }
    }

    // MARK: control (main thread)

    /// A dictation just landed. Cancels any open offer without logging it.
    func dictationLanded(_ text: String) {
        resetWatch()
        if enabled, !learnPath.isEmpty, !promotePath.isEmpty {
            original = text
            session = watch.begin(original: text)
            watchPhase = .watching
        }
        refreshPanel()
    }

    /// Close the open offer without logging (scratch command).
    func cancel() {
        resetWatch()
        refreshPanel()
    }

    /// The card on screen is this offer (not "Got it", not a silent offer
    /// of more pairs than a card shows).
    private var offerOnCard: Bool {
        learnPhase == .none && watchPhase == .offering
            && (1...Self.maxOfferedPairs).contains(pairs.count)
    }

    /// (Re)start the offer's countdown. Its timer always runs, so silence
    /// still files the edit in Review; the bar runs only when the card shows it.
    private func startOfferTimer(_ lifetime: TimeInterval = offerLifetime) {
        watchTimer?.invalidate()
        watchTimer = Self.timer(lifetime) { [weak self] in self?.expire() }
        if offerOnCard { panel.startCountdown(lifetime) }
    }

    private func startLearnedTimer(_ lifetime: TimeInterval = learnedLifetime) {
        learnTimer?.invalidate()
        learnTimer = Self.timer(lifetime) { [weak self] in
            self?.dismissLearned()
            self?.refreshPanel()
        }
        panel.startCountdown(lifetime)
    }

    /// Pause the countdown of the card under the pointer; resume it with a
    /// short grace period when the pointer leaves. A timer for anything not
    /// on the card (a silent offer behind "Got it") keeps running.
    private func hover(_ inside: Bool) {
        // A card that hid under the pointer reset `hovering`; its late
        // mouseExited must not cut the next card's time short.
        guard inside != hovering else { return }
        hovering = inside
        if inside {
            if learnPhase == .learned { learnTimer?.invalidate(); learnTimer = nil }
            if offerOnCard { watchTimer?.invalidate(); watchTimer = nil }
            return panel.pauseCountdown()
        }
        if learnPhase == .learned {
            startLearnedTimer(Self.afterHover)
        } else if offerOnCard {
            startOfferTimer(Self.afterHover)
        }
    }

    private func resetWatch() {
        fetchSeq += 1
        watch.cancel()
        watchTimer?.invalidate()
        watchTimer = nil
        watchPhase = .idle
        session = -1
        original = nil
        edit = nil
        pairs = []
    }

    private func resetAll() {
        resetWatch()
        learnGen += 1
        dismissLearned()
        refreshPanel()
    }

    private func dismissLearned() {
        learnTimer?.invalidate()
        learnTimer = nil
        learnPhase = .none
        learnedEdit = nil
        learnedPairs = []
        undoTokens = []
    }

    // MARK: field-watch events

    private func anchored(_ id: Int) {
        guard id == session, watchPhase == .watching, let text = original else { return }
        if claimInjection?(text) == false {
            resetWatch()
            refreshPanel()
        }
    }

    /// Further edits restart the offer: hide it until the text settles again.
    private func editing(_ id: Int) {
        guard id == session else { return }
        fetchSeq += 1  // a lookup for the previous text is now stale
        guard watchPhase == .offering, !hovering else { return }
        watchTimer?.invalidate()
        watchTimer = nil
        watchPhase = .watching
        refreshPanel()
    }

    private func settled(_ settledEdit: SettledEdit) {
        guard settledEdit.session == session, watchPhase != .idle else { return }
        watchTimer?.invalidate()
        watchTimer = nil
        fetchSeq += 1
        if settledEdit.corrected == settledEdit.original {  // put back as dictated
            edit = nil
            pairs = []
            watchPhase = .watching
            return refreshPanel()
        }
        let seq = fetchSeq
        let payload = Self.pairPayload(settledEdit)
        work.async { [weak self] in
            guard let self = self else { return }
            let found = payload.map(self.fetchPairs) ?? []
            DispatchQueue.main.async {
                guard seq == self.fetchSeq, self.watchPhase != .idle else { return }
                self.offer(found, for: settledEdit)
            }
        }
    }

    private func offer(_ found: [Pair], for settledEdit: SettledEdit) {
        guard !found.isEmpty else {  // nothing learnable (e.g. only punctuation)
            edit = nil
            pairs = []
            watchPhase = .watching
            return refreshPanel()
        }
        edit = settledEdit
        pairs = found
        watchPhase = .offering
        // A new visible offer replaces an older "Learned · Undo" chip.
        if learnPhase == .learned, found.count <= Self.maxOfferedPairs { dismissLearned() }
        refreshPanel()  // more than maxOfferedPairs: silent, expiry files them
        if !(hovering && offerOnCard) { startOfferTimer() }
    }

    // MARK: outcomes

    /// Silence: file the edit in Review. Nothing becomes an active rule.
    private func expire() {
        guard watchPhase == .offering, let settledEdit = edit else { return }
        resetWatch()
        refreshPanel()
        fileInReview(settledEdit)
    }

    private func fileInReview(_ settledEdit: SettledEdit) {
        guard let payload = Self.pairPayload(settledEdit) else { return }
        work.async { [weak self] in
            guard let self = self else { return }
            let result = runProcess(self.pythonPath, [self.learnPath], timeout: 60, input: payload)
            let count = Int(result.stdout.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
            if count > 0 {
                DispatchQueue.main.async { self.onQueueChanged?() }
            }
        }
    }

    /// ✓: write each pair into the active rules and keep its undo token.
    private func tick() {
        // The count check matches refreshPanel: never write pairs no chip showed.
        guard watchPhase == .offering, let settledEdit = edit,
              (1...Self.maxOfferedPairs).contains(pairs.count),
              learnPhase == .none || learnPhase == .learned else { return }
        dismissLearned()
        learnedEdit = settledEdit
        learnedPairs = pairs
        learnPhase = .learning
        panel.pauseCountdown()  // frozen while the rule is written
        resetWatch()  // the offer is resolved; later edits are new writing
        let gen = learnGen
        let toLearn = learnedPairs
        work.async { [weak self] in
            guard let self = self else { return }
            var learned: [(pair: Pair, token: String)] = []
            var failures: [String] = []
            for pair in toLearn {
                let result = self.promote(["--action", "promote", "--allow-unlogged"], pair)
                if (result["ok"] as? Bool) == true, let token = result["undo"],
                   let data = try? JSONSerialization.data(withJSONObject: token),
                   let json = String(data: data, encoding: .utf8) {
                    learned.append((pair, json))
                } else {
                    failures.append(Self.describe(result, pair))
                }
            }
            DispatchQueue.main.async { self.ticked(learned, failures, gen) }
        }
    }

    private func ticked(_ learned: [(pair: Pair, token: String)], _ failures: [String], _ gen: Int) {
        guard gen == learnGen else {
            // Feature turned off meanwhile: no Undo chip, but say what was written.
            if !learned.isEmpty {
                onMessage?("✓ Learned " + learned.map { "\($0.pair.from) → \($0.pair.to)" }
                    .joined(separator: ", ") + " — remove it in Review")
            }
            return
        }
        if !failures.isEmpty {
            onMessage?("⚠ Couldn't learn " + failures.joined(separator: "; "))
        }
        guard !learned.isEmpty else {
            dismissLearned()
            return refreshPanel()
        }
        if watchPhase == .offering {
            // A newer offer took the chip while this was written: say so
            // instead of hiding that offer. The rule is shown in Settings.
            dismissLearned()
            onMessage?("✓ Learned " + learned.map { "\($0.pair.from) → \($0.pair.to)" }
                .joined(separator: ", "))
            refreshPanel()  // the chip still shows the old pair: show the new offer
            if !hovering { startOfferTimer() }  // its bar restarts with the card
            return
        }
        undoTokens = learned
        learnPhase = .learned
        onLearned?()
        refreshPanel()
        guard !hovering else { return }  // hover(false) starts the countdown
        startLearnedTimer()
    }

    /// Undo: remove exactly what the tick wrote, then offer again un-ticked
    /// (so silence still files the edit in Review).
    private func undo() {
        guard learnPhase == .learned, !undoTokens.isEmpty else { return }
        learnPhase = .undoing
        learnTimer?.invalidate()
        learnTimer = nil
        let gen = learnGen
        let tokens = undoTokens
        work.async { [weak self] in
            guard let self = self else { return }
            var failures: [String] = []
            for entry in tokens.reversed() {
                let result = self.promote(["--action", "remove"], entry.pair, undo: entry.token)
                if (result["ok"] as? Bool) != true {
                    failures.append(Self.describe(result, entry.pair))
                }
            }
            DispatchQueue.main.async { self.undone(failures, gen) }
        }
    }

    private func undone(_ failures: [String], _ gen: Int) {
        guard gen == learnGen else {
            // Feature turned off meanwhile: no card, but a failed undo must
            // still be said, or a rule stays with nobody knowing.
            if !failures.isEmpty {
                onMessage?("⚠ Couldn't undo " + failures.joined(separator: "; ") + " — remove it in Review")
            }
            return
        }
        let settledEdit = learnedEdit
        let offered = learnedPairs
        dismissLearned()
        if !failures.isEmpty {
            onMessage?("⚠ Couldn't undo " + failures.joined(separator: "; ") + " — remove it in Review")
            return refreshPanel()
        }
        guard let settledEdit = settledEdit else { return refreshPanel() }
        if watchPhase == .idle {
            // Back to the un-ticked offer; silence files it in Review.
            edit = settledEdit
            pairs = offered
            watchPhase = .offering
            refreshPanel()
            if !hovering { startOfferTimer() }
        } else {
            // A newer dictation owns the chip now: file this one directly.
            fileInReview(settledEdit)
            onMessage?("Undone — the correction is in Review")
            refreshPanel()
            if watchPhase == .offering, !hovering { startOfferTimer() }
        }
    }

    // MARK: helpers

    /// Show whichever track owns the chip right now.
    private func refreshPanel() {
        switch learnPhase {
        case .learned:
            return showPanel(.learned, undoTokens.map { $0.pair }, near: learnedEdit?.rect)
        case .learning, .undoing:
            return  // keep what's on screen until the helper answers
        case .none:
            break
        }
        if watchPhase == .offering, (1...Self.maxOfferedPairs).contains(pairs.count) {
            showPanel(.offer, pairs, near: edit?.rect)
        } else {
            hovering = false  // a hidden card gets no mouseExited
            panel.hide()
        }
    }

    private func showPanel(_ mode: CorrectionOfferPanel.Mode, _ shown: [Pair], near: NSRect?) {
        let rows = shown.map { pair -> (from: String, to: String, flagged: Bool) in
            let single = !pair.from.contains(" ")
            return (pair.from, pair.to, single && (isCommonWord?(pair.from) ?? false))
        }
        panel.show(pairs: rows, mode: mode, near: near)
    }

    private static func timer(_ lifetime: TimeInterval = offerLifetime,
                              _ fire: @escaping () -> Void) -> Timer {
        let timer = Timer.scheduledTimer(withTimeInterval: lifetime, repeats: false) { _ in fire() }
        timer.tolerance = 0.5
        return timer
    }

    /// The span pair goes to Python on stdin, never argv.
    private static func pairPayload(_ edit: SettledEdit) -> Data? {
        try? JSONSerialization.data(withJSONObject: ["original": edit.original,
                                                     "corrected": edit.corrected])
    }

    /// learn.py --pairs-json: the filtered pairs, logging nothing. Uses
    /// learn.py's own similarity floor, the same one expiry's log run applies.
    private func fetchPairs(_ payload: Data) -> [Pair] {
        let result = runProcess(pythonPath, [learnPath, "--pairs-json"], timeout: 30, input: payload)
        if result.status != 0 {
            warn("VivoType: learn.py --pairs-json exited \(result.status); no correction offer")
        }
        guard result.status == 0, let data = result.stdout.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = obj["pairs"] as? [[String: Any]]
        else { return [] }
        return rows.compactMap { row in
            guard let from = row["from"] as? String, let to = row["to"] as? String,
                  !from.isEmpty, !to.isEmpty else { return nil }
            return (from, to)
        }
    }

    /// promote.py --apply for one pair, the pair (and any undo token) as
    /// JSON on stdin — a word starting with "-" can't be taken for an option.
    private func promote(_ args: [String], _ pair: Pair, undo: String? = nil) -> [String: Any] {
        // The words and the undo token go on stdin, never argv: any local
        // process can read another's command line.
        var payload: [String: Any] = ["from": pair.from, "to": pair.to]
        if let undo = undo, let data = undo.data(using: .utf8),
           let token = try? JSONSerialization.jsonObject(with: data) {
            payload["undo"] = token
        }
        guard let input = try? JSONSerialization.data(withJSONObject: payload)
        else { return ["ok": false, "status": "process_failed"] }
        let result = runProcess(pythonPath, [promotePath, "--apply", "--stdin-json"] + args,
                                timeout: 60, input: input)
        guard result.status == 0, let data = result.stdout.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return ["ok": false, "status": "process_failed"] }
        return obj
    }

    private static func describe(_ result: [String: Any], _ pair: Pair) -> String {
        let status = result["status"] as? String ?? "failed"
        return "\(pair.from) → \(pair.to) (\(status))"
    }
}
