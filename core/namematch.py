"""Fuzzy name correction against a personal names lexicon.

After ASR, snap a transcribed word to a known contact name when it is a clear
near-miss of one — e.g. "Shrivastav" -> "Shrivastava" — without disturbing
ordinary dictation. Safety rules (all must hold to change a word):

  1. The word is Capitalized (ASR's signal that it's a proper noun).
  2. The word is NOT a real English word (checked against the system word list),
     so common words like "Money"/"More" are never touched.
  3. The word is within a small edit distance of exactly one known name.

The names lexicon lives at core/data/lexicon/contacts.json (git-ignored). If it
is missing, this is a no-op.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:  # sibling module; works whether run as a package or a script
    from core.paths import contacts_lexicon
except ImportError:
    from paths import contacts_lexicon

LEXICON_PATH = contacts_lexicon()
SYSTEM_WORDS_PATH = Path("/usr/share/dict/words")
MIN_LEN = 4

_names_by_len = None
_english = None
_last_lexicon_mtime: float = 0


def _warn(message):
    """Emit a human-readable warning to stderr (never stdout, which carries text)."""
    print(f"VivoType: {message}", file=sys.stderr)


def load_names(path=None):
    path = Path(path) if path else LEXICON_PATH
    if not path.exists():
        return []
    # A corrupt or unreadable lexicon must be a no-op, never a crash: this runs
    # inside the dictation pipeline, so a raised JSONDecodeError would propagate
    # through correct_names -> postprocess -> CLI exit 1 and break ALL dictation.
    # Warn (to stderr only — stdout carries transcript text) so the user can tell
    # their contacts are being ignored rather than silently failing.
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        _warn(f"ignoring unreadable names lexicon '{path}' ({exc}); using no contacts.")
        return []
    if not isinstance(data, dict):
        _warn(f"ignoring malformed names lexicon '{path}' (not a JSON object); using no contacts.")
        return []
    return list(dict.fromkeys(data.get("names", [])))  # dedupe, preserve order


def load_english_words(path=None):
    path = Path(path) if path else SYSTEM_WORDS_PATH
    words = set()
    if path.exists():
        with path.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                w = line.strip().lower()
                if w:
                    words.add(w)
    return words


def _threshold(length):
    """How many character edits we tolerate for a word of this length."""
    return 1 if length <= 7 else 2


def _edit_distance(a, b, max_d):
    """Levenshtein with early exit; returns max_d+1 once the bound is exceeded."""
    la, lb = len(a), len(b)
    if abs(la - lb) > max_d:
        return max_d + 1
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        row_min = i
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > max_d:
            return max_d + 1
        prev = cur
    return prev[lb]


def _index_by_len(names):
    by_len = {}
    for nm in names:
        by_len.setdefault(len(nm), []).append(nm)
    return by_len


def correct_names(text, names=None, english=None):
    """Return text with near-miss proper nouns snapped to known names."""
    global _names_by_len, _english, _last_lexicon_mtime

    if names is None:
        mtime = Path(LEXICON_PATH).stat().st_mtime if Path(LEXICON_PATH).exists() else 0
        if mtime != _last_lexicon_mtime:
            _names_by_len = _index_by_len(load_names())
            _last_lexicon_mtime = mtime
        names_by_len = _names_by_len
    else:
        names_by_len = _index_by_len(names)
    if not names_by_len:
        return text

    if english is None:
        if _english is None:
            _english = load_english_words()
        english = _english

    def _maybe(match):
        word = match.group(0)
        # Rule 1: only proper-noun-shaped tokens (Capitalized, not ALLCAPS).
        if len(word) < MIN_LEN or not (word[0].isupper() and word[1:].islower()):
            return word
        wl = word.lower()
        # Rule 2: never touch real English words.
        if wl in english:
            return word
        # Rule 3: near exactly one known name within the edit-distance
        # threshold. If two distinct names tie at the best distance the match is
        # ambiguous, so we leave the word unchanged rather than snap to an
        # arbitrary contact.
        max_d = _threshold(len(wl))
        best, best_d, ambiguous = None, max_d + 1, False
        for length in range(len(wl) - max_d, len(wl) + max_d + 1):
            for nm in names_by_len.get(length, ()):
                d = _edit_distance(wl, nm.lower(), max_d)
                if d < best_d:
                    best, best_d, ambiguous = nm, d, False
                    if d == 0:
                        return word if nm == word else nm
                elif d == best_d and best is not None and nm.lower() != best.lower():
                    ambiguous = True
        if best is not None and best_d <= max_d and not ambiguous:
            return best
        return word

    # Match letters of any script (`[^\W\d_]` is \w minus digits and underscore)
    # so accented proper nouns like "José" are treated as one token, not split
    # into an ASCII prefix plus orphaned accents.
    return re.sub(r"[^\W\d_]+", _maybe, text, flags=re.UNICODE)
