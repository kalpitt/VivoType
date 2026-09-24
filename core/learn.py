#!/usr/bin/env python3
"""Record corrections the user made to VivoType's output (Step 2: headless learning).

Given the text VivoType inserted and the user's corrected version, diff them at the
word level and append the substitutions to core/data/corrections.jsonl for later
review and promotion into the names lexicon / dictionary. There is NO training
and NOTHING is auto-applied — this only collects "things you keep fixing".

Span-only callers (field watch) should pass the injected span and the user's
corrected span on stdin as JSON: {"original": "...", "corrected": "..."}.
Argv --original / --corrected remain supported for clipboard and tests.
With --pairs-json nothing is logged: the filtered pairs are printed as JSON
({"pairs": [...]}) so the correction offer can show them. The offer's expiry
then runs the same command without the flag, so both see the same pairs.

Prints the number of corrections recorded to stdout (the macOS helper reads this
to decide whether to play its audio confirmation). All data stays local.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path

try:  # sibling module; works whether run as a package or a script
    from core.paths import corrections_log
except ImportError:
    from paths import corrections_log

LOG_PATH = corrections_log()
# Letters (any script, so accented names like "José" survive), digits, the
# apostrophe in contractions ("don't", or the curly "don’t" many Mac apps
# type), and the combining marks of Indic scripts: Python's \w excludes
# Devanagari vowel signs, so "मैंने" was split into single letters and junk
# pairs reached the review queue. Only letters, marks and digits of the
# Indic blocks are added — their currency signs, danda and abbreviation
# marks (৳ ॰ ෴) stay separators. `[^\W_]` is \w minus underscore.
_INDIC_WORD_CHARS = "".join(
    chr(c) for c in range(0x0900, 0x0E00)
    if unicodedata.category(chr(c))[0] in "LMN")
_WORD = re.compile(r"(?:[^\W_]|['’]|[" + re.escape(_INDIC_WORD_CHARS) + r"])+", re.UNICODE)
# Quote marks at a word's edge are quoting, not part of the word: "‘Rahul’"
# is Rahul, and must not log "Rahul -> Rahul’".
_EDGE_QUOTES = "'’"

def is_pure_punctuation(text):
    """True when every non-space character in `text` is punctuation."""
    stripped = text.strip()
    if not stripped:
        return False
    return all(not ch.isalnum() and not ch.isspace() for ch in stripped)


def is_punctuation_pair(frm, to):
    """Whether a from→to pair turns a word into a bare symbol (or back).

    Only symbols count. Every spoken word stays a word: "comma", "period",
    "mark", "colon" are dictated as words and a fix involving them ("Colin ->
    colon", "mark -> Marc") is learned like any other. What is never learned
    is "comma -> ,": a rule like that would rewrite the real word everywhere."""
    return is_pure_punctuation(frm) or is_pure_punctuation(to)


def _tokens(text):
    return [t for t in (w.strip(_EDGE_QUOTES) for w in _WORD.findall(text)) if t]


def _is_garbage_pair(frm, to):
    """Pairs that can never become a good rule and only pollute the review
    queue: a side that is a single character, or nothing but digits (e.g. the
    observed '7' -> 'driven' — a numeral swap is an edit, not a mishearing)."""
    for side in (frm, to):
        if len(side) < 2:
            return True
        if all(tok.isdigit() for tok in side.split()):
            return True
    return False


def _comparable(word):
    """Case- and apostrophe-insensitive form: "Don’t" and "don't" are equal."""
    return word.lower().replace("’", "'")


def diff_corrections(original, corrected):
    """Return word-level substitutions turning `original` into `corrected`."""
    o = _tokens(original)
    c = _tokens(corrected)
    matcher = difflib.SequenceMatcher(a=[_comparable(w) for w in o],
                                      b=[_comparable(w) for w in c])
    corrections = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        frm = " ".join(o[i1:i2])
        to = " ".join(c[j1:j2])
        if _is_garbage_pair(frm, to):
            continue
        if frm and to and _comparable(frm) != _comparable(to):
            corrections.append({
                "from": frm,
                "to": to,
                # single 1:1 word swaps are the high-confidence (name) signal
                "single_word": (i2 - i1 == 1 and j2 - j1 == 1),
            })
    return corrections


def filter_corrections(corrections, drop_punctuation=True):
    """Apply optional symbol filter (default on): drop pairs that turn a word
    into bare punctuation, like "comma -> ,". Spoken words are never dropped."""
    if not drop_punctuation:
        return corrections
    return [c for c in corrections if not is_punctuation_pair(c["from"], c["to"])]


def similarity(original, corrected):
    """0..1 overall similarity; guards against logging unrelated clipboard copies."""
    return difflib.SequenceMatcher(a=original.lower(), b=corrected.lower()).ratio()


def _read_stdin_pair():
    """Read {"original": "...", "corrected": "..."} from stdin when present."""
    if sys.stdin is None or sys.stdin.isatty():
        return None, None
    raw = sys.stdin.read()
    if not raw.strip():
        return None, None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("stdin JSON must be an object with original/corrected keys")
    return data.get("original"), data.get("corrected")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vivotype-learn")
    parser.add_argument("--original", help="What VivoType inserted (or pass on stdin).")
    parser.add_argument("--corrected", help="The user's corrected text (or pass on stdin).")
    parser.add_argument("--min-similarity", type=float, default=0.5,
                        help="Below this overall similarity, treat as unrelated (no log).")
    parser.add_argument("--drop-punctuation", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Drop pairs that turn a word into bare punctuation (default on).")
    parser.add_argument("--pairs-json", action="store_true",
                        help="Print the filtered pairs as JSON and log nothing.")
    parser.add_argument("--log", default=str(LOG_PATH))
    args = parser.parse_args(argv)

    def report_none():
        print(json.dumps({"pairs": []}) if args.pairs_json else 0)
        return 0

    original = args.original
    corrected = args.corrected
    if original is None or corrected is None:
        stdin_original, stdin_corrected = _read_stdin_pair()
        if original is None:
            original = stdin_original
        if corrected is None:
            corrected = stdin_corrected
    if original is None or corrected is None:
        print("error: --original and --corrected required (argv or stdin JSON)",
              file=sys.stderr)
        return 2

    # If the texts are unrelated, this isn't a correction of our output.
    if similarity(original, corrected) < args.min_similarity:
        return report_none()

    corrections = filter_corrections(
        diff_corrections(original, corrected),
        drop_punctuation=args.drop_punctuation,
    )
    if not corrections:
        return report_none()
    if args.pairs_json:
        print(json.dumps({"pairs": corrections}, ensure_ascii=False))
        return 0

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as fh:
        for c in corrections:
            fh.write(json.dumps({**c, "at": stamp}, ensure_ascii=False) + "\n")

    print(len(corrections))
    return 0


if __name__ == "__main__":
    sys.exit(main())
