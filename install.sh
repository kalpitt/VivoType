#!/usr/bin/env bash
# VivoType — one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/kalpitt/VivoType/main/install.sh | bash

set -e

REPO="kalpitt/VivoType"
APP_NAME="VivoType"
INSTALL_DIR="/Applications"

bold=$(tput bold 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)
green=$(tput setaf 2 2>/dev/null || true)
red=$(tput setaf 1 2>/dev/null || true)

say() { echo "${bold}VivoType:${reset} $*"; }
ok()  { echo "${green}✓${reset} $*"; }
err() { echo "${red}Error:${reset} $*" >&2; exit 1; }

# ── pre-flight checks ────────────────────────────────────────────────────────

[[ "$(uname)" == "Darwin" ]] || err "VivoType only runs on macOS."

# sysctl is Rosetta-transparent: returns 1 on Apple Silicon even when the
# terminal is running under Rosetta 2 (where uname -m reports x86_64).
[[ "$(sysctl -n hw.optional.arm64 2>/dev/null)" == "1" ]] || \
  err "VivoType requires an Apple Silicon Mac (M1/M2/M3/M4). Intel Macs are not supported."

if ! command -v curl >/dev/null 2>&1; then
  err "curl is required but not found. Please install it and try again."
fi

# ── fetch latest release ─────────────────────────────────────────────────────

say "Finding the latest release…"
API_RESPONSE=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest")
DOWNLOAD_URL=$(echo "$API_RESPONSE" | grep '"browser_download_url"' | grep '\.zip' | head -1 | cut -d'"' -f4)
VERSION=$(echo "$API_RESPONSE" | grep '"tag_name"' | head -1 | cut -d'"' -f4)

[[ -n "$DOWNLOAD_URL" ]] || \
  err "No release found. Check https://github.com/$REPO/releases and try again."

# ── download ─────────────────────────────────────────────────────────────────

say "Downloading $APP_NAME $VERSION…"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Extract expected SHA256 from the API response (strip the "sha256:" prefix).
EXPECTED_SHA=$(echo "$API_RESPONSE" | grep -o '"digest": *"sha256:[^"]*"' | head -1 | sed 's/.*sha256://' | tr -d '"')

curl -fsSL --progress-bar "$DOWNLOAD_URL" -o "$TMP/VivoType.zip"

# Verify integrity — skipped only if GitHub didn't return a digest (shouldn't happen).
if [[ -n "$EXPECTED_SHA" ]]; then
  ACTUAL_SHA=$(shasum -a 256 "$TMP/VivoType.zip" | awk '{print $1}')
  if [[ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]; then
    err "Integrity check failed — the download may be corrupted. Please try again."
  fi
fi

unzip -q "$TMP/VivoType.zip" -d "$TMP"

[[ -d "$TMP/$APP_NAME.app" ]] || \
  err "Unexpected archive contents. Please report this at https://github.com/$REPO/issues"

# ── install ──────────────────────────────────────────────────────────────────

say "Installing to $INSTALL_DIR…"

if [[ -d "$INSTALL_DIR/$APP_NAME.app" ]]; then
  say "Removing previous version…"
  rm -rf "$INSTALL_DIR/$APP_NAME.app"
fi

cp -r "$TMP/$APP_NAME.app" "$INSTALL_DIR/"

# Remove macOS quarantine so the app opens without any security warning.
xattr -dr com.apple.quarantine "$INSTALL_DIR/$APP_NAME.app" 2>/dev/null || true

# ── done ─────────────────────────────────────────────────────────────────────

echo ""
ok "$APP_NAME $VERSION installed successfully."
echo ""
echo "  Open Finder → Applications → VivoType to launch."
echo "  Or paste this: open /Applications/VivoType.app"
echo ""
echo "  On first launch, VivoType will walk you through a short setup."
echo "  It downloads a small speech model (~150 MB) once and keeps everything local."
echo ""
