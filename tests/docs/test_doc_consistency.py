"""T28D (AUD-11): the user-facing docs agree with the Dockerfile and the image README.

Read-only text checks; no Docker, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "Dockerfile"
README = REPO / "README.md"
PROFILES_README = REPO / "docker" / "profiles" / "README.md"
CURRENT_ARCH = REPO / "mkdocs" / "docs" / "current-architecture.md"
MKDOCS_PAGES = sorted((REPO / "mkdocs" / "docs").glob("*.md"))

BUILD_LINE = re.compile(r"^docker build --target (\S+)\s+-t (\S+) \.\s*$", re.M)


def _dockerfile_gateway_port() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    defaults = set(re.findall(r"F2W_GATEWAY_PORT=(\d+)", text))
    exposed = set(re.findall(r"^EXPOSE (\d+)\s*$", text, re.M))
    assert len(defaults) == 1, defaults
    assert exposed == defaults, (exposed, defaults)
    return defaults.pop()


def test_gateway_port_in_docs_equals_the_dockerfile_default():
    port = _dockerfile_gateway_port()
    for doc in (README, PROFILES_README, CURRENT_ARCH):
        text = doc.read_text(encoding="utf-8")
        assert f"`F2W_GATEWAY_PORT`" in text, doc
        assert re.search(rf"(?<![\d.]){port}(?!\d)", text), (doc, port)
        # no other port is ever called the gateway or image default
        for other in re.findall(r"F2W_GATEWAY_PORT[^\n]{0,40}?default\s*`?(\d+)", text):
            assert other == port, (doc, other)
    readme = README.read_text(encoding="utf-8")
    assert f"port {port}" in readme or f"**port {port}**" in readme


def test_no_mkdocs_page_says_frontend_80():
    stale = re.compile(r"frontend:80(?!\d)")
    offenders = [p.name for p in MKDOCS_PAGES if stale.search(p.read_text(encoding="utf-8"))]
    assert offenders == [], offenders


def test_readme_image_build_commands_match_the_profiles_readme():
    readme_lines = set(BUILD_LINE.findall(README.read_text(encoding="utf-8")))
    profile_lines = set(BUILD_LINE.findall(PROFILES_README.read_text(encoding="utf-8")))
    assert profile_lines, "docker/profiles/README.md has no build commands"
    assert readme_lines == profile_lines, (readme_lines ^ profile_lines)
    targets = {target for target, _ in profile_lines}
    assert targets == {"local", "als-dev", "als-prod"}
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    for target in targets:
        assert re.search(rf"^FROM \S+ AS {re.escape(target)}\s*$", dockerfile, re.M), target


def test_docs_do_not_call_splash_the_default_graph_source():
    """Splash is the legacy mode; nothing may present it as the default any more."""
    stale = re.compile(r"(loads from the `splash_links` graph database by default|default `splash` graph source)")
    for doc in [README, *MKDOCS_PAGES]:
        assert not stale.search(doc.read_text(encoding="utf-8")), doc
