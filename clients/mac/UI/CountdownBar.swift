// VivoType — a thin bar along the bottom of a floating card that empties as
// the card's time runs out, so a card that will disappear on its own says so.
// It freezes where it is on pause (the pointer is on the card) and finishes
// from there on resume. Reusable by any self-dismissing card.

import AppKit
import QuartzCore

final class CountdownBar: NSView {
    private let fill = CALayer()
    /// How much of the bar is left (1 = full), as of the last pause/reset.
    private var fraction: CGFloat = 1

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        fill.anchorPoint = CGPoint(x: 0, y: 0.5)  // shrink toward the left edge
        layer?.addSublayer(fill)
    }

    required init?(coder: NSCoder) { nil }

    override var wantsUpdateLayer: Bool { true }

    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        fill.bounds = CGRect(x: 0, y: 0, width: bounds.width, height: bounds.height)
        fill.position = CGPoint(x: 0, y: bounds.midY)
        CATransaction.commit()
    }

    /// Full and still, in `color` (resolved now, so it follows light/dark).
    func reset(color: NSColor) {
        fill.removeAllAnimations()
        fraction = 1
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        fill.backgroundColor = color.cgColor
        fill.transform = CATransform3DIdentity
        CATransaction.commit()
    }

    /// Empty the rest of the bar over `duration` seconds, from where it is.
    func run(_ duration: TimeInterval) {
        let from = currentFraction()
        fill.removeAllAnimations()
        fraction = from
        let animation = CABasicAnimation(keyPath: "transform.scale.x")
        animation.fromValue = from
        animation.toValue = 0
        animation.duration = duration
        animation.timingFunction = CAMediaTimingFunction(name: .linear)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        fill.transform = CATransform3DMakeScale(0, 1, 1)  // the end state
        CATransaction.commit()
        fill.add(animation, forKey: "countdown")
    }

    /// Freeze the bar where it is right now.
    func pause() {
        let now = currentFraction()
        fill.removeAllAnimations()
        fraction = now
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        fill.transform = CATransform3DMakeScale(now, 1, 1)
        CATransaction.commit()
    }

    private func currentFraction() -> CGFloat {
        guard fill.animation(forKey: "countdown") != nil,
              let shown = fill.presentation()?.value(forKeyPath: "transform.scale.x") as? CGFloat
        else { return fraction }
        return shown
    }
}
