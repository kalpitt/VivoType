#!/usr/bin/env bash
# VivoType — core backend setup (idempotent).
#
# Creates the isolated Python virtual environment (.venv) used by the ASR
# backend and installs its dependencies. Safe to re-run at any time.
#
# Usage:
#   setup_core.sh <app-support-dir>           create/repair the venv, install deps
#   setup_core.sh --check <app-support-dir>   verify an existing venv; no installs
#
# <app-support-dir> is the writable directory where the .venv must live
# (e.g. ~/Library/Application Support/VivoType). It is REQUIRED — the script
# never guesses a location. requirements.txt is read from the directory that
# contains this script's parent (the bundle's Resources/, or the repo root).
#
# Completion marker: the LAST step of a successful setup writes
# <venv>/.vivotype-setup-complete, a copy of the requirements.txt it installed.
# The app treats a venv without a marker matching the bundled requirements.txt
# as "setup needed", so an interrupted install (quit, network drop) is repaired
# on the next launch instead of leaving a venv that looks ready but isn't.
# `--check` adopts venvs built before the marker existed: if the venv already
# satisfies requirements.txt and imports the runtime modules, it writes the
# marker (exit 0); otherwise it exits 3 and the app runs the full setup.
#
# Exit codes:
#   0   success
#   3   --check only: the venv is missing, incomplete or outdated
#   42  no compatible Python 3.11+ interpreter found (caller shows install help)
#   1   any other failure (human-readable message on stderr)
set -euo pipefail

err() { printf '%s\n' "$*" >&2; }

# --- arguments -------------------------------------------------------------
MODE="setup"
if [ "${1:-}" = "--check" ]; then
  MODE="check"
  shift
fi
if [ "$#" -lt 1 ] || [ -z "${1:-}" ]; then
  err "Error: missing required argument."
  err "Usage: setup_core.sh [--check] <app-support-dir>"
  exit 1
fi

APP_SUPPORT="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# requirements.txt sits alongside core/ and scripts/ (Resources/ or repo root).
RES_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REQUIREMENTS="$RES_DIR/requirements.txt"
VENV="$APP_SUPPORT/.venv"
MARKER="$VENV/.vivotype-setup-complete"

# On Apple Silicon the backend (MLX) only runs natively, so an Intel-only
# interpreter (an old /usr/local Homebrew, or anything under Rosetta) would
# build a venv that can never install mlx-whisper. hw.optional.arm64 is 1 on
# Apple Silicon even when this script itself runs translated.
REQUIRE_ARCH=""
if [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then
  REQUIRE_ARCH="arm64"
fi

# Python 3.11+ (and native arm64 on Apple Silicon).
is_compatible() {
  "$1" -c 'import platform, sys
ok = sys.version_info[:2] >= (3, 11)
if sys.argv[1]:
    ok = ok and platform.machine() == sys.argv[1]
raise SystemExit(0 if ok else 1)' "$REQUIRE_ARCH" >/dev/null 2>&1
}

# --- check mode: verify an existing venv, never install --------------------
# Every requirement line must be installed at a satisfying version (markers
# honoured), and the modules the daemon imports at runtime must load. Uses the
# `packaging` copy vendored in the venv's own pip, so nothing new is needed.
if [ "$MODE" = "check" ]; then
  if [ ! -f "$REQUIREMENTS" ]; then
    err "Error: requirements.txt not found at $REQUIREMENTS"
    exit 1
  fi
  if [ ! -x "$VENV/bin/python" ] || ! is_compatible "$VENV/bin/python"; then
    echo "==> No usable virtual environment at $VENV"
    exit 3
  fi
  if ! "$VENV/bin/python" - "$REQUIREMENTS" <<'PY'
import importlib.metadata as md
import sys

from pip._vendor.packaging.requirements import Requirement

with open(sys.argv[1], encoding="utf-8") as fh:
    for raw in fh:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        req = Requirement(line)
        if req.marker is not None and not req.marker.evaluate():
            continue
        try:
            version = md.version(req.name)
        except md.PackageNotFoundError:
            sys.exit(f"missing: {req.name}")
        if not req.specifier.contains(version, prereleases=True):
            sys.exit(f"outdated: {req.name} {version} (needs {req.specifier})")

import numpy  # noqa: E402,F401  (the daemon's runtime imports)
import mlx_whisper  # noqa: E402,F401
PY
  then
    echo "==> Virtual environment at $VENV does not satisfy requirements.txt"
    exit 3
  fi
  cp "$REQUIREMENTS" "$MARKER.tmp"
  mv -f "$MARKER.tmp" "$MARKER"
  echo "==> Existing virtual environment verified; marked complete"
  exit 0
fi

mkdir -p "$APP_SUPPORT"

# --- locate a compatible interpreter (Python 3.11+) ------------------------
# Strict requirement: a 3.11+ interpreter must exist to create the venv.
# Probe version-suffixed names from newest to oldest (so a freshly installed
# python3.14 is preferred over the system python3), then the generic names.
# The explicit upper bound is generous so new releases are picked up without
# another code change.
#
# When launched from the .app, this script inherits the GUI's minimal PATH
# (/usr/bin:/bin:...), which does NOT include where python.org (/usr/local/bin
# + the framework dir) or Homebrew (/opt/homebrew/bin) put Python — so a
# perfectly good interpreter looked "missing" (exit 42). Prepend those dirs.
# On Apple Silicon, native Homebrew (/opt/homebrew) goes before /usr/local,
# where a migrated Intel Homebrew may still leave x86_64-only interpreters.
if [ "$REQUIRE_ARCH" = "arm64" ]; then
  PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
else
  PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
fi
for _fw in /Library/Frameworks/Python.framework/Versions/3.*/bin; do
  [ -d "$_fw" ] && PATH="$_fw:$PATH"
done
export PATH

# Every match on PATH is tried (not just the first), so an incompatible copy
# earlier on PATH can't hide a good one later. /usr/bin is skipped: without the
# Command Line Tools /usr/bin/python3 is a stub that pops an install dialog,
# and with them it is Python 3.9, which is too old anyway.
find_python() {
  local name candidate minor
  local names=()
  for minor in $(seq 20 -1 11); do
    names+=("python3.$minor")
  done
  names+=(python3 python)
  for name in "${names[@]}"; do
    while IFS= read -r candidate; do
      case "$candidate" in
        ""|/usr/bin/*) continue ;;
      esac
      if is_compatible "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done < <(type -ap "$name" 2>/dev/null || true)
  done
  return 1
}

if ! PYTHON_BIN="$(find_python)"; then
  err "No compatible Python 3.11+ interpreter was found."
  exit 42
fi
echo "==> Using interpreter: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

# --- create the venv (idempotent) ------------------------------------------
# Validate any existing venv; recreate it if it is broken, older than the
# minimum Python, or (on Apple Silicon) not native arm64.
# pip must import too: a venv interrupted before pip was installed would
# otherwise be reused forever and fail every retry.
if [ -x "$VENV/bin/python" ] && is_compatible "$VENV/bin/python" \
   && "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  echo "==> Reusing existing virtual environment at $VENV"
else
  if [ -e "$VENV" ]; then
    echo "==> Existing .venv is incomplete or uses an incompatible Python — recreating"
    rm -rf "$VENV"
  fi
  echo "==> Creating virtual environment at $VENV"
  "$PYTHON_BIN" -m venv "$VENV"
fi

VENV_PY="$VENV/bin/python"

# Invalidate the completion marker before touching packages: if anything below
# fails or the app quits mid-install, the next launch sees "setup needed".
rm -f "$MARKER"

# --- install dependencies --------------------------------------------------
echo "==> Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip

if [ ! -f "$REQUIREMENTS" ]; then
  err "Error: requirements.txt not found at $REQUIREMENTS"
  exit 1
fi
echo "==> Installing core dependencies from requirements.txt"
"$VENV_PY" -m pip install -r "$REQUIREMENTS"

# --- mark complete (must stay the LAST step) --------------------------------
cp "$REQUIREMENTS" "$MARKER.tmp"
mv -f "$MARKER.tmp" "$MARKER"

echo ""
echo "Done. Virtual environment ready at:"
echo "    $VENV"
