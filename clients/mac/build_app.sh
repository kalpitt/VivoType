#!/usr/bin/env bash
# Build VivoType as a self-contained background menu-bar .app bundle (no Xcode).
#
# Produces clients/mac/build/VivoType.app. The bundle is fully self-contained:
#   Contents/MacOS/VivoType           — the compiled Swift app
#   Contents/Resources/core/       — the Python ASR backend (immutable, allowlisted)
#   Contents/Resources/scripts/    — setup_core.sh only (immutable)
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
# ALLOWLIST, not a denylist: only what the runtime opens ships in the bundle.
# A denylist leaked whatever else sat in the working tree — personal data
# (core/data/prompts), repo tooling (publish/record scripts, CLAUDE.md), .DS_Store
# and stray temp files. Mutable per-user state is created fresh in Application
# Support on first run (core/paths.py), never shipped. Adding a module to core/
# means adding it here; the import check below fails the build if you forget.
CORE_RUNTIME_FILES=(
  __init__.py asr.py audioio.py cli.py commands.py config.py daemon.py
  learn.py namematch.py paths.py postprocess.py promote.py
  postprocess_config.json
)
# Data files a module reads if present (namematch falls back to no guard list
# when it is missing), copied only when they exist in this checkout.
CORE_OPTIONAL_FILES=(namematch_allowlist.txt)
SCRIPTS_RUNTIME_FILES=(setup_core.sh)

echo "==> Copying runtime core/ and scripts/ files into Resources"
mkdir -p "$RES/core" "$RES/scripts"
# -X: no extended attributes (Finder metadata would fail codesign).
for f in "${CORE_RUNTIME_FILES[@]}"; do
  cp -X "$REPO_ROOT/core/$f" "$RES/core/$f"
done
for f in "${CORE_OPTIONAL_FILES[@]}"; do
  if [ -f "$REPO_ROOT/core/$f" ]; then cp -X "$REPO_ROOT/core/$f" "$RES/core/$f"; fi
done
for f in "${SCRIPTS_RUNTIME_FILES[@]}"; do
  cp -X "$REPO_ROOT/scripts/$f" "$RES/scripts/$f"
done
cp -X "$REPO_ROOT/requirements.txt" "$RES/requirements.txt"

# Every core module a bundled module imports (`from core.x`, `import core.x`,
# `from core import x`) must itself be bundled, or the app dies at runtime.
missing=""
for mod in $(sed -nE \
    -e 's/^[[:space:]]*from core\.([A-Za-z_][A-Za-z0-9_]*).*/\1/p' \
    -e 's/^[[:space:]]*import core\.([A-Za-z_][A-Za-z0-9_]*).*/\1/p' \
    -e 's/^[[:space:]]*from core import ([^#]*).*/\1/p' \
    "$RES"/core/*.py | sed -E 's/ as [A-Za-z_][A-Za-z0-9_]*//g; s/[(),]/ /g' \
    | tr ' ' '\n' | grep -E '^[A-Za-z_][A-Za-z0-9_]*$' | sort -u); do
  [ -f "$RES/core/$mod.py" ] || missing="$missing $mod"
done
if [ -n "$missing" ]; then
  echo "ERROR: bundled core/ imports modules missing from CORE_RUNTIME_FILES:$missing" >&2
  exit 1
fi

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
# A failed signature is a failed build: never report success for an unsigned
# (or half-signed) bundle.
SIGN_ID="${VIVOTYPE_SIGN_ID:--}"
if [ "$SIGN_ID" = "-" ]; then
  echo "==> Signing ad-hoc (VIVOTYPE_SIGN_ID not set: macOS permissions reset on every rebuild)"
else
  echo "==> Signing with identity \"$SIGN_ID\""
fi
if ! codesign --force --sign "$SIGN_ID" "$APP"; then
  echo "ERROR: codesign failed with identity \"$SIGN_ID\" (see message above)." >&2
  if [ "$SIGN_ID" != "-" ]; then
    echo "       Check the certificate exists: security find-identity -p codesigning" >&2
  fi
  exit 1
fi
if ! codesign --verify --deep --strict "$APP"; then
  echo "ERROR: the signed bundle does not verify (codesign --verify)." >&2
  exit 1
fi

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
if [ "$SIGN_ID" = "-" ]; then
  echo "  Signed with  : ad-hoc (permissions will reset on rebuild)"
else
  echo "  Signed with  : $SIGN_ID (verified)"
fi
echo "  Bundled files:"
check "core"
check "scripts"
check "VERSION"
check "AppIcon.icns"
echo "  VERSION      : $(cat "$RES/VERSION")"
echo "=============================================="
echo ""
echo "Launch:  open \"$DIR/$APP\""
