"""T28C / AUD-08: pixi.lock is the single hash-locked source of the dependency environments.

pixi.toml declares the application, dev, docs, Globus, legacy and Tiled-server environments;
pixi.lock records every conda package and PyPI artifact with its sha256, and CI and the images
install with ``--locked``. These tests keep the lock honest: every PyPI artifact is hashed, the
owner-pinned Tiled wheel is in the Tiled environment only, the application environment is the
CPU-only build, and no unhashed pip lock file survives next to it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest
import yaml

from app.modules.deployment import tiled_config

REPO = Path(__file__).resolve().parents[2]
ENVIRONMENTS = ("default", "dev", "docs", "globus", "legacy", "tiled-server")
PLATFORMS = ("linux-64", "linux-aarch64", "osx-arm64")


@pytest.fixture(scope="module")
def lock() -> Dict[str, Any]:
    return yaml.safe_load((REPO / "pixi.lock").read_text(encoding="utf-8"))


def _env_platform(lock: Dict[str, Any], env: str, platform: str) -> Any:
    """The package list of ``env`` on ``platform``. Lock format v7 (pixi >= 0.82) keys an environment that
    needs macOS 14 (faiss-cpu wheels, [system-requirements]) as osx-arm64-macos-14-0 instead of osx-arm64."""
    packages = lock["environments"][env]["packages"]
    return packages.get(platform) or packages[f"{platform}-macos-14-0"]


def _pypi_packages(lock: Dict[str, Any], env: str, platform: str = "linux-64") -> Set[str]:
    """Names of the PyPI packages ``env`` installs on ``platform`` (from the locked package list)."""
    by_url = {p["pypi"]: p for p in lock["packages"] if "pypi" in p}
    return {by_url[e["pypi"]]["name"].lower().replace("_", "-")
            for e in _env_platform(lock, env, platform) if "pypi" in e}


def test_lock_declares_every_environment_on_every_platform(lock):
    assert tuple(sorted(lock["environments"])) == tuple(sorted(ENVIRONMENTS))
    for env in ENVIRONMENTS:
        keys = {re.sub(r"-macos-\d+-\d+$", "", k) for k in lock["environments"][env]["packages"]}
        assert keys == set(PLATFORMS), env


def test_every_pypi_artifact_is_hashed(lock):
    pypi = [p for p in lock["packages"] if "pypi" in p]
    assert pypi
    missing = [p["name"] for p in pypi if not re.fullmatch(r"[0-9a-f]{64}", str(p.get("sha256", "")))]
    assert not missing, missing


def test_every_conda_package_is_hashed(lock):
    conda = [p for p in lock["packages"] if "conda" in p]
    assert conda
    missing = [p["conda"] for p in conda if not re.fullmatch(r"[0-9a-f]{64}", str(p.get("sha256", "")))]
    assert not missing, missing[:5]


def test_tiled_server_environment_carries_the_owner_pinned_wheel_hash(lock):
    wheel = [p for p in lock["packages"] if "pypi" in p and p["pypi"].endswith(tiled_config.PINNED_TILED_WHEEL)]
    assert wheel, "the pinned wheel is locked"
    assert {p["sha256"] for p in wheel} == {tiled_config.PINNED_TILED_WHEEL_SHA256}
    assert wheel[0]["version"] == tiled_config.PINNED_TILED_VERSION == "0.2.18"
    for platform in PLATFORMS:
        assert "tiled" in _pypi_packages(lock, "tiled-server", platform), platform


@pytest.mark.parametrize("env", [e for e in ENVIRONMENTS if e != "tiled-server"])
def test_only_the_tiled_server_environment_has_tiled(lock, env):
    """Tiled needs pydantic-settings<2.12; it never shares an environment with the application."""
    assert "tiled" not in _pypi_packages(lock, env), env


def test_application_environment_is_cpu_only(lock):
    for platform in PLATFORMS:
        names = _pypi_packages(lock, "default", platform)
        assert "torch" in names
        assert not [n for n in names if n.startswith("nvidia-") or n.startswith("cuda-")], platform
    torch = [p for p in lock["packages"] if "pypi" in p and p["name"] == "torch"]
    assert torch and all("pytorch.org/whl/cpu/" in p["pypi"] for p in torch)


def test_application_environment_carries_no_test_or_quality_tools(lock):
    names = _pypi_packages(lock, "default")
    # pytest itself is a runtime transitive (linkml-runtime -> prefixcommons -> pytest-logging), as in
    # the old runtime lock; the tools below are not.
    assert not names & {"mypy", "ruff", "black", "flake8", "isort", "deepeval", "mkdocs", "pytest-cov"}
    dev = _pypi_packages(lock, "dev")
    assert {"pytest", "mypy", "ruff", "isort", "flake8", "mkdocs"} <= dev


def test_python_is_3_12_everywhere(lock):
    for env in ENVIRONMENTS:
        conda = [e["conda"] for e in lock["environments"][env]["packages"]["linux-64"] if "conda" in e]
        pythons = [c for c in conda if re.search(r"/python-3\.\d+", c)]
        assert pythons and all("/python-3.12." in c for c in pythons), (env, pythons)


def test_no_unhashed_pip_lock_files_survive():
    """The pip-compile locks (and the Tiled constraints) are retired: pixi.lock is the only lock."""
    survivors: List[str] = [str(p.relative_to(REPO)) for p in
                            [REPO / "requirements.txt", *(REPO / "requirements").glob("*"),
                             *(REPO / "docker/profiles/tiled-server").glob("constraints*")] if p.exists()]
    assert not survivors, survivors
