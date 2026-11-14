"""Niibot Studio — driving a Niimbot B1 label printer over Bluetooth LE.

The version lives in :mod:`niibot.version` only; it is re-exported here so
``niibot.__version__`` can never disagree with the rest of the app.
"""

from __future__ import annotations

from .version import APP_NAME, APP_TAGLINE, __version__

__all__ = ["APP_NAME", "APP_TAGLINE", "__version__"]
