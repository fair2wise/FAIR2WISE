"""Support for *owned* worker processes (J-09): atomic files, pid checks, bounded log tails.

Only for processes this application started itself. There is no arbitrary shell execution:
:func:`worker_command` builds the argv for the one allowlisted worker entry point and refuses
anything else, and log output shown to operators is bounded and scrubbed of credential-like text.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/child_process.py (``write_text_atomic``,
``pid_alive``, ``pid_command``, ``tail_lines``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import cast

TAIL_BYTES = 64 * 1024
TAIL_LINE_CHARS = 400
#: The only module a worker process may run.
WORKER_MODULE = "app.modules.workers"
#: Credential-like text, applied in order by :func:`scrub`. Each pattern keeps the surrounding text readable and
#: replaces only the secret (or the whole ``name=value`` pair where the value is the secret).
_REDACTED = "[redacted]"
_KEY_NAME = (
    r"(?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|access[_-]?key|private[_-]?key|token|secret|passw(?:or)?d|credentials?)"
    r"|(?:[A-Za-z0-9]+[_-])+key"
)
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # JSON Web Tokens (three base64url parts, header starts with ``eyJ``).
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?:\.[A-Za-z0-9_-]*)?"),
    # ``Authorization: Bearer abc`` / ``Basic abc`` / ``"Authorization": "abc"``: the whole header value.
    re.compile(r"(?i)authorization[\"']?\s*[=:]\s*[\"']?(?:(?:bearer|basic|token|digest)\s+)?[^\s,;\"'&]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    # ``CBORG_API_KEY=v``, ``OPENALEX_API_KEY: v``, ``*_KEY=v``, ``*_TOKEN=v``, ``"api_key": "v"``, ``?token=v``.
    re.compile(rf"(?i)(?:{_KEY_NAME})[\"']?\s*[=:]\s*(?:\"[^\"]*\"|'[^']*'|[^\s&,;\"']+)"),
    # Query parameters named like a credential in a URL (``?key=...&sig=...``).
    re.compile(r"(?i)(?<=[?&])(?:key|auth|signature|sig|apikey|access_token|id_token)=[^&\s#]+"),
    # Vendor-shaped keys: ``sk-...`` (also ``sk-proj-...``), GitHub and Slack tokens.
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,})"),
    # Credentials in a URL (``https://user:pass@host``).
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),
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
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def pid_alive(pid: object) -> bool:
    """Whether a process with this pid exists (signal 0). Pids of 1 or below are refused outright."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # no such process
        return False
    except PermissionError:
        return True
    except OSError:  # cannot probe: treat as not running
        return False
    return True


def pid_command(pid: int) -> str:
    """The process's command line, or ``""`` when it cannot be read."""
    try:
        done = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):  # ps unavailable: identity unknown
        return ""
    return done.stdout.strip()


def is_our_worker(pid: object, marker: str = WORKER_MODULE) -> bool:
    """A live pid whose command line names the worker module (guards against pid reuse)."""
    return pid_alive(pid) and marker in pid_command(int(cast(int, pid)))  # pid_alive vetted it as an int-like pid


def scrub(line: str) -> str:
    """Remove credential-like text from a log line.

    Covers Bearer tokens and ``Authorization`` headers, JWTs, ``sk-...`` and other vendor-shaped keys,
    ``*_KEY=value`` / ``*_TOKEN=value`` / ``api_key`` / ``secret`` / ``password`` pairs (also quoted, JSON style and
    in URL query strings), credential-named URL query parameters and ``user:pass@`` URL credentials. Pure and
    idempotent; applied when a line is stored and again when it is read.
    """
    for pattern in _SECRET_PATTERNS:
        line = pattern.sub(_REDACTED, line)
    return line


def tail_lines(
    path: Path, n: int = 12, *, max_bytes: int = TAIL_BYTES, max_chars: int = TAIL_LINE_CHARS
) -> list[str]:
    """The last ``n`` non-empty lines of a log, each scrubbed and cut to ``max_chars``; ``[]`` when none."""
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - max_bytes))
            data = stream.read()
    except OSError:  # no log file
        return []
    lines = [line.rstrip() for line in data.decode("utf-8", "replace").splitlines()]
    return [scrub(line)[:max_chars] for line in lines if line][-n:]


def worker_command(db_path: str, *, factory: str, extra: Sequence[str] = ()) -> list[str]:
    """Build the argv for an owned worker process; refuses a factory outside ``app.modules.`` and any shell metacharacter."""
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
