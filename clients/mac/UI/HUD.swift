// VivoType — transient HUD chrome: the focus-safe panel base, the live-level
// "Listening…" pill, and the self-dismissing "correction captured" toast.
// None of these panels ever become key/main, so they never steal focus from
// the app the user is dictating into.

import Foundation
import AppKit

// MARK: - recording pill HUD

/// A panel that never becomes key/main, so it can never steal focus from the
/// app the user is dictating into.
final class NonActivatingPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// A row of bars whose heights track the live input level (a small "waveform").
final class MeterView: NSView {
    var level: CGFloat = 0 { didSet { needsDisplay = true } }
    var accent: NSColor = .systemRed { didSet { needsDisplay = true } }
    private let bars = 13

    override func draw(_ dirtyRect: NSRect) {
        let slot = bounds.width / CGFloat(bars)
        let barWidth = slot * 0.55
        let center = CGFloat(bars - 1) / 2
        for i in 0..<bars {
            // Arch the middle bars taller for a natural waveform shape.
            let shape = 1 - (abs(CGFloat(i) - center) / center) * 0.6
            let height = max(2, bounds.height * level * shape)
            let x = CGFloat(i) * slot + (slot - barWidth) / 2
            let y = (bounds.height - height) / 2
            let rect = NSRect(x: x, y: y, width: barWidth, height: height)
            let path = NSBezierPath(roundedRect: rect, xRadius: barWidth / 2, yRadius: barWidth / 2)
            (level > 0.02 ? accent : NSColor.tertiaryLabelColor).setFill()
            path.fill()
        }
    }
}

/// The transient "● Listening…" HUD shown while the hotkey is held, and the
/// persistent "● Hands-free" badge shown during toggle dictation.
final class RecordingPill {
    private let panel: NonActivatingPanel
    private let meter: MeterView
    private let dot = NSView()
    private let label = NSTextField(labelWithString: "Listening…")
    // Bumped on every show(); a pending hide() that finds it changed skips its
    // orderOut so a quick hide→show (double-tap → hands-free) can't blank the pill.
    private var showToken = 0
    // Distinct accents per mode: green = momentary push-to-talk, red = the
    // continuous hands-free mode (red reads as "actively recording, hands-off").
    private let holdAccent = NSColor.systemGreen
    private let handsFreeAccent = NSColor.systemRed

    init() {
        let width: CGFloat = 200
        let height: CGFloat = 44
        panel = makeHUDPanel(width: width, height: height)

        let blur = NSVisualEffectView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        blur.material = .hudWindow
        blur.state = .active
        blur.wantsLayer = true
        blur.layer?.cornerRadius = 12
        blur.layer?.masksToBounds = true

        dot.translatesAutoresizingMaskIntoConstraints = false
        dot.wantsLayer = true
        dot.layer?.backgroundColor = holdAccent.cgColor
        dot.layer?.cornerRadius = 4
        NSLayoutConstraint.activate([
            dot.widthAnchor.constraint(equalToConstant: 8),
            dot.heightAnchor.constraint(equalToConstant: 8)
        ])

        label.translatesAutoresizingMaskIntoConstraints = false
        label.font = .systemFont(ofSize: 12, weight: .medium)
        label.textColor = .labelColor
        label.backgroundColor = .clear
        label.isBezeled = false
        label.isEditable = false

        meter = MeterView()
        meter.translatesAutoresizingMaskIntoConstraints = false

        let stack = NSStackView(views: [dot, label, meter])
        stack.translatesAutoresizingMaskIntoConstraints = false
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.spacing = 8
        blur.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: blur.leadingAnchor, constant: 16),
            stack.trailingAnchor.constraint(equalTo: blur.trailingAnchor, constant: -14),
            stack.centerYAnchor.constraint(equalTo: blur.centerYAnchor),
            meter.heightAnchor.constraint(equalToConstant: 20)
        ])

        panel.contentView = blur
    }

    /// Switch between push-to-talk ("Listening…", red) and the continuous
    /// hands-free badge ("Hands-free", brand purple). Call before show().
    func setMode(handsFree: Bool) {
        label.stringValue = handsFree ? "Hands-free" : "Listening…"
        let accent = handsFree ? handsFreeAccent : holdAccent
        dot.layer?.backgroundColor = accent.cgColor
        meter.accent = accent
    }

    func show() {
        showToken += 1
        position()
        panel.orderFrontRegardless()  // never makeKey — must not steal focus
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.15
            panel.animator().alphaValue = 1
        }
    }

    func hide() {
        meter.level = 0
        let token = showToken
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.2
            panel.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            // A show() since this hide() began (e.g. double-tap → hands-free)
            // means we must stay visible — don't order out the live badge.
            guard let self = self, self.showToken == token else { return }
            self.panel.orderOut(nil)
        })
    }

    func update(level: Float) {
        meter.level = CGFloat(level)
    }

    private func position() {
        guard let screen = NSScreen.main else { return }
        let frame = screen.visibleFrame  // excludes the menu bar / notch inset
        let size = panel.frame.size
        panel.setFrameOrigin(NSPoint(x: frame.midX - size.width / 2,
                                     y: frame.maxY - size.height - 12))  // just below the menu bar / notch
    }
}

// MARK: - captured-correction toast

/// Builds a focus-safe, click-through HUD panel (shared by the toast).
func makeHUDPanel(width: CGFloat, height: CGFloat) -> NonActivatingPanel {
    let panel = NonActivatingPanel(
        contentRect: NSRect(x: 0, y: 0, width: width, height: height),
        styleMask: [.borderless, .nonactivatingPanel],
        backing: .buffered, defer: false
    )
    panel.level = .floating
    panel.isOpaque = false
    panel.backgroundColor = .clear
    panel.hasShadow = true
    panel.ignoresMouseEvents = true
    panel.hidesOnDeactivate = false
    panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
    panel.alphaValue = 0
    return panel
}

/// A self-dismissing "correction captured" chip. No clicks; never takes focus.
final class Toast {
    private let panel: NonActivatingPanel
    private let label: NSTextField
    private var pending: DispatchWorkItem?
    private let width: CGFloat = 220
    private let height: CGFloat = 40

    init() {
        panel = makeHUDPanel(width: width, height: height)
        let blur = NSVisualEffectView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        blur.material = .hudWindow
        blur.state = .active
        blur.wantsLayer = true
        blur.layer?.cornerRadius = 12
        blur.layer?.masksToBounds = true

        label = NSTextField(labelWithString: "")
        label.translatesAutoresizingMaskIntoConstraints = false
        label.font = .systemFont(ofSize: 12, weight: .medium)
        label.textColor = .labelColor
        label.alignment = .center
        label.backgroundColor = .clear
        label.isBezeled = false
        label.isEditable = false
        blur.addSubview(label)

        NSLayoutConstraint.activate([
            label.leadingAnchor.constraint(equalTo: blur.leadingAnchor, constant: 14),
            label.trailingAnchor.constraint(equalTo: blur.trailingAnchor, constant: -14),
            label.centerYAnchor.constraint(equalTo: blur.centerYAnchor)
        ])

        panel.contentView = blur
    }

    func show(_ message: String) {
        label.stringValue = message
        position()
        pending?.cancel()
        panel.orderFrontRegardless()  // never makeKey — must not steal focus
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.15
            panel.animator().alphaValue = 1
        }
        let work = DispatchWorkItem { [weak self] in self?.fadeOut() }
        pending = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5, execute: work)
    }

    private func fadeOut() {
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.25
            panel.animator().alphaValue = 0
        }, completionHandler: { [weak panel] in panel?.orderOut(nil) })
    }

    private func position() {
        guard let screen = NSScreen.main else { return }
        let frame = screen.visibleFrame  // excludes the menu bar / notch inset
        panel.setFrameOrigin(NSPoint(x: frame.midX - width / 2,
                                     y: frame.maxY - height - 52))  // below the pill's slot
    }
}
