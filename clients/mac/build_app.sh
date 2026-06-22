#!/usr/bin/env bash
# Build VivoType as a self-contained background menu-bar .app bundle (no Xcode).
#
# Produces clients/mac/build/VivoType.app. The bundle is fully self-contained:
#   Contents/MacOS/VivoType           — the compiled Swift app
#   Contents/Resources/core/       — the Python ASR backend (immutable)
#   Contents/Resources/scripts/    — setup_core.sh etc. (immutable)
#   Contents/Resources/requirements.txt — deps for first-run venv creation
#   Contents/Resources/VERSION     — plain-text build identifier
#
# Mutable runtime state (.venv, logs, dictionaries) lives in
# ~/Library/Application Support/VivoType/ — never inside the bundle.
#
# App Sandbox is intentionally DISABLED: VivoType spawns Python, injects text into
# other apps, and writes to Application Support — all of which the sandbox blocks.
# We ad-hoc / self-sign without any sandbox entitlement, so no sandbox applies.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/../.." && pwd)"
cd "$DIR"

# Terminate running app to avoid duplicate instances and locked files
pkill -x "VivoType" || true

APP="build/VivoType.app"
RES="$APP/Contents/Resources"
echo "==> Building $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"

# --- compile the Swift app -------------------------------------------------
# The app is split into multiple .swift files (App.swift, Daemon.swift,
# Dictation.swift, Support.swift, Settings.swift, and UI/*.swift). swiftc
# compiles them together as one module — order is irrelevant, and the @main
# attribute in App.swift provides the entry point (no main.swift required).
# Discover sources dynamically so new files are picked up without editing this
# script; the build/ output dir is excluded.
SWIFT_FILES=$(find . -name '*.swift' -not -path './build/*' | sort | tr '\n' ' ')
echo "==> Compiling Swift sources:"
printf '      %s\n' $SWIFT_FILES
# shellcheck disable=SC2086  # intentional word-splitting of the file list
swiftc -O $SWIFT_FILES -o "$APP/Contents/MacOS/VivoType" \
  -framework Foundation \
  -framework AVFoundation \
  -framework AppKit \
  -framework CoreGraphics \
  -framework ApplicationServices

cp Info.plist "$APP/Contents/Info.plist"

# --- app icon --------------------------------------------------------------
# Compile the multi-size iconset into a single AppIcon.icns that macOS uses for
# Finder, the Dock, and notifications. `iconutil` ships with macOS, so there is
# nothing to install. Info.plist points at this file via CFBundleIconFile.
ICONSET="$DIR/Assets/VivoType.iconset"
if command -v iconutil >/dev/null 2>&1 && [ -d "$ICONSET" ]; then
  echo "==> Building AppIcon.icns from $(basename "$ICONSET")"
  iconutil -c icns "$ICONSET" -o "$RES/AppIcon.icns"
else
  echo "   (app icon skipped — iconutil or Assets/VivoType.iconset not found)"
fi

# --- menu-bar template icon ------------------------------------------------
# Brand-wave template shown in the menu bar when idle. It is a TEMPLATE image
# (alpha-only), so macOS auto-tints it white/black to match the bar — we never
# set an explicit tint on it. @2x is picked up automatically on Retina.
echo "==> Copying menu-bar template icon into Resources"
for mb in MenuBarIcon.png "MenuBarIcon@2x.png"; do
  [ -f "$DIR/Assets/$mb" ] && cp "$DIR/Assets/$mb" "$RES/$mb"
done

# --- onboarding welcome logo ----------------------------------------------
# Flat brand logo shown in the Setup window (State 1–3). Bundled as a PNG —
# NSImage can't reliably load a raw .svg from disk, so build_app rasterizes via
# sips at build time isn't needed here (a checked-in PNG already exists), we
# simply copy it. Loaded by URL in SetupWindowController (no asset catalog).
echo "==> Copying onboarding welcome logo into Resources"
[ -f "$DIR/Assets/WelcomeLogo.png" ] && cp "$DIR/Assets/WelcomeLogo.png" "$RES/WelcomeLogo.png"

# --- bundle the immutable Python source ------------------------------------
echo "==> Copying core/ and scripts/ into Resources"
# Exclude caches, the test suite, and PERSONAL data. Mutable per-user state
# (corrections, dictionary, contacts, recordings) must never ship inside a
# distributable bundle — it is created fresh in Application Support on first run
# (see core/paths.py). Shipping it would both leak private data and write to a
# read-only location at runtime.
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache' \
  --exclude 'tests' \
  --exclude 'config.json' \
  --exclude 'benchmark.py' \
  --exclude 'record.py' \
  --exclude 'data/corrections.jsonl' \
  --exclude 'data/user_dictionary.json' \
  --exclude 'data/lexicon' \
  --exclude 'data/labels.csv' \
  --exclude 'data/raw' \
  "$REPO_ROOT/core/" "$RES/core/"
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "$REPO_ROOT/scripts/" "$RES/scripts/"
cp "$REPO_ROOT/requirements.txt" "$RES/requirements.txt"

# --- VERSION: exactly one source, in priority order ------------------------
#   1. Git tag  2. short commit hash  3. 'dev'
VERSION="dev"
if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  if TAG="$(git -C "$REPO_ROOT" describe --tags --exact-match 2>/dev/null)"; then
    VERSION="$TAG"
  elif HASH="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null)"; then
    VERSION="$HASH"
  fi
fi
printf '%s\n' "$VERSION" > "$RES/VERSION"

# --- sign ------------------------------------------------------------------
# Ad-hoc ("-") by default; rebuilds then change the code hash, which makes macOS
# reset Accessibility/Mic permissions (the "permission loop"). To keep
# permissions stable across rebuilds, create a self-signed cert once and export
# VIVOTYPE_SIGN_ID="<cert name>" before building. No sandbox entitlement is applied.
SIGN_ID="${VIVOTYPE_SIGN_ID:--}"
codesign --force --sign "$SIGN_ID" "$APP" 2>/dev/null || \
  echo "   (codesign step skipped)"

# --- build summary ---------------------------------------------------------
# Determine sandbox status from the signed bundle's entitlements.
sandbox_status() {
  local ents
  ents="$(codesign -d --entitlements :- "$APP" 2>/dev/null || true)"
  if printf '%s' "$ents" | grep -qi 'app-sandbox'; then
    if printf '%s' "$ents" | grep -A1 -i 'app-sandbox' | grep -qi '<true'; then
      echo "ENABLED (unexpected — check entitlements)"
    else
      echo "disabled (entitlement present but false)"
    fi
  else
    echo "disabled (no sandbox entitlement)"
  fi
}

check() { [ -e "$RES/$1" ] && echo "    ✓ Resources/$1" || echo "    ✗ Resources/$1 MISSING"; }

echo ""
echo "================ Build summary ================"
echo "  App path     : $DIR/$APP"
echo "  Sandbox      : $(sandbox_status)"
echo "  Signed with  : ${SIGN_ID/#-/ad-hoc}"
echo "  Bundled files:"
check "core"
check "scripts"
check "VERSION"
check "AppIcon.icns"
echo "  VERSION      : $(cat "$RES/VERSION")"
echo "=============================================="
echo ""
echo "Launch:  open \"$DIR/$APP\""
