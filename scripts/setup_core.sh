#!/usr/bin/env bash
# VivoType — core backend setup (idempotent).
#
# Creates the isolated Python virtual environment (.venv) used by the ASR
# backend and installs its dependencies. Safe to re-run at any time.
#
# Usage:
#   setup_core.sh <app-support-dir>
#
# <app-support-dir> is the writable directory where the .venv must live
# (e.g. ~/Library/Application Support/VivoType). It is REQUIRED — the script
# never guesses a location. requirements.txt is read from the directory that
# contains this script's parent (the bundle's Resources/, or the repo root).
#
# Exit codes:
#   0   success
#   42  no compatible Python 3.11+ interpreter found (caller shows install help)
#   1   any other failure (human-readable message on stderr)
set -euo pipefail

err() { printf '%s\n' "$*" >&2; }

# --- arguments -------------------------------------------------------------
if [ "$#" -lt 1 ] || [ -z "${1:-}" ]; then
  err "Error: missing required argument."
  err "Usage: setup_core.sh <app-support-dir>"
  exit 1
fi

APP_SUPPORT="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# requirements.txt sits alongside core/ and scripts/ (Resources/ or repo root).
RES_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REQUIREMENTS="$RES_DIR/requirements.txt"
VENV="$APP_SUPPORT/.venv"

mkdir -p "$APP_SUPPORT"

# --- locate a compatible interpreter (Python 3.11+) ------------------------
# Strict requirement: a 3.11+ interpreter must exist to create the venv.
# Probe version-suffixed names from newest to oldest (so a freshly installed
# python3.14 is preferred over the system python3), then the generic names.
# The explicit upper bound is generous so new releases are picked up without
# another code change.
is_compatible() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' >/dev/null 2>&1
}

find_python() {
  local candidate minor
  for minor in $(seq 20 -1 11); do
    candidate="python3.$minor"
    if command -v "$candidate" >/dev/null 2>&1 && is_compatible "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && is_compatible "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON_BIN="$(find_python)"; then
  err "No compatible Python 3.11+ interpreter was found."
  exit 42
fi
echo "==> Using interpreter: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

# --- create the venv (idempotent) ------------------------------------------
# Validate any existing venv; recreate it only if it is broken.
if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
  echo "==> Reusing existing virtual environment at $VENV"
else
  if [ -e "$VENV" ]; then
    echo "==> Existing .venv is incomplete — recreating"
    rm -rf "$VENV"
  fi
  echo "==> Creating virtual environment at $VENV"
  "$PYTHON_BIN" -m venv "$VENV"
fi

VENV_PY="$VENV/bin/python"

# --- install dependencies --------------------------------------------------
echo "==> Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip

if [ ! -f "$REQUIREMENTS" ]; then
  err "Error: requirements.txt not found at $REQUIREMENTS"
  exit 1
fi
echo "==> Installing core dependencies from requirements.txt"
"$VENV_PY" -m pip install -r "$REQUIREMENTS"

echo ""
echo "Done. Virtual environment ready at:"
echo "    $VENV"
