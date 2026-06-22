// VivoType — the app "mark" shown as a logo in the first-run windows.
//
// `makeVivoTypeMark(size:)` prefers the real AppIcon (see below). As a fallback it
// draws a small native mark: an accent-tinted rounded square with the `waveform`
// glyph (the same identity the menu-bar icon uses), built entirely from system
// colors so it follows the user's accent and appearance.

import AppKit

final class BrandMarkView: NSView {
    init(size: CGFloat) {
        super.init(frame: NSRect(x: 0, y: 0, width: size, height: size))
        wantsLayer = true
        layer?.cornerRadius = size * 0.28      // squircle-ish, like a macOS icon
        layer?.masksToBounds = true

        // Subtle top-down accent gradient so the mark reads as an icon, not a chip.
        let accent = NSColor.controlAccentColor
        let gradient = CAGradientLayer()
        gradient.frame = bounds
        gradient.colors = [
            (accent.blended(withFraction: 0.22, of: .white) ?? accent).cgColor,
            accent.cgColor,
        ]
        gradient.startPoint = CGPoint(x: 0.5, y: 0)
        gradient.endPoint = CGPoint(x: 0.5, y: 1)
        layer?.addSublayer(gradient)

        let glyphSize = size * 0.5
        let glyph = NSImageView(frame: NSRect(x: (size - glyphSize) / 2,
                                              y: (size - glyphSize) / 2,
                                              width: glyphSize, height: glyphSize))
        if let image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "VivoType") {
            let config = NSImage.SymbolConfiguration(pointSize: glyphSize, weight: .semibold)
            glyph.image = image.withSymbolConfiguration(config)
        }
        glyph.contentTintColor = .white
        glyph.imageScaling = .scaleProportionallyUpOrDown
        addSubview(glyph)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}

/// Returns the brand mark to show in first-run windows.
///
/// Now that VivoType ships a real AppIcon (`Assets/VivoType.iconset` → `AppIcon.icns`,
/// wired in `build_app.sh` + `Info.plist`), prefer the actual app icon so the
/// onboarding/permissions windows match Finder and the Dock. If the bundle has
/// no icon for any reason, fall back to the drawn `BrandMarkView` so these
/// windows never show an empty space.
func makeVivoTypeMark(size: CGFloat, rim: Bool = false) -> NSView {
    if let icon = NSApp.applicationIconImage, icon.isValid {
        let view = NSImageView(frame: NSRect(x: 0, y: 0, width: size, height: size))
        view.image = icon
        view.imageScaling = .scaleProportionallyUpOrDown
        // On the dark onboarding background the glossy app icon's edge "shimmer"
        // (the liquid-glass rim highlight) reads as distracting. When `rim` is set,
        // overpaint that edge with a thin white border — matching the wave colour
        // and the menu-bar icon — so the mark gets a crisp edge instead. Opt-in, so
        // only the onboarding window uses it. This never touches AppIcon.icns, so
        // Finder / Dock / the app switcher / notifications keep the pristine icon.
        if rim {
            view.wantsLayer = true
            let lw = size * 0.02                      // ≈ the "T4" weight (4px @ 200pt)
            let inset = lw / 2
            let rect = view.bounds.insetBy(dx: inset, dy: inset)
            let radius = (size - 2 * inset) * 0.2237  // macOS squircle corner ratio
            let rimLayer = CAShapeLayer()
            rimLayer.frame = view.bounds
            rimLayer.path = CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil)
            rimLayer.fillColor = NSColor.clear.cgColor
            rimLayer.strokeColor = NSColor.white.cgColor
            rimLayer.lineWidth = lw
            view.layer?.addSublayer(rimLayer)
        }
        return view
    }
    return BrandMarkView(size: size)
}
