#!/usr/bin/env bash
# publish_to_public.sh — sync sanitized code to ../VivoType (open-source mirror)
#
# Run from the root of the Private Master Repo (this directory).
# The public mirror gets a SEPARATE git history — private data can never leak
# through git log, even if git-filter-branch is run on the public side.
#
# What gets published is the COMMITTED tree of origin/main (via `git archive`),
# never the working tree, and the real run refuses unless the checkout is main,
# clean, and equal to origin/main. The push waits for a y/N typed at an
# interactive terminal.
#
# Usage:
#   ./scripts/publish_to_public.sh                  # sync + auto-commit in mirror
#   ./scripts/publish_to_public.sh --dry-run        # show what would change

set -euo pipefail

# shellcheck source=lib/publish_safety_gate.sh
source "$(cd "$(dirname "$0")" && pwd)/lib/publish_safety_gate.sh"

MASTER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC_DIR="$(cd "$MASTER_DIR/.." && pwd)/VivoType"
DRY_RUN=false

# The canary VALUE lives only in the excluded context/STATE.md — never as a
# literal here, because this script itself is published and a hardcoded token
# would plant the tripwire string into the very history the gate watches
# (found 2026-08-23 when the gate tripped on its own script). Fail closed if
# the canary is missing: an unplanted tripwire passes vacuously.
CANARY="$(grep -m1 -o 'VIVOTYPE-PRIVATE-CANARY-[A-Za-z0-9-]\+' \
  "$MASTER_DIR/context/STATE.md" || true)"
if [ -z "$CANARY" ]; then
  echo "FATAL: no canary planted in context/STATE.md — refusing to publish:" >&2
  echo "       the leak tripwire would pass vacuously. Plant it first." >&2
  exit 1
fi

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

echo "=== VivoType Publish Script ==="
echo "  Master : $MASTER_DIR"
echo "  Public : $PUBLIC_DIR"
if $DRY_RUN; then echo "  Mode   : DRY RUN (no changes written)"; fi
echo ""

# ── 0. Publish exactly origin/main, from a clean main checkout ───────────────
# Rsyncing the working tree used to publish whatever branch was checked out,
# plus any untracked file the denylist missed (a .bak copy of personal data, a
# *.tmp left by an interrupted atomic write, a test cache). A dry run reports
# these problems and carries on so the preview stays usable; a real run refuses.
PREFLIGHT_PROBLEMS=()
BRANCH="$(git -C "$MASTER_DIR" rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  PREFLIGHT_PROBLEMS+=("the checkout is on '$BRANCH', not main")
fi
if [ -n "$(git -C "$MASTER_DIR" status --porcelain --untracked-files=all)" ]; then
  PREFLIGHT_PROBLEMS+=("the working tree has uncommitted or untracked files (see git status)")
fi
if git -C "$MASTER_DIR" fetch --quiet origin main; then
  if [ "$(git -C "$MASTER_DIR" rev-parse HEAD)" != "$(git -C "$MASTER_DIR" rev-parse FETCH_HEAD)" ]; then
    PREFLIGHT_PROBLEMS+=("HEAD is not origin/main (pull first, or merge your PR first)")
  fi
else
  PREFLIGHT_PROBLEMS+=("could not fetch origin/main to compare against")
fi
if [ "${#PREFLIGHT_PROBLEMS[@]}" -gt 0 ]; then
  for problem in "${PREFLIGHT_PROBLEMS[@]}"; do echo "  PREFLIGHT: $problem" >&2; done
  if $DRY_RUN; then
    echo "  (dry run continues and previews HEAD; a real run would refuse)" >&2
    echo "" >&2
  else
    echo "FATAL: refusing to publish. Publish only a clean main that equals origin/main." >&2
    exit 1
  fi
fi

# Export the committed tree of HEAD. Untracked and ignored files never exist here.
EXPORT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vivotype-publish.XXXXXX")"
trap 'rm -rf "$EXPORT_DIR"' EXIT
git -C "$MASTER_DIR" archive --format=tar HEAD | tar -x -f - -C "$EXPORT_DIR"

# ── 1. Create the public directory if it doesn't exist ──────────────────────
if [ ! -d "$PUBLIC_DIR" ]; then
  if $DRY_RUN; then
    echo "[dry-run] Would create $PUBLIC_DIR"
  else
    mkdir -p "$PUBLIC_DIR"
    echo "Created $PUBLIC_DIR"
  fi
fi

# ── 2. rsync — copy code, exclude private data and build artifacts ───────────
RSYNC_ARGS=(
  --archive           # preserve permissions, timestamps, symlinks
  --delete            # remove files in public that no longer exist in master
  --checksum          # compare by content, not just mtime (more reliable)
  --prune-empty-dirs  # don't recreate dirs whose every file was filtered out
  --exclude=".git/"
  --exclude=".venv/"
  --exclude="__pycache__/"
  --exclude="*.pyc"
  --exclude="*.pyo"
  --exclude=".DS_Store"
  --exclude=".env"
  --exclude="core/data/corrections.jsonl"
  --exclude="core/data/labels.csv"
  --exclude="core/data/lexicon/"
  --exclude="core/data/user_dictionary.json"
  --exclude="core/data/raw/"
  --exclude="core/data/*.json"    # catch-all: any future personal json files in core/data/
  --exclude="core/data/*.jsonl"   # catch-all: any future personal jsonl files
  --exclude="core/data/*.csv"     # catch-all: any future personal csv files
  # core/data/ is an ALLOWLIST: only its README and .gitkeep placeholders are
  # public. The lines above name known files; these catch every rename, nested
  # copy or temp file (corrections.jsonl.bak, backup/contacts.json, *.tmp).
  # rsync applies the first matching rule, so keep these five together.
  --include="core/data/README.md"
  --include="core/data/.gitkeep"
  --include="core/data/**/"
  --include="core/data/**/.gitkeep"
  --exclude="core/data/**"
  --exclude="core/config.json"
  --exclude="clients/mac/build/"
  --exclude="models/"
  --exclude="*.bin"
  --exclude="*.wav"    # no personal audio recordings in the public mirror
  --exclude="context/"              # internal dev loop notes (STATE.md, ITERATION_LOG.md)
  --exclude="diff_report.txt"       # internal diff artifact
  --exclude="core/data/prompts/"    # personal training paragraph (names, anecdotes)
  --exclude="CLAUDE.md"             # internal workflow instructions for the private repo
  --exclude="AGENTS.md"             # rulebook renamed 2026-07-12 — same exclusion policy
  --exclude=".claude/"              # Claude Code session data
  --exclude="docs/FABLE5_SESSION_PROMPTS.md"  # private cross-repo planning content: names other
                                    # repos and contains local machine paths. Saavdhan's
                                    # publish_mirror.sh already strips this same file; this
                                    # copy was never excluded here — found 2026-07-31 when the
                                    # new local-path gate tripped on it during a dry run.
)

if $DRY_RUN; then
  RSYNC_ARGS+=(--dry-run --verbose)
fi

rsync "${RSYNC_ARGS[@]}" "$EXPORT_DIR/" "$PUBLIC_DIR/"

if $DRY_RUN; then
  echo ""
  echo "=== Dry run complete. No files were written. ==="
  exit 0
fi

# ── 3. Initialize git in the public mirror (once) ───────────────────────────
if [ ! -d "$PUBLIC_DIR/.git" ]; then
  git -C "$PUBLIC_DIR" init -b main
  echo "Initialized fresh git repo in $PUBLIC_DIR (separate history from master)"
fi

# ── 4. Auto-commit the snapshot ──────────────────────────────────────────────
cd "$PUBLIC_DIR"

# Stage everything (deletions included via --delete above already happened on disk)
git add -A

if git diff --cached --quiet; then
  echo "Public mirror is already up-to-date. Nothing to commit."
else
  MASTER_SHA=$(git -C "$MASTER_DIR" rev-parse --short HEAD)
  git commit -m "chore: sync from private master @ $MASTER_SHA"
  echo ""
  echo "Committed snapshot to public mirror."
fi

echo ""
echo "=== Done ==="
echo ""

# ── 5. Safety gates — defense-in-depth even though the mirror has fresh history ──
gate_path_absent_from_history "$PUBLIC_DIR" "context/"
gate_path_absent_from_history "$PUBLIC_DIR" "CLAUDE.md"
gate_path_absent_from_history "$PUBLIC_DIR" "AGENTS.md"
# Real personal data. Each of these was excluded by exactly one rsync line and
# nothing checked the result (audit, 2026-08-29). A denylist fails open on a
# rename or a typo; these gates fail the push instead of shipping the data.
gate_path_absent_from_history "$PUBLIC_DIR" "core/data/prompts/"   # name, anecdotes, a financial figure
gate_path_absent_from_history "$PUBLIC_DIR" "core/data/lexicon/"   # ~2,785 real contact names
gate_path_absent_from_history "$PUBLIC_DIR" ".claude/"             # session data, agent configs
# All of core/data/ except the allowlisted README and placeholders — matches
# the rsync allowlist above, so a rename or nested copy fails here too.
gate_path_absent_from_history "$PUBLIC_DIR" "core/data/" \
  ':(exclude)core/data/README.md' ':(exclude,glob)core/data/**/.gitkeep'
gate_canary_absent_from_history "$PUBLIC_DIR" "$CANARY"
gate_no_local_paths_in_content "$PUBLIC_DIR"
echo "Safety gates passed."

# ── 6. Push to public GitHub remote (if one is configured) ──────────────────
cd "$PUBLIC_DIR"
if git remote get-url origin &>/dev/null; then
  # The human confirmation step. It needs a real terminal: with no one at the
  # keyboard (an agent's shell, a pipe) it refuses rather than guess.
  if [ ! -t 0 ]; then
    echo "FATAL: the push asks for a typed y/N and stdin is not a terminal." >&2
    echo "       Run this in your own Terminal to push. The mirror commit is" >&2
    echo "       kept locally; re-running pushes it." >&2
    exit 1
  fi
  gate_confirm "Push $PUBLIC_DIR to $(git remote get-url origin) (public)?"
  git push origin main
  echo "Pushed to $(git remote get-url origin)"
else
  echo "No remote configured in $PUBLIC_DIR — add one with:"
  echo "  git remote add origin https://github.com/kalpitt/VivoType.git"
  echo "  git push -u origin main"
fi
