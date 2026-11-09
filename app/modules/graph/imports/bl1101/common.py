"""Shared helpers for the BL11.0.1.2 ingest: XPath wrappers, hashing, slugs, ids and skip rules."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from html import unescape
import logging
from pathlib import Path
import re
from typing import Any

from .paths import REPO_ROOT
from .specs import SECRET_NAMES, SECRET_PREFIXES, SKIP_DIR_NAMES, SKIP_DIR_PREFIXES, SKIP_SUFFIXES

LOGGER = logging.getLogger("ingest_bl1101")

def _xp_elems(node: Any, query: str) -> list[Any]:  # noqa: ANN401  # Any: lxml element (untyped third-party object)
    """Run an XPath that selects elements; lxml stubs type the result as a wide union."""
    # Any: lxml-stubs return a union of bool/float/str/list for xpath(); element queries yield lists.
    return list(node.xpath(query))


def _xp_strs(node: Any, query: str) -> list[str]:  # noqa: ANN401  # Any: lxml element (untyped third-party object)
    """Run an XPath that selects text nodes or attribute values (strings)."""
    return [str(v) for v in node.xpath(query)]


def _texts(node: Any) -> list[str]:  # noqa: ANN401  # Any: lxml element (untyped third-party object)
    """Return the text fragments of an lxml element (``itertext`` is typed str | bytes)."""
    return [str(t) for t in node.itertext()]


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string with a ``Z`` suffix, truncated to seconds."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of the file at ``path`` (read fully into memory)."""
    return sha256_bytes(path.read_bytes())


def slug(text: str) -> str:
    """Collapse runs of non-alphanumeric characters in ``text`` to single hyphens.

    Returns:
        The slug with leading and trailing hyphens stripped, or ``"unnamed"`` if
        nothing alphanumeric remains.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-")
    return cleaned or "unnamed"


def beam_id(*parts: str) -> str:
    """Build a ``beamline:`` node ID from name parts.

    Args:
        *parts: Name fragments. Falsy fragments are dropped and the rest are
            slugged and joined with hyphens.

    Returns:
        An ID of the form ``beamline:<slug>-<slug>``.
    """
    local = "-".join(slug(p) for p in parts if p)
    return f"beamline:{local}"


def strip_tags(value: str) -> str:
    """Remove HTML tags and entities from ``value`` and collapse whitespace to single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value or ""))).strip()


def _rel_to_repo(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def is_secret_path(path: Path) -> bool:
    """Report whether ``path`` looks like a secrets file that must never be ingested.

    Matches exact names in ``SECRET_NAMES`` and names starting with
    ``SECRET_PREFIXES``, except ``.env.example``.
    """
    name = path.name
    if name in SECRET_NAMES:
        return True
    if name.startswith(SECRET_PREFIXES) and name != ".env.example":
        return True
    return False


def should_skip_file(path: Path) -> bool:
    """Report whether ingest must ignore ``path``.

    True for secret files, skipped suffixes, and any path component that is a
    skipped directory name or starts with a skipped directory prefix.
    """
    if is_secret_path(path):
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    for part in path.parts:
        if part in SKIP_DIR_NAMES:
            return True
        if any(part.startswith(prefix) for prefix in SKIP_DIR_PREFIXES):
            return True
    return False
