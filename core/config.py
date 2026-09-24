"""Shared VivoType settings (core/config.json).

Written by the macOS Settings window and read by the Python backend so choices
like the ASR model persist across restarts and stay consistent between the app
and the CLI. Missing or malformed files fall back to DEFAULTS.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

try:  # sibling module; works whether run as a package or a script
    from core.paths import config_path as _config_path
except ImportError:
    from paths import config_path as _config_path

# Writable settings file: core/config.json in a dev checkout, or
# ~/Library/Application Support/VivoType/config.json once installed.
CONFIG_PATH = _config_path()


def atomic_write_text(path, text):
    """Write text via a temp file + rename, so a crash can't leave a half file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

DEFAULTS = {
    "model": "small.en",
    "hotkey_keycode": 61,        # Right Option
    "hotkey_label": "Right Option",
    "sound_enabled": True,
    "toast_enabled": True,
    "hud_enabled": True,         # False = sound-only, no on-screen recording pill
    "suggest_corrections": False,  # ✓/Undo offer after an in-place edit (Mac)
    "recording_sounds": False,   # start/stop cues, independent of the pill
    "voice_commands": False,     # True = act on "scratch that", "new line", ...
    "app_profiles": {},          # frontmost bundle ID -> postprocess profile name
}


def load_settings(path=None):
    """Return settings merged over DEFAULTS (defaults win on any missing key)."""
    path = Path(path) if path is not None else CONFIG_PATH
    settings = dict(DEFAULTS)
    if path.exists():
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                settings.update(data)
        except (ValueError, OSError, RecursionError) as exc:
            # Corrupt JSON, non-UTF-8 bytes (UnicodeDecodeError is a
            # ValueError) or pathological nesting: keep defaults so the daemon
            # and CLI still start, but say why.
            print(f"VivoType: ignoring unreadable config '{path}' "
                  f"({type(exc).__name__}); using defaults.", file=sys.stderr)
    # Soft type checks: a hand-edited "model": 123 must not become
    # whisper-123-mlx via asr.repo_for — fall back to the typed default.
    if not isinstance(settings.get("model"), str) or not settings["model"]:
        settings["model"] = DEFAULTS["model"]
    if not isinstance(settings.get("hotkey_keycode"), int):
        settings["hotkey_keycode"] = DEFAULTS["hotkey_keycode"]
    if not isinstance(settings.get("hotkey_label"), str):
        settings["hotkey_label"] = DEFAULTS["hotkey_label"]
    for flag in ("sound_enabled", "toast_enabled", "hud_enabled", "recording_sounds", "voice_commands",
                 "suggest_corrections"):
        if not isinstance(settings.get(flag), bool):
            settings[flag] = DEFAULTS[flag]
    # app_profiles must be a {bundle ID -> profile name} dict; a hand-edited
    # non-dict (or one with non-string values) falls back to empty rather than
    # leaking junk into the daemon protocol or crashing resolution.
    app_profiles = settings.get("app_profiles")
    if not isinstance(app_profiles, dict) or not all(
        isinstance(k, str) and k and isinstance(v, str)
        for k, v in app_profiles.items()
    ):
        settings["app_profiles"] = {}
    return settings


def save_settings(values, path=None):
    """Persist values (merged over DEFAULTS) to config.json; returns the merged dict."""
    path = Path(path) if path is not None else CONFIG_PATH
    merged = dict(DEFAULTS)
    merged.update(values)
    atomic_write_text(path, json.dumps(merged, indent=2, sort_keys=True) + "\n")
    return merged
