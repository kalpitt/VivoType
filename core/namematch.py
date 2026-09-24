"""Fuzzy name correction against a personal names lexicon.

After ASR, snap a transcribed word to a known contact name when it is a clear
near-miss of one — e.g. "Shrivastav" -> "Shrivastava" — without disturbing
ordinary dictation. Safety rules (all must hold to change a word):

  1. The word is Capitalized (ASR's signal that it's a proper noun).
  2. The word is NOT a real English word (checked against the system word list,
     including simple inflections like "Rakes"/"Stopped", plus the bundled
     allowlist in namematch_allowlist.txt for informal words, brands and Indian
     places such as "Gonna"/"Gmail"/"Pune"), so common words are never touched.
  3. The word is within a small edit distance of exactly one known name that
     starts with the same letter (or a routine Whisper swap: B/V, V/W, C/K),
     and a word under 5 letters may only swap one letter in place ("Ravy" ->
     "Ravi"; "Okay" must never become "Kay").

The names lexicon lives at core/data/lexicon/contacts.json (personal; kept off
the public mirror and out of the app bundle). If it is missing, this is a no-op.
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
ALLOWLIST_PATH = Path(__file__).with_name("namematch_allowlist.txt")
MIN_LEN = 4
MIN_FUZZY_LEN = 5  # shorter tokens snap only on a one-letter swap
# First letters Whisper routinely swaps on Indian names ("Barun" for "Varun");
# any other first-letter difference is a different word ("Gonna" vs "Donna").
_CONFUSABLE_FIRST = {frozenset("bv"), frozenset("vw"), frozenset("ck")}
_SENTENCE_END = ".!?:\n।॥"  # Whisper capitalizes after a colon too

_names_by_len = None
_english = None
_allowlist = None
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
    names = data.get("names", [])
    if not isinstance(names, list):
        _warn(f"ignoring malformed names lexicon '{path}' ('names' is not a list); using no contacts.")
        return []
    # A hand-edited entry that is not a non-empty string (a number, null) is
    # skipped rather than crashing the matcher on len()/lower().
    return list(dict.fromkeys(n for n in names if isinstance(n, str) and n))  # dedupe, preserve order


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


def load_allowlist(path=None):
    """Words that must never snap to a name (one per line, # comments)."""
    path = Path(path) if path else ALLOWLIST_PATH
    words = set()
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                w = line.strip().lower()
                if w and not w.startswith("#"):
                    words.add(w)
    except OSError:
        pass  # a missing allowlist only weakens the guard; never fatal
    return words


def _stems(wl):
    """The word plus plausible base forms for -s/-es/-ies/-ed/-ing endings, so
    "Rakes", "Varies", "Tamed", "Stopped" and "Hoping" count as English when
    their base form is in the word list. A bare "-es" is dropped only after a
    sibilant ("Boxes"), so "Hites" is not read as "hit"."""
    stems = {wl}
    if wl.endswith("ies") or wl.endswith("ied"):
        stems.add(wl[:-3] + "y")
    if wl.endswith("es") and wl[:-2].endswith(("s", "x", "z", "ch", "sh")):
        stems.add(wl[:-2])
    if wl.endswith("s"):
        stems.add(wl[:-1])
    for suffix in ("ed", "ing"):
        if wl.endswith(suffix):
            stem = wl[: -len(suffix)]
            stems.update((stem, stem + "e"))
            if len(stem) >= 2 and stem[-1] == stem[-2]:
                stems.add(stem[:-1])  # doubled consonant: stopped -> stop
    return stems


def _first_letters_compatible(a, b):
    return a[0] == b[0] or frozenset((a[0], b[0])) in _CONFUSABLE_FIRST


def _at_sentence_start(text, index):
    """True when only whitespace/opening quotes separate `index` from the start
    of the text or from a sentence-ending mark. Orphan ,;: at the very start
    (left by a removed "Um,") count as the start."""
    i = index
    while i > 0 and (text[i - 1].isspace() or text[i - 1] in "\"'“‘("):
        i -= 1
    if i == 0 or text[i - 1] in _SENTENCE_END:
        return True
    while i > 0 and text[i - 1] in " ,;:":
        i -= 1
    return i == 0


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
        if not isinstance(nm, str) or not nm:
            continue  # tolerate a wrong-shaped caller-supplied list
        by_len.setdefault(len(nm), []).append(nm)
    return by_len


def correct_names(text, names=None, english=None):
    """Return text with near-miss proper nouns snapped to known names."""
    global _names_by_len, _english, _allowlist, _last_lexicon_mtime

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
    if _allowlist is None:
        _allowlist = load_allowlist()
    allowlist = _allowlist

    def _maybe(match):
        word = match.group(0)
        # Rule 1: only proper-noun-shaped tokens (Capitalized, not ALLCAPS).
        if len(word) < MIN_LEN or not (word[0].isupper() and word[1:].islower()):
            return word
        wl = word.lower()
        # Rule 2: never touch real English words or allowlisted everyday
        # words. Inflected forms ("Rakes", "Stopped") count as English only
        # at a sentence start, where every word is capitalized; mid-sentence
        # the capital is ASR's proper-noun signal ("I met Rakes" -> Rakesh).
        if wl in english or wl in allowlist:
            return word
        if _at_sentence_start(match.string, match.start()) and any(
                s in english or s in allowlist for s in _stems(wl)):
            return word
        # Rule 3: near exactly one known name within the edit-distance
        # threshold. If two distinct names tie at the best distance the match is
        # ambiguous, so we leave the word unchanged rather than snap to an
        # arbitrary contact. Ties are judged across ALL names before the
        # first-letter rule, so that rule can never break a tie ("Reena" with
        # Reema and Leena stays "Reena").
        max_d = _threshold(len(wl))
        best, best_d, ambiguous = None, max_d + 1, False
        for length in range(len(wl) - max_d, len(wl) + max_d + 1):
            for nm in names_by_len.get(length, ()):
                nl = nm.lower()
                if nl == wl:
                    return word if nm == word else nm
                d = _edit_distance(wl, nl, max_d)
                if d < best_d:
                    best, best_d, ambiguous = nm, d, False
                elif d == best_d and best is not None and nm.lower() != best.lower():
                    ambiguous = True
        if best is None or best_d > max_d or ambiguous:
            return word
        # The closest name must also share the first letter (or a known
        # Whisper swap), and a short token may only swap one letter in place:
        # "Ravy" -> "Ravi", never "Okay" -> "Kay".
        bl = best.lower()
        if not _first_letters_compatible(wl, bl):
            return word
        if len(wl) < MIN_FUZZY_LEN and len(bl) != len(wl):
            return word
        return best

    # Match letters of any script (`[^\W\d_]` is \w minus digits and underscore)
    # so accented proper nouns like "José" are treated as one token, not split
    # into an ASCII prefix plus orphaned accents.
    return re.sub(r"[^\W\d_]+", _maybe, text, flags=re.UNICODE)
