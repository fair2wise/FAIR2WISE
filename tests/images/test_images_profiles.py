"""The three baked profiles agree with the application's deployment rules (T24B).

``docker/profiles/<name>.json`` is the only profile an image carries. These tests tie it
to the validators the app enforces at startup (``DeploymentSettings``,
``load_tiled_settings``), the companion profile matrix and the job/worker wiring, and
check that an operator environment cannot switch an image to another trust model.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from app.api.legacy_ui import deployment_settings_from
from app.modules.deployment import tiled_config
from app.modules.security import authorization

REPO = Path(__file__).resolve().parents[2]
NAMES = ("local", "als-dev", "als-prod")
ALS = ("als-dev", "als-prod")


def _supervisor():
    spec = importlib.util.spec_from_file_location("f2w_supervisor_t24b",
                                                  REPO / "docker/entrypoint/f2w_supervisor.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


SUP = _supervisor()


def raw(name):
    return json.loads((REPO / f"docker/profiles/{name}.json").read_text(encoding="utf-8"))


def resolved(name, environ=None):
    env, _ = SUP.resolve_environment(dict(environ or {}), SUP.load_profile(REPO / f"docker/profiles/{name}.json"))
    return env


@pytest.mark.parametrize("name", NAMES)
def test_profile_parses_and_matches_the_companion_matrix(name):
    profile = SUP.load_profile(REPO / f"docker/profiles/{name}.json")
    data = raw(name)
    matrix = json.loads((REPO / "docs/migration/companion/deployment_profiles.json").read_text(encoding="utf-8"))
    row = next(p for p in matrix["profiles"] if p["tag"] == name)
    assert data["docker_target"] == row["docker_target"] == data["image_tag"] == name
    assert profile.tiled_mode == row["tiled_mode"] == tiled_config.PROFILE_MODES[name][0]
    assert data["bundles_tiled_server"] is row["bundles_tiled_server"]
    if row["tiled_url"]:
        assert data["tiled_url"] == row["tiled_url"].rstrip("/") == tiled_config.ALS_PROFILE_URLS[name]
    assert data["authorization_mode"] == row["authorization_mode"]
    assert data["authorization_mode"] == authorization._mode_for(authorization.DeploymentProfile(name)).value


@pytest.mark.parametrize("name", NAMES)
def test_services_and_order(name):
    profile = SUP.load_profile(REPO / f"docker/profiles/{name}.json")
    names = [s.name for s in profile.services]
    if name == "local":
        assert names == ["tiled", "api", "worker", "gateway"]
        tiled = profile.services[0]
        assert not tiled.required and tiled.restart_max > 0 and 78 in tiled.no_restart_exit_codes
        assert tiled.argv[-1].endswith("/tiled_service.py")
    else:
        assert names == ["api", "worker", "gateway"]  # no managed Tiled server on ALS
        assert not any("tiled" in a for s in profile.services for a in s.argv)
    for service in profile.services:
        if service.name != "tiled":
            assert service.required, service.name


@pytest.mark.parametrize("name", NAMES)
def test_api_worker_gateway_wiring(name):
    env = resolved(name)
    profile = SUP.load_profile(REPO / f"docker/profiles/{name}.json")
    units = {s.name: SUP.substitute(s.argv, env) for s in profile.services}
    api = units["api"]
    assert api[-5:] == ["api", "--host", "127.0.0.1", "--port", "8090"]
    assert env["F2W_API_UPSTREAM"] == "127.0.0.1:8090"
    worker = units["worker"]
    assert worker[1:3] == ["-m", "app.modules.workers"]
    factory = worker[worker.index("--factory") + 1]
    assert re.fullmatch(r"app\.modules(\.[A-Za-z_][A-Za-z0-9_]*)+:[A-Za-z_][A-Za-z0-9_]*", factory)
    # The worker drains the same job store the API's operations use (<workdir>/operations).
    db = worker[worker.index("--db") + 1]
    workdir = api[api.index("--workdir") + 1]
    assert db == f"{workdir}/operations/jobs.sqlite3"
    assert workdir.startswith("/var/lib/fair2wise/state/")
    for graph in env["F2W_GRAPH"].split(","):
        assert (REPO / graph).is_file(), graph
    assert (REPO / env["F2W_SCHEMA"]).is_file()
    assert units["gateway"][0] == "nginx"
    health = {s.name: s.health for s in profile.services}
    assert profile.services[-1].health.http == "http://127.0.0.1:${F2W_GATEWAY_PORT}/fair2wise/healthz"
    assert env["F2W_GATEWAY_PORT"] == "8080"  # default; an operator may set another port
    assert SUP.substitute([profile.services[-1].health.http], env) == ["http://127.0.0.1:8080/fair2wise/healthz"]
    assert resolved(name, {"F2W_GATEWAY_PORT": "9000"})["F2W_GATEWAY_PORT"] == "9000"
    assert health["api"].http == "http://127.0.0.1:8090/health"


@pytest.mark.parametrize("name", NAMES)
def test_mounts(name):
    mounts = [m["path"] for m in raw(name)["required_mounts"]]
    assert "/var/lib/fair2wise/state" in mounts
    assert ("/var/lib/fair2wise/tiled" in mounts) is (name == "local")
    if name == "local":
        assert raw(name)["locked_env"]["F2W_TILED_DATA_DIR"] == "/var/lib/fair2wise/tiled"


@pytest.mark.parametrize("name", NAMES)
def test_resolved_environment_passes_the_app_startup_validators(name):
    env = resolved(name)
    settings = deployment_settings_from(env)
    assert settings.profile.value == name
    assert settings.mode.value == raw(name)["authorization_mode"]
    tiled = tiled_config.load_tiled_settings(env)
    assert tiled.mode == raw(name)["tiled_mode"]
    if name == "local":
        assert settings.tiled_target == authorization.LOCAL_DEFAULT_TILED_TARGET
        assert tiled.managed.port == 8765 and str(tiled.managed.data_dir) == "/var/lib/fair2wise/tiled"
        assert settings.tiled_target.endswith(f":{tiled.managed.port}")
    else:
        assert tiled.external.url == tiled_config.ALS_PROFILE_URLS[name]
        assert tiled.external.require_writer_role == "admin"
        assert settings.tiled_target.rstrip("/") == tiled_config.ALS_PROFILE_URLS[name]
    assert env["F2W_PUBLIC_PREFIX"] == "/fair2wise" and env["F2W_DOTENV"] == "off"


@pytest.mark.parametrize("name", ALS)
@pytest.mark.parametrize("environ", [
    {"F2W_DEPLOYMENT_PROFILE": "local"},
    {"F2W_DEPLOYMENT_PROFILE": "test"},
    {"F2W_AUTHORIZATION_MODE": "local_trusted"},
    {"F2W_TILED_MODE": "managed"},
    {"F2W_TILED_URL": "http://127.0.0.1:8765"},
    {"F2W_TILED_DATA_DIR": "/tmp/tiled"},
    {"F2W_TILED_PYTHON": "/usr/bin/python3"},
    {"F2W_TILED_PORT": "8765"},
    {"F2W_LOCAL_BIND": "network"},
    {"F2W_TILED_API_KEY": "secret-value"},
    {"F2W_PUBLIC_PREFIX": "/other"},
])
def test_als_images_cannot_become_local_or_managed(name, environ):
    with pytest.raises(SUP.ProfileError):
        resolved(name, environ)


@pytest.mark.parametrize("environ", [
    {"F2W_DEPLOYMENT_PROFILE": "als-prod"},
    {"F2W_AUTHORIZATION_MODE": "tiled_authenticated"},
    {"F2W_TILED_MODE": "external"},
    {"F2W_TILED_URL": "https://tiled.als.lbl.gov"},
    {"F2W_TILED_PYTHON": "/usr/bin/python3"},
    {"F2W_TILED_PORT": "9999"},
    {"F2W_TILED_WRITER_SECRET": "secret-value"},
])
def test_local_image_cannot_switch_mode_or_target(environ):
    with pytest.raises(SUP.ProfileError):
        resolved("local", environ)


def test_als_dev_and_prod_cannot_be_swapped():
    with pytest.raises(SUP.ProfileError):
        resolved("als-dev", {"F2W_DEPLOYMENT_PROFILE": "als-prod"})
    with pytest.raises(SUP.ProfileError):
        resolved("als-prod", {"F2W_TILED_URL": "https://tiled-staging.als.lbl.gov/"})
    env = resolved("als-prod", {"F2W_TILED_URL": "https://tiled.als.lbl.gov/",
                                "F2W_DEPLOYMENT_PROFILE": "als-prod"})
    assert env["F2W_TILED_URL"] == "https://tiled.als.lbl.gov"


@pytest.mark.parametrize("name", NAMES)
def test_legacy_tiled_names_never_reach_the_services(name):
    env = resolved(name, {"TILED_URI": "https://tiled.als.lbl.gov", "TILED_API_KEY": "k", "F2W_LIVE_TILED": "1",
                          "TILED_CATALOG_URI": "sqlite:///x.db"})
    for var in ("TILED_URI", "TILED_API_KEY", "F2W_LIVE_TILED", "TILED_CATALOG_URI"):
        assert var not in env


@pytest.mark.parametrize("name", NAMES)
def test_forbidden_names_cover_the_app_rules(name):
    forbidden = set(raw(name)["forbidden_env"])
    assert set(tiled_config._SECRET_VALUE_VARS) <= forbidden
    if name in ALS:
        assert set(tiled_config._MANAGED_ONLY_VARS) <= forbidden and "F2W_LOCAL_BIND" in forbidden
        assert "F2W_FOLDER_ROOTS" in forbidden  # T32 (B4): folder browsing is a :local feature
        assert "F2W_LIBRARY_DIR" in forbidden  # T35 (A8): the library is a :local feature
    else:
        assert "F2W_TILED_URL" in forbidden


@pytest.mark.parametrize("name", NAMES)
def test_check_mode_prints_the_plan_without_starting(name, tmp_path):
    import subprocess
    import sys

    done = subprocess.run([sys.executable, str(REPO / "docker/entrypoint/f2w_supervisor.py"),
                           "--profile-file", str(REPO / f"docker/profiles/{name}.json"), "--check"],
                          capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                          timeout=30)
    assert done.returncode == 0, done.stderr
    plan = json.loads(done.stdout)
    assert plan["profile"] == name and plan["env"]["F2W_DEPLOYMENT_PROFILE"] == name
    assert [s["name"] for s in plan["services"]][-3:] == ["api", "worker", "gateway"]
