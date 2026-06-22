"""Shared VivoType settings (core/config.json).

Written by the macOS Settings window and read by the Python backend so choices
like the ASR model persist across restarts and stay consistent between the app
and the CLI. Missing or malformed files fall back to DEFAULTS.
"""

from __future__ import annotations

import json
import os
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
        except (json.JSONDecodeError, OSError):
            pass  # keep defaults on a corrupt/unreadable file
    return settings


def save_settings(values, path=None):
    """Persist values (merged over DEFAULTS) to config.json; returns the merged dict."""
    path = Path(path) if path is not None else CONFIG_PATH
    merged = dict(DEFAULTS)
    merged.update(values)
    atomic_write_text(path, json.dumps(merged, indent=2, sort_keys=True) + "\n")
    return merged
