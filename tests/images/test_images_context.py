"""The private corpus never reaches an image layer (T24B; H0 E3/E4, owner O-D1).

The build context is simulated from the working tree (pruned by ``.dockerignore``) plus
synthetic private files at the paths a working checkout uses. Two independent controls
are checked: the COPY allowlist of each final, and ``.dockerignore`` as a backstop.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List

import pytest

from dockerfile_model import ignored, image_paths, load

REPO = Path(__file__).resolve().parents[2]
DF, IGNORE = load(REPO)
FINALS = ("local", "als-dev", "als-prod")

#: Paths a real working checkout can hold that must never be image content.
PRIVATE = [
    "papers/rsoxs/some_paper.pdf",
    "papers/rsoxs/manifest.json",
    "storage/source_index/literature/chunks.jsonl",
    "storage/source_index/ops_bl1101/index.faiss",
    "storage/tiled_catalog.db",
    "storage/tiled_catalog.db-wal",
    "storage/kg/upload.pdf",
    "storage/kg/jobs.sqlite3",
    "storage/terminology/cache.sqlite",
    "storage/schema/.env",
    "app/.env",
    ".env",
    ".env.local",
    "runs/ui_session_rsoxs/operations/jobs.sqlite3",
    "runs/traces/turn.json",
    "logs/api.log",
    ".cache/bl1101/pages/index.html",
    "splash_links/links.sqlite",
    "ui/.env",
    "ui/node_modules/x/index.js",
    "tests/results/out.json",
    "scripts/app/modules/kg_rag_api.py",  # stale developer copy, "not packaged" (AUD-02)
]
FORBIDDEN = re.compile(
    r"(^|/)(papers|runs|logs|source_index|node_modules)(/|$)|\.pdf$|\.sqlite3?$|\.db(-\w+)?$|(^|/)\.env$"
    r"|(^|/)\.env\.(?!example$)|^\.cache/|^tests/|^evals/|^docs/|^mkdocs/|^ui/|^splash_links/|^\.github/"
    r"|^scripts/app/"
)


def context_files() -> List[str]:
    """Files Docker would send (``.dockerignore`` applied) plus the synthetic private ones."""
    found: List[str] = []
    for root, dirs, files in os.walk(REPO):
        rel_root = os.path.relpath(root, REPO)
        rel_root = "" if rel_root == "." else rel_root
        keep = []
        for d in dirs:
            rel = f"{rel_root}/{d}" if rel_root else d
            if d == ".git" or ignored(rel, IGNORE):
                continue
            keep.append(d)
        dirs[:] = keep
        for f in files:
            rel = f"{rel_root}/{f}" if rel_root else f
            if not ignored(rel, IGNORE):
                found.append(rel)
    return sorted(set(found) | set(PRIVATE))


CONTEXT = context_files()


@pytest.mark.parametrize("path", PRIVATE)
def test_dockerignore_backstop_excludes_private_paths(path):
    assert ignored(path, IGNORE), path


def test_dockerignore_keeps_what_the_build_needs():
    for path in ("app/modules/deployment/gateway.py", "storage/kg/matkg_rsoxs_v1.json",
                 "storage/terminology", "storage/schema/rsoxs_schema.yaml", "docker/nginx.conf",
                 "docker/entrypoint/f2w_supervisor.py", "docker/profiles/local.json", "ui/package.json",
                 "mkdocs/mkdocs.yml", "requirements/runtime.txt", ".env.example"):
        assert not ignored(path, IGNORE), path


@pytest.mark.parametrize("name", FINALS)
def test_final_images_hold_no_private_content(name):
    # The COPY allowlist alone (ignoring .dockerignore) already leaves out every private
    # location outside the allowlisted code/schema/KG directories ...
    allowlisted = image_paths(DF, name, PRIVATE, [])
    assert all(p.startswith(("app/", "scripts/", "storage/kg/", "storage/terminology/", "storage/schema/"))
               for p in allowlisted), allowlisted
    # ... and .dockerignore removes the ones planted inside those directories.
    assert not image_paths(DF, name, PRIVATE, IGNORE)
    shipped = image_paths(DF, name, CONTEXT, IGNORE)
    bad = [p for p in shipped if FORBIDDEN.search(p)]
    assert not bad, bad[:20]


@pytest.mark.parametrize("name", FINALS)
def test_final_images_keep_code_schemas_and_tracked_kg_json(name):
    shipped = set(image_paths(DF, name, CONTEXT, IGNORE))
    for required in ("app/modules/workers/__main__.py", "app/api/main.py", "scripts/tiled_server.py",
                     "storage/kg/matkg_rsoxs_v1.json", "storage/kg/matkg_bl1101_v2.json",
                     "storage/schema/rsoxs_schema.yaml", "config.yml", "docker/entrypoint/preflight.py",
                     f"docker/profiles/{name}.json"):
        assert required in shipped, required
    assert any(p.startswith("storage/terminology/") for p in shipped)  # O-D1 / H0 E3
    other = {"local", "als-dev", "als-prod"} - {name}
    assert not [p for p in shipped if any(p == f"docker/profiles/{o}.json" for o in other)]
    assert "docker/profiles/tiled-server/constraints.txt" not in shipped  # build stage only
    assert "docker/profiles/tiled-server/constraints.lock.txt" not in shipped


@pytest.mark.parametrize("name", FINALS)
def test_final_images_do_not_ship_the_stale_scripts_app_copy(name):
    """AUD-02: ``scripts/app/modules/kg_rag_api.py`` is a stale duplicate of the module that still
    calls ``load_dotenv()`` at import; ``COPY scripts/`` must not carry it (T17B retires it)."""
    tracked = [p for p in CONTEXT if p.startswith("scripts/app/")]
    assert "scripts/app/modules/kg_rag_api.py" in tracked  # the planted path is in the simulated context
    shipped = image_paths(DF, name, CONTEXT, IGNORE)
    assert not [p for p in shipped if p.startswith("scripts/app/")]
    assert "scripts/tiled_server.py" in shipped  # the rest of scripts/ still ships
