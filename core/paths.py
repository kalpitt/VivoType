#!/usr/bin/env python3
"""Resolve VivoType's writable runtime paths — the single source of truth.

Immutable code ships read-only inside VivoType.app (Contents/Resources); all
mutable state — settings, the corrections log, the user dictionary, the names
lexicon — must live in a writable per-user directory. Centralizing the
resolution here keeps every backend module (cli, daemon, learn, promote,
postprocess, namematch) in agreement about where that data lives, whether
VivoType runs from a repo checkout or from /Applications.

Resolution order for the data directory:
  1. $VIVOTYPE_DATA_DIR                                  (explicit override)
  2. $VIVOTYPE_APP_SUPPORT/data                          (set by the macOS app)
  3. <bundled core/data> if writable                 (running from a repo: dev)
  4. ~/Library/Application Support/VivoType/data      (installed default)

When the writable dir differs from the bundled one, known data files are seeded
from the bundled copy on first use so any shipped defaults survive the move.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_BUNDLED_DIR = Path(__file__).resolve().parent
_BUNDLED_DATA = _BUNDLED_DIR / "data"

# Files seeded from the bundle into a fresh writable data dir (when present).
_SEED_FILES = (
    "user_dictionary.json",
    "corrections.jsonl",
    str(Path("lexicon") / "contacts.json"),
)


def app_support_dir() -> Path:
    """The writable per-user home (…/VivoType), honouring $VIVOTYPE_APP_SUPPORT."""
    env = os.environ.get("VIVOTYPE_APP_SUPPORT")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Library" / "Application Support" / "VivoType"


def _resolve_data_dir() -> Path:
    env = os.environ.get("VIVOTYPE_DATA_DIR")
    if env:
        return Path(env).expanduser()
    if os.environ.get("VIVOTYPE_APP_SUPPORT"):
        return app_support_dir() / "data"
    # No override: use the bundled dir when it is writable (a development
    # checkout), otherwise fall back to App Support (installed, read-only bundle).
    if _BUNDLED_DATA.exists() and os.access(_BUNDLED_DATA, os.W_OK):
        return _BUNDLED_DATA
    return app_support_dir() / "data"


def _seed(target: Path) -> None:
    """Copy bundled default data files into a fresh writable dir (best effort)."""
    if target == _BUNDLED_DATA:
        return
    for rel in _SEED_FILES:
        src = _BUNDLED_DATA / rel
        dst = target / rel
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass


def data_dir() -> Path:
    """Writable data directory, created (and seeded) on first access."""
    d = _resolve_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    _seed(d)
    return d


def corrections_log() -> Path:
    return data_dir() / "corrections.jsonl"


def user_dictionary() -> Path:
    return data_dir() / "user_dictionary.json"


def contacts_lexicon() -> Path:
    return data_dir() / "lexicon" / "contacts.json"


def models_dir() -> Path:
    """Writable directory for downloaded ASR model weights (the HF cache root).

    Honours ``$VIVOTYPE_MODELS_DIR``; otherwise lives under the app's writable home
    (``…/VivoType/models``, or ``$VIVOTYPE_APP_SUPPORT/models`` once installed) so
    the read-only ``.app`` bundle is never written to. Created on first access.
    """
    env = os.environ.get("VIVOTYPE_MODELS_DIR")
    d = Path(env).expanduser() if env else app_support_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    """Writable settings file (config.json)."""
    env = os.environ.get("VIVOTYPE_CONFIG")
    if env:
        return Path(env).expanduser()
    if os.environ.get("VIVOTYPE_APP_SUPPORT"):
        return app_support_dir() / "config.json"
    bundled = _BUNDLED_DIR / "config.json"
    if os.access(_BUNDLED_DIR, os.W_OK):
        return bundled  # development checkout
    return app_support_dir() / "config.json"
