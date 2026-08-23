# VivoType — Competitive Landscape & Prioritized Roadmap

*Research pass: 2026-07-18. Synthesis of current (2026) product research across Wispr Flow,
Willow Voice, Superwhisper, MacWhisper, VoiceInk, Aqua Voice, Talon Voice, and Apple's
built-in macOS Dictation — feature sets, pricing, architecture (local vs. cloud), and UI/UX
implementation. Gathered via three parallel research passes + one synthesis pass (Opus,
high effort, per the judgment-tier model routing rule for taste-critical prioritization).
This document is a **proposal** — ROADMAP.md stays human-owned; see "ROADMAP fit" at the
bottom for suggested placement.*

> **Status update 2026-08-23:** the analysis below is preserved as research, but parts of
> it have shipped since: all of Tier 1 (trust surface) and the first two Tier 2 items
> (per-app profiles "Contexts", bounded voice-editing commands). The comparison matrix
> rows for voice editing and per-app profiles are therefore out of date — VivoType is no
> longer Absent/Weak there. Tier 2's "What can I say?" card and history search, and all of
> Tier 3 (local-LLM Polish mode, Hindi/Hinglish), remain open.

## 1. Executive framing

VivoType's wedge is the intersection of three things no single competitor holds together: an
**absolute, no-caveats local-only guarantee**, a **genuine consumer product surface** (guided
onboarding, HUD, correction-learning, brand), and a **built-in Indic text layer**. Split the
field cleanly. The **cannot-match** competitors — Wispr Flow, Willow Voice, and Aqua Voice —
are architecturally excluded from ever truthfully saying "audio never leaves your Mac": Wispr
and Aqua are cloud-only, Willow's offline mode is an explicit degraded fallback. That is a
permanent moat against the three best-funded, best-designed players. The **must-match-or-beat**
field is the local-capable set — Superwhisper, VoiceInk, MacWhisper, Talon, and Apple Dictation
— where privacy is *not* a differentiator and VivoType must win on product. The nearest threat
is **VoiceInk** (open-source, local, cheap lifetime, weekly releases, per-app *and* per-website
profiles) with **Superwhisper** close behind on power-user features. The single biggest risk if
VivoType stands still: the local field has already moved dictation past "type what I said" into
"produce finished text, in the right tone, for the right app" (per-app profiles, local-LLM
cleanup, voice-editing), while VivoType stops at raw transcription plus a dictionary plus
global Indic rules. Privacy alone doesn't produce *better text* — and on Apple Silicon, Apple's
own free Dictation is also local. If VivoType does nothing, its moat stays real but invisible,
and it becomes "the nicely-onboarded local app that edits worse than free Apple Dictation and
does less than VoiceInk."

## 2. Feature comparison matrix

Rated from a user's point of view: **Strong / Adequate / Weak / Absent**.

| Capability (user-facing) | VivoType | Wispr Flow | Willow | Superwhisper | MacWhisper | VoiceInk | Aqua | Talon | Apple Dictation |
|---|---|---|---|---|---|---|---|---|---|
| Activation model | Strong | Strong | Strong | Strong | Adequate | Adequate | Strong | Adequate | Adequate |
| Correction / editing-by-voice | Weak | Strong | Adequate | Adequate | Weak | Adequate | **Strong** | Strong | Weak |
| Personal vocabulary / dictionary | Strong | Strong | Strong | Strong | Weak | Strong | Adequate | Adequate | Weak |
| Per-app tone/format adaptation | **Absent** | Strong | Strong | Strong | Absent | **Strong** | Strong | Adequate | Absent |
| Multi-language dictation | **Weak** | Strong | Strong | Strong | Strong | Strong | Strong | Weak | Strong |
| History / searchable archive | **Absent** | Strong | Adequate | Strong | **Strong** | Adequate | Weak | Weak | Absent |
| Onboarding quality | **Strong** | Strong | Adequate | Adequate | Weak | Adequate | Weak | Weak | Weak |
| HUD / indicator design | Strong | Strong | Adequate | Strong | Weak | Adequate | Adequate | Weak | Weak |
| Discoverability of power features | Weak | Adequate | Weak | Adequate | Adequate | Adequate | Weak | **Strong** | Weak |
| Model / speed choice | Adequate | Weak | Weak | **Strong** | Strong | Strong | Adequate | Adequate | Absent |
| Pricing / access model | **Strong** | Weak | Weak | Adequate | Strong | Strong | Weak | Strong | Strong |
| Local-only guarantee | **Strong** | Absent | Weak | Adequate | Adequate | Strong | Absent | Strong | Adequate |

**Honest read:** VivoType is Strong on privacy, pricing, onboarding, dictionary, and HUD — and
visibly behind on the things that turn dictation into finished text: **Absent** in per-app
adaptation and history, **Weak** in voice-editing, multi-language, and discoverability. Those
four cells are the whole strategic to-do list.

## 3. UX pattern inventory — best-in-class per theme

- **Onboarding & first-run — Wispr Flow.** Guided live practice against a "Say something!"
  prompt that *teaches the behavior* instead of narrating it. VivoType already adapted this
  (the "Try it once" rep); the remaining steal is Wispr's explicit *privacy-preferences step*
  inside the flow — turn the local guarantee into an onboarding beat, not a buried setting.
- **Activation & recording indicator — Wispr Flow (Flow Bar).** Edge-snapping, reorienting,
  four sizes, and critically **suppressing all error/reconnect UI mid-recording** so flow state
  never breaks. Runner-up worth naming: Superwhisper's **"context-captured" light**.
- **Correction & voice-editing — Aqua Voice.** Mode-less conversational editing ("make this a
  bullet list," "formal tone") spoken against your own last utterance — no fixed grammar to
  memorize. This is the single most transferable idea in the entire set for VivoType.
- **Personalization & vocabulary — VoiceInk.** Single-file settings export/import bundling
  dictionary + modes + prompts as a portable local profile — a clean backup / multi-machine
  story with zero cloud. (Superwhisper's CSV bulk vocab import is the runner-up.)
- **Discoverability of power features — Talon.** In-band spoken self-discovery ("help
  alphabet," command history) teaches the grammar without leaving the flow.
- **Settings & trust/transparency — Apple Dictation (as inverse lesson) + Superwhisper.** Apple
  *is* local on Apple Silicon but never says so; VivoType should make "always fully local" an
  explicit, checkable statement. Pair with Superwhisper's context-capture light: show the user
  what the app actually read.
- **Sound & motion design — Superwhisper.** The option to run **HUD-less with only start/stop
  sound cues** — motion restraint as a feature. Combined with Wispr's rule of never flashing
  error UI while the user is mid-utterance.

## 4. Prioritized "build now" list

### Tier 1 — Quick wins (days, `clients/mac` Swift, no ML)

1. **Explicit "always fully local" trust surface + live network indicator.** *(Inspired by
   Apple Dictation, inverse lesson.)* A plainly-worded, always-visible line in Settings —
   "100% on-device. Audio and text never leave this Mac. The only network call ever is the
   one-time model download." — plus a small live "Network: idle" state. Converts the invisible
   moat into felt value; it's the one claim Wispr/Willow/Aqua literally cannot make.
   **Layer:** `clients/mac` (`SettingsController.swift`). Highest leverage-to-effort item in
   the brief.
2. **Plain-language model ladder.** *(Superwhisper.)* Relabel the shipped Settings /
   menu models (`tiny.en` / `small.en`) as **Fast / Balanced** (English); keep the
   model IDs underneath. `medium.en` ("Most accurate") is a valid Whisper size the
   backend can load via `asr.repo_for()`, but it is **not** in the UI today — add it
   only when explicitly productizing that rung. **Layer:** `clients/mac` label change
   over existing `core/config.py` values. Removes model-jargon from a consumer surface
   for near-zero cost.
3. **Surface the correction-learning loop with a pending-corrections badge.** *(Novel —
   activates a feature already built.)* A menu-bar badge showing count of captured corrections
   awaiting Promote, with one-click access to the existing Review panel. Makes an underused,
   already-shipped differentiator discoverable. **Layer:** `clients/mac`
   (`ReviewController.swift`, menu-bar item).
4. **HUD-less "sound-only" mode + guarantee no error glyph mid-recording.** *(Superwhisper +
   Wispr.)* A toggle to suppress the pill and rely on start/stop cues, and a rule that no
   error/status glyph ever paints while the hotkey is held. **Layer:** `clients/mac`
   (`HUD.swift`, `ActivationCoordinator.swift`).
5. **Portable single-file profile export/import.** *(VoiceInk.)* Bundle the existing
   dictionary, contacts lexicon, and `postprocess_config.json` into one file a user can back up
   or move between Macs — no cloud. **Layer:** `clients/mac` file picker over `core/` config
   files (`core/paths.py`). Mostly plumbing, no ML.

### Tier 2 — High-impact, bounded, fully local

1. **Per-app profiles ("Contexts").** *(VoiceInk Power Mode / Superwhisper per-app modes /
   Wispr Styles.)* Detect the frontmost app (NSWorkspace) and map it to a profile that
   selects which **post-processing rules** apply — e.g. a "Code" profile disables ₹/lakh/crore
   conversion and filler-stripping; a "Messages" profile stays casual. **Do not switch the ASR
   model per app** (ADR-0002: one warm model). See `docs/next-features-handoff.md`.
   **User outcome:** correct formatting per destination with zero manual switching.
   **Layer:** `clients/mac` for detection + picker; `core/` for the profile→rule config
   (extend the already-config-driven `postprocess_config.json` + `config.py`). This closes the
   most glaring matrix gap (per-app adaptation: Absent) and is the substrate Tier 3 tone/LLM
   work will attach to. *Highest impact in this tier.*
2. **Bounded local voice-editing command set.** *(Aqua's conversational editing, scoped like
   Talon's "scratch that.")* A small, default-on set of **deterministic** commands recognized
   in the utterance — "scratch that" (delete last insertion), "new paragraph," "make that a
   bullet list," "all caps that," "cap that." No LLM required; pure text transforms. **User
   outcome:** fix and format hands-free. **Layer:** `core/` (new `core/commands.py` or extend
   `postprocess.py` for parsing/transform) + `clients/mac` (`Dictation.swift` for undo/
   injection). Moves "editing-by-voice" from Weak to Adequate and pairs naturally with the
   correction-learning story.
3. **In-band "What can I say?" card.** *(Talon.)* Once commands exist, a hotkey-summoned quick
   card listing the handful of available commands + current active profile. Cheap once Tier 2
   #2 lands. **Layer:** `clients/mac` overlay reading the command list from `core/`.
4. **Local transcript history (opt-in, searchable).** *(Superwhisper / MacWhisper.)* Persist
   transcriptions on-disk in Application Support with a simple searchable window; also feeds
   "re-insert last." **User outcome:** recall and reuse past dictations. **Layer:** `core/`
   store + `clients/mac` History window (model it on `ReviewController.swift`). **Ship opt-in
   with a working auto-delete** — do not repeat VoiceInk's broken "auto-delete" or
   Superwhisper's save-by-default complaint.

### Tier 3 — Bigger bets (multi-phase, local model)

1. **Local LLM text-cleanup / tone-adaptation ("Polish mode").** *(Wispr Styles / Willow Scribe
   / Superwhisper Super Mode / VoiceInk AI Enhancement — but strictly local.)* An **on-device**
   small instruct model (quantized, via MLX-LM or llama.cpp) that rewrites raw transcription to
   a chosen tone per profile, as an **explicit, inspectable, toggleable second pass** — never
   silent (MacWhisper's visible-cleanup lesson). This is the feature that turns "transcribe what
   I said" into "write what I meant" while staying 100% local — beating cloud apps (they can't
   be private) *and* local apps (few ship a genuinely-local rewrite well). **Layer:** `core/`
   (new local-LLM module). **Gate:** adding a local LLM runtime + a GGUF/MLX model is a new
   dependency and packaging change → **requires explicit dep approval (Hard Constraint #4) and
   an ADR** before starting; validate RAM/latency first. Attach it to the Tier-2 profile
   substrate.
2. **Multilingual dictation, Hindi/Hinglish first.** *(Competitors' 100+ languages is a real
   gap; VivoType ships `.en` only.)* Whisper multilingual models run locally. This leans
   directly into the India wedge: **private Hindi/Hinglish dictation is something no
   cloud-averse Indian user can get anywhere else** — cloud apps do multilingual but not
   privately; local apps do multilingual but don't tune Indic. **Layer:** `core/` (`asr.py`
   model handling, `config.py`) + `clients/mac` language picker. Multi-phase for model size,
   latency, and Indic accuracy tuning — but the highest-*differentiation* bet in the brief.
3. *(Conditional)* **Deeper composable command grammar** (Talon-lite formatters/ordinals) —
   only if usage signals show the Tier-2 command set gets heavy use. Low priority; noted for
   completeness.

### Explicitly not now / against the grain

- **Any cloud path — permanently disqualified,** not deprioritized (Wispr/Aqua ASR, cloud
  Command Mode, cloud LLM rewrite). Hard Constraint #1.
- **BYO-key cloud enhancement** (offered by Superwhisper, VoiceInk, MacWhisper). Even
  *optional* cloud muddies the one absolute claim VivoType owns and Wispr/Willow/Aqua cannot.
  The clarity of the guarantee is worth more than the feature — decline it. This discipline is
  what makes the wedge legible.
- **Subscriptions / degrading free tiers / word caps** (Wispr, Willow, Aqua). Against the
  MIT-free identity; it would nuke the goodwill moat that a solo open-source project runs on.
  Stay free.
- **Full Talon-style scripting platform.** Wrong audience — Talon's candid "first two weeks are
  slow and frustrating" is exactly what VivoType's onboarding exists to defeat. Take the
  *discoverability* idea, not the complexity.
- **Recordings/history saved by default.** If history ships, opt-in only, with an auto-delete
  that actually works — fix what VoiceInk got wrong rather than inherit it.
- **File-transcription / meeting-IDE workspace** (MacWhisper's core). A different product.
  Don't get pulled into being a transcription IDE; stay a dictation tool.
- **Always-on persistent floating pill** (Superwhisper). VivoType's transient-only pill is a
  deliberate, better-for-focus choice — offer the HUD-less mode instead of a persistent window.

## 5. Closing recommendation — the three to greenlight

If Kalpit can approve only three before the next ROADMAP review, pick for **sequencing and
compounding**, not raw score: **(1) the Tier-1 trust surface, (2) Tier-2 per-app profiles,
(3) the Tier-2 voice-editing command set** — see section 4 for what each one is and why it
matters on its own. The sequencing case, not repeated from section 4: profiles are the
*substrate* the eventual local-LLM Polish mode must attach to, so building that plumbing now
pre-scaffolds the Tier-3 crown jewel rather than leaving it to be retrofitted later. Hold the
local-LLM Polish and Hinglish bets deliberately — both need an ADR and dependency approval, and
Polish specifically needs the profile substrate first (greenlighting the LLM before profiles
would be building the roof before the walls). Propose those two onto ROADMAP via handoff as
"Later" candidates pending an ADR — don't start them.

## ROADMAP fit (proposal only — ROADMAP.md is human-owned)

- **Now:** Tier-1 items are pure polish that fit the current active workstream — no new
  workstream needed.
- **Next:** Per-app profiles + the bounded voice-editing command set, after the daemon
  watchdog.
- **Later:** Local-LLM Polish mode and multilingual/Hinglish dictation — both require an ADR
  and dependency sign-off before any work begins.

One genuine gap with no viable local substitute worth accepting: Wispr/Willow's effortless
100+-language coverage at cloud scale. VivoType's local answer is narrower (Hinglish-first),
and that's the right trade for this product's identity.
