#!/usr/bin/env bash
# publish_to_public.sh — sync sanitized code to ../VivoType (open-source mirror)
#
# Run from the root of the Private Master Repo (this directory).
# The public mirror gets a SEPARATE git history — private data can never leak
# through git log, even if git-filter-branch is run on the public side.
#
# Usage:
#   ./scripts/publish_to_public.sh                  # sync + auto-commit in mirror
#   ./scripts/publish_to_public.sh --dry-run        # show what would change

set -euo pipefail

MASTER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC_DIR="$(cd "$MASTER_DIR/.." && pwd)/VivoType"
DRY_RUN=false

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
  --exclude="core/config.json"
  --exclude="clients/mac/build/"
  --exclude="models/"
  --exclude="*.bin"
  --exclude="*.wav"    # no personal audio recordings in the public mirror
  --exclude="context/"              # internal dev loop notes (STATE.md, ITERATION_LOG.md)
  --exclude="diff_report.txt"       # internal diff artifact
  --exclude="core/data/prompts/"    # personal training paragraph (names, anecdotes)
  --exclude="CLAUDE.md"             # internal workflow instructions for the private repo
  --exclude=".claude/"              # Claude Code session data
)

if $DRY_RUN; then
  RSYNC_ARGS+=(--dry-run --verbose)
fi

rsync "${RSYNC_ARGS[@]}" "$MASTER_DIR/" "$PUBLIC_DIR/"

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

# ── 5. Push to public GitHub remote (if one is configured) ──────────────────
cd "$PUBLIC_DIR"
if git remote get-url origin &>/dev/null; then
  git push origin main
  echo "Pushed to $(git remote get-url origin)"
else
  echo "No remote configured in $PUBLIC_DIR — add one with:"
  echo "  git remote add origin https://github.com/kalpitt/VivoType.git"
  echo "  git push -u origin main"
fi
