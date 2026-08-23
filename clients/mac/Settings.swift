// VivoType — settings model. Mirrors core/config.json (shared with the Python
// backend) so the macOS UI and the ASR backend agree on model, hotkey, etc.

import Foundation

// MARK: - model catalog

/// One selectable ASR model: what the user sees, and what the backend is told.
struct ModelOption {
    let label: String
    let id: String
}

/// The single source of truth for which models the UI offers. Both the Settings
/// popup and the menu-bar Model submenu read this — they used to carry two
/// independently-hardcoded lists that could drift apart. Labels are
/// plain-language; `id` is what the daemon loads.
///
/// `medium.en` is intentionally NOT offered here. It is a valid `--model` value
/// for the CLI (see README), but ROADMAP.md scopes the shipped ladder to
/// Fast/Balanced and calls medium "a later ladder rung, not in Settings today":
/// picking it from a menu would kick off a multi-gigabyte download with no
/// warning UI. Adding it later is one line — that's the point of this type.
enum ModelCatalog {
    static let all: [ModelOption] = [
        ModelOption(label: "Fast (tiny)",      id: "tiny.en"),
        ModelOption(label: "Balanced (small)", id: "small.en"),
    ]

    /// Display label for a stored model id; falls back to the raw id so a
    /// hand-edited config.json still renders something meaningful.
    static func label(for id: String) -> String {
        all.first { $0.id == id }?.label ?? id
    }

    /// Index of a stored model id, or nil if it isn't one we offer.
    static func index(of id: String) -> Int? {
        all.firstIndex { $0.id == id }
    }
}

// MARK: - settings

/// Mirrors core/config.json (shared with the Python backend).
struct Settings {
    var model = "small.en"
    var hotkeyKeycode: UInt16 = 61
    var hotkeyLabel = "Right Option"
    var soundEnabled = true
    var toastEnabled = true
    /// False = sound-only: suppress the on-screen recording pill (not the ⚠ toast).
    var hudEnabled = true
    /// Per-app post-processing contexts: frontmost bundle ID -> profile name
    /// from postprocess_config.json's "profiles". MUST round-trip through
    /// load/save below — save() rewrites config.json wholesale, so a key it
    /// doesn't know would be silently erased on the next settings change.
    var appProfiles: [String: String] = [:]

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
        if let v = obj["hud_enabled"] as? Bool { s.hudEnabled = v }
        // Coerce per-entry, not with one all-or-nothing cast: a single
        // hand-edited non-string value must drop only that entry, not wipe
        // every mapping on the next save.
        if let raw = obj["app_profiles"] as? [String: Any] {
            for (bundleID, profile) in raw {
                if let profileName = profile as? String { s.appProfiles[bundleID] = profileName }
            }
        }
        return s
    }

    func save(to path: String) {
        let obj: [String: Any] = [
            "model": model,
            "hotkey_keycode": Int(hotkeyKeycode),
            "hotkey_label": hotkeyLabel,
            "sound_enabled": soundEnabled,
            "toast_enabled": toastEnabled,
            "hud_enabled": hudEnabled,
            "app_profiles": appProfiles,
        ]
        if let data = try? JSONSerialization.data(withJSONObject: obj,
                                                  options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
        }
    }
}
