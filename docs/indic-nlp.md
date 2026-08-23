## Indic Post-Processing Module

The live pipeline is `core/postprocess.py`; rules live in
`core/postprocess_config.json` (shipped defaults + your editable overrides and
per-app profiles). This doc describes what the code actually does today; the
module is independent of the ML model.

Pipeline order per transcript:

1. **Repetition collapse** — Whisper decoder loops ("guilty guilty guilty…")
   collapse to one occurrence; deliberate doubles and digit/letter runs survive.
2. **Script-boundary spacing** — a space inserted where Devanagari and
   Latin/digit/currency runs are glued ("मुझे$10k" → "मुझे $10k") so the
   currency regexes can see amounts.
3. **Currency conversion** — strict adjacent-token regexes: `$10k` → `₹10
   lakh`, `$1M` → `₹1 crore`, `Rs 500`/`500 rupees` → `₹500`. Never matches
   inside URLs, backtick code spans, or double-quoted strings.
4. **Filler removal** — whole-word, case-insensitive, configurable list
   (`fillers` in the JSON config).
5. **Dictionary replacement** — single pass, longest source first
   (`replacements`): "blr" → "Bengaluru", "shrivastava" → "Srivastava", etc.
6. **Name correction** — near-miss proper nouns snap to known contacts
   (`core/namematch.py`, edit-distance gated).
7. **Whitespace/punctuation normalization + leading-capital restore.**

Per-app profiles: a named entry under `profiles` in the JSON config may turn
currency conversion or filler removal OFF and add replacement overrides that
win over both defaults and promoted terms. Apps are mapped to profiles in the
macOS app's Settings; unmapped apps get the default behavior above.

Spoken commands ("scratch that", "all caps that", …) are detected on the RAW
ASR text BEFORE this pipeline runs — cleanup must never mangle a phrase — and
their text transforms apply AFTER it. See `core/commands.py` and the Feature 4
notes in `docs/next-features-handoff.md`.
