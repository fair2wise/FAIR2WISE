"""T17B step 4: the promoted operator scripts are console scripts with launch facades (offline)."""

from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

import pytest

REPO = Path(__file__).resolve().parents[2]

#: console script -> (module under app/modules, entry function, launch facade in scripts/ or None)
EXPECTED = {
    "f2w-run": ("commands.run", "cli", "run.py"),
    "f2w-tiled-server": ("commands.tiled_server", "main", "tiled_server.py"),
    "f2w-harvest": ("commands.harvest_rsoxs", "main", "harvest_rsoxs.py"),
    "f2w-paper-finder": ("commands.paper_finder", "main", "paper_finder.py"),
    "f2w-corpus-qa": ("commands.corpus_qa", "_main_app", "corpus_qa.py"),
    "f2w-download-pdfs": ("literature.download_pdfs", "main", "download_pdfs.py"),
    "f2w-repair-corpus": ("commands.repair_corpus", "main", "repair_corpus.py"),
    "f2w-kg-snapshot": ("commands.build_kg_snapshot", "main", "build_kg_snapshot.py"),
    "f2w-kg-release": ("commands.prepare_kg_release", "main", "prepare_kg_release.py"),
    "f2w-kg-adopt": ("commands.adopt_legacy_kg", "main", "adopt_legacy_kg.py"),
}
SECRETISH = re.compile(r"key|token|secret|password|passwd|credential", re.I)


def _scripts_table() -> dict[str, str]:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]


def test_pyproject_lists_exactly_the_promoted_console_scripts():
    table = _scripts_table()
    assert set(table) == set(EXPECTED)
    for name, (module, func, _) in EXPECTED.items():
        assert table[name] == f"f2w.modules.{module}:{func}"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_entry_points_resolve_to_callables(name: str):
    module, func, _ = EXPECTED[name]
    target = getattr(importlib.import_module(f"app.modules.{module}"), func)
    assert callable(target)


def test_commands_package_is_declared_for_packaging():
    packages = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["packages"]
    assert "f2w.modules.commands" in packages and (REPO / "app/modules/commands/__init__.py").is_file()


def _parsers(monkeypatch: pytest.MonkeyPatch) -> dict[str, argparse.ArgumentParser]:
    """Capture each argparse CLI's parser without running it."""
    found: dict[str, argparse.ArgumentParser] = {}
    from app.modules.commands import (
        adopt_legacy_kg,
        build_kg_snapshot,
        harvest_rsoxs,
        paper_finder,
        prepare_kg_release,
        run,
        tiled_server,
    )

    real = argparse.ArgumentParser.parse_args

    def capture(key: str):
        def inner(self: argparse.ArgumentParser, *args: object, **kwargs: object):
            found[key] = self
            return real(self, *args, **kwargs)

        return inner

    for key, call in {
        "harvest": lambda: harvest_rsoxs.parse_args([]),
        "paper_finder": lambda: paper_finder.parse_args(["serve"]),
        "snapshot": lambda: build_kg_snapshot.parse_args(["--corpus", "c", "--terms", "t.json"]),
        "release": lambda: prepare_kg_release.parse_args(["export", "--descriptor", "d", "--dest", "x"]),
        "adopt": lambda: adopt_legacy_kg.parse_args(["adopt"]),
    }.items():
        monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture(key))
        call()
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", real)
    monkeypatch.setattr(sys, "argv", ["f2w-run"])
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture("run"))
    run.parse_args()
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", real)
    found["tiled_server"] = tiled_server.build_parser()
    return found


def test_no_command_takes_a_secret_in_argv(monkeypatch: pytest.MonkeyPatch):
    parsers = _parsers(monkeypatch)
    assert len(parsers) == 7

    def options(parser: argparse.ArgumentParser) -> list[str]:
        names = [o for action in parser._actions for o in action.option_strings]
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for sub in action.choices.values():
                    names += options(sub)
        return names

    offenders = {
        key: [o for o in options(p) if SECRETISH.search(o) and not o.endswith(("-file", "-path", "-bundle"))]
        for key, p in parsers.items()
    }
    assert {k: v for k, v in offenders.items() if v} == {}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_launch_facade_still_works(name: str, tmp_path: Path):
    """``python scripts/<name>.py --help`` runs the packaged implementation (no network, no .env)."""
    facade = EXPECTED[name][2]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "F2W_DOTENV": "none"}
    env.update({k: v for k, v in os.environ.items() if k.startswith("F2W_TILED_PYTHON")})
    args = ["--help"]
    out = subprocess.run([sys.executable, "-I", str(REPO / "scripts" / facade), *args], capture_output=True,
                         text=True, cwd=tmp_path, env=env, timeout=180)
    assert out.returncode == 0, out.stderr[-1500:]
    assert "usage" in (out.stdout + out.stderr).lower()


def test_corpus_qa_is_promoted_byte_for_byte():
    """Its SHA-256 is pinned by tests/operations/test_corpus_audit.py; the facade is not the implementation."""
    facade = (REPO / "scripts" / "corpus_qa.py").read_text(encoding="utf-8")
    assert "app.modules.commands" in facade and "def audit" not in facade
