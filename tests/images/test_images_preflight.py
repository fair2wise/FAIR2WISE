"""Image preflight (``docker/entrypoint/preflight.py``) and the API service wrapper (T24B)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.deployment import gateway as gw

REPO = Path(__file__).resolve().parents[2]
EP = REPO / "docker" / "entrypoint"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"t24b_{name}", EP / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PRE = _load("preflight")
SUP = _load("f2w_supervisor")


def setup(tmp_path: Path, name: str, *, mounted=True):
    """A profile whose mounts, UI manifest and runtime dirs live under ``tmp_path``."""
    profile = json.loads((REPO / f"docker/profiles/{name}.json").read_text(encoding="utf-8"))
    mounts = []
    for m in profile["required_mounts"]:
        path = tmp_path / "mnt" / Path(m["path"]).name
        path.mkdir(parents=True)
        mounts.append(dict(m, path=str(path)))
    profile["required_mounts"] = mounts
    ui = tmp_path / "www" / "fair2wise"
    ui.mkdir(parents=True)
    manifest = json.dumps(gw.PublicLayout("/fair2wise").as_manifest())
    (ui / gw.BUILD_MANIFEST_NAME).write_text(manifest, encoding="utf-8")
    run = tmp_path / "run"
    profile["gateway"] = {"build_manifest": str(ui / gw.BUILD_MANIFEST_NAME),
                          "template": str(REPO / "docker/nginx.conf"),
                          "rendered": str(run / "conf.d" / "default.conf"),
                          "runtime_dirs": [str(run / "tmp" / "proxy")]}
    env, _ = SUP.resolve_environment({}, SUP.parse_profile(profile))
    if name == "local":
        python = tmp_path / "tiled-venv" / "python"
        python.parent.mkdir()
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        python.chmod(0o755)
        env.update(F2W_TILED_DATA_DIR=mounts[1]["path"], F2W_TILED_PYTHON=str(python))
    mountinfo = tmp_path / "mountinfo"
    lines = ["22 1 0:21 / / rw - overlay overlay rw"]
    if mounted:
        lines += [f"{30 + i} 22 8:1 / {m['path']} rw - ext4 /dev/sda1 rw" for i, m in enumerate(mounts)]
    mountinfo.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return profile, env, mountinfo


@pytest.mark.parametrize("name", ["local", "als-dev", "als-prod"])
def test_preflight_accepts_the_image_layout(tmp_path, name):
    profile, env, mountinfo = setup(tmp_path, name)
    result = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "no-tiled")
    assert result["ok"], result["problems"]
    rendered = Path(profile["gateway"]["rendered"]).read_text(encoding="utf-8")
    assert "server 127.0.0.1:8090;" in rendered and "${" not in rendered
    assert Path(profile["gateway"]["runtime_dirs"][0]).is_dir()
    assert result["facts"]["tiled_mode"] == profile["tiled_mode"]


@pytest.mark.parametrize("name", ["als-dev", "als-prod"])
def test_als_image_with_a_tiled_server_environment_is_refused(tmp_path, name):
    profile, env, mountinfo = setup(tmp_path, name)
    (tmp_path / "opt-tiled" / "venv").mkdir(parents=True)
    result = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "opt-tiled")
    assert not result["ok"] and any("must not carry a Tiled server" in p for p in result["problems"])


def test_unmounted_state_is_refused(tmp_path):
    profile, env, mountinfo = setup(tmp_path, "als-prod", mounted=False)
    result = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")
    assert any("is not a mount point" in p for p in result["problems"])


def test_state_below_one_shared_mount_is_accepted(tmp_path):
    profile, env, mountinfo = setup(tmp_path, "als-dev", mounted=False)
    mountinfo.write_text(f"22 1 0:21 / / rw - overlay overlay rw\n30 22 8:1 / {tmp_path / 'mnt'} rw - ext4 x rw\n",
                         encoding="utf-8")
    assert PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["ok"]


def test_missing_mount_and_unreadable_secret_are_refused(tmp_path):
    profile, env, mountinfo = setup(tmp_path, "local")
    os.rmdir(profile["required_mounts"][0]["path"])
    env["F2W_TILED_WRITER_SECRET_FILE"] = str(tmp_path / "absent-secret")
    problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
    assert any("is missing: mount persistent storage there" in p for p in problems)
    assert any("F2W_TILED_WRITER_SECRET_FILE" in p for p in problems)


def test_local_without_the_tiled_interpreter_is_refused(tmp_path):
    profile, env, mountinfo = setup(tmp_path, "local")
    env["F2W_TILED_PYTHON"] = str(tmp_path / "missing" / "python")
    problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
    assert any("managed Tiled interpreter" in p for p in problems)


def test_ui_built_for_another_prefix_is_refused(tmp_path):
    profile, env, mountinfo = setup(tmp_path, "als-dev")
    Path(profile["gateway"]["build_manifest"]).write_text(json.dumps(gw.PublicLayout("/other").as_manifest()),
                                                          encoding="utf-8")
    problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
    assert any("gateway: UI bundle was built for prefix '/other'" in p for p in problems)


def test_profile_mismatch_is_refused_by_the_app_validators(tmp_path):
    profile, env, mountinfo = setup(tmp_path, "als-dev")
    env["F2W_TILED_URL"] = "https://tiled.als.lbl.gov"  # bypassing the supervisor lock on purpose
    problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
    assert any("deployment settings refused" in p for p in problems)
    assert any("Tiled settings refused" in p for p in problems)


def test_preflight_cli_exit_code(tmp_path):
    profile, env, mountinfo = setup(tmp_path, "als-dev", mounted=False)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    run_env = dict(env, PYTHONPATH=str(REPO), PATH=os.environ.get("PATH", ""), HOME=str(tmp_path))
    args = [sys.executable, str(EP / "preflight.py"), "--profile-file", str(path), "--mountinfo", str(mountinfo),
            "--tiled-root", str(tmp_path / "none")]
    done = subprocess.run(args, env=run_env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 78 and "not a mount point" in done.stderr
    mountinfo.write_text(f"30 22 8:1 / {tmp_path / 'mnt'} rw - ext4 x rw\n", encoding="utf-8")
    done = subprocess.run(args, env=run_env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["ok"] is True


# --------------------------------------------------------------------------- api service


def test_api_service_serves_the_jobs_api_under_the_prefix(tmp_path, monkeypatch):
    api = _load("api_service")
    env, _ = SUP.resolve_environment({}, SUP.load_profile(REPO / "docker/profiles/local.json"))
    for key, value in env.items():
        if key.startswith("F2W_") and key != "F2W_RESOURCE_ROOT":
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("F2W_WORKDIR", str(tmp_path / "session"))
    monkeypatch.setenv("F2W_TRACE_DIR", str(tmp_path / "traces"))
    argv = ["--backend", "cborg", "--model", "lbl/cborg-chat", "--kg-mode", "json",
            "--graph", "storage/kg/matkg_rsoxs_v1.json", "--schema", "storage/schema/rsoxs_schema.yaml",
            "--workdir", str(tmp_path / "session"), "api", "--host", "127.0.0.1", "--port", "8090"]
    app, options = api.build(argv, os.environ)
    assert options == {"host": "127.0.0.1", "port": 8090, "root_path": "/fair2wise/api"}
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/health" in paths and any(p.startswith("/v1/jobs") for p in paths)
    assert (tmp_path / "session" / "operations" / "jobs.sqlite3").exists()  # the worker's --db
    with pytest.raises(SystemExit):
        api.build(argv[:-4] + ["--host", "0.0.0.0", "--port", "8090"], os.environ)
