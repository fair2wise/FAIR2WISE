"""T04: resource discovery and the pyproject package list."""
import re
import tomllib
from pathlib import Path

import pytest

from app.modules import project_config, resources
from app.modules.hybrid_rag import sources

REPO = Path(__file__).resolve().parents[2]


def test_checkout_root_is_the_repo_root(monkeypatch):
    monkeypatch.delenv(resources.RESOURCE_ROOT_VAR, raising=False)
    assert resources.is_checkout()
    assert resources.resource_root() == REPO
    assert sources.repo_root() == REPO
    assert project_config.default_config_path() == REPO / "config.yml"


def test_schema_defaults_resolve_from_any_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv(resources.RESOURCE_ROOT_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    rel = "storage/schema/matkg_schema.yaml"
    assert resources.resolve_resource(rel) == REPO / rel
    local = tmp_path / "storage/schema/matkg_schema.yaml"
    local.parent.mkdir(parents=True)
    local.write_text("id: local\n")
    # An explicit relative path that exists from the CWD keeps its meaning.
    assert resources.resolve_resource(rel) == Path(rel)
    assert resources.resolve_resource(local) == local


def test_schema_helper_loads_default_schema_outside_repo(tmp_path, monkeypatch):
    from app.modules.term_extractor.schema import SchemaHelper

    monkeypatch.delenv(resources.RESOURCE_ROOT_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    helper = SchemaHelper("storage/schema/matkg_schema.yaml")
    assert helper.schema_path == "storage/schema/matkg_schema.yaml"
    assert helper.schema_view.all_classes()


def test_explicit_root_and_installed_mode(tmp_path, monkeypatch):
    monkeypatch.setenv(resources.RESOURCE_ROOT_VAR, str(tmp_path))
    assert resources.resource_root() == tmp_path.resolve()
    assert resources.resolve_resource("storage/x.yaml") == tmp_path.resolve() / "storage/x.yaml"
    monkeypatch.delenv(resources.RESOURCE_ROOT_VAR)
    monkeypatch.setattr(resources, "is_checkout", lambda: False)
    assert resources.resource_root() is None
    assert project_config.default_config_path() is None
    monkeypatch.chdir(tmp_path)
    with pytest.raises(resources.ResourceRootError, match=resources.RESOURCE_ROOT_VAR):
        resources.resolve_resource("storage/schema/matkg_schema.yaml")


def test_installed_mode_config_falls_back_to_defaults(monkeypatch):
    monkeypatch.delenv(resources.RESOURCE_ROOT_VAR, raising=False)
    monkeypatch.delenv("FAIR2WISE_CONFIG", raising=False)
    monkeypatch.setattr(resources, "is_checkout", lambda: False)
    project_config.load_project_config.cache_clear()
    try:
        assert project_config.load_project_config() == {}
    finally:
        project_config.load_project_config.cache_clear()


def test_pyproject_lists_every_package():
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    setuptools = data["tool"]["setuptools"]
    assert setuptools["package-dir"] == {"f2w": "app"}
    on_disk = sorted(
        ".".join(["f2w", *init.parent.relative_to(REPO / "app").parts])
        for init in (REPO / "app").rglob("__init__.py")
        if "__pycache__" not in init.parts
    )
    assert sorted(setuptools["packages"]) == on_disk
    assert not any(p.startswith(("app", "modules")) for p in setuptools["packages"])


def test_extras_match_the_pixi_workspace():
    """pyproject.toml is the PyPI packaging metadata; pixi.toml is the locked environment source.

    Every direct dependency named in pyproject (base and the api/agents/parser/semantic extras) is
    declared in the pixi default environment, and the Globus extra is the pixi ``globus`` feature.
    Only names are compared: pixi pins the application stack exactly while pyproject keeps ranges.
    """
    data = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    pixi = tomllib.loads((REPO / "pixi.toml").read_text())

    def norm(req):
        return re.split(r"[<>=!~\[ ;]", req.strip(), maxsplit=1)[0].lower().replace("_", "-")

    default = {norm(n) for n in pixi["pypi-dependencies"]}
    extras = data["optional-dependencies"]
    direct = {norm(r) for r in data["dependencies"]}
    for extra in ("api", "agents", "parser", "semantic"):
        direct |= {norm(r) for r in extras[extra]}
    assert direct <= default, sorted(direct - default)
    assert {norm(r) for r in extras["globus"]} == {norm(n) for n in pixi["feature"]["globus"]["pypi-dependencies"]}
    split = {norm(r) for e in ("api", "agents", "parser") for r in extras[e]}
    assert not {norm(r) for r in data["dependencies"]} & split
    assert set(extras) >= {"api", "agents", "semantic", "parser", "tiled", "figures", "full"}
