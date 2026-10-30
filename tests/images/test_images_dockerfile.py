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
    expected = ("frontend-build", "docs-build", "frontend", "tiled-server-build", "runtime-base", *FINALS, "agent")
    for name in expected:
        assert name in names, name
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
    # The verified wheel is what gets installed: pip runs in --require-hashes mode from the
    # lock, whose tiled entry carries the same sha256, and the checked file is offered first
    # through --find-links (T28C; the hash-lock test below checks the lock itself).
    assert "--require-hashes --find-links /tmp/tiled/wheel -r /tmp/tiled/constraints.lock.txt" in run
    assert "pip check" in run
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
    assert '"http://127.0.0.1:${F2W_GATEWAY_PORT}/fair2wise/healthz"' in health  # gateway liveness only
    assert DF.last(name, "EXPOSE").args.strip() == "8080"
    assert DF.last(name, "STOPSIGNAL").args.strip() == "SIGTERM"
    copies = [c for s in DF.lineage(name) for c in s.copies()]
    assert (None, ["docker/entrypoint/"], "/opt/fair2wise/entrypoint/") in copies


def test_runtime_base_disables_implicit_dotenv_and_fixes_the_prefix():
    env = DF.effective_env("runtime-base")
    assert env["F2W_DOTENV"] == "off"
    assert env["F2W_PUBLIC_PREFIX"] == "/fair2wise"
    assert env["F2W_API_UPSTREAM"] == "127.0.0.1:8090"
    assert env["F2W_GATEWAY_PORT"] == "8080"
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


def test_splash_stays_only_in_the_legacy_agent_target():
    """Splash retirement waits for its gate (T16B/H7 or H8): agent keeps it, the new images do not."""
    agent = DF.stage("agent")
    assert DF.stage("pixi").base == "ghcr.io/prefix-dev/pixi:0.70.2"
    assert ("pixi", ["/usr/local/bin/pixi"], "/usr/local/bin/pixi") in agent.copies()
    assert (None, ["splash_links"], "./splash_links") in agent.copies()
    assert agent.env()["SPLASH_LINKS_REPO"] == "/app/splash_links"
    assert any("pixi install --manifest-path splash_links/pixi.toml --locked" in i.args for i in agent.of("RUN"))
    for name in ("runtime-base", *FINALS):
        assert "pixi" not in DF.copied_from_stages(name), name
        assert not any(p.startswith("splash_links") for p in DF.context_sources(name)), name
        assert "SPLASH_LINKS_REPO" not in DF.effective_env(name), name
        assert not any("pixi" in i.args for s in DF.lineage(name) for i in s.of("RUN")), name


def test_gateway_listens_unprivileged_without_setcap():
    """C6: the gateway port is 8080 (T24A contract), so nginx gets no file capability."""
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "setcap" not in text and "libcap2-bin" not in text and "cap_net_bind_service=" not in text
    for name in ("frontend", "runtime-base", *FINALS):
        assert DF.effective_env(name)["F2W_GATEWAY_PORT"] == "8080", name
        assert DF.last(name, "EXPOSE").args.strip() == "8080", name
        assert "${F2W_GATEWAY_PORT}" in DF.last(name, "HEALTHCHECK").args, name
    for compose, host in (("compose.yaml", "5175"), ("compose.splash.yaml", "5173")):
        body = (REPO / compose).read_text(encoding="utf-8")
        assert f'"127.0.0.1:${{F2W_UI_PORT:-{host}}}:8080"' in body and ":80\"" not in body, compose
        assert '"http://127.0.0.1:8080/healthz"' in body, compose


# --------------------------------------------------------------- semantic retrieval -----
def _runtime_base_runs() -> str:
    return " ".join(i.args for i in DF.stage("runtime-base").of("RUN"))


def test_semantic_deps_install_cpu_torch_and_refuse_cuda_builds():
    """A5/AUD-08: the default index gives CUDA torch (~7 GB); images must take the CPU build."""
    run = _runtime_base_runs()
    assert "--require-hashes -r requirements/runtime.lock.txt" in run and "requirements/semantic.in" in run
    assert "pip install -r requirements/runtime.txt" not in run  # the hashed lock, not the version list
    assert "--index-url https://download.pytorch.org/whl/cpu" in run
    assert "torch.version.cuda is None" in run
    assert "pip install -r requirements.txt" not in run  # semantic.in is installed separately, after CPU torch


def test_embedding_models_are_baked_at_pinned_revisions_and_match_code_defaults():
    run = _runtime_base_runs()
    env = DF.effective_env("runtime-base")
    assert env.get("HF_HOME") == "/opt/fair2wise/hf"
    kg = (REPO / "app/modules/kg_rag_api.py").read_text(encoding="utf-8")
    merge = (REPO / "app/modules/term_extractor/merge_index.py").read_text(encoding="utf-8")
    for default, src in (("all-MiniLM-L6-v2", kg), ("paraphrase-MiniLM-L6-v2", merge)):
        assert default in src, default
        m = re.search(r"d\('sentence-transformers/%s', revision='([0-9a-f]{40})'\)" % re.escape(default), run)
        assert m, f"{default} is not baked at a pinned revision"
    assert "chmod -R a+rX /opt/fair2wise/hf" in run  # readable by uid 10001; no chown -R (mount-point test)
    for name in FINALS:
        assert DF.effective_env(name).get("HF_HUB_OFFLINE", json.load(open(REPO / f"docker/profiles/{name}.json"))["env"].get("HF_HUB_OFFLINE")) == "1", name


def test_tiled_server_venv_installs_from_the_hash_lock():
    """AUD-08/B24: the Tiled venv is built with --require-hashes from constraints.lock.txt; the
    wheel sha256sum check stays, and the checked wheel is the one pip installs (--find-links)."""
    run = " ".join(i.args for i in DF.stage("tiled-server-build").of("RUN"))
    assert "--require-hashes" in run and "-r /tmp/tiled/constraints.lock.txt" in run
    assert "--find-links /tmp/tiled/wheel" in run
    assert "sha256sum -c" in run and run.index("sha256sum -c") < run.index("pip install")
    assert "--constraint /tmp/tiled/constraints.txt" not in run
    copies = [p for _, srcs, _ in DF.stage("tiled-server-build").copies() for p in srcs]
    assert copies == ["docker/profiles/tiled-server/constraints.lock.txt"]
    lock = (REPO / "docker/profiles/tiled-server/constraints.lock.txt").read_text(encoding="utf-8")
    tiled_block = re.search(r"^tiled\[server\]==%s((?:\s*\\\n\s*--hash=\S+)+)" % re.escape(tiled_config.PINNED_TILED_VERSION),
                            lock, re.M)
    assert tiled_block, "the lock pins tiled[server] with hashes"
    assert f"--hash=sha256:{tiled_config.PINNED_TILED_WHEEL_SHA256}" in tiled_block.group(1)
