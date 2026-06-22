// VivoType — transient foreground activation for setup / preferences windows.
//
// VivoType normally runs as a background menu-bar agent (.accessory): no Dock icon,
// never steals focus. That is essential — dictation injects text into whatever
// app you are using, so VivoType must stay in the background while another app is
// frontmost. But its setup, permissions, settings and review windows DO need to
// be real, activatable foreground windows.
//
// On macOS 14+ a background (.accessory) app cannot reliably bring its own
// window to the front (cooperative activation / focus-stealing prevention). That
// is why the permissions window dropped behind other apps after a system
// permission dialog dismissed: NSApp.activate(ignoringOtherApps:) is both
// deprecated and largely ignored for accessory apps.
//
// This coordinator flips the app to .regular while any such window is open and
// back to .accessory once the last one closes — so setup behaves like a normal
// app, then VivoType vanishes back into the menu bar. It is reference counted
// because several windows can be open at once (onboarding hands straight off to
// permissions), and the revert is deferred one runloop turn so that hand-off
// never flickers the Dock icon.

import AppKit

final class ActivationCoordinator {
    static let shared = ActivationCoordinator()
    private init() {}

    /// Number of currently-open foreground windows.
    private var openCount = 0

    /// Bring the app forward as a regular (Dock-visible) app for a window that is
    /// about to be shown. Each `begin()` must be balanced by exactly one `end()`.
    func begin() {
        openCount += 1
        if NSApp.activationPolicy() != .regular {
            NSApp.setActivationPolicy(.regular)
        }
        activate()
    }

    /// Re-assert foreground focus for an already-open window — e.g. after a system
    /// permission dialog dismissed and dropped us into the background. Does not
    /// touch the reference count.
    func refocus(_ window: NSWindow?) {
        activate()
        window?.makeKeyAndOrderFront(nil)
    }

    /// Balance a `begin()`. When the last foreground window closes, drop back to
    /// the menu-bar agent policy. The check is deferred to the next runloop turn
    /// so a close-then-immediately-open hand-off (onboarding → permissions) never
    /// flickers the Dock icon: the follow-up `begin()` runs first and keeps the
    /// count above zero.
    func end() {
        openCount = max(0, openCount - 1)
        DispatchQueue.main.async {
            if self.openCount == 0 { NSApp.setActivationPolicy(.accessory) }
        }
    }

    /// Modern, non-deprecated foreground activation (macOS 14+), with a fallback
    /// for older systems that predate `NSApplication.activate()`.
    private func activate() {
        if #available(macOS 14.0, *) {
            NSApp.activate()
        } else {
            NSApp.activate(ignoringOtherApps: true)
        }
    }
}
