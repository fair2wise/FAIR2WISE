"""The image MkDocs config builds in the Dockerfile's docs stage, which copies ``mkdocs/`` alone."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.deployment.gateway import mkdocs_image_config

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "mkdocs" / "mkdocs.yml"


def test_image_config_clears_site_url_and_drops_the_serve_only_watch_block():
    out = mkdocs_image_config(SOURCE.read_text(encoding="utf-8"))
    assert 'site_url: ""' in out
    assert "\nwatch:" not in out and not out.startswith("watch:")
    assert "../app" not in out
    assert "plugins:" in out and "nav:" in out


def test_watch_block_in_the_middle_keeps_the_following_keys():
    src = 'site_url: https://x/\nwatch:\n  - ../app\n  - ../ui/src\nnav:\n  - Home: index.md\n'
    assert mkdocs_image_config(src) == 'site_url: ""\nnav:\n  - Home: index.md\n'


def test_strict_build_from_the_docs_stage_layout(tmp_path: Path):
    pytest.importorskip("mkdocs")
    pytest.importorskip("material")
    stage = tmp_path / "build"
    shutil.copytree(REPO / "mkdocs", stage / "mkdocs", ignore=shutil.ignore_patterns("site", ".mkdocs.image.yml"))
    image_cfg = stage / "mkdocs" / ".mkdocs.image.yml"
    image_cfg.write_text(mkdocs_image_config((stage / "mkdocs" / "mkdocs.yml").read_text(encoding="utf-8")),
                         encoding="utf-8")
    done = subprocess.run([sys.executable, "-m", "mkdocs", "build", "--strict", "-f", str(image_cfg),
                           "-d", str(tmp_path / "site")], cwd=stage, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    assert (tmp_path / "site" / "index.html").is_file()
