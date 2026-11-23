"""CI split helpers: path filter and test sharding (scripts/ci/changed_paths.py, pytest_shard.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "ci"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


changed_paths = _load("changed_paths")
pytest_shard = _load("pytest_shard")


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["README.md", "docs/migration/x.md", "mkdocs/docs/ci.md"], (False, False, True)),
        (["ui/src/App.tsx"], (False, True, False)),
        (["ui/src/App.tsx", "docs/a.md"], (False, True, True)),
        (["app/modules/x.py"], (True, False, True)),
        (["pixi.lock"], (True, False, True)),
        ([".github/workflows/forge-merge-ci.yml"], (True, True, True)),
        (["docs/migration/reviews/H0/known_failures.json"], (True, False, True)),
        ([], (False, False, False)),
    ],
)
def test_classify(paths: list[str], expected: tuple[bool, bool, bool]) -> None:
    flags = changed_paths.classify(paths)
    assert (flags["python"], flags["frontend"], flags["docs"]) == expected


def test_unknown_base_runs_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert changed_paths.main(["--base", "0" * 40]) == 0
    assert capsys.readouterr().out.split() == ["python=true", "frontend=true", "docs=true"]
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert changed_paths.main(["--force-all"]) == 0
    assert out.read_text().split() == ["python=true", "frontend=true", "docs=true"]


def test_every_test_is_in_exactly_one_shard() -> None:
    ids = [f"tests/a/test_{i}.py::test_{i}[{j}]" for i in range(40) for j in range(5)]
    owners = [pytest_shard.shard_of(n, 3) for n in ids]
    assert set(owners) == {1, 2, 3}
    sizes = [owners.count(s) for s in (1, 2, 3)]
    assert min(sizes) > len(ids) / 3 * 0.7, sizes


@pytest.mark.parametrize("bad", ["0/3", "4/3", "1/0", "x", "2", "1/"])
def test_bad_shard_spec_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        pytest_shard.parse_shard(bad)
