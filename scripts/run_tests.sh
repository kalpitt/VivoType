#!/usr/bin/env bash
# Run VivoType's core test suite (stdlib unittest — no extra dependencies).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "==> Running core tests"
python -m unittest discover -s core/tests -t . -v
