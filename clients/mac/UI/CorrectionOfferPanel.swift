// VivoType — the correction offer: a floating card that appears just above
// the word the user fixed ("Always write “Kalpit”?  You changed Kalpith →
// Kalpit") with Remember / Not now, then turns green ("Got it — VivoType will
// write “Kalpit” from now on") with Undo. Hovering it pauses its countdown.
// While it shows, the number keys answer it: 1 = Remember, 2 = Not now / Undo.
// Unlike the capture Toast (HUD.swift) it takes clicks, but it still never
// becomes key or main, so clicking it never steals focus from the app being
// typed into.

import Foundation
import AppKit

/// A button that works on the first click inside a panel that never
/// becomes key (the default would swallow that click to "activate"). AppKit
/// draws no hover state for a window of an inactive app, so it tracks the
/// pointer itself and reports it through `onHover`.
private final class FirstClickButton: NSButton {
    var onHover: ((Bool) -> Void)?
    private var hoverArea: NSTrackingArea?

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let area = hoverArea { removeTrackingArea(area) }
        let area = NSTrackingArea(rect: .zero,
                                  options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                  owner: self, userInfo: nil)
        addTrackingArea(area)
        hoverArea = area
    }

    override func mouseEntered(with event: NSEvent) {
        NSCursor.pointingHand.set()
        onHover?(true)
    }

    override func mouseExited(with event: NSEvent) {
        NSCursor.arrow.set()
        onHover?(false)
    }
}

final class CorrectionOfferPanel {
    enum Mode {
        case offer   // "Always write “X”?" + Remember / Not now
        case learned // "Got it" + Undo
    }

    var onTick: (() -> Void)?
    var onUndo: (() -> Void)?
    var onDismiss: (() -> Void)?
    /// The pointer entered (true) or left (false) the card.
    var onHover: ((Bool) -> Void)?

    private let panel: NonActivatingPanel
    private let card = NSVisualEffectView()
    private let icon = NSImageView()
    private let title = NSTextField(labelWithString: "")
    private let detail = NSTextField(labelWithString: "")
    private let warning = NSTextField(labelWithString: "")
    private let primary = FirstClickButton(title: "", target: nil, action: nil)
    private let close = FirstClickButton(title: "Not now", target: nil, action: nil)
    /// "1" = Remember, "2" = Not now / Undo, live only while the card shows.
    private let shortcuts = CardShortcuts()
    private var primaryLabel = ""
    private var closeHovered = false
    /// The card grows to fit its text between these widths; past the max,
    /// lines truncate at the end.
    private let minWidth: CGFloat = 320
    private let maxWidth: CGFloat = 560
    private var width: CGFloat = 320
    private let buttons = NSStackView()
    /// Empties as the card's time runs out; frozen while hovered.
    private let countdown = CountdownBar()
    private let baseHeight: CGFloat = 56
    private let warningHeight: CGFloat = 74
    private var height: CGFloat = 56
    /// Gap between the fixed text and the card.
    private let gap: CGFloat = 10
    /// The tracking area's owner. NSTrackingArea doesn't retain its owner,
    /// so the panel keeps it alive.
    private var hoverRelay: HoverRelay?

    init() {
        panel = makeHUDPanel(width: width, height: height)
        panel.ignoresMouseEvents = false  // Remember, Not now and Undo must be clickable
        panel.becomesKeyOnlyIfNeeded = true

        card.frame = NSRect(x: 0, y: 0, width: minWidth, height: baseHeight)
        card.autoresizingMask = [.width, .height]
        card.material = .popover  // follows light / dark mode
        card.state = .active
        card.wantsLayer = true
        card.layer?.cornerRadius = 12
        card.layer?.masksToBounds = true
        card.layer?.borderWidth = 1

        icon.translatesAutoresizingMaskIntoConstraints = false
        icon.symbolConfiguration = .init(pointSize: 18, weight: .semibold)

        for field in [title, detail, warning] {
            field.translatesAutoresizingMaskIntoConstraints = false
            field.lineBreakMode = .byTruncatingTail
            field.backgroundColor = .clear
            field.isBezeled = false
            field.isEditable = false
            field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        }
        title.font = .systemFont(ofSize: 13, weight: .semibold)
        title.textColor = .labelColor
        detail.font = .systemFont(ofSize: 12)
        detail.textColor = .labelColor
        warning.font = .systemFont(ofSize: 11)
        warning.textColor = .systemOrange

        primary.translatesAutoresizingMaskIntoConstraints = false
        primary.bezelStyle = .push
        primary.controlSize = .regular
        primary.target = self
        primary.action = #selector(primaryPressed)
        primary.setContentHuggingPriority(.required, for: .horizontal)
        primary.setContentCompressionResistancePriority(.required, for: .horizontal)

        close.translatesAutoresizingMaskIntoConstraints = false
        close.isBordered = false
        close.font = .systemFont(ofSize: 12)
        close.toolTip = "Keep it in Review to decide later"
        close.target = self
        close.action = #selector(closePressed)
        close.setContentHuggingPriority(.required, for: .horizontal)
        close.wantsLayer = true
        close.layer?.cornerRadius = 6
        primary.onHover = { [weak self] inside in self?.styleButtons(primaryHovered: inside) }
        close.onHover = { [weak self] inside in self?.styleButtons(closeHovered: inside) }

        let text = NSStackView(views: [title, detail, warning])
        text.translatesAutoresizingMaskIntoConstraints = false
        text.orientation = .vertical
        text.alignment = .leading
        text.spacing = 2

        // A stack so a hidden "Not now" takes no space: Undo sits at the edge.
        buttons.translatesAutoresizingMaskIntoConstraints = false
        buttons.orientation = .horizontal
        buttons.spacing = 8
        buttons.detachesHiddenViews = true
        buttons.addArrangedSubview(primary)
        buttons.addArrangedSubview(close)
        buttons.setContentHuggingPriority(.required, for: .horizontal)
        buttons.setContentCompressionResistancePriority(.required, for: .horizontal)

        countdown.translatesAutoresizingMaskIntoConstraints = false
        for view in [icon, text, buttons, countdown] { card.addSubview(view) }
        NSLayoutConstraint.activate([
            icon.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 14),
            icon.centerYAnchor.constraint(equalTo: card.centerYAnchor),
            icon.widthAnchor.constraint(equalToConstant: 22),
            text.leadingAnchor.constraint(equalTo: icon.trailingAnchor, constant: 10),
            text.centerYAnchor.constraint(equalTo: card.centerYAnchor),
            buttons.leadingAnchor.constraint(greaterThanOrEqualTo: text.trailingAnchor, constant: 12),
            buttons.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -12),
            buttons.centerYAnchor.constraint(equalTo: card.centerYAnchor),
            countdown.leadingAnchor.constraint(equalTo: card.leadingAnchor),
            countdown.trailingAnchor.constraint(equalTo: card.trailingAnchor),
            countdown.bottomAnchor.constraint(equalTo: card.bottomAnchor),
            countdown.heightAnchor.constraint(equalToConstant: 3),
        ])
        panel.contentView = card
        let relay = HoverRelay(self)
        hoverRelay = relay
        card.addTrackingArea(NSTrackingArea(rect: .zero,
                                            options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                            owner: relay, userInfo: nil))
        shortcuts.onPress = { [weak self] key in self?.keyPressed(key) }
    }

    /// NSTrackingArea's owner; forwards to the panel without a retain cycle.
    private final class HoverRelay: NSResponder {
        weak var target: CorrectionOfferPanel?
        init(_ target: CorrectionOfferPanel) { self.target = target; super.init() }
        required init?(coder: NSCoder) { nil }
        override func mouseEntered(with event: NSEvent) { target?.onHover?(true) }
        override func mouseExited(with event: NSEvent) { target?.onHover?(false) }
    }

    private var mode: Mode = .offer
    /// False once hide() starts: its fade-out completion orders the panel
    /// out only if show() hasn't brought it back in the meantime.
    private var visible = false

    /// Show (or update in place) the pairs. `flagged` marks pairs whose `from`
    /// is a common English word: learning them changes that word everywhere.
    /// `near` is the fixed text's screen rect (Cocoa coordinates) when the app
    /// reports it; the card sits just above it, else at the top of the screen.
    func show(pairs: [(from: String, to: String, flagged: Bool)], mode: Mode, near: NSRect?) {
        self.mode = mode
        visible = true
        let flagged = pairs.filter { $0.flagged }
        switch mode {
        case .offer:
            icon.image = NSImage(systemSymbolName: "sparkles", accessibilityDescription: "Correction")
            icon.contentTintColor = .controlAccentColor
            title.stringValue = pairs.count == 1 ? "Always write “\(pairs[0].to)”?"
                                                 : "Remember these \(pairs.count) fixes?"
            // Every pair Remember would write is listed (at most 3 reach a card).
            detail.stringValue = "You changed "
                + pairs.map { "\($0.from) → \($0.to)" }.joined(separator: " · ")
            if let first = flagged.first {
                warning.stringValue = "“\(first.from)” is a common word. This changes it everywhere."
                warning.isHidden = false
                primaryLabel = "Remember anyway"
                primaryTint = nil
            } else {
                warning.isHidden = true
                primaryLabel = "Remember"
                primaryTint = .controlAccentColor
            }
            close.isHidden = false
            card.layer?.borderColor = NSColor.controlAccentColor.cgColor
        case .learned:
            icon.image = NSImage(systemSymbolName: "checkmark.circle.fill", accessibilityDescription: "Learned")
            icon.contentTintColor = .systemGreen
            title.stringValue = "Got it"
            detail.stringValue = pairs.count == 1
                ? "VivoType will write “\(pairs[0].to)” from now on"
                : "VivoType will remember " + pairs.map { "\($0.from) → \($0.to)" }.joined(separator: " · ")
            warning.isHidden = true
            primaryLabel = "Undo"
            primaryTint = nil
            close.isHidden = true
            card.layer?.borderColor = NSColor.systemGreen.cgColor
        }
        primary.bezelColor = primaryTint
        // Yes = 1, back out = 2: a double-tapped 1 can't undo what it saved.
        shortcuts.set(mode == .offer ? [.one, .two] : [.two])
        styleButtons(closeHovered: false)
        // A new state starts full and still; the owner runs it (startCountdown).
        countdown.reset(color: mode == .offer ? .controlAccentColor : .systemGreen)
        height = warning.isHidden ? baseHeight : warningHeight
        width = fittingWidth()
        panel.setContentSize(NSSize(width: width, height: height))
        let spoken = mode == .offer ? "VivoType: \(title.stringValue) \(detail.stringValue)"
                                    : "VivoType: \(title.stringValue). \(detail.stringValue)"
        NSAccessibility.post(element: panel, notification: .announcementRequested,
                             userInfo: [.announcement: spoken,
                                        .priority: NSAccessibilityPriorityLevel.medium.rawValue])
        position(near: near)
        panel.orderFrontRegardless()  // never makeKey — must not steal focus
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? 0 : 0.2
            panel.animator().alphaValue = 1
        }
    }

    /// The primary button's tint for the current state (nil = the standard
    /// grey bezel).
    private var primaryTint: NSColor?

    /// Hover feedback the system won't draw for us in a card that never
    /// takes focus: the standard button shows its own pressed look (its tint
    /// and hover bezel are ignored in an unfocused window); "Not now" gets a
    /// pill and darker text.
    private func styleButtons(primaryHovered: Bool? = nil, closeHovered: Bool? = nil) {
        if let on = primaryHovered { primary.highlight(on) }
        if let on = closeHovered {
            self.closeHovered = on
            close.layer?.backgroundColor = on ? NSColor.quaternaryLabelColor.cgColor : nil
        }
        applyTitles()
    }

    /// Button titles with their key shown after the label in a lighter grey,
    /// like the numbers in a menu. A key that failed to register (another app
    /// owns it) shows no number.
    private func applyTitles() {
        primary.attributedTitle = keyedTitle(primaryLabel, key: mode == .offer ? .one : .two,
                                             color: .controlTextColor, size: 13)
        close.attributedTitle = keyedTitle("Not now", key: .two,
                                           color: closeHovered ? .labelColor : .secondaryLabelColor,
                                           size: 12)
    }

    private func keyedTitle(_ text: String, key: CardShortcuts.Key,
                            color: NSColor, size: CGFloat) -> NSAttributedString {
        let title = NSMutableAttributedString(
            string: text, attributes: [.foregroundColor: color, .font: NSFont.systemFont(ofSize: size)])
        if shortcuts.isActive(key) {
            title.append(NSAttributedString(
                string: "   \(key.rawValue)",
                attributes: [.foregroundColor: NSColor.tertiaryLabelColor,
                             .font: NSFont.monospacedDigitSystemFont(ofSize: size - 1, weight: .semibold)]))
        }
        return title
    }

    private func keyPressed(_ key: CardShortcuts.Key) {
        guard visible else { return }
        switch (mode, key) {
        case (.offer, .one): onTick?()
        case (.offer, .two): onDismiss?()
        case (.learned, .two): onUndo?()
        case (.learned, .one): break
        }
    }

    /// Empty the countdown bar over `duration`, from where it stands.
    func startCountdown(_ duration: TimeInterval) {
        card.layoutSubtreeIfNeeded()
        countdown.run(duration)
    }

    /// Freeze the countdown bar (pointer on the card, or a write in flight).
    func pauseCountdown() {
        countdown.pause()
    }

    /// Wide enough for the longest line and the buttons, within min/max.
    private func fittingWidth() -> CGFloat {
        let lines = [title, detail, warning].filter { !$0.isHidden }
        let textWidth = lines.map { ceil($0.attributedStringValue.size().width) + 4 }.max() ?? 0
        buttons.layoutSubtreeIfNeeded()
        let chrome: CGFloat = 14 + 22 + 10 + 12 + 12  // insets, icon, gaps
        let needed = chrome + textWidth + buttons.fittingSize.width
        return min(max(needed, minWidth), maxWidth)
    }

    func hide() {
        guard visible else { return }
        visible = false
        shortcuts.set([])  // 1 and 2 type normally again
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.2
            panel.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            guard let self = self, !self.visible else { return }
            self.panel.orderOut(nil)
        })
    }

    @objc private func primaryPressed() {
        switch mode {
        case .offer: onTick?()
        case .learned: onUndo?()
        }
    }

    @objc private func closePressed() {
        onDismiss?()
    }

    /// Just above the fixed line, right-aligned to the word's end, so it
    /// never covers the lines being written; below only when there's no room
    /// above. No usable rect: top-centre of the screen under the pointer.
    private func position(near rect: NSRect?) {
        if let rect = rect,
           let screen = NSScreen.screens.first(where: { $0.frame.intersects(rect) }) {
            let area = screen.visibleFrame
            var y = rect.maxY + gap
            if y + height > area.maxY { y = rect.minY - gap - height }
            let x = min(max(rect.maxX - width + 24, area.minX + 8), area.maxX - width - 8)
            y = min(max(y, area.minY + 8), area.maxY - height - 8)
            panel.setFrameOrigin(NSPoint(x: x, y: y))
            return
        }
        let mouse = NSEvent.mouseLocation
        guard let screen = NSScreen.screens.first(where: { $0.frame.contains(mouse) }) ?? NSScreen.main
        else { return }
        let area = screen.visibleFrame  // excludes the menu bar / notch inset
        panel.setFrameOrigin(NSPoint(x: area.midX - width / 2,
                                     y: area.maxY - height - 96))  // below the toast's slot
    }
}
