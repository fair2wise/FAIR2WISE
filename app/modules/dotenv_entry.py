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

Secrets listed in :data:`PROCESS_ONLY_VARS` are never taken from a ``.env`` file
at all (owner decision 2026-11-09, ``OPENALEX_API_KEY``): a value the file sets
is dropped again unless the variable was already exported in the process.
"""
from __future__ import annotations

import os
from pathlib import Path

from .resources import is_checkout

DOTENV_VAR = "F2W_DOTENV"
_DISABLED = {"", "0", "none", "off", "false"}
#: Variables a ``.env`` file may not supply: exported in the process or absent.
PROCESS_ONLY_VARS = frozenset({"OPENALEX_API_KEY"})


def find_entrypoint_dotenv(anchor: str | Path) -> Path | None:
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


def load_entrypoint_dotenv(anchor: str | Path) -> Path | None:
    """Load the entry point's ``.env`` without overriding the process environment; return its path."""
    path = find_entrypoint_dotenv(anchor)
    if path is None:
        return None
    from dotenv import load_dotenv

    exported = {name for name in PROCESS_ONLY_VARS if name in os.environ}
    load_dotenv(path, override=False)
    for name in PROCESS_ONLY_VARS - exported:
        os.environ.pop(name, None)  # the file's value is not used; the name (never the value) is not logged either
    return path
