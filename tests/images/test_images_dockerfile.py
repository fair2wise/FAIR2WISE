"""Static checks of the T24B image targets (no Docker needed).

Acceptance: one shared runtime base and three finals; only ``:local`` has the managed
Tiled 0.2.18 server (hash-checked); the private corpus is not copied into any layer;
every final runs the supervisor under ``tini`` as a non-root user with a fixed profile.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.modules.deployment import tiled_config
from dockerfile_model import load

REPO = Path(__file__).resolve().parents[2]
DF, IGNORE = load(REPO)
FINALS = ("local", "als-dev", "als-prod")
ALS = ("als-dev", "als-prod")
SUPERVISOR = "/opt/fair2wise/entrypoint/f2w_supervisor.py"
PROFILE_PATH = "/etc/fair2wise/profile.json"


def test_stage_layout():
    names = DF.names
    for name in ("frontend-build", "docs-build", "frontend", "tiled-server-build", "runtime-base", *FINALS, "agent"):
        assert name in names, name
    assert "pixi" not in names  # Splash/pixi retired
    for name in (*FINALS, "agent"):
        assert DF.stage(name).base == "runtime-base", name
    assert DF.stage("runtime-base").base == "${PYTHON_IMAGE}"
    assert DF.stage("tiled-server-build").base == "${PYTHON_IMAGE}"
    assert DF.global_args["PYTHON_IMAGE"].startswith("python:3.12")


def test_t24c_stages_are_kept():
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV F2W_PUBLIC_PREFIX=/fair2wise" in text
    assert "COPY docker/nginx.conf /etc/nginx/templates/default.conf.template" in text
    assert DF.stage("frontend").base == "nginx:1.27-alpine"
    base = DF.stage("runtime-base").copies()
    assert ("frontend-build", ["/build/ui/dist"], "/usr/share/nginx/html/fair2wise") in base
    assert ("docs-build", ["/build/site"], "/usr/share/nginx/html/fair2wise/docs") in base
    assert (None, ["docker/nginx.conf"], "/etc/fair2wise/nginx/gateway.conf.template") in base


def test_only_local_bundles_the_managed_tiled_server():
    assert "tiled-server-build" in DF.copied_from_stages("local")
    for name in (*ALS, "agent", "runtime-base"):
        assert "tiled-server-build" not in DF.copied_from_stages(name), name
        env = DF.effective_env(name)
        for var in ("F2W_TILED_DATA_DIR", "F2W_TILED_PYTHON", "F2W_TILED_PORT"):
            assert var not in env, (name, var)
    local = [c for c in DF.stage("local").copies() if c[0] == "tiled-server-build"]
    assert local == [("tiled-server-build", ["/opt/tiled/venv"], "/opt/tiled/venv")]


def test_tiled_server_pin_is_the_owner_pin_and_hash_checked():
    stage = DF.stage("tiled-server-build")
    run = " ".join(i.args for i in stage.of("RUN"))
    version = tiled_config.PINNED_TILED_VERSION
    assert version == "0.2.18"
    assert f'"tiled=={version}"' in run and "--no-deps" in run and "--only-binary=:all:" in run
    assert f"{tiled_config.PINNED_TILED_WHEEL_SHA256}  /tmp/tiled/wheel/{tiled_config.PINNED_TILED_WHEEL}" in run
    assert "sha256sum -c -" in run
    # The verified wheel file is what gets installed, never a re-resolved name.
    assert f'"/tmp/tiled/wheel/{tiled_config.PINNED_TILED_WHEEL}[server]"' in run
    assert "--constraint /tmp/tiled/constraints.txt" in run and "pip check" in run
    assert f"tiled.__version__ != '{version}'" in run
    assert run.index("sha256sum -c -") < run.index("python -m venv")
    constraints = (REPO / "docker/profiles/tiled-server/constraints.txt").read_text(encoding="utf-8")
    pins = dict(line.split("==", 1) for line in constraints.splitlines() if line and not line.startswith("#"))
    assert "tiled" not in pins
    assert pins["pydantic-settings"].startswith("2.11")  # tiled needs <2.12 (separate env)


@pytest.mark.parametrize("name", FINALS)
def test_finals_bake_their_own_fixed_profile(name):
    copies = [c for s in DF.lineage(name) for c in s.copies() if c[2] == PROFILE_PATH]
    assert copies == [(None, [f"docker/profiles/{name}.json"], PROFILE_PATH)]
    profile = json.loads((REPO / f"docker/profiles/{name}.json").read_text(encoding="utf-8"))
    env = DF.effective_env(name)
    assert env["F2W_DEPLOYMENT_PROFILE"] == name == profile["profile"] == profile["docker_target"]
    for key, value in profile["locked_env"].items():
        if key in env:
            assert env[key].rstrip("/") == value.rstrip("/"), (name, key)
    labels = {k: v.strip('"') for k, v in DF.stage(name).labels().items()}
    assert labels["f2w.profile"] == name
    assert labels["f2w.tiled_mode"] == profile["tiled_mode"]
    assert labels["f2w.base_path"] == "/fair2wise"
    assert labels["f2w.tiled_version"] == tiled_config.PINNED_TILED_VERSION


@pytest.mark.parametrize("name", FINALS)
def test_finals_run_the_supervisor_under_tini_as_non_root(name):
    entry = DF.last(name, "ENTRYPOINT").words()
    assert entry == ["/usr/bin/tini", "--", "python", SUPERVISOR, "--profile-file", PROFILE_PATH]
    assert DF.last(name, "CMD") is None
    assert DF.last(name, "USER").args.strip() == "10001:10001"
    health = DF.last(name, "HEALTHCHECK").args
    assert "http://127.0.0.1/fair2wise/healthz" in health  # gateway liveness only
    assert DF.last(name, "STOPSIGNAL").args.strip() == "SIGTERM"
    copies = [c for s in DF.lineage(name) for c in s.copies()]
    assert (None, ["docker/entrypoint/"], "/opt/fair2wise/entrypoint/") in copies


def test_runtime_base_disables_implicit_dotenv_and_fixes_the_prefix():
    env = DF.effective_env("runtime-base")
    assert env["F2W_DOTENV"] == "off"
    assert env["F2W_PUBLIC_PREFIX"] == "/fair2wise"
    assert env["F2W_API_UPSTREAM"] == "127.0.0.1:8090"
    assert "F2W_DEPLOYMENT_PROFILE" not in env  # only the finals choose a profile


def test_no_secrets_or_broad_copies():
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    for stage in DF.stages:
        for ins in stage.of("ARG"):
            assert not re.search(r"(?i)key|token|secret|passw", ins.args), (stage.name, ins.args)
        for _, sources, _ in stage.copies():
            assert "." not in sources and "./" not in sources, stage.name
            assert not any(s.startswith(".env") for s in sources), stage.name
    assert "COPY . ." not in text
    assert not re.search(r"(?i)ENV\s+\S*(API_KEY|TOKEN|SECRET)", text)


def test_runtime_has_no_runtime_pip_or_seed_steps():
    """No runtime installs or implicit imports: the entrypoint is the supervisor only."""
    for name in FINALS:
        for stage in DF.lineage(name):
            for ins in stage.of("RUN"):
                assert "ingest_tiled_sim" not in ins.args and "tiled_seed" not in ins.args
    text = (REPO / "docker/entrypoint/f2w_supervisor.py").read_text(encoding="utf-8")
    assert "pip install" not in text


def test_mount_points_exist_but_hold_no_image_data():
    run = " ".join(i.args for i in DF.stage("runtime-base").of("RUN"))
    assert "mkdir -p /var/lib/fair2wise/state /var/lib/fair2wise/cache /run/fair2wise" in run
    assert "chown -R" not in run
    local_run = " ".join(i.args for i in DF.stage("local").of("RUN"))
    assert "/var/lib/fair2wise/tiled" in local_run
    for name in ALS:
        assert not DF.stage(name).of("RUN")  # ALS finals add only their profile
    for name in FINALS:
        for _, _, dest in (c for s in DF.lineage(name) for c in s.copies()):
            assert not dest.startswith("/var/lib/fair2wise"), (name, dest)


def test_build_checks_the_ui_prefix():
    run = " ".join(i.args for i in DF.stage("runtime-base").of("RUN"))
    assert ("gateway.py check-build --manifest /usr/share/nginx/html/fair2wise/f2w-build.json "
            "--prefix /fair2wise") in run


def test_agent_target_stays_compatible_with_compose():
    agent = DF.stage("agent")
    assert DF.last("agent", "CMD").words() == ["./scripts/start_agent_backend.sh"]
    assert DF.last("agent", "ENTRYPOINT").words() == []
    assert "http://127.0.0.1:8090/health" in DF.last("agent", "HEALTHCHECK").args
    assert agent.env()["F2W_AGENT_HOST"] == "0.0.0.0"
    assert "tiled-server-build" not in DF.copied_from_stages("agent")
    compose = (REPO / "compose.yaml").read_text(encoding="utf-8")
    assert "target: agent" in compose and "target: frontend" in compose
