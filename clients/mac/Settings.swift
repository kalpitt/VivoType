// VivoType — settings model. Mirrors core/config.json (shared with the Python
// backend) so the macOS UI and the ASR backend agree on model, hotkey, etc.

import Foundation

// MARK: - settings

/// Mirrors core/config.json (shared with the Python backend).
struct Settings {
    var model = "small.en"
    var hotkeyKeycode: UInt16 = 61
    var hotkeyLabel = "Right Option"
    var soundEnabled = true
    var toastEnabled = true

    static func load(from path: String) -> Settings {
        var s = Settings()
        guard let data = FileManager.default.contents(atPath: path),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return s }
        if let v = obj["model"] as? String { s.model = v }
        if let v = obj["hotkey_keycode"] as? Int { s.hotkeyKeycode = UInt16(v) }
        if let v = obj["hotkey_label"] as? String { s.hotkeyLabel = v }
        if let v = obj["sound_enabled"] as? Bool { s.soundEnabled = v }
        if let v = obj["toast_enabled"] as? Bool { s.toastEnabled = v }
        return s
    }

    func save(to path: String) {
        let obj: [String: Any] = [
            "model": model,
            "hotkey_keycode": Int(hotkeyKeycode),
            "hotkey_label": hotkeyLabel,
            "sound_enabled": soundEnabled,
            "toast_enabled": toastEnabled,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: obj,
                                                  options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
        }
    }
}
