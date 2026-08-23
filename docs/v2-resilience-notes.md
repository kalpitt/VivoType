# v2 resilience notes — field bugs, root causes, and the defenses now in place

Written 2026-07-12 during the v2 overhaul session. Companion to the commit
history on `claude/app-v2-overhaul-bugs-1934ae`; kept as a doc because these
failure modes are endemic to Whisper dictation apps and WILL come up again.

## The four field bugs and what actually caused them

| Bug | Root cause | Defenses shipped |
|---|---|---|
| Pressing the key silently types "VivoType, menu" | `buildInitialPrompt()` fed learned dictionary VALUES to Whisper; on silence Whisper echoes its prompt. The prompt was literally the user's two learned values | 1. Silence gate (`core/audioio.has_speech`) — silent clips never reach the model. 2. Prompt-echo guard (daemon) — a transcript that IS the prompt is dropped even at low `no_speech_prob`. 3. Prompt hygiene (App.swift) — only proper-noun-shaped terms enter the prompt |
| "guilty guilty guilty …" spam | Whisper decoder repetition loop on noisy/marginal audio, amplified by segment-context feedback | `condition_on_previous_text=False`; `collapse_repetitions()` in postprocess (3+ consecutive repeats of a 1–4-word unit collapse to one) |
| Menu bar stuck on ERROR until force-quit | Installed build predated PR #6 daemon resilience; bare "Error" with no cause and no recovery path | Error detail in the menu (hover for full text); "Restart speech engine" menu action; 5 s auto-recovery from transient errors |
| Learn loop promoted garbage ('minu → menu', '7 → driven') | No quality gate between a clipboard edit and an active rule | Capture filter (digit/single-char swaps dropped); `assess_risk()` flags common-English-word sources and single sightings; bulk promotion refuses risky rules; Review UI shows ⚠ + reason |

## Why layered defense (not one fix)

High-confidence hallucinations can have LOW `no_speech_prob` and HIGH
`avg_logprob` — no single threshold catches them (see arxiv 2501.11378).
The layers are ordered by cost: measure the audio first (free, kills the
whole class), then decode conservatively, then filter the text.

## Research-validated patterns already in the codebase

- Clipboard save/restore around paste injection (Superwhisper's top complaint).
- Reinstall-aware Accessibility recovery (TCC keys grants to the code
  signature; System Settings lies when it goes stale).
- Config-change cancellation mid-recording (AirPods renegotiate the input to
  8–16 kHz SCO when the mic engages; appending mismatched buffers corrupts
  the clip).
- Secure-input detection before injection (new in v2): synthetic keystrokes
  AND ⌘V vanish silently under secure event input; text is parked on the
  clipboard (ConcealedType-marked, auto-cleared in 60 s) and excluded from
  correction learning.

## Deliberately NOT done in v2 (candidates for later — propose via ROADMAP)

1. ~~**Guided practice dictation in onboarding**~~ — shipped on `feature/onboarding-delight`
   (practice screen after daemon ready, real hotkey → in-window field).
2. ~~**Daemon health-check watchdog**~~ — shipped 2026-07-18 (`Daemon.swift`):
   consecutive request failures → SIGTERM/SIGKILL → respawn, capped independently
   of the crash-restart one-shot. Manual "Restart speech engine" remains for
   budget exhaustion. GUI end-to-end verification on real hardware still worth a
   pass (see `context/handoffs/2026-07-18-daemon-watchdog-and-next-plan.md`).
3. **Silero VAD** as a smarter gate than the RMS energy gate — would need a
   new dependency (allowlist gate) for marginal gain over the current stack.
4. **✨ marker on auto-learned dictionary entries** (attribution, Wispr Flow
   pattern) and spoken meta-commands ("press enter").

Full research dump (issue links, sources): generated in-session from public
trackers of superwhisper/VoiceInk/whisper.cpp/OpenWhispr and Wispr Flow docs;
key sources cited inline above where load-bearing.
