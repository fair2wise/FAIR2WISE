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
PIXI_VERSION = "0.84.2"  # the pixi that wrote pixi.lock
PIXI_LOCK = (REPO / "pixi.lock").read_text(encoding="utf-8")
PIXI_TOML = (REPO / "pixi.toml").read_text(encoding="utf-8")
TILED_ENV = "/opt/tiled/ws/.pixi/envs/tiled-server"
APP_ENV = "/opt/fair2wise/pixi/.pixi/envs/default"


def test_stage_layout():
    names = DF.names
    expected = ("frontend-build", "pixi-app", "docs-build", "frontend", "app-env-build", "tiled-server-build",
                "runtime-base", *FINALS, "agent")
    for name in expected:
        assert name in names, name
    for name in (*FINALS, "agent"):
        assert DF.stage(name).base == "runtime-base", name
    assert DF.stage("runtime-base").base == "${PYTHON_IMAGE}"
    # Dependency installs run in pixi stages (pixi.lock, --locked); only runtime-base is a plain Python image.
    assert DF.stage("pixi-app").base == "${PIXI_IMAGE}"
    assert DF.global_args["PIXI_IMAGE"].startswith(f"ghcr.io/prefix-dev/pixi:{PIXI_VERSION}@sha256:")
    for name in ("docs-build", "app-env-build", "tiled-server-build"):
        assert DF.stage(name).base == "pixi-app", name
    assert DF.global_args["PYTHON_IMAGE"].startswith("python:3.12-slim@sha256:")
    assert DF.global_args["NODE_IMAGE"].startswith("node:22-bookworm-slim@sha256:")  # UI build on Node 22 LTS
    assert DF.stage("frontend-build").base == "${NODE_IMAGE}"


DIGEST_PIN = re.compile(r"^[a-z0-9./_-]+(?::[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$")


def test_every_from_is_pinned_by_digest_through_an_overridable_build_arg():
    """Each FROM is a stage name or ``${ARG}`` whose global default is ``name:tag@sha256:<64 hex>`` (the
    multi-arch index digest), so a rebuild gets the same bytes and a deployment can override the pin."""
    stage_names = set(DF.names)
    for stage in DF.stages:
        if stage.base in stage_names:
            continue
        match = re.fullmatch(r"\$\{([A-Z0-9_]+)\}", stage.base)
        assert match, f"stage {stage.name}: FROM {stage.base} is neither a stage nor a ${{BUILD_ARG}}"
        default = DF.global_args.get(match.group(1))
        assert default and DIGEST_PIN.match(default), f"{match.group(1)} default {default!r} has no sha256 digest"
    raw = [m for m in re.findall(r"^FROM\s+(\S+)", (REPO / "Dockerfile").read_text(encoding="utf-8"), re.M)]
    assert raw and not [b for b in raw if "@sha256:" in b], "digests belong in the ARG defaults, not in FROM lines"
    assert {"PYTHON_IMAGE", "NODE_IMAGE", "NGINX_IMAGE", "PIXI_IMAGE", "PIXI_LEGACY_IMAGE"} <= set(DF.global_args)


def test_t24c_stages_are_kept():
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV F2W_PUBLIC_PREFIX=/fair2wise" in text
    assert "COPY docker/nginx.conf /etc/nginx/templates/default.conf.template" in text
    assert DF.stage("frontend").base == "${NGINX_IMAGE}"
    assert DF.global_args["NGINX_IMAGE"].startswith("nginx:1.27-alpine@sha256:")
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
    assert local == [("tiled-server-build", [TILED_ENV], TILED_ENV)]
    assert any(f"ln -s {TILED_ENV} /opt/tiled/venv" in i.args for i in DF.stage("local").of("RUN"))


def _lock_entry(url_fragment: str) -> str:
    """The pixi.lock package block (url line to the next package) whose URL contains the fragment."""
    m = re.search(r"^- pypi: \S*%s\S*\n(?:  .*\n)+" % re.escape(url_fragment), PIXI_LOCK, re.M)
    assert m, f"pixi.lock has no entry for {url_fragment}"
    return m.group(0)


def test_tiled_server_pin_is_the_owner_pin_and_hash_checked():
    """O-T1: the Tiled server is the owner-pinned 0.2.18 wheel; pixi.lock carries its sha256 and the
    image installs with --locked, so no other bytes can be installed (AUD-08 / T28C)."""
    version = tiled_config.PINNED_TILED_VERSION
    assert version == "0.2.18"
    assert re.search(r'^tiled = \{ version = "==%s", extras = \["server"\] \}' % re.escape(version), PIXI_TOML, re.M)
    entry = _lock_entry(tiled_config.PINNED_TILED_WHEEL)
    assert f"sha256: {tiled_config.PINNED_TILED_WHEEL_SHA256}" in entry
    stage = DF.stage("tiled-server-build")
    run = " ".join(i.args for i in stage.of("RUN"))
    assert "pixi install --locked -e tiled-server" in run
    assert f"tiled.__version__ != '{version}'" in run
    assert [p for _, srcs, _ in stage.copies() for p in srcs] == ["pixi.toml", "pixi.lock"]
    # tiled needs pydantic-settings<2.12: the isolated environment resolves it separately from the
    # application environment (which takes a newer one).
    versions = set(re.findall(r"^  name: pydantic-settings\n  version: (\S+)", PIXI_LOCK, re.M))
    assert any(v.startswith("2.11") for v in versions) and any(not v.startswith("2.11") for v in versions), versions


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
    assert DF.stage("pixi").base == "${PIXI_LEGACY_IMAGE}"
    assert DF.global_args["PIXI_LEGACY_IMAGE"].startswith("ghcr.io/prefix-dev/pixi:0.70.2@sha256:")
    assert ("pixi", ["/usr/local/bin/pixi"], "/usr/local/bin/pixi") in agent.copies()
    assert (None, ["splash_links"], "./splash_links") in agent.copies()
    assert agent.env()["SPLASH_LINKS_REPO"] == "/app/splash_links"
    assert any("pixi install --manifest-path splash_links/pixi.toml --locked" in i.args for i in agent.of("RUN"))
    for name in ("runtime-base", *FINALS):
        assert "pixi" not in DF.copied_from_stages(name), name
        assert not any(p.startswith("splash_links") for p in DF.context_sources(name)), name
        assert "SPLASH_LINKS_REPO" not in DF.effective_env(name), name
        # The pixi tool never runs in a final image (locked environments are built in earlier stages and
        # copied; `.pixi/` paths of those environments are fine, a `pixi <command>` is not).
        assert not any(re.search(r"(?<![./\w])pixi\s", i.args) for s in DF.lineage(name) for i in s.of("RUN")), name


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
    run = " ".join(i.args for i in DF.stage("app-env-build").of("RUN"))
    assert "pixi install --locked -e default" in run
    assert "torch.version.cuda is None" in run
    assert 'index = "https://download.pytorch.org/whl/cpu"' in PIXI_TOML
    torch_urls = re.findall(r"^- pypi: (\S*/torch-\S+)$", PIXI_LOCK, re.M)
    assert torch_urls and all("download-r2.pytorch.org/whl/cpu/" in u or "download.pytorch.org/whl/cpu/" in u
                              for u in torch_urls), torch_urls
    assert not re.findall(r"^  name: (?:nvidia-\S+|cuda-\S+)$", PIXI_LOCK, re.M)  # no CUDA wheel is locked
    assert f"ENV PATH={APP_ENV}/bin:$PATH" in " ".join(i.args or "" for i in DF.stage("runtime-base").of("ENV")) or \
        f"PATH={APP_ENV}/bin" in " ".join(i.args for i in DF.stage("runtime-base").of("ENV"))
    assert (("app-env-build", [APP_ENV], APP_ENV) in DF.stage("runtime-base").copies())


def test_docling_weights_are_baked_at_pinned_commits_and_never_tracked():
    """The parser's model weights are fetched at build time (not in the repo) by the one script that
    names the pinned commits, into the folder layout Docling reads, readable by uid 10001."""
    import sys

    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import fetch_docling_models as fetch
    finally:
        sys.path.remove(str(REPO / "scripts"))
    stage = DF.stage("runtime-base")
    run = " ".join(i.args for i in stage.of("RUN"))
    assert DF.effective_env("runtime-base").get("F2W_DOCLING_ARTIFACTS_PATH") == "/opt/fair2wise/docling"
    assert "python -I /tmp/fetch_docling_models.py --dest /opt/fair2wise/docling" in run
    assert "chmod -R a+rX /opt/fair2wise/docling" in run
    assert (None, ["scripts/fetch_docling_models.py"], "/tmp/fetch_docling_models.py") in stage.copies()
    assert set(fetch.PINNED_MODELS) == {"docling-project/docling-layout-heron", "docling-project/docling-models"}
    assert all(re.fullmatch(r"[0-9a-f]{40}", rev) for rev in fetch.PINNED_MODELS.values())  # commits, not tags
    assert fetch.model_folder("docling-project/docling-models") == "docling-project--docling-models"
    import subprocess

    listing = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True)
    if listing.returncode == 0:
        weights = [p for p in listing.stdout.splitlines() if p.endswith((".safetensors", ".onnx", ".pt", ".ckpt"))]
        assert not weights, weights  # no model weights are tracked: images fetch them at build time


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


def test_pixi_environments_install_only_from_the_lock():
    """AUD-08/B24: every dependency install in the Dockerfile is `pixi install --locked`; pip never
    installs application or server packages, and each stage copies only the manifest and the lock."""
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install" not in re.sub(r"(?m)^\s*#.*$", "", text)
    for stage, env in (("app-env-build", "default"), ("tiled-server-build", "tiled-server"), ("docs-build", "docs")):
        run = " ".join(i.args for i in DF.stage(stage).of("RUN"))
        assert f"pixi install --locked -e {env}" in run, stage
        assert [p for _, srcs, _ in DF.stage(stage).copies() for p in srcs if p.startswith(("pixi", "requirements"))] \
            == ["pixi.toml", "pixi.lock"], stage
    # Every PyPI artifact in the lock carries a sha256 (pixi writes one per artifact).
    entries = re.split(r"(?m)^- pypi: ", PIXI_LOCK)[1:]
    assert entries and all("  sha256: " in e.split("\n- ")[0] for e in entries)


@pytest.mark.parametrize("name", FINALS)
def test_finals_record_the_build_commit_in_the_revision_label(name):
    """The demo seed reads the revision label for ``f2w-kg-adopt --code-commit``; a build without the ARG says so."""
    stage = DF.stage(name)
    args = [i.args.strip() for i in stage.of("ARG")]
    assert args == ["GIT_COMMIT=unknown"], args  # never a made-up hash as the default
    labels = {k: v.strip('"') for k, v in stage.labels().items()}
    assert labels["org.opencontainers.image.revision"] == "${GIT_COMMIT}"
    arg_line = stage.of("ARG")[0].line
    label_line = min(i.line for i in stage.of("LABEL"))
    assert arg_line < label_line  # an ARG is visible only after it is declared in the stage
