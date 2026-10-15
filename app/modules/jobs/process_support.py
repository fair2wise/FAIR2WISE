"""Support for *owned* worker processes (J-09): atomic files, pid checks, bounded log tails.

Only for processes this application started itself. There is no arbitrary shell execution:
:func:`worker_command` builds the argv for the one allowlisted worker entry point and refuses
anything else, and log output shown to operators is bounded and scrubbed of credential-like text.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/child_process.py (``write_text_atomic``,
``pid_alive``, ``pid_command``, ``tail_lines``) (BSD-3-Clause).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Sequence

TAIL_BYTES = 64 * 1024
TAIL_LINE_CHARS = 400
#: The only module a worker process may run.
WORKER_MODULE = "app.modules.workers"
_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|secret|passw(or)?d|authorization)\s*[=:]\s*\S+|Bearer\s+\S+|sk-[A-Za-z0-9]{12,}"
)


def write_text_atomic(path: Path, text: str) -> None:
    """Write ``text`` so a reader sees the old file or the new one, never half of either."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as out:
            temporary = Path(out.name)
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def pid_alive(pid: object) -> bool:
    """Whether a process with this pid exists (signal 0). Pids of 1 or below are refused outright."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def pid_command(pid: int) -> str:
    """The process's command line, or ``""`` when it cannot be read."""
    try:
        done = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def is_our_worker(pid: object, marker: str = WORKER_MODULE) -> bool:
    """A live pid whose command line names the worker module (guards against pid reuse)."""
    return pid_alive(pid) and marker in pid_command(int(pid))  # type: ignore[arg-type]


def scrub(line: str) -> str:
    """Remove credential-like text from a log line."""
    return _SECRET.sub("[redacted]", line)


def tail_lines(
    path: Path, n: int = 12, *, max_bytes: int = TAIL_BYTES, max_chars: int = TAIL_LINE_CHARS
) -> List[str]:
    """The last ``n`` non-empty lines of a log, each scrubbed and cut to ``max_chars``; ``[]`` when none."""
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - max_bytes))
            data = stream.read()
    except OSError:
        return []
    lines = [line.rstrip() for line in data.decode("utf-8", "replace").splitlines()]
    return [scrub(line)[:max_chars] for line in lines if line][-n:]


def worker_command(db_path: str, *, factory: str, extra: Sequence[str] = ()) -> List[str]:
    """argv for an owned worker process; refuses a factory outside ``app.modules.`` and any shell metacharacter."""
    if not re.fullmatch(r"app\.modules(\.[A-Za-z_][A-Za-z0-9_]*)+:[A-Za-z_][A-Za-z0-9_]*", factory):
        raise ValueError("worker factory must be 'app.modules.<package>:<callable>'")
    return [sys.executable, "-m", WORKER_MODULE, "--db", str(db_path), "--factory", factory, *extra]


__all__ = (
    "WORKER_MODULE",
    "is_our_worker",
    "pid_alive",
    "pid_command",
    "scrub",
    "tail_lines",
    "worker_command",
    "write_text_atomic",
)
