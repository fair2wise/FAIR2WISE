"""Sanitise free text before it reaches a report (no secrets, no local absolute paths)."""

from __future__ import annotations

import re

MAX_LEN = 400

_PATTERNS = (
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer <redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "<redacted-key>"),
    (
        re.compile(r"\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*)\s*[=:](?![:=])\s*\S+"),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|credential)s?\s*=\s*\S+"), r"\1=<redacted>"),
    (re.compile(r"/(?:Users|home|private|tmp|var|opt)/[^\s'\"):,]+"), "<path>"),
    (re.compile(r"[A-Za-z]:\\[^\s'\"):,]+"), "<path>"),
)


def sanitize(text: object, limit: int = MAX_LEN) -> str:
    """Redact secrets and absolute paths, collapse whitespace and bound the length."""
    out = str(text if text is not None else "")
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    out = " ".join(out.split())
    return out if len(out) <= limit else out[: limit - 3] + "..."
