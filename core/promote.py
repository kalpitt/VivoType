#!/usr/bin/env python3
"""Review captured corrections and promote them into VivoType's active rules.

Reads core/data/corrections.jsonl (written by core/learn.py), groups identical
corrections by frequency (most-frequent first), and asks [y/N/d] for each
(promote / skip / discard). On approval it routes the rule to the right place:

  • near-miss proper nouns -> names lexicon   (core/data/lexicon/contacts.json)
  • abbreviations / phrases -> personal overlay (user_dictionary.json)

Promoted and discarded corrections are removed from the pending log; skipped
ones are kept for next time. Terminal-only — no visual UI.

Usage:
    python core/promote.py            # interactive review
    python core/promote.py --dry-run  # just list what's pending, change nothing
    python core/promote.py --yes      # promote everything not flagged risky
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:  # sibling modules
    from core.learn import is_punctuation_pair
    from core.namematch import _edit_distance, _threshold, correct_names, load_english_words, LEXICON_PATH
    from core.config import atomic_write_text
    from core.paths import corrections_log, user_dictionary
except ImportError:
    from learn import is_punctuation_pair
    from namematch import _edit_distance, _threshold, correct_names, load_english_words, LEXICON_PATH
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
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
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


# A rule must be seen this many times before bulk promotion will take it —
# one sighting is as likely a one-off edit as a systematic mishearing.
MIN_SIGHTINGS = 2


def assess_risk(frm, to, count, english):
    """Return (risky, reason) for a candidate rule.

    Risky rules are excluded from bulk promotion (--yes / the GUI's "Promote
    all") and flagged in the interactive/GUI review, but a user who reads the
    warning can still promote them one at a time. This is the gate that would
    have stopped 'minu' -> 'menu' (common-word rewrite fed into the ASR
    prompt) and 'give' -> 'make' (single sighting of an ordinary edit)."""
    reasons = []
    if " " not in frm and frm.lower() in english:
        reasons.append(
            "'%s' is a real English word — this rule would rewrite it every "
            "time you say it" % frm)
    if count < MIN_SIGHTINGS:
        reasons.append("seen only once — could be a one-off edit, not a mishearing")
    return (bool(reasons), "; ".join(reasons))


def _load_english_or_warn():
    """The system word list, warning loudly (stderr) when it's unavailable —
    without it the common-word risk gate silently stops catching rules like
    'give' -> 'make', which is worth the user knowing."""
    english = load_english_words()
    if not english:
        print("VivoType: system word list unavailable — the common-word risk "
              "check on promotions is disabled.", file=sys.stderr)
    return english


def classify(frm, to, english):
    """Route a correction: 'lexicon' for near-miss names, else 'dictionary'."""
    frm, to = frm.strip(), to.strip()
    name_like = (
        " " not in to and to.isalpha()
        and to[:1].isupper() and to[1:].islower()
        and to.lower() not in english
    )
    # Lexicon only when the fuzzy matcher itself would turn `frm` into `to`.
    # Its own rules (capitalised input only, English words untouched, its
    # distance threshold) differ from a bare edit distance, so a pair routed
    # here on distance alone was reported promoted but never fired. A
    # dictionary rule always fires.
    if " " not in frm and name_like:
        if correct_names(frm, names=[to], english=english) == to:
            return "lexicon"
    return "dictionary"


def _promotion_refused(frm, to):
    """A rule that turns a word into a bare symbol ("comma -> ,") is never
    promoted: it would rewrite the real word everywhere. Words stay words."""
    return is_punctuation_pair(frm, to)


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


def _require_rule_shapes(data, path):
    """Refuse to rewrite a lexicon/dictionary whose arrays or map have the wrong
    shape, rather than crash mid-edit or rewrite them as something else."""
    for key in ("names", "learned", "learned_added"):
        if key in data and not (isinstance(data[key], list)
                                and all(isinstance(n, str) for n in data[key])):
            raise ValueError(f"Refusing to modify '{path}': '{key}' is not a list of names.")
    if "replacements" in data and not isinstance(data["replacements"], dict):
        raise ValueError(f"Refusing to modify '{path}': 'replacements' is not an object.")


def promote_to_lexicon(name, path):
    data = _load_json_object(path)
    _require_rule_shapes(data, path)
    if not data:
        data = {"_comment": "Names lexicon for VivoType's fuzzy matcher.", "names": []}
    names = data.setdefault("names", [])
    added_name = name.lower() not in {n.lower() for n in names}
    if added_name:
        names.append(name)
        names.sort(key=str.lower)
    learned = data.setdefault("learned", [])
    added_learned = name.lower() not in {n.lower() for n in learned}
    if added_learned:
        learned.append(name)
        learned.sort(key=str.lower)
    # Names learning put into `names` (not ones that were already contacts),
    # so deleting a learned name later never removes a real contact.
    if added_name:
        learned_added = data.setdefault("learned_added", [])
        if name.lower() not in {n.lower() for n in learned_added}:
            learned_added.append(name)
            learned_added.sort(key=str.lower)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return added_name, added_learned


def promote_to_dictionary(frm, to, path):
    """Write the rule; return the value it replaced (None if the key was new)."""
    data = _load_json_object(path)
    _require_rule_shapes(data, path)
    replacements = data.setdefault("replacements", {})
    previous = replacements.get(frm.lower())
    replacements[frm.lower()] = to
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return previous


def revert_lexicon(undo, path):
    """Undo one lexicon promotion: remove only what that promotion added, so a
    name that was already there (e.g. imported from Contacts) survives.

    A name leaves `names` only while it is still in `learned`: the first Undo
    clears `learned`, so replaying the same token cannot delete a name that
    came back since. Tokens are still single-use by contract."""
    data = _load_json_object(path)
    _require_rule_shapes(data, path)
    low = undo["name"].strip().lower()
    still_learned = low in {str(n).lower() for n in data.get("learned", [])}
    changed = False
    for key, flag in (("names", "added_name"), ("learned", "added_learned"),
                      ("learned_added", "added_name")):
        if key == "names" and not still_learned:
            continue
        if undo[flag] and key in data:
            kept = [n for n in data[key] if str(n).lower() != low]
            changed = changed or len(kept) != len(data[key])
            data[key] = kept
    if changed:
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return "reverted" if changed else "nothing_to_undo"


def revert_dictionary(undo, path):
    """Undo one dictionary promotion: restore the value it replaced, or drop the
    key if it was new. Leaves the rule alone if it changed since the promotion."""
    data = _load_json_object(path)
    _require_rule_shapes(data, path)
    replacements = data.get("replacements", {})
    key = undo["key"]
    if replacements.get(key) != undo["to"]:
        return "changed_since"
    if undo["previous"] is None:
        replacements.pop(key)
    else:
        replacements[key] = undo["previous"]
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return "reverted"


def list_rules(lexicon_path, config_path):
    """The user's active rules, for the Review window: every personal
    dictionary replacement, and every name learning added."""
    rules = []
    config = _load_json_object(config_path)
    replacements = config.get("replacements", {})
    if isinstance(replacements, dict):
        rules = [{"from": k, "to": v} for k, v in replacements.items() if isinstance(v, str)]
        rules.sort(key=lambda r: r["from"].lower())
    lexicon = _load_json_object(lexicon_path)
    learned = lexicon.get("learned", [])
    names = sorted((n for n in learned if isinstance(n, str)), key=str.lower) \
        if isinstance(learned, list) else []
    return {"rules": rules, "names": names}


def delete_rule(frm, to, path):
    """Delete one dictionary rule, only if it still reads frm -> to."""
    data = _load_json_object(path)
    _require_rule_shapes(data, path)
    replacements = data.get("replacements", {})
    # The key as listed (a hand-edited "Foo" stays "Foo"), else the
    # lowercased form promote writes.
    key = frm if frm in replacements else frm.strip().lower()
    if key not in replacements:
        return "not_found"
    if replacements[key] != to:
        return "changed_since"
    replacements.pop(key)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return "deleted"


def delete_name(name, path):
    """Forget one learned name. It leaves `names` only when learning added
    it (`learned_added`); a name that was already a contact stays."""
    data = _load_json_object(path)
    _require_rule_shapes(data, path)
    low = name.strip().lower()
    if low not in {str(n).lower() for n in data.get("learned", [])}:
        return "not_found", False
    added = low in {str(n).lower() for n in data.get("learned_added", [])}
    keys = ("learned", "learned_added", "names") if added else ("learned",)
    for key in keys:
        if key in data:
            data[key] = [n for n in data[key] if str(n).lower() != low]
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return "deleted", added


def _undo_token_valid(undo):
    """Shape check for a token that arrives as JSON on argv."""
    rows = undo.get("rows", [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return False
    if undo.get("target") == "lexicon":
        return (isinstance(undo.get("name"), str) and undo["name"].strip() != ""
                and isinstance(undo.get("added_name"), bool)
                and isinstance(undo.get("added_learned"), bool))
    if undo.get("target") == "dictionary":
        return (isinstance(undo.get("key"), str) and undo["key"] != ""
                and isinstance(undo.get("to"), str)
                and "previous" in undo
                and (undo["previous"] is None or isinstance(undo["previous"], str)))
    return False


def rewrite_log(path, entries):
    """Rewrite the corrections log atomically (crash-safe)."""
    path = Path(path)
    body = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
    atomic_write_text(path, body)


def apply_one(action, frm, to, log_path, lexicon_path, config_path, english=None,
              allow_unlogged=False, undo=None):
    """Apply a single promote/discard/remove to one from->to group (used by the GUI).

    A promote returns an `undo` token describing exactly what it changed,
    including the review-queue rows it consumed. `remove` requires that token
    and reverses only that change. Callers must use each token at most once."""
    frm = frm.strip()
    to = to.strip()
    items = load_corrections(log_path)
    key = (frm.lower(), to)

    def group_key(it):
        return (str(it.get("from", "")).strip().lower(), str(it.get("to", "")).strip())

    matched = [it for it in items if group_key(it) == key]
    rest = [it for it in items if group_key(it) != key]

    if action in ("delete-rule", "delete-name"):
        try:
            if action == "delete-rule":
                status, from_names = delete_rule(frm, to, config_path), False
            else:
                status, from_names = delete_name(to, lexicon_path)
        except ValueError as exc:
            return {"ok": False, "status": "write_failed", "error": str(exc)}
        return {"ok": status == "deleted", "action": action, "status": status,
                "removed_from_names": from_names}

    if action == "remove":
        if not isinstance(undo, dict):
            return {"ok": False, "status": "undo_required"}
        if not _undo_token_valid(undo):
            return {"ok": False, "status": "undo_invalid"}
        target = undo["target"]
        if target == "lexicon":
            matches = undo["name"].strip().lower() == to.lower()
        else:
            matches = undo["key"] == frm.lower() and undo["to"] == to
        if not matches:
            return {"ok": False, "status": "undo_mismatch"}
        try:
            if target == "lexicon":
                status = revert_lexicon(undo, lexicon_path)
            else:
                status = revert_dictionary(undo, config_path)
        except ValueError as exc:
            return {"ok": False, "status": "write_failed", "error": str(exc)}
        if status == "changed_since":
            return {"ok": False, "action": action, "target": target, "status": status,
                    "restored": 0}
        # Put back the review-queue rows the promote consumed, once each.
        missing = [r for r in undo.get("rows", []) if r not in items]
        if missing:
            rewrite_log(log_path, items + missing)
        return {"ok": True, "action": action, "target": target, "status": status,
                "restored": len(missing)}

    if not matched and not (action == "promote" and allow_unlogged):
        return {"ok": False, "status": "not_found"}

    target = None
    undo_token = None
    if action == "promote":
        if _promotion_refused(frm, to):
            return {"ok": False, "status": "refused_punctuation", "target": None}
        # Deliberately NOT risk-gated: --apply is the per-rule path where the
        # user has already seen the ⚠ warning in the Review UI and chosen to
        # promote anyway. Only BULK promotion refuses risky rules.
        if english is None:
            english = load_english_words()
        target = classify(frm, to, english)
        try:
            if target == "lexicon":
                added_name, added_learned = promote_to_lexicon(to, lexicon_path)
                undo_token = {"target": "lexicon", "name": to,
                              "added_name": added_name, "added_learned": added_learned,
                              "rows": matched}
            else:
                previous = promote_to_dictionary(frm, to, config_path)
                undo_token = {"target": "dictionary", "key": frm.lower(), "to": to,
                              "previous": previous, "rows": matched}
        except ValueError as exc:
            # Promotion failed (e.g. a corrupt target file). Keep the corrections
            # in the log so the user can retry after fixing the file.
            return {"ok": False, "status": "write_failed", "error": str(exc)}
    if matched:
        rewrite_log(log_path, rest)  # both promote and discard remove the entries
    result = {"ok": True, "action": action, "target": target,
              "removed": len(matched)}
    if undo_token is not None:
        result["undo"] = undo_token
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="vivotype-promote",
        description="Promote captured corrections into VivoType's rules.",
    )
    parser.add_argument("--log", default=str(LOG_PATH))
    parser.add_argument("--lexicon", default=str(LEXICON_PATH))
    parser.add_argument("--config", default=str(USER_DICT_PATH),
                        help="Where dictionary promotions are written (default: personal overlay).")
    parser.add_argument("--yes", action="store_true",
                        help="Promote everything not flagged risky, no prompts.")
    parser.add_argument("--dry-run", action="store_true", help="List suggestions; change nothing.")
    parser.add_argument("--list-json", action="store_true", help="Print pending corrections as JSON and exit.")
    parser.add_argument("--apply", action="store_true", help="Apply one action (with --from/--to/--action).")
    parser.add_argument("--from", dest="from_word", help="The misrecognized word (with --apply).")
    parser.add_argument("--to", dest="to_word", help="The correct word (with --apply).")
    parser.add_argument("--action",
                        choices=["promote", "discard", "remove", "delete-rule", "delete-name"],
                        help="Action for --apply. delete-rule/delete-name take no undo "
                             "token: they delete an active rule from the Review window.")
    parser.add_argument("--list-rules-json", action="store_true",
                        help="Print the active dictionary rules and learned names as JSON.")
    parser.add_argument("--allow-unlogged", action="store_true",
                        help="With --apply promote, write the rule even when no log row exists.")
    parser.add_argument("--undo", help="With --apply remove: the JSON `undo` token a promote returned.")
    parser.add_argument("--stdin-json", action="store_true",
                        help="With --apply: read {\"from\", \"to\", \"undo\"?} as a JSON object on "
                             "stdin instead of --from/--to/--undo, so the words (and the "
                             "undo token's log rows) never appear on the command line.")
    args = parser.parse_args(argv)

    if args.list_rules_json:
        try:
            print(json.dumps(list_rules(args.lexicon, args.config), ensure_ascii=False))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.list_json:
        english = _load_english_or_warn()
        groups = group_corrections(load_corrections(args.log))
        payload = []
        for g in groups:
            risky, reason = assess_risk(g["from"], g["to"], g["count"], english)
            payload.append({"from": g["from"], "to": g["to"], "count": g["count"],
                            "target": classify(g["from"], g["to"], english),
                            "risky": risky, "risk_reason": reason,
                            # Old log rows can hold pairs promote now refuses;
                            # the Review UI offers only Discard for them.
                            "refused": _promotion_refused(g["from"], g["to"])})
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if args.apply:
        raw_undo = args.undo
        if args.stdin_json:
            try:
                payload = json.loads(sys.stdin.read())
            except (ValueError, RecursionError):  # JSONDecodeError is a ValueError
                payload = None
            if not (isinstance(payload, dict)
                    and isinstance(payload.get("from"), str)
                    and isinstance(payload.get("to"), str)):
                print("error: --stdin-json needs a JSON object with string "
                      "\"from\" and \"to\" on stdin", file=sys.stderr)
                return 2
            args.from_word, args.to_word = payload["from"], payload["to"]
            raw_undo = payload.get("undo")
        if not (args.from_word and args.to_word and args.action):
            print("error: --apply requires --from, --to, and --action", file=sys.stderr)
            return 2
        undo = None
        if isinstance(raw_undo, dict):
            undo = raw_undo
        elif raw_undo:
            try:
                undo = json.loads(raw_undo)
            except (TypeError, ValueError, RecursionError):  # JSONDecodeError is a ValueError
                print("error: --undo must be JSON", file=sys.stderr)
                return 2
        result = apply_one(args.action, args.from_word, args.to_word,
                           args.log, args.lexicon, args.config,
                           allow_unlogged=args.allow_unlogged, undo=undo)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    groups = group_corrections(load_corrections(args.log))
    if not groups:
        print("No pending corrections. 🎉")
        return 0

    english = _load_english_or_warn()
    print(f"{len(groups)} pending correction(s), most frequent first:\n")

    kept = []
    promoted = 0
    discarded = 0
    for group in groups:
        target = classify(group["from"], group["to"], english)
        risky, reason = assess_risk(group["from"], group["to"], group["count"], english)
        where = "names lexicon" if target == "lexicon" else "dictionary"
        summary = f"{group['from']!r} → {group['to']!r}   [{where}, seen {group['count']}×]"
        if risky:
            summary += f"\n  ⚠ {reason}"

        if args.dry_run:
            print("  " + summary)
            kept.extend(group["entries"])
            continue

        if args.yes:
            if risky:
                # Bulk promotion never takes a risky rule — review it manually.
                print("  " + summary + "\n  → skipped (risky)")
                kept.extend(group["entries"])
                continue
            if _promotion_refused(group["from"], group["to"]):
                print("  " + summary + "\n  → skipped (punctuation target)")
                kept.extend(group["entries"])
                continue
            choice = "y"
            print("  " + summary + "  → promoting")
        else:
            choice = input(
                "  " + summary + "\n  Promote / Skip / Discard? [y/N/d] "
            ).strip().lower()

        if choice == "y":
            if _promotion_refused(group["from"], group["to"]):
                print("  ! skipped — symbol target")
                kept.extend(group["entries"])
                continue
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
