#!/usr/bin/env python3
"""Review captured corrections and promote them into VivoType's active rules.

Reads core/data/corrections.jsonl (written by core/learn.py), groups identical
corrections by frequency (most-frequent first), and asks [y/N/d] for each
(promote / skip / discard). On approval it routes the rule to the right place:

  • near-miss proper nouns -> names lexicon   (core/data/lexicon/contacts.json)
  • abbreviations / phrases -> Indic dictionary (core/postprocess_config.json)

Promoted and discarded corrections are removed from the pending log; skipped
ones are kept for next time. Terminal-only — no visual UI.

Usage:
    python core/promote.py            # interactive review
    python core/promote.py --dry-run  # just list what's pending, change nothing
    python core/promote.py --yes      # promote everything without prompting
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:  # sibling modules
    from core.namematch import _edit_distance, _threshold, load_english_words, LEXICON_PATH
    from core.config import atomic_write_text
    from core.paths import corrections_log, user_dictionary
except ImportError:
    from namematch import _edit_distance, _threshold, load_english_words, LEXICON_PATH
    from config import atomic_write_text
    from paths import corrections_log, user_dictionary

# Promotions write to the personal overlay in the writable data dir, never the
# shipped default dictionary, so the tracked config stays generic.
USER_DICT_PATH = user_dictionary()
LOG_PATH = corrections_log()


def load_corrections(path):
    path = Path(path)
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def group_corrections(items):
    """Group identical from->to pairs, with counts and their source entries."""
    groups = {}
    for it in items:
        frm = str(it.get("from", "")).strip()
        to = str(it.get("to", "")).strip()
        if not frm or not to:
            continue
        key = (frm.lower(), to)
        group = groups.get(key)
        if group is None:
            group = {"from": frm, "to": to, "count": 0, "entries": []}
            groups[key] = group
        group["count"] += 1
        group["entries"].append(it)
    return sorted(groups.values(), key=lambda g: (-g["count"], g["from"].lower()))


def classify(frm, to, english):
    """Route a correction: 'lexicon' for near-miss names, else 'dictionary'."""
    frm, to = frm.strip(), to.strip()
    name_like = (
        " " not in to and to.isalpha()
        and to[:1].isupper() and to[1:].islower()
        and to.lower() not in english
    )
    if " " not in frm and name_like:
        if _edit_distance(frm.lower(), to.lower(), 3) <= _threshold(len(to)):
            return "lexicon"
    return "dictionary"


def _load_json_object(path):
    """Read a JSON object from path: {} if missing, but raise a clear error if the
    file exists yet is unparseable or not an object. Promotion targets are
    rewritten in full, so silently treating a corrupt file as {} would destroy
    every rule the user had already promoted — refuse instead of clobbering it."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(
            f"Refusing to modify '{path}': it is not readable JSON ({exc}). "
            "Fix or remove the file, then try again."
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"Refusing to modify '{path}': expected a JSON object. "
            "Fix or remove the file, then try again."
        )
    return data


def promote_to_lexicon(name, path):
    data = _load_json_object(path)
    if not data:
        data = {"_comment": "Names lexicon for VivoType's fuzzy matcher.", "names": []}
    names = data.setdefault("names", [])
    if name.lower() not in {n.lower() for n in names}:
        names.append(name)
        names.sort(key=str.lower)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def promote_to_dictionary(frm, to, path):
    data = _load_json_object(path)
    data.setdefault("replacements", {})[frm.lower()] = to
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def rewrite_log(path, entries):
    path = Path(path)
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def apply_one(action, frm, to, log_path, lexicon_path, config_path, english=None):
    """Apply a single promote/discard to one from->to group (used by the GUI)."""
    items = load_corrections(log_path)
    key = (frm.strip().lower(), to.strip())

    def group_key(it):
        return (str(it.get("from", "")).strip().lower(), str(it.get("to", "")).strip())

    matched = [it for it in items if group_key(it) == key]
    rest = [it for it in items if group_key(it) != key]
    if not matched:
        return {"ok": False, "status": "not_found"}

    target = None
    if action == "promote":
        if english is None:
            english = load_english_words()
        target = classify(frm, to, english)
        try:
            if target == "lexicon":
                promote_to_lexicon(to, lexicon_path)
            else:
                promote_to_dictionary(frm, to, config_path)
        except ValueError as exc:
            # Promotion failed (e.g. a corrupt target file). Keep the corrections
            # in the log so the user can retry after fixing the file.
            return {"ok": False, "status": "write_failed", "error": str(exc)}
    rewrite_log(log_path, rest)  # both promote and discard remove the entries
    return {"ok": True, "action": action, "target": target, "removed": len(matched)}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="vivotype-promote",
        description="Promote captured corrections into VivoType's rules.",
    )
    parser.add_argument("--log", default=str(LOG_PATH))
    parser.add_argument("--lexicon", default=str(LEXICON_PATH))
    parser.add_argument("--config", default=str(USER_DICT_PATH),
                        help="Where dictionary promotions are written (default: personal overlay).")
    parser.add_argument("--yes", action="store_true", help="Promote everything, no prompts.")
    parser.add_argument("--dry-run", action="store_true", help="List suggestions; change nothing.")
    parser.add_argument("--list-json", action="store_true", help="Print pending corrections as JSON and exit.")
    parser.add_argument("--apply", action="store_true", help="Apply one action (with --from/--to/--action).")
    parser.add_argument("--from", dest="from_word", help="The misrecognized word (with --apply).")
    parser.add_argument("--to", dest="to_word", help="The correct word (with --apply).")
    parser.add_argument("--action", choices=["promote", "discard"], help="Action for --apply.")
    args = parser.parse_args(argv)

    if args.list_json:
        english = load_english_words()
        groups = group_corrections(load_corrections(args.log))
        payload = [{"from": g["from"], "to": g["to"], "count": g["count"],
                    "target": classify(g["from"], g["to"], english)} for g in groups]
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if args.apply:
        if not (args.from_word and args.to_word and args.action):
            print("error: --apply requires --from, --to, and --action", file=sys.stderr)
            return 2
        result = apply_one(args.action, args.from_word, args.to_word,
                           args.log, args.lexicon, args.config)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    groups = group_corrections(load_corrections(args.log))
    if not groups:
        print("No pending corrections. 🎉")
        return 0

    english = load_english_words()
    print(f"{len(groups)} pending correction(s), most frequent first:\n")

    kept = []
    promoted = 0
    discarded = 0
    for group in groups:
        target = classify(group["from"], group["to"], english)
        where = "names lexicon" if target == "lexicon" else "dictionary"
        summary = f"{group['from']!r} → {group['to']!r}   [{where}, seen {group['count']}×]"

        if args.dry_run:
            print("  " + summary)
            kept.extend(group["entries"])
            continue

        if args.yes:
            choice = "y"
            print("  " + summary + "  → promoting")
        else:
            choice = input(
                "  " + summary + "\n  Promote / Skip / Discard? [y/N/d] "
            ).strip().lower()

        if choice == "y":
            try:
                if target == "lexicon":
                    promote_to_lexicon(group["to"], args.lexicon)
                else:
                    promote_to_dictionary(group["from"], group["to"], args.config)
                promoted += 1
            except ValueError as exc:
                # Don't abort the whole review on one bad target; keep the entry.
                print(f"  ! skipped — {exc}")
                kept.extend(group["entries"])
        elif choice == "d":
            discarded += 1  # drop from the log permanently, without promoting
        else:
            kept.extend(group["entries"])  # skip — keep for next time

    if not args.dry_run:
        rewrite_log(args.log, kept)
        print(f"\nPromoted {promoted}; discarded {discarded}; "
              f"kept {len(groups) - promoted - discarded} for later.")
        if promoted:
            print("Re-run dictation — the new rules are already active.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
