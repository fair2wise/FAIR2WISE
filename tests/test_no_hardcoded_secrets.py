"""No credential-shaped literal is assigned to a key/token/secret variable anywhere under ``app/``.

A Materials Project API key was found committed as a fallback for ``MP_API_KEY`` (found 2026-11-10 by the GitHub
readiness audit, docs/migration/demo/GITHUB_READINESS.md). The key must come from the process environment only.
This guard fails on any assignment of a 24+ character alphanumeric literal to a name that mentions a key, token or
secret; test fakes live under ``tests/`` and are not scanned.
"""

from __future__ import annotations

from pathlib import Path
import re

APP = Path(__file__).resolve().parents[1] / "app"
SECRETISH_NAME = r"(?:api[_-]?key|apikey|secret|token|password|passwd|access[_-]?key)"
ASSIGNED_LITERAL = re.compile(
    rf"(?i)\b\w*{SECRETISH_NAME}\w*\s*(?::[^=\n]+)?=\s*[\"']([A-Za-z0-9_\-]{{24,}})[\"']"
)
#: Words that make a long literal an obvious placeholder, not a credential.
PLACEHOLDER = re.compile(r"(?i)(your|example|placeholder|changeme|dummy|test|fake|redacted|xxxx|<)")


def _python_files() -> list[Path]:
    skip = {"graphify-out", "__pycache__"}
    return [p for p in APP.rglob("*.py") if not skip & set(p.parts)]


def test_no_credential_shaped_literal_is_assigned_to_a_key_variable_in_app() -> None:
    offenders: list[str] = []
    for path in _python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            match = ASSIGNED_LITERAL.search(line)
            if match and not PLACEHOLDER.search(match.group(1)):
                offenders.append(f"{path.relative_to(APP.parent).as_posix()}:{number}")
    assert offenders == [], f"credential-shaped literal assigned in: {offenders} (read it from the environment)"


def test_the_guard_catches_the_shape_it_exists_for() -> None:
    fake = 'mp_api_key = "' + "a1B2c3D4" * 4 + '"'
    assert ASSIGNED_LITERAL.search(fake) is not None
    assert ASSIGNED_LITERAL.search('api_key = "your-api-key-goes-here-please"') is not None
    assert PLACEHOLDER.search("your-api-key-goes-here-please") is not None
    assert ASSIGNED_LITERAL.search('mp_api_key = os.environ.get("MP_API_KEY", "")') is None
