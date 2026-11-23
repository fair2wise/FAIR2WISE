"""The root ``.env.example`` stays loadable by ``docker run --env-file`` and free of stale or real values.

It is the file people copy for the launchers and for the image, so the checks are structural: no inline
comments on value lines (``docker run --env-file`` keeps them in the value), the JSON graph source, no value
that looks like a credential, and every listed name still read somewhere in ``app/`` or ``scripts/``.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / ".env.example"
_VALUE_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
# Optional (commented-out) settings are written as `# NAME=value` and count as listed names too.
_OPTIONAL_LINE = re.compile(r"^# ([A-Z][A-Z0-9_]*)=(.*)$")
_TEXT_SUFFIXES = {".py", ".sh", ".toml", ".yaml", ".yml", ".json", ".md", ".txt", ".cfg", ".ini", ".env", ""}


def _entries() -> list[tuple[str, str, bool]]:
    """(name, value, optional) for every `NAME=value` and `# NAME=value` line."""
    found: list[tuple[str, str, bool]] = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = _VALUE_LINE.match(line)
        if match:
            found.append((match.group(1), match.group(2), False))
            continue
        match = _OPTIONAL_LINE.match(line)
        if match:
            found.append((match.group(1), match.group(2), True))
    return found


def _source_text() -> str:
    chunks: list[str] = []
    for top in ("app", "scripts"):
        for path in (ROOT / top).rglob("*"):
            if not path.is_file() or path.name == ".env.example" or path.suffix not in _TEXT_SUFFIXES:
                continue
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            try:
                chunks.append(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, OSError):
                continue
    return "\n".join(chunks)


def test_file_lists_settings() -> None:
    assert len(_entries()) > 20


def test_no_inline_comments_on_value_lines() -> None:
    bad = [name for name, value, _ in _entries() if re.search(r"\s#", value) or value.lstrip().startswith("#")]
    assert bad == []


def test_no_quotes_or_export() -> None:
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    assert [ln for ln in lines if ln.startswith("export ")] == []
    assert [name for name, value, _ in _entries() if value[:1] in {'"', "'"}] == []


def test_graph_source_is_json() -> None:
    active = {name: value for name, value, optional in _entries() if not optional}
    assert active["KG_RAG_GRAPH_SOURCE"] == "json"
    assert not any(name == "KG_RAG_GRAPH_SOURCE" and value == "splash" for name, value, optional in _entries() if not optional)


@pytest.mark.parametrize("secret_name", ["CBORG_API_KEY"])
def test_secrets_are_blank(secret_name: str) -> None:
    active = {name: value for name, value, optional in _entries() if not optional}
    assert active[secret_name] == ""


def test_no_value_looks_like_a_real_key() -> None:
    suspicious = []
    for name, value, _ in _entries():
        if value.startswith(("http://", "https://")):
            continue
        if value.startswith("sk-") or re.search(r"[A-Za-z0-9_\-]{24,}", value):
            suspicious.append(name)
        if re.search(r"(?i)(key|token|secret|password)", name) and value not in {"", "changeme"}:
            suspicious.append(name)
    assert suspicious == []


def test_no_private_addresses_or_hosts() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert not re.search(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+", text)
    assert not re.search(r"(?i)als\.lbl\.gov", text)


def test_every_listed_name_is_read_in_app_or_scripts() -> None:
    source = _source_text()
    stale = sorted({
        name for name, _, _ in _entries()
        if not re.search(rf"(?<![A-Z0-9_]){re.escape(name)}(?![A-Z0-9_])", source)
    })
    assert stale == [], f"names in .env.example that nothing in app/ or scripts/ references: {stale}"


def test_openalex_key_is_documented_as_process_only() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"(?i)process-only", text) and "OPENALEX_API_KEY" in text
