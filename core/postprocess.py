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


def _warn(message):
    """Emit a human-readable warning to stderr (never stdout, which carries text)."""
    print(f"VivoType: {message}", file=sys.stderr)


def load_config(path=None, user_path=None):
    """Load fillers + replacements, merging the personal overlay over the defaults."""
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

    return {"fillers": fillers, "replacements": replacements}


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


def _format_amount(num_str):
    """Keep "1.5" as-is but normalize "1.0" -> "1" so "$1.0M" reads "₹1 crore"."""
    if "." in num_str:
        value = float(num_str)
        return str(int(value)) if value.is_integer() else num_str
    return num_str


def convert_currency(text):
    """Relabel USD amounts to INR, skipping URLs, code spans, and quotes."""
    stash = []

    def _protect(match):
        stash.append(match.group(0))
        return "\x00%d\x00" % (len(stash) - 1)

    masked = text
    for pattern in _PROTECT_PATTERNS:
        masked = pattern.sub(_protect, masked)

    def _convert(match):
        amount = _format_amount(match.group(1))
        unit = _SUFFIX_UNIT[match.group(2).lower()]
        return "₹%s %s" % (amount, unit)

    masked = _CURRENCY_RE.sub(_convert, masked)

    # Restore protected spans (loop handles spans nested inside one another).
    def _restore(match):
        return stash[int(match.group(1))]

    while "\x00" in masked:
        restored = _PLACEHOLDER_RE.sub(_restore, masked)
        if restored == masked:
            break
        masked = restored
    return masked


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
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)         # no space before punctuation
    text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)  # collapse repeated commas etc.
    text = re.sub(r"^[\s,;:]+", "", text)                # trim leading punctuation/space
    return text.strip()


def postprocess(text, config=None):
    """Run the full cleanup pipeline on a transcript string."""
    if config is None:
        config = load_config()
    text = convert_currency(text)
    text = remove_fillers(text, config.get("fillers", []))
    text = apply_replacements(text, config.get("replacements", {}))
    text = correct_names(text)  # snap near-miss proper nouns to known contacts
    return _normalize_whitespace(text)
