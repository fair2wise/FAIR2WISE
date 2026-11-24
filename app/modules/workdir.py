"""Where the app keeps its session state: ``F2W_WORKDIR``, else the agent's built-in default."""
from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path

#: Startup setting naming the session working directory (the images pass it as ``--workdir``).
WORKDIR_ENV = "F2W_WORKDIR"
#: The agent's ``--workdir`` default (relative to the process working directory).
DEFAULT_WORKDIR = "runs/session"


def resolve_workdir(environ: Mapping[str, str] | None = None) -> Path:
    """The session working directory the app uses: ``F2W_WORKDIR`` when set, else :data:`DEFAULT_WORKDIR`.

    Args:
        environ: Startup environment (``os.environ`` when ``None``).

    Returns:
        The directory path (it may not exist).
    """
    env = os.environ if environ is None else environ
    return Path(str(env.get(WORKDIR_ENV) or "").strip() or DEFAULT_WORKDIR)
