# publish_safety_gate.sh — shared private→public mirror safety checks.
#
# Same file, same functions, in every repo that publishes a private master repo
# to a public mirror (VivoType, Saavdhan). Each repo's own publish script sources
# this and calls the gates right before the push step. Keep the two copies
# identical — if you fix a bug in one, copy the fix to the other.
#
# Usage (from a publish script, after `set -euo pipefail`):
#   source "$(dirname "$0")/lib/publish_safety_gate.sh"
#   gate_path_absent_from_history "$MIRROR_DIR" "context/"
#   gate_canary_absent_from_history "$MIRROR_DIR" "MYPROJECT-PRIVATE-CANARY-xxxx"
#   gate_confirm "Push to public remote?"

# gate_path_absent_from_history <repo_dir> <path>
# Dies if <path> appears anywhere in <repo_dir>'s git history (not just the working tree —
# deleting a folder doesn't remove it from old commits).
gate_path_absent_from_history() {
  local repo_dir="$1" path="$2"
  if [ -n "$(git -C "$repo_dir" log --all --oneline -- "$path" 2>/dev/null || true)" ]; then
    echo "GATE FAILED: '$path' still present in mirror history ($repo_dir)." >&2
    exit 1
  fi
}

# gate_canary_absent_from_history <repo_dir> <canary_string>
# Dies if <canary_string> appears in any blob of any commit in <repo_dir>. Plant the canary
# string in a private-only file (e.g. context/STATE.md) so a hit can ONLY mean private content
# leaked into the mirror — never a false positive from legitimate public wording.
gate_canary_absent_from_history() {
  local repo_dir="$1" canary="$2"
  if git -C "$repo_dir" grep -l "$canary" $(git -C "$repo_dir" rev-list --all) >/dev/null 2>&1; then
    echo "GATE FAILED: canary string found in mirror history ($repo_dir) — private content leaked." >&2
    exit 1
  fi
}

# gate_confirm <prompt>
# Interactive y/N gate; aborts (exit 1) on anything but y/Y.
gate_confirm() {
  local prompt="$1" ok
  read -r -p "$prompt (y/N) " ok
  [[ "$ok" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
}

# gate_no_local_paths_in_content <repo_dir>
# Dies if any tracked file in <repo_dir>'s current tree contains a hardcoded local filesystem
# path — macOS `/Users/<name>/...` or Linux `/home/<name>/...`. This catches a whole CLASS of
# leak (a dev-machine path baked into a script/asset), not just one fixed path — see
# branding/_staging/make_brand.swift in VivoType, which shipped a `/Users/<name>/...` path to the
# public mirror this way.
#
# IMPORTANT: call this on the SANITISED artifact (the filter-repo'd stage dir / the
# rsync-excluded public dir), never on the raw private repo — context/ (private dev notes)
# legitimately contains machine paths that never reach a mirror, and scanning it first trains
# everyone to ignore the gate. Deliberately checks the current tree, not full history: for a
# mirror that force-pushes freshly rewritten history (Saavdhan) or accumulates commits over
# time (VivoType), a leak already sitting in an old, already-published commit can't be
# unpublished by this gate — its job is to stop the NEXT publish from shipping a new one.
#
# Known false positive: scripts/drift_check.sh legitimately *describes* this exact pattern in
# its own comments/messages (it runs the equivalent check on the raw repo) — that file is
# self-excluded so the gate doesn't trip on its own tooling.
gate_no_local_paths_in_content() {
  local repo_dir="$1"
  local pattern='/(Users|home)/[A-Za-z0-9_.-]+/'
  local hits
  hits="$(git -C "$repo_dir" grep -InE "$pattern" -- . 2>/dev/null \
    | grep -Ev ':scripts/drift_check\.sh:' || true)"
  if [ -n "$hits" ]; then
    echo "GATE FAILED: hardcoded local filesystem path (/Users/... or /home/...) found in $repo_dir:" >&2
    echo "$hits" | sed 's/^/        /' >&2
    exit 1
  fi
}
