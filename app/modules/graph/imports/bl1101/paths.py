"""Filesystem locations used by the BL11.0.1.2 ingest (repo checkout, schema, KG and cache directories).

Kept apart from ``specs`` (which has no paths and no I/O). Every module that touches files imports its
locations from here, so one patched value (a test redirecting ``TERMS_DIR``) reaches every user; see ``compat``.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
SCHEMA_PATH = REPO_ROOT / "storage/schema/bl1101_schema.yaml"
MOTOR_STAGE_MAP_PATH = REPO_ROOT / "storage/schema/bl1101_motor_stage_map.yaml"
BLUEPRINT_PATH = (
    REPO_ROOT / "papers/rsoxs/beamline_blueprint/blueprint_bl11012_version09092026.html"
)
KG_DIR = REPO_ROOT / "storage/kg"
TERMS_DIR = REPO_ROOT / "storage/terminology"
CACHE_DIR = REPO_ROOT / ".cache/bl1101"
REPOS_DIR = CACHE_DIR / "repos"
READMES_DIR = CACHE_DIR / "readmes"
PAGES_DIR = CACHE_DIR / "pages"
WORK_DIR = CACHE_DIR / "work"
