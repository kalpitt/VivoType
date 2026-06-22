// VivoType brand asset generator — wordmark lockups + GitHub/web icons.
// Pure AppKit (no dependencies). Source artwork = the attached canonical icons.
// Run:  swift make_brand.swift
import AppKit

// ---- paths ----------------------------------------------------------------
let CANON = "/Users/kalpit/Documents/Voice/clients/mac/Assets/VivoType.iconset"
let MASTER = "\(CANON)/icon_512x512@2x.png"
let OUT = "/Users/kalpit/Documents/Voice/branding"

let fm = FileManager.default
for d in ["wordmark", "github", "web"] {
    try? fm.createDirectory(atPath: "\(OUT)/\(d)", withIntermediateDirectories: true)
}

guard let masterImg = NSImage(contentsOfFile: MASTER) else {
    FileHandle.standardError.write("ERROR: cannot load master icon\n".data(using: .utf8)!); exit(1)
}

// ---- helpers --------------------------------------------------------------
func bitmap(_ w: Int, _ h: Int) -> NSBitmapImageRep {
    let r = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    r.size = NSSize(width: w, height: h)
    return r
}
func draw(into rep: NSBitmapImageRep, _ body: () -> Void) {
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    body()
    NSGraphicsContext.current?.flushGraphics()
    NSGraphicsContext.restoreGraphicsState()
}
func save(_ rep: NSBitmapImageRep, _ path: String) {
    let data = rep.representation(using: .png, properties: [:])!
    try! data.write(to: URL(fileURLWithPath: path))
    print("  ✓ \(path)  (\(rep.pixelsWide)x\(rep.pixelsHigh))")
}
func font(_ names: [String], _ size: CGFloat, weight: NSFont.Weight = .semibold) -> NSFont {
    for n in names { if let f = NSFont(name: n, size: size) { return f } }
    return NSFont.systemFont(ofSize: size, weight: weight)
}
// sample a representative pixel color from an icon
func sample(_ path: String, _ fx: CGFloat, _ fy: CGFloat) -> NSColor {
    guard let img = NSImage(contentsOfFile: path),
          let tiff = img.tiffRepresentation,
          let bm = NSBitmapImageRep(data: tiff) else { return .black }
    let x = Int(CGFloat(bm.pixelsWide) * fx), y = Int(CGFloat(bm.pixelsHigh) * fy)
    return bm.colorAt(x: x, y: y) ?? .black
}

// ---- palette (sampled from canonical icons) -------------------------------
// sample interior (top-center, above the wave) — corners are transparent/rounded
let darkBG  = sample(MASTER, 0.50, 0.10)                                   // charcoal field
// Since the tinted icon isn't available in the iconset, we use a fallback accent color or sample from another part.
let accent  = NSColor(red: 0.55, green: 0.40, blue: 0.97, alpha: 1.0) // purple field fallback
let lightBG = NSColor.white
let inkDark = NSColor(white: 0.08, alpha: 1)
let inkLite = NSColor.white
func hex(_ c: NSColor) -> String { let r=c.usingColorSpace(.deviceRGB)!; return String(format:"#%02X%02X%02X", Int(r.redComponent*255),Int(r.greenComponent*255),Int(r.blueComponent*255)) }
print("Palette → darkBG \(hex(darkBG))  accent \(hex(accent))")

let S: CGFloat = 2  // supersample factor for crisp text

// ---- wordmark lockup ------------------------------------------------------
// Latin "VivoType" wordmark: icon + text on a transparent canvas.
enum BG { case dark, light, clear }
func lockup(bg: BG, path: String) {
    let H: CGFloat = 240 * S                 // canvas height
    let pad: CGFloat = 40 * S
    let iconH: CGFloat = H - pad*2
    let gap: CGFloat = 36 * S
    let ink = (bg == .light) ? inkDark : inkLite

    // build text
    let title = NSAttributedString(string: "VivoType", attributes: [
        .font: font(["Inter-SemiBold","Inter-Bold"], 120*S), .foregroundColor: ink])

    let titleSize = title.size()
    let textBlockW = titleSize.width
    let iconW = iconH * (masterImg.size.width / masterImg.size.height)
    let W = pad + iconW + gap + textBlockW + pad

    let rep = bitmap(Int(W), Int(H))
    draw(into: rep) {
        // wordmarks ship transparent; `bg` only selects ink for the target surface
        _ = lightBG
        // icon (rounded already in artwork)
        masterImg.draw(in: NSRect(x: pad, y: (H-iconH)/2, width: iconW, height: iconH))
        let tx = pad + iconW + gap
        title.draw(at: NSPoint(x: tx, y: (H - titleSize.height)/2))
    }
    save(rep, path)
}

print("\n[1/3] Wordmark lockups → branding/wordmark/")
lockup(bg: .dark,  path: "\(OUT)/wordmark/vivotype_on-dark.png")   // white ink, transparent
lockup(bg: .light, path: "\(OUT)/wordmark/vivotype_on-light.png")  // dark ink, transparent

// ---- banner (GitHub social 1280x640 / web og 1200x630) --------------------
func banner(w: Int, h: Int, path: String) {
    let rep = bitmap(w, h)
    let W = CGFloat(w), H = CGFloat(h)
    draw(into: rep) {
        // subtle vertical gradient on brand charcoal
        let g = NSGradient(colors: [darkBG.blended(withFraction: 0.10, of: .black) ?? darkBG,
                                    darkBG.blended(withFraction: 0.18, of: accent) ?? darkBG])!
        g.draw(in: NSRect(x:0,y:0,width:W,height:H), angle: -90)
        let iconH = H * 0.42
        let iconW = iconH * (masterImg.size.width/masterImg.size.height)
        let cx = W/2
        masterImg.draw(in: NSRect(x: cx - iconW/2, y: H*0.40, width: iconW, height: iconH))
        let title = NSMutableAttributedString()
        title.append(NSAttributedString(string: "VivoType", attributes: [.font: font(["Inter-SemiBold","Inter-Bold"], H*0.12), .foregroundColor: inkLite]))
        let ts = title.size()
        title.draw(at: NSPoint(x: cx - ts.width/2, y: H*0.20))
        // No Devanagari subtext
    }
    save(rep, path)
}
print("\n[2/3] Banners")
banner(w: 1280, h: 640, path: "\(OUT)/github/social-preview.png")   // GitHub repo social preview
banner(w: 1200, h: 630, path: "\(OUT)/web/og-image.png")            // website Open Graph

// ---- square web/github icons (downscaled master) --------------------------
print("\n[3/3] Square icons → branding/web + branding/github")
func square(_ size: Int, _ path: String) {
    let rep = bitmap(size, size)
    draw(into: rep) { masterImg.draw(in: NSRect(x:0,y:0,width:size,height:size)) }
    save(rep, path)
}
for s in [16,32,48,180,192,512] { square(s, "\(OUT)/web/favicon-\(s).png") }
square(512, "\(OUT)/github/icon-512.png")
print("\nDONE")
