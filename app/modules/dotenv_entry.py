"""Explicit ``.env`` loading for CLI and launcher entry points.

Library modules never load a ``.env`` at import (ADR package-and-imports,
decision 8). Entry points call :func:`load_entrypoint_dotenv` first, before
any module that reads configuration at import is imported.

Lookup order:

1. ``F2W_DOTENV`` set to a file path: load exactly that file (a missing file is
   an error). Set to ``""``, ``0``, ``none`` or ``off``: load nothing.
2. Checkout (``app/`` tree, including an editable install): the first ``.env``
   found walking up from the entry point's directory. This matches what the
   former import-time ``load_dotenv()`` calls found, so the repo-root ``.env``
   is still used by the documented local launchers.
3. Installed wheel (``site-packages/f2w``): the first ``.env`` walking up from
   the current working directory, never from ``site-packages``, so an install
   inside another project does not pick up that project's ``.env`` by location.

Values never override variables already set in the process environment
(``override=False``, as before).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

DOTENV_VAR = "F2W_DOTENV"
_DISABLED = {"", "0", "none", "off", "false"}


def is_checkout() -> bool:
    """True when this code runs from the ``app/`` source tree rather than an installed ``f2w`` wheel."""
    return Path(__file__).resolve().parents[1].name == "app"


def find_entrypoint_dotenv(anchor: Union[str, Path]) -> Optional[Path]:
    """Return the ``.env`` an entry point at ``anchor`` should load, or ``None``."""
    explicit = os.environ.get(DOTENV_VAR)
    if explicit is not None:
        if explicit.strip().lower() in _DISABLED:
            return None
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"{DOTENV_VAR}={explicit!r} is not a file")
        return path
    if is_checkout():
        start = Path(anchor).resolve()
        if not start.is_dir():
            start = start.parent
    else:
        start = Path.cwd()
    for directory in (start, *start.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_entrypoint_dotenv(anchor: Union[str, Path]) -> Optional[Path]:
    """Load the entry point's ``.env`` without overriding the process environment; return its path."""
    path = find_entrypoint_dotenv(anchor)
    if path is None:
        return None
    from dotenv import load_dotenv

    load_dotenv(path, override=False)
    return path
