"""The ``cluster`` profile (Kubernetes, restricted Pod Security): no Docker needed.

Covers the fixed profile (``docker/profiles/cluster.json``), the optional gateway, internal traffic that
is never proxied, the declared writable paths against what preflight requires, the Dockerfile target
(numeric non-root uid, no name lookups) and the documentation of every writable path. ``:local``,
``:als-dev`` and ``:als-prod`` must not gain any of the new profile keys.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from app.api.legacy_ui import deployment_settings_from
from app.modules.deployment import gateway as gw
from app.modules.deployment import tiled_config
from dockerfile_model import load

REPO = Path(__file__).resolve().parents[2]
EP = REPO / "docker" / "entrypoint"
DF, _IGNORE = load(REPO)


def _load(name):
    spec = importlib.util.spec_from_file_location(f"cluster_{name}", EP / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SUP = _load("f2w_supervisor")
PRE = _load("preflight")
API = _load("api_service")
PROFILE = REPO / "docker/profiles/cluster.json"


def raw():
    return json.loads(PROFILE.read_text(encoding="utf-8"))


def resolved(environ=None):
    return SUP.resolve_environment(dict(environ or {}), SUP.load_profile(PROFILE))[0]


def units(environ=None):
    env = resolved(environ)
    return {u.service.name: u for u in SUP.Supervisor(SUP.load_profile(PROFILE), env).units}, env


# ---------------------------------------------------------------- profile ----------------------------------

def test_profile_is_the_local_trust_model_with_bundled_tiled():
    data = raw()
    assert data["profile"] == data["image_tag"] == data["docker_target"] == "cluster"
    assert data["deployment_profile"] == "local" and data["locked_env"]["F2W_DEPLOYMENT_PROFILE"] == "local"
    local = json.loads((REPO / "docker/profiles/local.json").read_text(encoding="utf-8"))
    for key in ("authorization_mode", "tiled_mode", "bundles_tiled_server", "locked_env", "forbidden_env",
                "stripped_env"):
        assert data[key] == local[key], key
    env = resolved()
    settings = deployment_settings_from(env)
    assert settings.profile.value == "local" and settings.mode.value == "local_trusted"
    tiled = tiled_config.load_tiled_settings(env)
    assert tiled.mode == "managed" and tiled.managed.port == 8765
    assert env["F2W_PUBLIC_PREFIX"] == "/fair2wise" and env["F2W_DOTENV"] == "off"
    assert env["F2W_GATEWAY"] == "off" and env["F2W_SAFE_FETCH_TRUST_PROXY"] == "0"


@pytest.mark.parametrize("environ", [
    {"F2W_DEPLOYMENT_PROFILE": "als-prod"}, {"F2W_AUTHORIZATION_MODE": "tiled_authenticated"},
    {"F2W_TILED_MODE": "external"}, {"F2W_TILED_URL": "https://tiled.als.lbl.gov"},
    {"F2W_TILED_PYTHON": "/usr/bin/python3"}, {"F2W_TILED_WRITER_SECRET": "secret-value"},
])
def test_cluster_cannot_switch_trust_model_or_tiled_target(environ):
    with pytest.raises(SUP.ProfileError):
        resolved(environ)


def test_legacy_tiled_names_are_still_stripped():
    env = resolved({"TILED_URI": "https://tiled.als.lbl.gov", "TILED_API_KEY": "k", "F2W_LIVE_TILED": "1"})
    assert not {"TILED_URI", "TILED_API_KEY", "F2W_LIVE_TILED"} & set(env)


@pytest.mark.parametrize("name", ["local", "als-dev", "als-prod"])
def test_other_profiles_gain_none_of_the_new_keys(name):
    data = json.loads((REPO / f"docker/profiles/{name}.json").read_text(encoding="utf-8"))
    for key in ("deployment_profile", "enum_env", "no_proxy_append", "ensure_dirs", "writable_paths"):
        assert key not in data, (name, key)
    assert "switch_env" not in data["gateway"]
    profile = SUP.load_profile(REPO / f"docker/profiles/{name}.json")
    assert all(not s.when_env for s in profile.services) and not any(profile.preflight_when)
    env = SUP.resolve_environment({"NO_PROXY": "x.example"}, profile)[0]
    assert env["NO_PROXY"] == "x.example" and "no_proxy" not in env and "F2W_GATEWAY" not in env


# ---------------------------------------------------------------- gateway switch ---------------------------

def test_gateway_off_is_the_default_and_runs_the_api_as_the_only_listener():
    by_name, env = units()
    assert list(by_name) == ["tiled", "api", "worker"]  # no gateway process
    api = by_name["api"]
    assert api.argv[-6:] == ["api", "--host", "0.0.0.0", "--port", "8080", "--front"]
    assert api.health_url == "http://127.0.0.1:8080/fair2wise/api/health"
    assert resolved({"F2W_GATEWAY_PORT": "9000"})["F2W_GATEWAY_PORT"] == "9000"
    by_name9000, _ = units({"F2W_GATEWAY_PORT": "9000"})
    assert by_name9000["api"].health_url == "http://127.0.0.1:9000/fair2wise/api/health"
    assert by_name["tiled"].service.required is False and by_name["worker"].service.required is True


def test_gateway_on_matches_the_local_wiring():
    by_name, env = units({"F2W_GATEWAY": "on"})
    assert list(by_name) == ["tiled", "api", "worker", "gateway"]
    assert by_name["api"].argv[-5:] == ["api", "--host", "127.0.0.1", "--port", "8090"]
    assert "--front" not in by_name["api"].argv
    assert by_name["api"].health_url == "http://127.0.0.1:8090/health"
    assert by_name["gateway"].argv[0] == "nginx"
    assert by_name["gateway"].health_url == "http://127.0.0.1:8080/fair2wise/healthz"
    assert env["F2W_GATEWAY"] == "on"
    local = SUP.load_profile(REPO / "docker/profiles/local.json")
    local_units = {s.name: SUP.substitute(s.argv, env) for s in local.services}
    assert local_units["api"] == by_name["api"].argv and local_units["worker"] == by_name["worker"].argv


def test_gateway_value_is_validated_and_case_insensitive():
    assert resolved({"F2W_GATEWAY": " ON "})["F2W_GATEWAY"] == "on"
    with pytest.raises(SUP.ProfileError):
        resolved({"F2W_GATEWAY": "maybe"})
    with pytest.raises(SUP.ProfileError):
        resolved({"F2W_GATEWAY": "nginx"})
    assert resolved({"F2W_GATEWAY": ""})["F2W_GATEWAY"] == "off"  # blank is unset: the profile default


def test_nginx_syntax_check_runs_only_with_the_gateway(tmp_path):
    def plan(extra):
        done = subprocess.run([sys.executable, str(EP / "f2w_supervisor.py"), "--profile-file", str(PROFILE),
                               "--check"], capture_output=True, text=True, timeout=30,
                              env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), **extra})
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    off = plan({})
    assert [s[0] for s in off["preflight"]] == [sys.executable] and off["env"]["F2W_GATEWAY"] == "off"
    assert [s["name"] for s in off["services"]] == ["tiled", "api", "worker"]
    on = plan({"F2W_GATEWAY": "on"})
    assert [s[0] for s in on["preflight"]][-1] == "nginx"
    assert [s["name"] for s in on["services"]][-1] == "gateway"


def test_duplicate_service_names_need_a_condition():
    data = raw()
    data["services"] = [dict(data["services"][1]), dict(data["services"][1])]
    for service in data["services"]:
        service.pop("when_env", None)
    with pytest.raises(SUP.ProfileError):
        SUP.parse_profile(data)


# ---------------------------------------------------------------- proxies ----------------------------------

def test_proxy_variables_reach_every_child_and_internal_traffic_is_never_proxied():
    proxy = "http://user:pw@squid.cluster.local:3128"
    by_name, env = units({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "NO_PROXY": ".cluster.local,ollama.example",
                          "F2W_SAFE_FETCH_TRUST_PROXY": "1"})
    assert env["HTTP_PROXY"] == env["HTTPS_PROXY"] == proxy  # nothing strips or rewrites the proxy
    assert env["F2W_SAFE_FETCH_TRUST_PROXY"] == "1"
    entries = env["NO_PROXY"].split(",")
    assert entries[:2] == [".cluster.local", "ollama.example"]  # the operator's entries first, never removed
    assert {"127.0.0.1", "localhost", "::1"} <= set(entries)
    assert env["no_proxy"] == env["NO_PROXY"]
    assert len(entries) == len(set(entries))
    # One environment goes to every child (api, worker, tiled): the supervisor passes ``env`` to all.
    assert len(by_name) == 3


def test_no_proxy_is_added_even_without_a_proxy_and_keeps_both_spellings():
    env = resolved({"no_proxy": "a.example", "NO_PROXY": "b.example,a.example"})
    assert env["NO_PROXY"].split(",")[:2] == ["a.example", "b.example"]
    assert resolved()["NO_PROXY"] == "127.0.0.1,localhost,::1"


def test_supervisor_health_probes_do_not_use_the_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    assert SUP.http_ok("http://127.0.0.1:1/healthz", timeout=0.2) is False  # refused locally, not via the proxy


def test_check_output_shows_no_proxy_but_never_a_proxy_url(tmp_path):
    done = subprocess.run([sys.executable, str(EP / "f2w_supervisor.py"), "--profile-file", str(PROFILE), "--check"],
                          capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                               "HTTPS_PROXY": "http://user:topsecret@squid:3128", "NO_PROXY": "x.example"})
    assert done.returncode == 0 and "topsecret" not in done.stdout + done.stderr
    assert "127.0.0.1" in json.loads(done.stdout)["env"]["NO_PROXY"]


# ---------------------------------------------------------------- api service ------------------------------

def test_api_service_listens_beyond_loopback_only_with_front():
    args = ["--backend", "cborg", "--model", "m", "--kg-mode", "json", "--graph", "g.json", "--workdir", "/w",
            "api", "--host", "0.0.0.0", "--port", "8080"]
    with pytest.raises(SystemExit):
        API.command(args, {})
    out = API.command([*args, "--front"], {})
    assert "--front" not in out and "--jobs-api" in out and out[out.index("--host") + 1] == "0.0.0.0"
    loopback = [*args[:-4], "--host", "127.0.0.1", "--port", "8090"]
    kept = API.command(loopback, {})
    assert kept[kept.index("--host") + 1] == "127.0.0.1" and "--front" not in kept
    with pytest.raises(SystemExit):
        API.command([*args[:-4], "--host", "10.0.0.5", "--port", "8080", "--front"], {})


# ---------------------------------------------------------------- preflight --------------------------------

def _layout(tmp_path, gateway):
    profile = raw()
    mounts = []
    for item in profile["required_mounts"]:
        path = tmp_path / "mnt" / item["path"].strip("/").replace("/", "_")
        path.mkdir(parents=True)
        mounts.append(dict(item, path=str(path)))
    profile["required_mounts"] = mounts
    cache = Path(mounts[2]["path"])
    profile["ensure_dirs"] = [str(cache / "home"), str(cache / "xdg")]
    ui = tmp_path / "www" / "fair2wise"
    (ui / "docs").mkdir(parents=True)
    (ui / gw.BUILD_MANIFEST_NAME).write_text(json.dumps(gw.PublicLayout("/fair2wise").as_manifest()), encoding="utf-8")
    (ui / "index.html").write_text("ui", encoding="utf-8")
    (ui / "docs" / "index.html").write_text("docs", encoding="utf-8")
    run = tmp_path / "run"
    profile["gateway"] = dict(profile["gateway"], build_manifest=str(ui / gw.BUILD_MANIFEST_NAME),
                              template=str(REPO / "docker/nginx.conf"), rendered=str(run / "conf.d" / "default.conf"),
                              runtime_dirs=[str(run / "tmp" / "proxy")])
    env = SUP.resolve_environment({"F2W_GATEWAY": gateway}, SUP.parse_profile(profile))[0]
    python = tmp_path / "tiled-venv" / "python"
    python.parent.mkdir()
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    env.update(F2W_TILED_DATA_DIR=mounts[1]["path"], F2W_TILED_PYTHON=str(python))
    mountinfo = tmp_path / "mountinfo"
    persistent = [m for m in mounts if not m.get("ephemeral")]
    mountinfo.write_text("22 1 0:21 / / rw - overlay overlay rw\n" + "\n".join(
        f"{30 + i} 22 8:1 / {m['path']} rw - ext4 /dev/sda1 rw" for i, m in enumerate(persistent)) + "\n",
        encoding="utf-8")
    return profile, env, mountinfo


@pytest.mark.parametrize("gateway", ["off", "on"])
def test_preflight_accepts_the_layout_with_ephemeral_mounts_not_being_mount_points(tmp_path, gateway):
    profile, env, mountinfo = _layout(tmp_path, gateway)
    result = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "no-tiled")
    assert result["ok"], result["problems"]
    assert result["facts"]["gateway"] == ("off" if gateway == "off" else "nginx")
    assert Path(profile["gateway"]["rendered"]).exists() is (gateway == "on")
    assert (Path(profile["required_mounts"][2]["path"]) / "home").is_dir()  # ensure_dirs


def test_preflight_still_requires_the_persistent_mounts_to_be_mounts(tmp_path):
    profile, env, mountinfo = _layout(tmp_path, "off")
    mountinfo.write_text("22 1 0:21 / / rw - overlay overlay rw\n", encoding="utf-8")
    problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
    flagged = {p.split(" is not a mount point")[0] for p in problems if "is not a mount point" in p}
    persistent = {m["path"] for m in profile["required_mounts"] if not m.get("ephemeral")}
    assert flagged == persistent


def test_preflight_refuses_a_missing_or_unwritable_ephemeral_mount(tmp_path):
    profile, env, mountinfo = _layout(tmp_path, "off")
    os.rmdir(profile["required_mounts"][4]["path"])  # /tmp
    problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
    assert any("is missing: mount persistent storage there" in p for p in problems)


def test_gateway_off_needs_the_ui_and_docs_bundles(tmp_path):
    profile, env, mountinfo = _layout(tmp_path, "off")
    (Path(profile["gateway"]["build_manifest"]).parent / "docs" / "index.html").unlink()
    problems = PRE.check(env, profile, mountinfo=mountinfo, tiled_root=tmp_path / "none")["problems"]
    assert any(p.startswith("gateway:") and "docs" in p for p in problems)


# ---------------------------------------------------------------- writable paths ---------------------------

def test_declared_writable_paths_match_what_preflight_requires():
    data = raw()
    declared = data["writable_paths"]
    required = {w["path"] for w in declared if w["required"] == "required"}
    optional = {w["path"] for w in declared if w["required"] == "optional"}
    assert required == {m["path"] for m in data["required_mounts"] if m.get("writable")}
    assert optional == {m["path"] for m in data["optional_mounts"] if m.get("writable")}
    assert len({w["path"] for w in declared}) == len(declared)
    ephemeral = {m["path"] for m in data["required_mounts"] if m.get("ephemeral")}
    assert ephemeral == {w["path"] for w in declared if not w["persist"] and w["required"] == "required"}
    for w in declared:
        assert w["purpose"] and w["size"] and w["writer"] and "relocate_env" in w, w["path"]
        for sub in w["subpaths"]:
            assert sub["path"].startswith(w["path"] + "/"), sub
    # What preflight itself creates lies on a declared mount.
    for path in data["ensure_dirs"] + data["gateway"]["runtime_dirs"]:
        assert any(path.startswith(w["path"] + "/") for w in declared if w["required"] == "required"), path
    # The profile's own state paths are on the state mount; the data dirs are the mounts.
    env = data["env"]
    for var in ("F2W_WORKDIR", "F2W_JOB_DB", "F2W_TRACE_DIR", "HYBRID_RAG_LITERATURE_INDEX"):
        assert env[var].startswith("/var/lib/fair2wise/state/"), var
    assert env["F2W_SUPERVISOR_STATUS"].startswith("/run/fair2wise/")
    assert data["locked_env"]["F2W_TILED_DATA_DIR"] == "/var/lib/fair2wise/tiled"
    assert env["F2W_LIBRARY_DIR"] == "/var/lib/fair2wise/library"


def test_image_environment_points_every_cache_at_the_cache_mount():
    env = DF.effective_env("cluster")
    for var in ("HOME", "XDG_CACHE_HOME", "MPLCONFIGDIR", "PYSTOW_HOME"):
        assert env[var].startswith("/var/lib/fair2wise/cache/"), var
    assert env["TMPDIR"] == "/tmp" and env["PYTHONDONTWRITEBYTECODE"] == "1"  # no __pycache__ writes
    assert env["HF_HOME"].startswith("/opt/") and env["F2W_DOCLING_ARTIFACTS_PATH"].startswith("/opt/")  # read-only
    ensured = raw()["ensure_dirs"]
    for var, sub in (("HOME", "home"), ("XDG_CACHE_HOME", "xdg"), ("MPLCONFIGDIR", "matplotlib"),
                     ("PYSTOW_HOME", "pystow")):
        assert env[var] in ensured and env[var].endswith("/" + sub), var


DOC_PAGES = (REPO / "docs" / "cluster-profile.md", REPO / "mkdocs" / "docs" / "cluster-image.md")


@pytest.mark.parametrize("page", DOC_PAGES, ids=lambda p: p.name)
def test_docs_list_every_writable_path_and_its_relocation_variable(page):
    text = page.read_text(encoding="utf-8")
    for w in raw()["writable_paths"]:
        assert f"`{w['path']}`" in text, (page.name, w["path"])
        for sub in w["subpaths"]:
            assert f"`{sub['path']}`" in text, (page.name, sub["path"])
        for var in re.findall(r"[A-Z][A-Z0-9_]{3,}", w["relocate_env"]):
            assert f"`{var}`" in text or var in text, (page.name, var)
    for needle in ("runAsUser: 10001", "readOnlyRootFilesystem: true", "drop: [\"ALL\"]", "RuntimeDefault",
                   "/fair2wise/api/livez", "/fair2wise/api/readyz", "F2W_GATEWAY", "F2W_SAFE_FETCH_TRUST_PROXY",
                   "NO_PROXY", "FORWARDED_ALLOW_IPS"):
        assert needle in text, (page.name, needle)


# ---------------------------------------------------------------- Dockerfile -------------------------------

def test_cluster_stage_follows_the_local_pattern_and_runs_as_a_numeric_uid():
    stage = DF.stage("cluster")
    assert stage.base == "runtime-base"
    assert DF.last("cluster", "USER").args.strip() == "10001:10001"
    assert re.fullmatch(r"\d+:\d+", DF.last("cluster", "USER").args.strip())
    copies = [c for s in DF.lineage("cluster") for c in s.copies() if c[2] == "/etc/fair2wise/profile.json"]
    assert copies == [(None, ["docker/profiles/cluster.json"], "/etc/fair2wise/profile.json")]
    assert ("tiled-server-build", ["/opt/tiled/ws/.pixi/envs/tiled-server"],
            "/opt/tiled/ws/.pixi/envs/tiled-server") in stage.copies()
    env = DF.effective_env("cluster")
    assert env["F2W_DEPLOYMENT_PROFILE"] == "local" and env["F2W_TILED_PORT"] == "8765"
    labels = {k: v.strip('"') for k, v in stage.labels().items()}
    assert labels["f2w.profile"] == "cluster" and labels["f2w.tiled_mode"] == "managed"
    entry = DF.last("cluster", "ENTRYPOINT").words()
    assert entry == ["/usr/bin/tini", "--", "python", "/opt/fair2wise/entrypoint/f2w_supervisor.py",
                     "--profile-file", "/etc/fair2wise/profile.json"]
    assert [i.args.strip() for i in stage.of("ARG")] == ["GIT_COMMIT=unknown"]
    assert DF.last("cluster", "EXPOSE").args.strip() == "8080"


def test_no_user_name_lookup_su_or_gosu_at_start():
    """The runtime user is a bare number: no passwd entry, ``su``, ``gosu`` or name lookup is needed to start."""
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    for stage in DF.lineage("cluster"):
        for ins in stage.of("USER"):
            assert re.fullmatch(r"(root|\d+:\d+)", ins.args.strip()), ins.args
    assert not re.search(r"\b(gosu|su-exec|sudo)\b", text)
    for path in sorted(EP.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"\bimport (pwd|grp|getpass)\b|getpwuid|getpwnam|getuser\(|os\.getlogin", source), path.name
        assert "gosu" not in source and "'su'" not in source and '"su"' not in source, path.name
    cluster_run = " ".join(i.args for i in DF.stage("cluster").of("RUN"))
    assert "useradd" not in cluster_run and "groupadd" not in cluster_run


def test_cluster_is_not_a_published_channel_and_other_stages_are_untouched():
    for name in ("local", "als-dev", "als-prod"):
        assert DF.stage(name).base == "runtime-base"
        assert "cluster" not in DF.copied_from_stages(name)
    assert "cluster.json" not in " ".join(p for n in ("local", "als-dev", "als-prod") for p in DF.context_sources(n))
