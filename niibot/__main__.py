"""``python -m niibot`` opens the web panel (any CLI subcommand also works)."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
