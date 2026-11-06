"""The image installs every ``[project.scripts]`` command as a launcher (E2E finding 2026-11-06).

``f2w-kg-adopt`` did not exist in the running ``:local`` container because the project is never
pip-installed there. ``docker/entrypoint/install_console_scripts.py`` generates the launchers from
``pyproject.toml``; these tests pin the table it reads, the ``f2w`` -> ``app`` rewrite and the
Dockerfile step that runs it. Launchers are checked by importing their targets, not by running them.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import stat
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "docker" / "entrypoint" / "install_console_scripts.py"
PYPROJECT = ROOT / "pyproject.toml"


def _load():
    spec = importlib.util.spec_from_file_location("install_console_scripts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_project_script_maps_to_an_importable_app_callable():
    ics = _load()
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    entries = ics.scripts(data)
    assert "f2w-kg-adopt" in entries and len(entries) == len(data["project"]["scripts"])
    for name, (module, attr) in entries.items():
        assert module.startswith("app."), (name, module)  # package-dir {"f2w": "app"} applied
        target = importlib.import_module(module)
        assert callable(getattr(target, attr)), (name, module, attr)


def test_launchers_are_written_executable_with_the_requested_interpreter(tmp_path):
    ics = _load()
    names = ics.install(PYPROJECT, tmp_path / "bin", "/opt/env/bin/python")
    assert "f2w-kg-adopt" in names
    launcher = tmp_path / "bin" / "f2w-kg-adopt"
    text = launcher.read_text(encoding="utf-8")
    assert text.startswith("#!/opt/env/bin/python -I\n")
    assert "from app.modules.commands.adopt_legacy_kg import main as _entry" in text
    mode = launcher.stat().st_mode
    assert mode & stat.S_IXUSR and mode & stat.S_IXOTH and mode & stat.S_IROTH


@pytest.mark.parametrize("spec", ["bad spec", "mod:", ":attr", "mod:attr:extra", "mod.x:a b"])
def test_malformed_entry_points_are_refused(spec):
    ics = _load()
    with pytest.raises(ValueError):
        ics.scripts({"project": {"scripts": {"x": spec}}})


def test_dockerfile_runs_the_installer_into_usr_local_bin():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    step = text[text.index("install_console_scripts.py"):]
    assert "--pyproject /app/pyproject.toml" in step and "--bin /usr/local/bin" in step
    assert "/opt/fair2wise/pixi/.pixi/envs/default/bin/python" in step
