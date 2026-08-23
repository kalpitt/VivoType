# ROADMAP — VivoType

Last reviewed by Kalpit: 23 aug 2026 (post-#36)

## Now (this week — the only active workstream)

## Next (after Now ships)


## Later (parked — do not start without moving it up)

- Liquid Glass `.icon` (Icon Composer) for macOS 26; editable vector master;
  finalized wordmark in Inter
- **Local LLM "Polish mode"** (tone/formatting rewrite, strictly on-device) — needs an ADR +
  dependency approval before starting; depends on per-app profiles landing first.
  See competitive-landscape.md Tier 3.
- **Hindi/Hinglish multilingual dictation** — needs an ADR + model-size/latency validation
  before starting. See competitive-landscape.md Tier 3.

## Not doing (decided against — don't re-propose)

- namematch diacritic folding — assessed 2026-07-04, no user-felt gain
- Any cloud API path, ever — Hard Constraint #1

## Shipped recently (cleared from Now — do not re-open)

- Daemon health-check watchdog (hung-alive recovery) — 2026-07-18
- Governance-migration §2b validation run — 2026-07-18 (Edit/Write guard
  harness gap still open; see 2026-07-18 handoff)
