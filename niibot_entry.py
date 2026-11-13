"""Entry point used by PyInstaller (and handy as ``python niibot_entry.py``)."""

from __future__ import annotations

import multiprocessing
import sys

from niibot.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
