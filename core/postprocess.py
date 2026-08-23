"""VivoType Indic post-processing (independent of the ML model).

Cleans up a raw ASR transcript in three steps (see docs/indic-nlp.md):

  1. USD -> INR relabeling via strict, adjacent-token regex:
       $10k   -> ₹10 lakh
       $1.5k  -> ₹1.5 lakh
       $1M    -> ₹1 crore
     It never touches amounts inside URLs, backtick code spans, or quoted
     strings.
  2. Filler-word removal (configurable; defaults to "um", "uh", ...).
  3. Dictionary replacement for common Indian misrecognitions / terms
     (e.g. "blr" -> "Bengaluru").

Word lists are loaded from a JSON config file so non-developers can edit
terms without touching code.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:  # sibling modules; works whether run as a package or a script
    from core.namematch import correct_names
    from core.paths import user_dictionary
except ImportError:
    from namematch import correct_names
    from paths import user_dictionary

# Bundled config shipped alongside this module.
DEFAULT_CONFIG_PATH = Path(__file__).with_name("postprocess_config.json")

# Personal overlay: user-promoted terms merged on top of the shipped defaults,
# kept in the writable data dir so private corrections never live in the bundle.
USER_DICT_PATH = user_dictionary()

# Used only if no config file is found, so the module always works standalone.
DEFAULT_CONFIG = {
    "fillers": ["um", "uh", "umm", "uhh", "er", "erm"],
    "replacements": {
        "blr": "Bengaluru",
        "100k": "1 lakh",
        "shrivastava": "Srivastava",
    },
}

# A $<number><k|M> token, e.g. "$10k", "$1.5k", "$1M". The trailing \b stops
# "$10kg" from matching. We deliberately match only the currency token itself
# (adjacent-token), not the surrounding sentence.
_CURRENCY_RE = re.compile(r"\$(\d+(?:\.\d+)?)\s*([kKmM])\b")
_SUFFIX_UNIT = {"k": "lakh", "m": "crore"}

# Rupee mentions normalize to the ₹ symbol, adjacent-number only, so a bare
# "rs"/"INR" with no amount next to it is never touched.
_RS_PREFIX_RE = re.compile(r"\b(?:rs\.?|inr)\s*(\d[\d,]*(?:\.\d+)?)\b", re.IGNORECASE)
_RUPEE_SUFFIX_RE = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s*rupees?\b", re.IGNORECASE)

# Spans the currency regex must never reach into. We protect double-quoted
# strings but NOT single-quoted ones: apostrophes in contractions ("it's",
# "isn't") would otherwise form bogus "quoted" spans and hide real amounts.
_PROTECT_PATTERNS = [
    re.compile(r"`[^`]*`"),        # backtick code spans
    re.compile(r"https?://\S+"),   # http/https URLs
    re.compile(r"www\.\S+"),       # bare www URLs
    re.compile(r"\"[^\"]*\""),     # double-quoted strings
]

# Null bytes never appear in ASR text, so they make a safe placeholder marker.
_PLACEHOLDER_RE = re.compile("\x00(\\d+)\x00")

# Devanagari letters/signs/digits, excluding the danda marks U+0964/U+0965
# (those are punctuation and bind to the preceding word, not a script run).
_DEVANAGARI = "ऀ-ॣ०-ॿ"
_SCRIPT_JOIN_RES = [
    re.compile(r"(?<=[%s])(?=[A-Za-z0-9₹$])" % _DEVANAGARI),  # मेरीmeeting, मुझे$10k
    re.compile(r"(?<=[A-Za-z0-9])(?=[%s])" % _DEVANAGARI),    # officeजाना
]


def _space_script_boundaries(text):
    """Insert a space where Devanagari and Latin/digit/currency runs are glued.

    Runs BEFORE convert_currency so a glued amount ("मैंने50 rupees") is spaced
    in time for the adjacent-token currency regexes to see it."""
    for boundary in _SCRIPT_JOIN_RES:
        text = boundary.sub(" ", text)
    return text


def _warn(message):
    """Emit a human-readable warning to stderr (never stdout, which carries text)."""
    print(f"VivoType: {message}", file=sys.stderr)


def load_config(path=None, user_path=None):
    """Load fillers + replacements (+ optional profiles), merging the personal
    overlay over the defaults."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    data = None
    if path.exists():
        # A corrupt base/--config file must fall back to defaults, not crash:
        # load_config feeds the CLI and the daemon at boot, so a raised
        # JSONDecodeError here would break all dictation (mirrors the overlay).
        try:
            with path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded
            else:
                _warn(f"ignoring malformed config '{path}' (not a JSON object); using defaults.")
        except (json.JSONDecodeError, OSError) as exc:
            _warn(f"ignoring unreadable config '{path}' ({exc}); using defaults.")
    if data is not None:
        fillers = list(data.get("fillers", DEFAULT_CONFIG["fillers"]))
        replacements = dict(data.get("replacements", DEFAULT_CONFIG["replacements"]))
    else:
        fillers = list(DEFAULT_CONFIG["fillers"])
        replacements = dict(DEFAULT_CONFIG["replacements"])

    overlay_path = Path(user_path) if user_path is not None else USER_DICT_PATH
    if overlay_path.exists():
        try:
            with overlay_path.open("r", encoding="utf-8") as fh:
                overlay = json.load(fh)
            for filler in overlay.get("fillers", []):
                if filler not in fillers:
                    fillers.append(filler)
            replacements.update(overlay.get("replacements", {}))
        except (json.JSONDecodeError, OSError) as exc:
            _warn(f"ignoring unreadable dictionary overlay '{overlay_path}' ({exc}).")

    return {
        "fillers": fillers,
        "replacements": replacements,
        "profiles": _validated_profiles(data.get("profiles") if data else None),
    }


def _validated_profiles(raw):
    """Validate the optional 'profiles' object from a config file.

    Each entry is {convert_currency?: bool, remove_fillers?: bool,
    replacements?: {str: str}}. Invalid entries are skipped with one stderr
    warning — never fatal (a hand-edited file must not break dictation). A
    literal "default" key is ignored: the flat top-level rules ARE the default,
    and two sources of truth for them would drift."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _warn("ignoring 'profiles' (not a JSON object); using default rules only.")
        return {}
    profiles = {}
    for name, entry in raw.items():
        if name == "default":
            _warn("ignoring profiles['default'] — top-level rules are the default; "
                  "rename the profile instead.")
            continue
        ok = (
            isinstance(name, str) and name and
            isinstance(entry, dict) and
            isinstance(entry.get("replacements", {}), dict) and
            all(isinstance(k, str) and isinstance(v, str)
                for k, v in entry.get("replacements", {}).items()) and
            isinstance(entry.get("convert_currency", True), bool) and
            isinstance(entry.get("remove_fillers", True), bool)
        )
        if ok:
            profiles[name] = entry
        else:
            _warn(f"ignoring malformed profile '{name}' (must be an object with "
                  "optional boolean convert_currency/remove_fillers and string "
                  "replacements).")
    return profiles


# Unknown profile names are warned once per process, not once per utterance —
# the daemon would otherwise repeat the warning on every dictation.
_WARNED_PROFILES = set()


def resolve_profile(config, name="default"):
    """Resolve a named profile against the default rule set.

    Returns {"fillers", "replacements", "convert_currency", "remove_fillers"}:
    the flat top-level rules with the named profile's toggles applied and its
    replacements merged OVER them (a context-specific term beats both the
    shipped defaults and personally promoted terms). "default" or any unknown
    name yields today's behavior unchanged."""
    resolved = {
        "fillers": list(config.get("fillers", [])),
        "replacements": dict(config.get("replacements", {})),
        "convert_currency": True,
        "remove_fillers": True,
    }
    profiles = config.get("profiles") or {}
    profile = profiles.get(name) if isinstance(name, str) else None
    if profile is None:
        if name != "default" and isinstance(name, str) and name not in _WARNED_PROFILES:
            _WARNED_PROFILES.add(name)
            _warn(f"unknown profile '{name}'; using default rules.")
        return resolved
    resolved["convert_currency"] = profile.get("convert_currency", True)
    resolved["remove_fillers"] = profile.get("remove_fillers", True)
    resolved["replacements"].update(profile.get("replacements", {}))
    return resolved


def config_mtime(path=None, user_path=None):
    """Latest modification time across the active config files (0 if none exist).

    Lets a long-running process (the daemon) detect dictionary/filler edits — e.g.
    a freshly promoted term — and reload, so promoted rules take effect live
    without a restart or model switch."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    overlay = Path(user_path) if user_path is not None else USER_DICT_PATH
    latest = 0
    for f in (path, overlay):
        try:
            latest = max(latest, f.stat().st_mtime)
        except OSError:
            pass  # missing/unreadable file simply doesn't contribute
    return latest


def _format_value(value):
    """Render a float minimally: 1.0 -> "1", 2.5 -> "2.5" (shortest repr).

    Rounds away binary-float noise first (0.1 * 100 == 10.000000000000002)."""
    value = round(value, 6)
    return str(int(value)) if value.is_integer() else str(value)


def convert_currency(text):
    """Relabel USD amounts to INR and normalize rupee mentions ("Rs 500",
    "INR 500", "500 rupees") to the ₹ symbol, skipping URLs, code spans,
    and quotes."""
    stash = []

    def _protect(match):
        stash.append(match.group(0))
        return "\x00%d\x00" % (len(stash) - 1)

    masked = text
    for pattern in _PROTECT_PATTERNS:
        masked = pattern.sub(_protect, masked)

    def _convert(match):
        unit = _SUFFIX_UNIT[match.group(2).lower()]
        value = float(match.group(1))
        # _format_value normalizes "1.0" -> "1" and "1.50" -> "1.5".
        amount = _format_value(value)
        # 100 lakh IS 1 crore — large sums are always spoken in crore, and
        # fractional-crore sums ("₹0.5 crore") are always spoken in lakh.
        if unit == "lakh" and value >= 100:
            return "₹%s crore" % _format_value(value / 100)
        if unit == "crore" and value < 1:
            return "₹%s lakh" % _format_value(value * 100)
        return "₹%s %s" % (amount, unit)

    masked = _CURRENCY_RE.sub(_convert, masked)
    masked = _RS_PREFIX_RE.sub(r"₹\1", masked)
    masked = _RUPEE_SUFFIX_RE.sub(r"₹\1", masked)

    # Restore protected spans (loop handles spans nested inside one another).
    def _restore(match):
        return stash[int(match.group(1))]

    while "\x00" in masked:
        restored = _PLACEHOLDER_RE.sub(_restore, masked)
        if restored == masked:
            break
        masked = restored
    return masked


# A word or short phrase (1–4 words) repeated 3+ times back-to-back, e.g.
# "guilty guilty guilty" or "thank you. thank you. thank you." — Whisper's
# decoder-loop signature, not how people dictate. Backreference honours
# IGNORECASE, so "Guilty guilty guilty" collapses too.
_REPEAT_RE = re.compile(
    r"(\S+?(?:\s+\S+?){0,3}?)"      # the repeating unit, shortest first (lazy,
    r"(?:[\s,;:.!?।॥]+\1){2,}"      # so trailing punctuation stays a separator)
    r"(?=[\s,;:.!?।॥]|$)",          # 2+ further copies (3+ total)
    re.IGNORECASE | re.UNICODE,
)


def _collapse_unit(match):
    """Collapse one repetition run — unless the unit is a single character or
    pure digits ("1 1 1 1" is someone dictating a PIN, "A A A" a spelling,
    not a decoder loop)."""
    unit = match.group(1)
    if len(unit) == 1 or unit.replace(" ", "").isdigit():
        return match.group(0)
    return unit


def collapse_repetitions(text):
    """Collapse a word/phrase repeated 3+ times in a row to one occurrence.

    Kills Whisper repetition loops (hallucinated "guilty guilty guilty …")
    while leaving deliberate doubles ("very very good") and digit/single-char
    runs (PINs, spelled letters) untouched. A deliberate triple ("no no no")
    is collapsed too — the accepted cost of stopping the loops. Applied
    repeatedly until stable so nested loops fully unwind."""
    while True:
        collapsed = _REPEAT_RE.sub(_collapse_unit, text)
        if collapsed == text:
            return text
        text = collapsed


def remove_fillers(text, fillers):
    """Remove whole-word filler tokens, case-insensitively."""
    if not fillers:
        return text
    pattern = re.compile(
        r"\b(?:%s)\b" % "|".join(re.escape(f) for f in fillers),
        flags=re.IGNORECASE,
    )
    return pattern.sub("", text)


def apply_replacements(text, replacements):
    """Apply whole-word dictionary replacements, case-insensitively.

    Single pass: every rule is matched against the ORIGINAL text in one go, so one
    rule's output can never be re-matched by another rule (results don't depend on
    dict order). Longer sources are tried first, so a more specific rule wins over a
    shorter overlapping one. dst is inserted literally — a value like "\\1" or "\\g"
    from a user dictionary must not be parsed as a regex backreference."""
    by_lower = {}
    for src, dst in replacements.items():
        if src:  # skip empty sources (an empty pattern would match everywhere)
            by_lower.setdefault(src.lower(), dst)  # first wins on case-duplicates
    if not by_lower:
        return text
    sources = sorted(by_lower, key=len, reverse=True)
    pattern = re.compile(
        r"\b(?:%s)\b" % "|".join(re.escape(s) for s in sources),
        flags=re.IGNORECASE,
    )
    return pattern.sub(lambda m: by_lower[m.group(0).lower()], text)


def _normalize_whitespace(text):
    """Tidy spacing/punctuation left behind by filler removal."""
    text = re.sub(r"\s+", " ", text)                     # collapse whitespace runs
    # A removed filler between two marks leaves "word. . Then" — keep the first
    # mark only. Requires whitespace between marks so a real "..." survives.
    # । (danda) and ॥ (double danda) are Devanagari sentence punctuation.
    text = re.sub(r"([,.!?;:।॥])(?:\s+[,.!?;:।॥])+", r"\1", text)
    text = re.sub(r"\s+([,.!?;:।॥])", r"\1", text)       # no space before punctuation
    text = re.sub(r"।(?=[^\s।॥])", "। ", text)           # danda binds left, space follows
    text = re.sub(r"(?<=\d)\s+%", "%", text)             # "10 %" -> "10%"
    text = re.sub(r"([₹$])\s+(?=\d)", r"\1", text)       # "₹ 500" -> "₹500"
    text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)  # collapse repeated commas etc.
    text = re.sub(r"^[\s,;:]+", "", text)                # trim leading punctuation/space
    # A leading filler like "Um." leaves an orphan mark ("Um. Then" → ". Then").
    # Strip a single sentence mark + following space only — not "^[.]+", which
    # would eat a genuine leading ellipsis ("... okay").
    text = re.sub(r"^[.!?;:।॥]\s+", "", text)
    return text.strip()


def _restore_leading_capital(original, text):
    """Re-capitalize a sentence whose opening filler was stripped.

    "Um, actually we go." -> cleanup yields "actually we go." — the lowercase
    start is a pipeline artifact, not the speaker's. Only fires when the original
    began uppercase, the result begins lowercase, and the new first word is fully
    lowercase (so "iPhone" is never forced to "IPhone")."""
    if not text or not text[0].islower():
        return text
    first_alpha = next((c for c in original if c.isalpha()), "")
    if not first_alpha.isupper():
        return text
    first_word = re.match(r"[^\W\d_]+", text, flags=re.UNICODE)
    if first_word and not first_word.group(0).islower():
        return text
    return text[0].upper() + text[1:]


def postprocess(text, config=None, profile="default"):
    """Run the full cleanup pipeline on a transcript string.

    `profile` selects a named context from config["profiles"] (per-app rules):
    only convert_currency and remove_fillers are toggleable, and the profile's
    replacements merge over the defaults. "default" (or an unknown name) is
    exactly the historical behavior."""
    if config is None:
        config = load_config()
    resolved = resolve_profile(config, profile)
    original = text
    text = collapse_repetitions(text)
    text = _space_script_boundaries(text)
    if resolved["convert_currency"]:
        text = convert_currency(text)
    if resolved["remove_fillers"]:
        text = remove_fillers(text, resolved["fillers"])
    text = apply_replacements(text, resolved["replacements"])
    text = correct_names(text)  # snap near-miss proper nouns to known contacts
    text = _normalize_whitespace(text)
    return _restore_leading_capital(original, text)
