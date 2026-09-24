// VivoType — number-key shortcuts for a floating card ("1" Remember,
// "2" Not now / Undo). Registered with the system only while the card is on
// screen, and only for those exact keys: VivoType is told when one of them is
// pressed and never sees any other keystroke. While registered, the key goes
// to the card instead of the frontmost app.

import Carbon.HIToolbox

final class CardShortcuts {
    /// The keys a card can offer. Raw values are the hot-key ids.
    enum Key: UInt32, CaseIterable {
        case one = 1, two = 2

        var keyCode: UInt32 {
            switch self {
            case .one: return UInt32(kVK_ANSI_1)
            case .two: return UInt32(kVK_ANSI_2)
            }
        }
    }

    /// Main thread: a registered key was pressed.
    var onPress: ((Key) -> Void)?

    private var registered: [Key: EventHotKeyRef] = [:]
    private var handler: EventHandlerRef?
    private static let signature: OSType = 0x5654_4B59  // 'VTKY'

    init() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                 eventKind: UInt32(kEventHotKeyPressed))
        let context = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(GetApplicationEventTarget(), { _, event, context in
            guard let event = event, let context = context else { return OSStatus(eventNotHandledErr) }
            var id = EventHotKeyID()
            let status = GetEventParameter(event, EventParamName(kEventParamDirectObject),
                                           EventParamType(typeEventHotKeyID), nil,
                                           MemoryLayout<EventHotKeyID>.size, nil, &id)
            guard status == noErr, id.signature == CardShortcuts.signature,
                  let key = Key(rawValue: id.id) else { return OSStatus(eventNotHandledErr) }
            let shortcuts = Unmanaged<CardShortcuts>.fromOpaque(context).takeUnretainedValue()
            DispatchQueue.main.async { shortcuts.onPress?(key) }
            return noErr
        }, 1, &spec, context, &handler)
    }

    deinit {
        set([])
        if let handler = handler { RemoveEventHandler(handler) }
    }

    /// Register exactly `keys` (no modifiers); unregister the rest. A key
    /// another app already owns fails to register and simply isn't offered.
    func set(_ keys: Set<Key>) {
        for (key, ref) in registered where !keys.contains(key) {
            UnregisterEventHotKey(ref)
            registered[key] = nil
        }
        for key in keys where registered[key] == nil {
            var ref: EventHotKeyRef?
            let id = EventHotKeyID(signature: Self.signature, id: key.rawValue)
            if RegisterEventHotKey(key.keyCode, 0, id, GetApplicationEventTarget(), 0, &ref) == noErr,
               let ref = ref {
                registered[key] = ref
            }
        }
    }

    /// Whether `key` is live right now (its badge is shown only if so).
    func isActive(_ key: Key) -> Bool { registered[key] != nil }
}
