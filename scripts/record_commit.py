#!/usr/bin/env python3
"""record_commit.py — the ONLY sanctioned direct write to `main` (record lane).

Usage (from the repo root):
    python3 scripts/record_commit.py "<message>" <file> [<file>...]
    python3 scripts/record_commit.py "<message>" --merged <file> ...

`--merged <file>`: assert that you ALREADY read the newer version of <file>
on origin/main and merged it into your copy — use it only when this script's
lost-update check told you to, never preemptively.

What it does, in order:
 1. Validates every file against the ```record-lane allowlist fence in
    AGENTS.md **as merged on origin/main** — a local edit to the fence has no
    effect, so widening the allowlist genuinely requires a Kalpit-merged PR.
 2. Refuses deletions, renames, secret-shaped content, and anything not
    allowlisted. Files under context/handoffs/ are append-only: the version
    on origin/main must be a byte-for-byte prefix of the new content.
 3. Lost-update check per file: if it changed on origin/main since this
    checkout last saw it AND your copy differs, it refuses — no silent
    last-writer-wins.
 4. Commits ONLY the named files in a temporary worktree of origin/main and
    pushes. Never force. Retries a few times on a push race. Your checkout
    and your branch are never committed on, whatever happens.

Everything this script refuses belongs to the review lane:
branch -> PR -> Kalpit merges. See AGENTS.md "Write policy".

Design rules (personal-os governance standard, same family as guard hooks):
- Stdlib only, system python3.
- FAIL-LOUD, never destructive: on any conflict or surprise it stops, explains,
  and leaves your files untouched. It never deletes remote content.
"""
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ALLOWLIST_FENCE = "```record-lane"
PREFIXES = ("state:", "handoff:", "log:", "queue:", "record:")
PUSH_ATTEMPTS = 5
# Append-only logs: a record commit may add to them, never rewrite history.
APPEND_ONLY_PREFIXES = ("context/handoffs/",)

# Mirrors the guard hooks' SECRET_PATTERNS: records reach main without PR
# review, so a pasted credential must be stopped here, before the commit.
# Boundaries are "no letter/digit before" rather than \b: \b fails after an
# underscore, so an env-style "TOKEN_ghp_..." would slip through.
_B = r"(?<![A-Za-z0-9])"
SECRET_PATTERNS = [
    (r"AIza[0-9A-Za-z_-]{35}", "Google API key"),
    (_B + r"sk-[A-Za-z0-9_-]{32,}", "OpenAI/Anthropic-style secret key"),
    # ghp_ classic PAT, gho_ OAuth, ghu_ user-to-server, ghs_ server-to-server,
    # ghr_ refresh token; github_pat_ fine-grained PAT.
    (_B + r"gh[pousr]_[A-Za-z0-9]{36,}|" + _B + r"github_pat_[A-Za-z0-9_]{22,}", "GitHub token"),
    (_B + r"AKIA[0-9A-Z]{16}(?![0-9A-Z])", "AWS access key ID"),
    (_B + r"xox[baprs]-[0-9A-Za-z-]{10,}", "Slack token"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "private key material"),
]


def die(msg):
    print(f"record-commit: BLOCKED — {msg}", file=sys.stderr)
    sys.exit(1)


def sh(args, cwd, check=True):
    r = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if check and r.returncode != 0:
        die(f"`{' '.join(args)}` failed:\n{(r.stderr or r.stdout).strip()}")
    return r


def load_allowlist(root):
    """Parse the record-lane fence from AGENTS.md AS MERGED ON origin/main."""
    r = sh(["git", "show", "FETCH_HEAD:AGENTS.md"], root, check=False)
    if r.returncode != 0:
        die("no AGENTS.md on origin/main — this repo has no record lane yet; "
            "use a branch + PR.")
    entries, inside, fences = [], False, 0
    for line in r.stdout.splitlines():
        s = line.strip()
        if s == ALLOWLIST_FENCE:
            fences += 1
            inside = True
            continue
        if inside and s.startswith("```"):
            inside = False
            continue
        if inside and s and not s.startswith("#"):
            entries.append(s)
    if fences == 0 or not entries:
        die("no ```record-lane allowlist in AGENTS.md on origin/main — this "
            "repo has no record lane; use a branch + PR.")
    if fences > 1:
        die("multiple ```record-lane fences in AGENTS.md — ambiguous "
            "allowlist; fix AGENTS.md via the review lane first.")
    for e in entries:
        if e.startswith("/") or ".." in e or e in (".", "./"):
            die(f"malformed allowlist entry '{e}' — fix AGENTS.md via the "
                "review lane first.")
    return entries


def normalize(arg, root):
    rel = os.path.relpath(os.path.realpath(os.path.abspath(arg)),
                          os.path.realpath(root))
    if rel.startswith(".."):
        die(f"'{arg}' is outside this repository.")
    return rel.replace(os.sep, "/")


def check_allowed(rel, allowlist):
    for entry in allowlist:
        if entry.endswith("/"):
            if rel.startswith(entry):
                return
        elif rel == entry:
            return
    die(f"'{rel}' is not on the record-lane allowlist (as merged on "
        "origin/main). Records only — everything else goes branch -> PR "
        "(review lane). If this file genuinely belongs on the allowlist, "
        "that widening is itself a review-lane change to AGENTS.md.")


def scan_secrets(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        die(f"cannot read '{rel}': {e}")
    for pat, what in SECRET_PATTERNS:
        if re.search(pat, text):
            die(f"'{rel}' contains secret-shaped content ({what}). Records "
                "skip PR review, so secrets must never ride them. Redact it, "
                "then re-run.")


def check_append_only(root, rel):
    """For append-only logs, origin/main's version must be a byte-for-byte
    prefix of the new content — a record may add lines, never drop or edit
    them. Compared as bytes so a line-ending rewrite counts as an edit."""
    if not rel.startswith(APPEND_ONLY_PREFIXES):
        return
    r = subprocess.run(["git", "show", f"FETCH_HEAD:{rel}"], cwd=root,
                       capture_output=True)
    if r.returncode != 0:
        return  # new file on main — nothing to preserve
    try:
        with open(os.path.join(root, rel), "rb") as f:
            new = f.read()
    except OSError as e:
        die(f"cannot read '{rel}': {e}")
    if not new.startswith(r.stdout):
        die(f"'{rel}' is append-only, and your copy does not start with the "
            "version on origin/main (a line was removed or edited). Restore "
            "the existing text (git show FETCH_HEAD:<file>), add your notes "
            "after it, then re-run. A genuine correction to an old handoff "
            "is review-lane: branch -> PR.")


def read_ref_text(root, ref, rel):
    """Text of ref:rel, or None if that path doesn't exist there."""
    r = sh(["git", "show", f"{ref}:{rel}"], root, check=False)
    return r.stdout if r.returncode == 0 else None


def blob(root, ref, rel):
    """Blob SHA of ref:rel, or None if absent there."""
    r = sh(["git", "rev-parse", f"{ref}:{rel}"], root, check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def state_path(root):
    gd = sh(["git", "rev-parse", "--git-dir"], root).stdout.strip()
    return os.path.join(root if not os.path.isabs(gd) else "", gd,
                        "record-lane-state.json")


def load_state(root):
    try:
        with open(state_path(root), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(root, state):
    try:
        with open(state_path(root), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass  # best-effort cache; losing it only means an extra --merged ask


def staged_ok(cwd, files):
    r = sh(["git", "diff", "--cached", "--name-status"], cwd)
    for l in [l for l in r.stdout.strip().splitlines() if l.strip()]:
        status, _, path = l.partition("\t")
        if status.strip() not in ("A", "M") or path.strip() not in files:
            die(f"staged change '{l}' is not a plain add/update of a named "
                "file — deletions, renames, and strays are never record-lane.")


def push_with_retry(wt):
    """Push HEAD:main; on a race, rebase our one commit onto the newer
    origin/main and retry with backoff. On a genuine conflict: abort and stop
    (the worktree is discarded; your files are untouched — nothing lost)."""
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        p = sh(["git", "push", "origin", "HEAD:main"], wt, check=False)
        if p.returncode == 0:
            return sh(["git", "rev-parse", "--short", "HEAD"], wt).stdout.strip()
        if attempt == PUSH_ATTEMPTS:
            die(f"push to main failed after {PUSH_ATTEMPTS} attempts:\n"
                f"{(p.stderr or p.stdout).strip()}")
        time.sleep(attempt)
        sh(["git", "fetch", "origin", "main"], wt)
        r = sh(["git", "rebase", "FETCH_HEAD"], wt, check=False)
        if r.returncode != 0:
            sh(["git", "rebase", "--abort"], wt, check=False)
            die("another session pushed a conflicting change to main in the "
                "same instant. Nothing was lost — your files are untouched in "
                "your working tree. Re-run record_commit.py; if it reports a "
                "lost-update conflict, follow its --merged instructions.")
    return None  # unreachable


def main():
    if len(sys.argv) < 3:
        die('usage: python3 scripts/record_commit.py "<message>" <file> ...')
    message = sys.argv[1].strip()
    if not message.lower().startswith(PREFIXES):
        message = "state: " + message

    root = sh(["git", "rev-parse", "--show-toplevel"], os.getcwd()).stdout.strip()
    sh(["git", "fetch", "origin", "main"], root)
    allowlist = load_allowlist(root)

    candidates, merged_ok, args, i = [], set(), sys.argv[2:], 0
    while i < len(args):
        arg = args[i]
        is_merged = arg == "--merged"
        if is_merged:
            i += 1
            if i >= len(args):
                die("--merged needs a file after it.")
            arg = args[i]
        rel = normalize(arg, root)
        check_allowed(rel, allowlist)
        if not os.path.isfile(os.path.join(root, rel)):
            die(f"'{rel}' does not exist as a regular file — record commits "
                "only add or update files; deletions and renames are "
                "review-lane.")
        scan_secrets(root, rel)
        check_append_only(root, rel)
        candidates.append(rel)
        if is_merged:
            merged_ok.add(rel)
        i += 1

    # Lost-update check. Base = what this checkout last saw: its HEAD version,
    # or the blob this script itself last recorded from this checkout.
    last = load_state(root)
    files, conflicts, mine_sha = [], [], {}
    for rel in candidates:
        mine = sh(["git", "hash-object", os.path.join(root, rel)],
                  root).stdout.strip()
        base = blob(root, "HEAD", rel)
        origin = blob(root, "FETCH_HEAD", rel)
        mine_sha[rel] = mine
        if origin == mine:
            continue  # already recorded — no-op
        if (origin is None or origin == base or origin == last.get(rel)
                or rel in merged_ok):
            files.append(rel)
        else:
            conflicts.append(rel)
    if conflicts:
        die("changed on origin/main since this session read them: "
            f"{', '.join(conflicts)}. No silent overwrite — read the newer "
            "version (git show FETCH_HEAD:<file>), merge your update into "
            "your copy by hand, then re-run with --merged <file> to confirm "
            "you did.")
    if not files:
        print("record-commit: nothing to record (files already match main).")
        return

    branch = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], root).stdout.strip()

    # Commit in a clean temporary worktree of origin/main — the primary
    # checkout and its branch are never committed on, and stray local-main
    # commits can never ride along with the push.
    tmp = tempfile.mkdtemp(prefix="record-lane-")
    wt = os.path.join(tmp, "wt")
    try:
        sh(["git", "worktree", "add", "--detach", wt, "FETCH_HEAD"], root)
        for rel in files:
            dest = os.path.join(wt, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(os.path.join(root, rel), dest)
        sh(["git", "add", "--"] + files, wt)
        staged_ok(wt, files)
        sh(["git", "commit", "-m", message], wt)
        commit = push_with_retry(wt)
    finally:
        sh(["git", "worktree", "remove", "--force", wt], root, check=False)
        shutil.rmtree(tmp, ignore_errors=True)

    last.update({rel: mine_sha[rel] for rel in files})
    save_state(root, last)

    if branch == "main":
        # Bring the checkout current so the recorded files read as committed
        # state. Stash-first: the working copies are never reverted, even if
        # the fast-forward is refused by unrelated local changes.
        dirty = sh(["git", "status", "--porcelain", "--"] + files,
                   root).stdout.strip()
        stashed = False
        if dirty:
            s = sh(["git", "stash", "push", "--include-untracked",
                    "-m", "record-lane", "--"] + files, root, check=False)
            stashed = s.returncode == 0 and "No local changes" not in s.stdout
        sh(["git", "fetch", "origin", "main"], root)
        r = sh(["git", "merge", "--ff-only", "FETCH_HEAD"], root, check=False)
        if r.returncode == 0:
            if stashed:
                sh(["git", "stash", "drop"], root, check=False)
        else:
            if stashed:
                sh(["git", "stash", "pop"], root, check=False)
            print("record-commit: note — the record is on origin/main, but "
                  "local main could not fast-forward (unrelated local "
                  "changes). Run `git pull --ff-only` when convenient.")
    else:
        print(f"record-commit: NOTE — {', '.join(files)} stay in your working "
              "tree unchanged. Do NOT commit them to this branch (no `git add "
              "-A`); they are already on main.")

    print(f"record-commit: recorded to main @ {commit}: {', '.join(files)}")


if __name__ == "__main__":
    main()
