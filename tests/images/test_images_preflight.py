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


ALS_ORIGIN = "https://fair2wise.example.lbl.gov"


def setup(tmp_path: Path, name: str, *, mounted=True, cors=True):
    """A profile whose mounts, UI manifest and runtime dirs live under ``tmp_path``.

    ``cors``: give an ALS profile its required ``F2W_CORS_ORIGINS`` (C7).
    """
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
    if cors and name != "local":
        env["F2W_CORS_ORIGINS"] = ALS_ORIGIN
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
    assert "listen 8080;" in rendered and result["facts"]["gateway_port"] == 8080
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


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660])
def test_group_or_world_readable_secret_is_refused(tmp_path, mode):
    """AUD-15: preflight checks mode and owner, not just readability."""
    profile, env, mountinfo = setup(tmp_path, "local")
    secret = tmp_path / "writer.key"
    secret.write_text("abc123\n", encoding="utf-8")
    secret.chmod(mode)
    env["F2W_TILED_WRITER_SECRET_FILE"] = str(secret)
    problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
    assert any("F2W_TILED_WRITER_SECRET_FILE" in p and "group- or world-accessible" in p for p in problems)
    assert not any("abc123" in p for p in problems)
    secret.chmod(0o600)
    problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
    assert not any("F2W_TILED_WRITER_SECRET_FILE" in p for p in problems)


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
    argv = ["--backend", "cborg", "--model", "lbl/cborg-chat", "--kg-mode", "splash",
            "--graph", "storage/kg/matkg_rsoxs_v1.json", "--schema", "storage/schema/rsoxs_schema.yaml",
            "--workdir", str(tmp_path / "session"), "api", "--host", "127.0.0.1", "--port", "8090"]
    # C6: the service runs the CLI's own ``api --jobs-api`` path (app.api.main.run_api), which
    # applies root_path from F2W_PUBLIC_PREFIX; uvicorn.run is captured instead of serving.
    import uvicorn

    served = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.append((app, kw)))
    # Owner decision 2026-11-09: the image's editable default must never reach a Splash service while starting.
    import urllib.request

    import requests

    splash_hits: list[str] = []

    def _splash_trip(request, *_a, **_k):
        splash_hits.append(str(getattr(request, "full_url", None) or getattr(request, "url", request)))
        raise OSError("no Splash service on :local")

    monkeypatch.setattr(urllib.request, "urlopen", _splash_trip)
    monkeypatch.setattr(requests.Session, "send", _splash_trip)
    assert api.command(argv, os.environ) == argv + ["--jobs-api"]
    assert api.main(argv) == 0
    assert splash_hits == []
    ((app, options),) = served
    assert options == {"host": "127.0.0.1", "port": 8090, "root_path": "/fair2wise/api"}
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/health" in paths and any(p.startswith("/v1/jobs") for p in paths)
    assert (tmp_path / "session" / "operations" / "jobs.sqlite3").exists()  # the worker's --db
    with pytest.raises(SystemExit):
        api.command(argv[:-4] + ["--host", "0.0.0.0", "--port", "8090"], os.environ)
    with pytest.raises(SystemExit, match="only the 'api' command"):
        api.command(argv[:-5] + ["status"], os.environ)


def test_api_service_passes_cors_origins_from_the_environment(monkeypatch):
    api = _load("api_service")
    argv = ["api", "--host", "127.0.0.1", "--port", "8090"]
    env = {"F2W_CORS_ORIGINS": "https://a.example, ,https://b.example"}
    assert api.command(argv, env) == argv + ["--jobs-api", "--cors-origin", "https://a.example",
                                             "--cors-origin", "https://b.example"]
    assert api.command(argv + ["--jobs-api"], {}) == argv + ["--jobs-api"]
    with pytest.raises(SystemExit, match="not both"):
        api.command(argv + ["--cors-origin", "https://c.example"], env)


def test_jobs_api_run_api_applies_root_path_from_the_public_prefix(monkeypatch):
    """C6: ``cli api --jobs-api`` (app.api.main.run_api) gets root_path like the legacy run_api."""
    import uvicorn

    from app.api import main as api_main

    served, built = [], []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.append(kw))
    monkeypatch.setattr(api_main, "create_api_app",
                        lambda cfg, cors_origins=None, **kw: built.append(kw) or ("app", cors_origins))
    monkeypatch.setenv("F2W_PUBLIC_PREFIX", "/fair2wise")
    api_main.run_api(object(), host="127.0.0.1", port=8090)
    monkeypatch.delenv("F2W_PUBLIC_PREFIX")
    api_main.run_api(object(), host="127.0.0.1", port=8090)
    api_main.run_api(object(), host="127.0.0.1", port=8090, root_path="/x/api")
    monkeypatch.setenv("F2W_PUBLIC_PREFIX", "/fair2wise")
    api_main.run_api(object(), host="127.0.0.1", port=8090, root_path="")
    assert served == [{"host": "127.0.0.1", "port": 8090, "root_path": "/fair2wise/api"},
                      {"host": "127.0.0.1", "port": 8090},
                      {"host": "127.0.0.1", "port": 8090, "root_path": "/x/api"},
                      {"host": "127.0.0.1", "port": 8090}]
    # B7: the image mounts the model API, whose public root follows the same trusted prefix setting.
    assert [(kw["model_api"], kw["model_api_root_path"]) for kw in built] == [
        (True, "/fair2wise/model-api"), (True, "/model-api"), (True, "/model-api"), (True, "/fair2wise/model-api")]
    # D3.21: no profile in the environment means :local, which has no identity port or session store.
    assert all(kw["deployment"].is_local and kw["identity_port"] is None and kw["credential_store"] is None
               for kw in built)


def test_jobs_api_run_api_wires_als_identity_from_the_environment(monkeypatch):
    """D3.21: on an ALS profile ``run_api`` builds the Tiled identity port and the session store itself
    (``als_identity_wiring``, cookie Path = the public prefix) and hands both to ``create_api_app``; before
    this the ALS ``api`` service refused to start (``build_guard`` needs both)."""
    import uvicorn

    from app.api import main as api_main
    from app.api import sessions

    served, built, wired = [], [], []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.append(kw))
    monkeypatch.setattr(api_main, "create_api_app", lambda cfg, cors_origins=None, **kw: built.append(kw) or "app")

    def fake_wiring(settings, http_client=None, *, timeout=5.0, **store_kwargs):
        wired.append((settings.profile.value, store_kwargs))
        return "port", "store"

    monkeypatch.setattr(sessions, "als_identity_wiring", fake_wiring)
    for key in ("F2W_DEPLOYMENT_PROFILE", "F2W_TILED_WRITER_IDENTITY", "F2W_PUBLIC_PREFIX"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("F2W_DEPLOYMENT_PROFILE", "als-dev")
    monkeypatch.setenv("F2W_TILED_WRITER_IDENTITY", "f2w-writer")
    monkeypatch.setenv("F2W_PUBLIC_PREFIX", "/fair2wise")
    api_main.run_api(object(), host="127.0.0.1", port=8090)
    assert wired == [("als-dev", {"cookie_path": "/fair2wise"})]
    (kw,) = built
    assert kw["deployment"].profile.value == "als-dev" and not kw["deployment"].is_local
    assert (kw["identity_port"], kw["credential_store"]) == ("port", "store")
    assert served == [{"host": "127.0.0.1", "port": 8090, "root_path": "/fair2wise/api"}]
    # unprefixed: the cookie covers the whole origin
    monkeypatch.delenv("F2W_PUBLIC_PREFIX")
    api_main.run_api(object(), host="127.0.0.1", port=8090)
    assert wired[-1] == ("als-dev", {"cookie_path": "/"})


@pytest.mark.parametrize("port,ok", [("8080", True), ("9000", True), ("8090", False), ("8765", False),
                                     ("http", False), ("0", False)])
def test_preflight_gateway_port(tmp_path, port, ok):
    """C6: F2W_GATEWAY_PORT renders into ``listen``; it may not be the API or managed Tiled port."""
    profile, env, mountinfo = setup(tmp_path, "local")
    env["F2W_GATEWAY_PORT"] = port
    result = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "no-tiled")
    assert result["ok"] is ok, result["problems"]
    if ok:
        assert f"listen {port};" in Path(profile["gateway"]["rendered"]).read_text(encoding="utf-8")
    else:
        assert any("gateway" in p and "F2W_GATEWAY_PORT" in p for p in result["problems"])


# --------------------------------------------------------------------------- C7: CORS on ALS images


@pytest.mark.parametrize("name", ["als-dev", "als-prod"])
def test_als_preflight_requires_cors_origins_without_a_dev_fallback(tmp_path, name):
    profile, env, mountinfo = setup(tmp_path, name, cors=False)
    assert profile["cors_origins_required"] is True
    for value in (None, "", " , "):
        if value is None:
            env.pop("F2W_CORS_ORIGINS", None)
        else:
            env["F2W_CORS_ORIGINS"] = value
        problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
        assert any("F2W_CORS_ORIGINS is required" in p and "localhost" in p for p in problems), problems
    env["F2W_CORS_ORIGINS"] = f"{ALS_ORIGIN},https://other.example:8443"
    result = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")
    assert result["ok"], result["problems"]
    assert result["facts"]["cors_origins"] == 2


@pytest.mark.parametrize("bad", ["*", "https://ui.example/fair2wise", "ui.example", "https://user@ui.example",
                                 "ftp://ui.example", "https://ui.example/"])
def test_cors_origins_must_be_bare_origins(tmp_path, bad):
    profile, env, mountinfo = setup(tmp_path, "als-dev")
    env["F2W_CORS_ORIGINS"] = f"{ALS_ORIGIN},{bad}"
    problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
    assert any("is not an origin" in p for p in problems), problems


def test_local_keeps_its_default_origins(tmp_path):
    profile, env, mountinfo = setup(tmp_path, "local")
    assert profile["cors_origins_required"] is False and "F2W_CORS_ORIGINS" not in env
    result = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")
    assert result["ok"], result["problems"]
    api = _load("api_service")
    argv = ["api", "--host", "127.0.0.1", "--port", "8090"]
    assert api.command(argv, {"F2W_DEPLOYMENT_PROFILE": "local"}) == argv + ["--jobs-api"]


def test_als_preflight_cli_exits_78_without_cors_origins(tmp_path):
    profile, env, mountinfo = setup(tmp_path, "als-prod", cors=False)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    run_env = dict(env, PYTHONPATH=str(REPO), PATH=os.environ.get("PATH", ""), HOME=str(tmp_path))
    run_env.pop("F2W_CORS_ORIGINS", None)
    args = [sys.executable, str(EP / "preflight.py"), "--profile-file", str(path), "--mountinfo", str(mountinfo),
            "--tiled-root", str(tmp_path / "none")]
    done = subprocess.run(args, env=run_env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 78 and "F2W_CORS_ORIGINS is required" in done.stderr
    done = subprocess.run(args, env=dict(run_env, F2W_CORS_ORIGINS=ALS_ORIGIN), capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("name", ["als-dev", "als-prod"])
def test_als_api_service_refuses_to_fall_back_to_dev_origins(name):
    api = _load("api_service")
    argv = ["api", "--host", "127.0.0.1", "--port", "8090"]
    with pytest.raises(SystemExit, match="F2W_CORS_ORIGINS is required"):
        api.command(argv, {"F2W_DEPLOYMENT_PROFILE": name})
    env = {"F2W_DEPLOYMENT_PROFILE": name, "F2W_CORS_ORIGINS": ALS_ORIGIN}
    assert api.command(argv, env) == argv + ["--jobs-api", "--cors-origin", ALS_ORIGIN]
