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


def _is_writable(path: Path) -> bool:
    """True when *path* can be created and written to."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".niibot-write-test"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """Writable directory for the database, uploads and caches.

    Next to the executable (or the repository), so the app stays portable. When that
    folder is read-only (Program Files, a locked drive) the app falls back to
    %LOCALAPPDATA%\\Niibot Studio instead of failing to start.
    """
    override = os.environ.get("NIIBOT_DATA")
    if override:
        return Path(override)
    preferred = app_dir() / "data"
    if _is_writable(preferred):
        return preferred
    fallback = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Niibot Studio" / "data"
    if _is_writable(fallback):
        return fallback
    raise RuntimeError(
        f"Cannot write the Niibot Studio data folder ({preferred} or {fallback}). "
        "Set NIIBOT_DATA to a writable directory."
    )
