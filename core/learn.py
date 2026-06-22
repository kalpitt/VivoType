#!/usr/bin/env python3
"""Record corrections the user made to VivoType's output (Step 2: headless learning).

Given the text VivoType inserted and the user's corrected version, diff them at the
word level and append the substitutions to core/data/corrections.jsonl for later
review and promotion into the names lexicon / dictionary. There is NO training
and NOTHING is auto-applied — this only collects "things you keep fixing".

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
from pathlib import Path

try:  # sibling module; works whether run as a package or a script
    from core.paths import corrections_log
except ImportError:
    from paths import corrections_log

LOG_PATH = corrections_log()
# Letters (any script, so accented names like "José" survive), digits, and the
# apostrophe in contractions ("don't"); `[^\W_]` is \w minus underscore.
_WORD = re.compile(r"(?:[^\W_]|')+", re.UNICODE)


def _tokens(text):
    return _WORD.findall(text)


def diff_corrections(original, corrected):
    """Return word-level substitutions turning `original` into `corrected`."""
    o = _tokens(original)
    c = _tokens(corrected)
    matcher = difflib.SequenceMatcher(a=[w.lower() for w in o], b=[w.lower() for w in c])
    corrections = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        frm = " ".join(o[i1:i2])
        to = " ".join(c[j1:j2])
        if frm and to and frm.lower() != to.lower():
            corrections.append({
                "from": frm,
                "to": to,
                # single 1:1 word swaps are the high-confidence (name) signal
                "single_word": (i2 - i1 == 1 and j2 - j1 == 1),
            })
    return corrections


def similarity(original, corrected):
    """0..1 overall similarity; guards against logging unrelated clipboard copies."""
    return difflib.SequenceMatcher(a=original.lower(), b=corrected.lower()).ratio()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vivotype-learn")
    parser.add_argument("--original", required=True, help="What VivoType inserted.")
    parser.add_argument("--corrected", required=True, help="The user's corrected text.")
    parser.add_argument("--min-similarity", type=float, default=0.5,
                        help="Below this overall similarity, treat as unrelated (no log).")
    parser.add_argument("--log", default=str(LOG_PATH))
    args = parser.parse_args(argv)

    # If the texts are unrelated, this isn't a correction of our output.
    if similarity(args.original, args.corrected) < args.min_similarity:
        print(0)
        return 0

    corrections = diff_corrections(args.original, args.corrected)
    if not corrections:
        print(0)
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
