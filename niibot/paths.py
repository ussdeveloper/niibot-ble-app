"""Path helpers that keep the app portable both from source and from the frozen EXE."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Directory of the executable (frozen) or of the repository (source)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Where bundled read-only assets (web/, CHANGELOG.md) live."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return app_dir()


def data_dir() -> Path:
    """Writable directory for the database, uploads and caches."""
    override = os.environ.get("NIIBOT_DATA")
    if override:
        return Path(override)
    return app_dir() / "data"
