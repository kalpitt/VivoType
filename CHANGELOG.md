# Changelog

What changed in each VivoType release, in plain words. Everything still runs
100% on your Mac: no cloud, no account, no audio or text ever leaves it.

## [v0.3.0] — 2026-09-24

The biggest update since the first release: VivoType now learns from your
fixes, works fully offline once set up, and got a long list of reliability
and privacy fixes.

### New

- **VivoType learns from the words you fix.** Fix a word VivoType typed (say
  "Kalpith" → "Kalpit") and pause: a small card appears just above the line
  asking **"Always write “Kalpit”?"**.
  - **Remember** (or press **1**) and the next dictation gets it right. The card
    turns green — "Got it" — with **Undo** (or press **2**) for 8 seconds.
  - **Not now** (or press **2**), or simply ignoring the card for 20 seconds,
    saves the fix to **Review** for later and changes nothing.
  - A thin bar along the bottom shows how long the card stays; hovering it
    pauses the countdown.
  - Fixing a common word (like "there" → "their") shows a warning first,
    because a rule would change that word everywhere.
  - Off by default: **Settings → Suggest corrections after edits**. Works in
    apps that let VivoType read their text fields (TextEdit, Safari and most
    standard apps). Apps that autocorrect dictated text themselves, such as
    Notes, may not show the card.
- **Manage your rules in Review.** **Review corrections…** now has a **Your
  rules** section listing every word rule and learned name, each with
  **Delete**. Your contacts are never deleted.
- **Per-app contexts.** Give different apps different clean-up rules — for
  example currency conversion on in Messages but off in your code editor.
  Settings → Contexts → **Add app…**, then pick **Standard clean-up** or
  **Code** for it.
- **Voice commands** — "scratch that" deletes what VivoType just typed; "new
  line", "new paragraph", "cap that", "all caps that" and "make that a bullet
  list" act on what you just said. **Off by default** (Settings → Voice
  commands), so these phrases are typed as words unless you turn it on.
- **Guided first run** with a "Try it once" practice screen, and a guided
  Python setup.
- **Backup & restore** your settings and dictionary in one file (contact names
  only if you opt in).
- **Privacy card** in Settings showing processing is local and any network
  activity live ("Idle" / "Downloading model…").
- **Recording indicator and sounds, your choice:** show or hide the on-screen
  recording pill, and turn start/stop sounds on or off — separately.
- **Clearer model choices:** "Fastest" (tiny) or "Most accurate" (small) — both are fast.

### Better dictation

- **Proper punctuation and capitals** on long, rambling dictation.
- **Names:** the contact-name matcher no longer turns everyday words into names
  ("Okay" no longer becomes "Kay", "Pune" stays "Pune").
- **Spoken words stay words:** saying "comma", "period" or "mark" types the
  word, and fixes involving such words can be learned.
- **Indian English and Hinglish:** better ₹ / lakh / crore conversion ("$100k"
  → "₹1 crore", "Rs 500" → "₹500"), punctuation, and spacing around Devanagari.
- **Cleaner output:** fillers ("um", "uh") removed without leaving stray
  punctuation; code, URLs and file names are left untouched; repeated words
  from a stuck model ("guilty guilty guilty") are collapsed.
- **No phantom text:** silence and background noise no longer type the model's
  own prompt ("VivoType, menu").
- A 7-second slowdown on very long dictations is gone.

### Reliability

- **Fully offline once set up.** Loading the speech model no longer contacts
  the internet, and a half-finished model download repairs itself instead of
  failing every launch.
- The background speech engine recovers on its own if it hangs or crashes,
  and shuts down cleanly with the app.
- An interrupted install no longer leaves the app stuck on setup.
- A broken dictionary file or bad request can no longer take dictation down.
- Your clipboard is restored more reliably after VivoType pastes; emoji and
  long text type correctly.
- If your microphone changes mid-dictation, VivoType tells you the clip was
  lost instead of silently dropping it.
- If Accessibility permission is missing, your words are put on the clipboard
  so you can paste them.
- Custom push-to-talk keys survive backup and restore.

### Privacy and security

- Text typed into password fields is never read or learned from.
- The words you correct are passed between VivoType's own parts privately,
  not on the command line where other programs on the Mac could read them.
- Hugging Face telemetry is turned off.
- Your personal files (dictionary, contacts, corrections) are never included
  in the app itself or in the public source code.

### Look and feel

- Settings is readable in both light and dark mode, and scrolls on smaller screens instead of running under the Dock.
- Review shows any error clearly instead of failing silently.

### Tested

Tested by hand by the maintainer on an Apple Silicon Mac before release:

- Learning from fixes in TextEdit and Safari: the card's placement,
  Remember, Undo, Not now, the 1 / 2 keys, the countdown bar, hover, and the
  common-word warning. (Notes autocorrects dictated text itself, so the card
  often doesn't appear there.)
- Deleting a rule in Review → Your rules.
- Settings in light and dark mode, and scrolling on a smaller screen.
- Per-app contexts: Safari set to Code keeps "um" and "$" as spoken.
- The recording indicator and the start/stop sounds, switched separately.

Plus 462 automated tests of the speech-processing core.

## [v0.1.0] — 2026-06-22

First public release: hold a hotkey, speak, release — fully local dictation
into any Mac app, with Indian-English-aware cleanup and a personal dictionary.
