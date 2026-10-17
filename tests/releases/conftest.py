"""Isolated Git fixtures for T26B (committed-artifact validation and export).

Every repository here is created under pytest's ``tmp_path`` with ``git init``. The code under test only reads
Git; the fixtures (test code) play the owner and run ``git add``/``git commit`` inside those temporary
repositories only, never in the F2W checkout. No remote is ever written: the push tests prove it.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.modules.graph.releases import canonical_json_bytes, sha256_bytes
from app.modules.graph.snapshot import SnapshotRequest, SnapshotResult, create_snapshot
from app.modules.schema.registry import SchemaRegistry
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS, REPO

GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "owner", "GIT_AUTHOR_EMAIL": "owner@example.org",
    "GIT_COMMITTER_NAME": "owner", "GIT_COMMITTER_EMAIL": "owner@example.org", "LC_ALL": "C",
    "GIT_TERMINAL_PROMPT": "0",
}
REPOSITORY = "github.com/example/f2w-release-fixture"
GITIGNORE = """\
papers/**
!papers/**/
!papers/rsoxs/beamline_blueprint/blueprint_*.html
storage/kg/*.json
storage/terminology/*.json
storage/source_index/
.env
"""


def git(repo: Path, *args: str, check: bool = True) -> str:
    """Owner-side Git in a temporary fixture repository (test code only)."""
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=GIT_ENV,
                          check=check, stdin=subprocess.DEVNULL)
    return done.stdout.strip()


def git_input(repo: Path, data: bytes, *args: str) -> str:
    """Owner-side Git with stdin (test code only, temporary repositories only)."""
    done = subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True, env=GIT_ENV,
                          check=True)
    return done.stdout.decode().strip()


def write_json(path: Path, doc: Any) -> None:
    path.write_bytes(canonical_json_bytes(doc))


@dataclass
class Fixture:
    """A release fixture: a Git repository with a committed base and one uncommitted frozen snapshot."""

    root: Path
    tmp: Path
    snapshot: Optional[SnapshotResult] = None

    @property
    def rel(self) -> str:
        return f"storage/kg/{self.snapshot.corpus}/{self.snapshot.snapshot_id}"

    def git(self, *args: str, check: bool = True) -> str:
        return git(self.root, *args, check=check)

    def registry(self) -> SchemaRegistry:
        return SchemaRegistry.discover(self.root / "storage" / "schema")

    def build(self, *, terms: Optional[Dict[str, Any]] = None, corpus: str = "synthetic", **overrides):
        """Freeze a snapshot into the repository (not committed)."""
        terms_path = self.tmp / "inputs" / f"{corpus}.terms.json"
        terms_path.parent.mkdir(exist_ok=True)
        data = terms if terms is not None else json.loads((INPUTS / "core_dict.terms.json").read_text("utf-8"))
        terms_path.write_text(json.dumps(data), encoding="utf-8")
        args = dict(corpus=corpus, terms_path=terms_path, storage_kg=self.root / "storage" / "kg",
                    terms_ref="private:terms/core_dict.terms.json", code_commit=COMMIT, registry=self.registry())
        args.update(overrides)
        self.snapshot = create_snapshot(SnapshotRequest(**args))
        return self.snapshot

    def report(self, *, status: str = "PASS", subject: Optional[Dict[str, Any]] = "auto", **extra) -> Path:
        """An executed binary report (evals.binary shape) bound to the current snapshot's bytes."""
        snap = self.snapshot
        body: Dict[str, Any] = {
            "report_kind": "binary_evaluation_report", "runner_revision": "t11b-runner-1",
            "manifest_version": "t11b-1", "manifest_sha256": "e" * 64,
            "source": {"commit": COMMIT, "app_tree": "b" * 40, "dirty": False},
            "gate": {"status": status, "blocking_cases": [], "gate_reasons": []},
            "results": [], "counts": {},
        }
        if subject == "auto":
            subject = {"kind": "kg_snapshot", "corpus": snap.corpus, "snapshot_id": snap.snapshot_id,
                       "graph_sha256": sha256_bytes(snap.graph_path.read_bytes()),
                       "manifest_sha256": sha256_bytes(snap.manifest_path.read_bytes())}
        if subject is not None:
            body["subject"] = subject
        body.update(extra)
        path = self.tmp / "reports" / f"binary_{status.lower()}_{len(list(self.tmp.glob('reports/*')))}.json"
        path.parent.mkdir(exist_ok=True)
        write_json(path, body)
        return path

    def run_owner_commands(self, record: Dict[str, Any]) -> str:
        """Run the printed git commands exactly as an owner would (here: only in the temporary repository)."""
        for line in record["owner_commands"]:
            words = shlex.split(line, comments=True)
            if not words or words[0] != "git":
                continue
            assert "push" not in words
            subprocess.run(words, cwd=self.root, env=GIT_ENV, check=True, capture_output=True,
                           stdin=subprocess.DEVNULL)
        return self.git("rev-parse", "HEAD")

    def rewrite_manifest(self, **changes) -> None:
        """Edit manifest.json in place (keeps the canonical serialization)."""
        doc = json.loads(self.snapshot.manifest_path.read_text("utf-8"))
        for dotted, value in changes.items():
            node = doc
            *parents, last = dotted.split("__")
            for key in parents:
                node = node[key]
            if value is None:
                node.pop(last, None)
            else:
                node[last] = value
        self.snapshot.manifest_path.chmod(0o644)
        write_json(self.snapshot.manifest_path, doc)


def make_repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    (root / "storage" / "kg").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    shutil.copytree(REPO / "storage" / "schema", root / "storage" / "schema")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    git(root, "add", ".gitignore", "README.md", "storage/schema")
    git(root, "commit", "-q", "-m", "base: schema and ignore rules")
    return root


@pytest.fixture(autouse=True)
def _quiet(caplog):
    caplog.set_level(logging.ERROR)


@pytest.fixture()
def fx(tmp_path) -> Fixture:
    """Repository plus a freshly frozen (uncommitted) synthetic/v1 snapshot."""
    fixture = Fixture(make_repo(tmp_path), tmp_path)
    fixture.build()
    return fixture


@pytest.fixture()
def empty_fx(tmp_path) -> Fixture:
    """Repository with no snapshot yet (tests build their own)."""
    return Fixture(make_repo(tmp_path), tmp_path)


def terms_with(**node_fields) -> Dict[str, Any]:
    """The core_dict fixture terms with extra fields on the first term."""
    data = json.loads((INPUTS / "core_dict.terms.json").read_text("utf-8"))
    data["terms"][0].update(node_fields)
    return data


def finding_rules(exc) -> List[str]:
    return sorted({f.rule for f in exc.findings})
