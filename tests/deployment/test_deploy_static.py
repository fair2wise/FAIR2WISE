"""Static checks of the Ansible handoff under ``deploy/`` (T24E, DEP-16).

No Ansible, Docker or network: the plays, role, templates and example inventory are parsed
as YAML/Jinja and checked against the deployment contract (ANSIBLE_HANDOFF.md,
DEPLOYMENT_STRATEGY.md sections 3, 6 and 8, docker/profiles/README.md). ``ansible-lint`` and
``ansible-playbook --syntax-check`` are not installed in the test environment, so the
structural shape (plays, FQCN modules, one module per task) is validated here instead.
"""

from __future__ import annotations

import ast
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, Iterable, List

import jinja2
import pytest
import yaml
from jinja2.nativetypes import NativeEnvironment

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"
ROLE = DEPLOY / "roles" / "fair2wise"
PLAYBOOKS = sorted((DEPLOY / "playbooks").glob("*.yml"))
TASK_FILES = sorted((ROLE / "tasks").glob("*.yml"))
TEMPLATE = ROLE / "templates" / "edge-traefik.yml.j2"
INVENTORY = DEPLOY / "inventory.example.yml"
TEXT_FILES = sorted(p for p in DEPLOY.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
CONFIG_FILES = [p for p in TEXT_FILES if p.suffix in (".yml", ".j2", ".cfg")]
PROFILES = ("als-dev", "als-prod", "local")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64
VAR_RE = re.compile(r"\b(fair2wise_[a-z0-9_]+)\b")


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def tasks_in(data: Any) -> Iterable[Dict[str, Any]]:
    """Every task dict in a task file or a play (tasks/pre_tasks/post_tasks, block bodies)."""
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                if "block" in item:
                    yield from tasks_in(item["block"])
                elif any(k in item for k in ("tasks", "pre_tasks", "post_tasks", "roles")):
                    for key in ("pre_tasks", "tasks", "post_tasks"):
                        yield from tasks_in(item.get(key) or [])
                else:
                    yield item


def module_of(task: Dict[str, Any]) -> str:
    keys = [k for k in task if "." in k]
    assert len(keys) == 1, f"task {task.get('name')!r} must name exactly one FQCN module: {sorted(task)}"
    return keys[0]


# ---- a tiny Ansible-flavoured Jinja resolver for the defaults ----------------------------
class _Omit:
    pass


def _combine(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    out.update(b)
    return out


def _env() -> NativeEnvironment:
    env = NativeEnvironment()
    env.filters["combine"] = _combine
    env.filters["basename"] = lambda v: os.path.basename(str(v))
    env.filters["dirname"] = lambda v: os.path.dirname(str(v))
    env.filters["from_json"] = __import__("json").loads
    env.filters["bool"] = lambda v: str(v).lower() in ("1", "true", "yes")
    env.tests["match"] = lambda v, pattern: re.match(pattern, str(v)) is not None
    env.globals["omit"] = _Omit()
    return env


def resolve_defaults(profile: str, inventory_vars: Dict[str, Any]) -> Dict[str, Any]:
    """Render roles/fair2wise/defaults/main.yml for one profile with the example inventory."""
    env = _env()
    values: Dict[str, Any] = dict(load_yaml(ROLE / "defaults" / "main.yml"))
    values.update(inventory_vars)
    values["fair2wise_profile"] = profile

    def render(value: Any, ctx: Dict[str, Any]) -> Any:
        if isinstance(value, str) and "{{" in value:
            out = env.from_string(value).render(**ctx)
            if isinstance(out, str) and out.strip().startswith(("[", "{")):
                try:
                    return ast.literal_eval(out)
                except (ValueError, SyntaxError):
                    return out
            return out
        if isinstance(value, list):
            return [render(v, ctx) for v in value]
        if isinstance(value, dict):
            return {k: render(v, ctx) for k, v in value.items()}
        return value

    for _ in range(8):  # variables refer to variables; iterate to a fixpoint
        new = {k: render(v, values) for k, v in values.items()}
        if new == values:
            return new
        values = new
    raise AssertionError("defaults did not resolve to a fixpoint")


def inventory_group_vars(group: str) -> Dict[str, Any]:
    inv = load_yaml(INVENTORY)
    f2w = inv["all"]["children"]["fair2wise"]
    merged = dict(f2w.get("vars") or {})
    merged.update(f2w["children"][group].get("vars") or {})
    return merged


@pytest.fixture(scope="module")
def als_dev() -> Dict[str, Any]:
    return resolve_defaults("als-dev", inventory_group_vars("als_dev"))


@pytest.fixture(scope="module")
def als_prod() -> Dict[str, Any]:
    return resolve_defaults("als-prod", inventory_group_vars("als_prod"))


@pytest.fixture(scope="module")
def local() -> Dict[str, Any]:
    base = inventory_group_vars("als_dev")
    for key in list(base):
        if key in ("fair2wise_fqdn", "fair2wise_cors_origins", "fair2wise_tiled_writer_secret",
                   "fair2wise_tls_certificate_path", "fair2wise_tls_key_path"):
            del base[key]
    return resolve_defaults("local", base)


# ---- shape -----------------------------------------------------------------------------
@pytest.mark.parametrize("path", [p for p in TEXT_FILES if p.suffix == ".yml"], ids=lambda p: p.relative_to(DEPLOY).as_posix())
def test_every_yaml_file_parses(path: Path) -> None:
    data = load_yaml(path)
    assert data is not None and isinstance(data, (list, dict))


@pytest.mark.parametrize("path", PLAYBOOKS, ids=lambda p: p.name)
def test_playbooks_are_plays_against_the_fair2wise_group_with_become(path: Path) -> None:
    plays = load_yaml(path)
    assert isinstance(plays, list) and plays
    for play in plays:
        assert play["hosts"] == "fair2wise", "plays target the inventory group, never a literal host"
        assert play["become"] is True
        assert "roles" in play or "tasks" in play
        for task in tasks_in([play]):
            module_of(task)
    # The role is applied by every play (directly or via import_role).
    text = path.read_text(encoding="utf-8")
    assert "role: fair2wise" in text or "name: fair2wise" in text


@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.name)
def test_task_files_use_named_tasks_with_fully_qualified_modules(path: Path) -> None:
    tasks = load_yaml(path)
    assert isinstance(tasks, list) and tasks
    for task in tasks_in(tasks):
        assert task.get("name"), f"unnamed task in {path.name}"
        module = module_of(task)
        assert module.startswith(("ansible.builtin.", "community.docker.")), module


def test_role_has_meta_handlers_and_only_prefixed_defaults() -> None:
    meta = load_yaml(ROLE / "meta" / "main.yml")
    assert "community.docker" in meta["collections"]
    handlers = load_yaml(ROLE / "handlers" / "main.yml")
    assert any(h.get("listen") == "fair2wise edge changed" for h in handlers)
    defaults = load_yaml(ROLE / "defaults" / "main.yml")
    assert all(k.startswith("fair2wise_") for k in defaults), sorted(defaults)


# ---- image references: digest only -------------------------------------------------------
def test_container_image_is_repository_at_digest_and_validated_by_regex(als_dev: Dict[str, Any]) -> None:
    container = {t["name"]: t for t in load_yaml(ROLE / "tasks" / "container.yml")}
    start = container["Start the container from the digest"]["community.docker.docker_container"]
    assert start["image"] == "{{ fair2wise_image_ref }}"
    assert start["pull"] is False, "the digest is pulled by an explicit docker_image task first"
    pull = container["Pull the approved digest"]["community.docker.docker_image"]
    assert pull == {"name": "{{ fair2wise_image_ref }}", "source": "pull"}
    assert als_dev["fair2wise_image_ref"] == f"ghcr.io/fair2wise/fair2wise@{PLACEHOLDER_DIGEST}"
    validate = (ROLE / "tasks" / "validate.yml").read_text(encoding="utf-8")
    assert "fair2wise_image_digest is match('^sha256:[0-9a-f]{64}$')" in validate


@pytest.mark.parametrize("path", TEXT_FILES, ids=lambda p: p.relative_to(DEPLOY).as_posix())
def test_no_tag_reference_anywhere(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(r"ghcr\.io/[\w./-]+(:[\w.-]+)?", text):
        assert not match.group(1), f"{path.name}: tag reference {match.group(0)}"
    assert not re.search(r"fair2wise:(local|als-dev|als-prod)\b", text), path.name
    assert "image: \"{{ fair2wise_image_repository }}:" not in text


# ---- no secrets or hostnames ---------------------------------------------------------------
FORBIDDEN = (
    re.compile(r"lbl\.gov"),
    re.compile(r"tiled-staging|tiled\.als"),
    re.compile(r"!vault|\$ANSIBLE_VAULT"),
    re.compile(r"AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b)(\d{1,3}\.){3}\d{1,3}\b"),
)


@pytest.mark.parametrize("path", TEXT_FILES, ids=lambda p: p.relative_to(DEPLOY).as_posix())
def test_no_secret_hostname_or_real_digest_is_committed(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for pattern in FORBIDDEN:
        assert not pattern.search(text), f"{path.name}: {pattern.pattern}"
    for digest in re.findall(r"sha256:[0-9a-f]{64}", text):
        assert digest == PLACEHOLDER_DIGEST, f"{path.name}: a real digest is committed"
    for key, value in re.findall(r"^\s*(\w*(?:secret|password|api_key|token)\w*)\s*:\s*(.+)$", text, re.M | re.I):
        assert "{{" in value or value.strip().startswith(("/", '"/', "'/")) or value.strip() in ("", "{}"), \
            f"{path.name}: {key} has a literal value"


def test_example_inventory_uses_placeholders_and_vault_references_only() -> None:
    inv = load_yaml(INVENTORY)
    groups = inv["all"]["children"]["fair2wise"]["children"]
    assert set(groups) == {"als_dev", "als_prod"}
    for name, group in groups.items():
        for host in group["hosts"]:
            assert host.endswith(".example.invalid"), host
        vars_ = group["vars"]
        assert vars_["fair2wise_fqdn"].endswith(".example.invalid")
        assert vars_["fair2wise_image_digest"] == PLACEHOLDER_DIGEST
        assert re.fullmatch(r"\{\{ vault_fair2wise_tiled_writer_secret_als_(dev|prod) \}\}", vars_["fair2wise_tiled_writer_secret"])
        assert vars_["fair2wise_profile"] == name.replace("_", "-")
        for origin in vars_["fair2wise_cors_origins"].split(","):
            assert re.fullmatch(r"https://[^/]+", origin), "origins are scheme/host, never paths"
        assert f"https://{vars_['fair2wise_fqdn']}" in vars_["fair2wise_cors_origins"].split(",")
    text = INVENTORY.read_text(encoding="utf-8")
    assert "supplied by the operator" in text and "ANSIBLE_HANDOFF.md" in text


# ---- edge proxy (Traefik, owner decision D3.19) -------------------------------------------------
def _rendered_edge(als_dev: Dict[str, Any]) -> Dict[str, Any]:
    text = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(
        TEMPLATE.read_text(encoding="utf-8")).render(**als_dev, ansible_managed="test")
    assert "{{" not in text and "}}" not in text
    return yaml.safe_load(text)


def test_edge_template_overwrites_forwarded_proto_and_forwards_the_prefix_unchanged() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert 'X-Forwarded-Proto: "https"' in code
    assert "X-Forwarded-Proto" not in code.replace('X-Forwarded-Proto: "https"', ""), \
        "the client's header is never relayed; the only mention in the configuration is the overwrite"
    assert "http://{{ fair2wise_gateway_bind }}:{{ fair2wise_gateway_host_port }}" in text, \
        "no path in the service URL keeps /fair2wise intact; the gateway strips it once"
    assert "buffering:" not in text, "a Traefik buffering middleware would buffer the SSE responses"


def test_edge_template_renders_for_the_example_inventory(als_dev: Dict[str, Any]) -> None:
    edge = _rendered_edge(als_dev)
    http = edge["http"]
    app = http["routers"]["fair2wise-app"]
    assert app["rule"] == ("Host(`f2w-dev.example.invalid`) && (Path(`/fair2wise`) || "
                           "PathPrefix(`/fair2wise/`))")
    assert app["entryPoints"] == ["websecure"] and app["tls"] == {} and app["service"] == "fair2wise-gateway"
    headers = http["middlewares"]["fair2wise-headers"]["headers"]["customRequestHeaders"]
    assert headers["X-Forwarded-Proto"] == "https"
    assert headers["X-Forwarded-Host"] == headers["X-Forwarded-Port"] == headers["X-Forwarded-Prefix"] == ""
    lb = http["services"]["fair2wise-gateway"]["loadBalancer"]
    assert lb["servers"] == [{"url": "http://127.0.0.1:8080"}] and lb["passHostHeader"] is True
    assert lb["responseForwarding"]["flushInterval"] == "-1ms"
    transport = http["serversTransports"]["fair2wise-transport"]["forwardingTimeouts"]
    assert transport["responseHeaderTimeout"] == "900s"
    redirect = http["routers"]["fair2wise-redirect"]
    assert redirect["entryPoints"] == ["web"] and redirect["middlewares"] == ["fair2wise-https"]
    assert http["middlewares"]["fair2wise-https"]["redirectScheme"] == {"scheme": "https", "permanent": True}
    root = http["routers"]["fair2wise-root"]
    assert root["rule"] == "Host(`f2w-dev.example.invalid`) && Path(`/`)"
    assert http["middlewares"]["fair2wise-root-redirect"]["redirectRegex"]["replacement"] == \
        "https://${1}/fair2wise/"
    cert = edge["tls"]["certificates"][0]
    assert cert["certFile"] == "/etc/pki/tls/certs/f2w-dev.example.invalid.crt"
    assert edge["tls"]["options"]["default"]["minVersion"] == "VersionTLS12"
    # Nothing outside the prefix or the root is routed: every router is bound to this host name.
    assert all("Host(`f2w-dev.example.invalid`)" in r["rule"] for r in http["routers"].values())


def test_proxy_task_templates_the_edge_and_checks_the_file(als_dev: Dict[str, Any]) -> None:
    tasks = {t["name"]: t for t in load_yaml(ROLE / "tasks" / "proxy.yml")}
    task = tasks["Edge Traefik dynamic configuration for {{ fair2wise_fqdn }}"]
    assert task["ansible.builtin.template"]["src"] == "edge-traefik.yml.j2"
    assert task["ansible.builtin.template"]["dest"] == "{{ fair2wise_edge_conf_path }}"
    assert task["notify"] == "fair2wise edge changed"
    handlers = {h["name"]: h for h in load_yaml(ROLE / "handlers" / "main.yml")}
    check = handlers["Check the edge Traefik file is valid YAML"]
    assert check["listen"] == "fair2wise edge changed" and "yaml.safe_load" in " ".join(check["ansible.builtin.command"]["argv"])
    assert als_dev["fair2wise_edge_conf_path"] == "/etc/traefik/dynamic/fair2wise.yml"


# ---- mounts, environment, stop timeout, health -----------------------------------------------
def test_state_is_the_only_fair2wise_mount_on_als_and_local_adds_tiled(als_dev, als_prod, local) -> None:
    for resolved, profile in ((als_dev, "als-dev"), (als_prod, "als-prod")):
        mounts = resolved["fair2wise_mounts"]
        assert mounts == [
            f"/srv/fair2wise/{profile}/state:/var/lib/fair2wise/state:rw",
            f"/srv/fair2wise/{profile}/secrets/tiled_writer:/run/secrets/tiled_writer:ro",
        ]
        in_var_lib = [m for m in mounts if m.split(":")[1].startswith("/var/lib/fair2wise")]
        assert [m.split(":")[1] for m in in_var_lib] == ["/var/lib/fair2wise/state"]
        assert not any("/var/lib/fair2wise/tiled" in m for m in mounts)
    assert local["fair2wise_mounts"] == [
        "/srv/fair2wise/local/state:/var/lib/fair2wise/state:rw",
        "/srv/fair2wise/local/tiled:/var/lib/fair2wise/tiled:rw",
    ]
    container = {t["name"]: t for t in load_yaml(ROLE / "tasks" / "container.yml")}
    start = container["Start the container from the digest"]["community.docker.docker_container"]
    assert start["volumes"] == "{{ fair2wise_mounts }}"
    assert start["comparisons"]["volumes"] == "strict"


def test_als_environment_provides_cors_origins_and_the_secret_file_path(als_dev, local) -> None:
    assert als_dev["fair2wise_env"] == {
        "F2W_CORS_ORIGINS": "https://f2w-dev.example.invalid",
        "F2W_TILED_WRITER_SECRET_FILE": "/run/secrets/tiled_writer",
    }
    assert "F2W_CORS_ORIGINS" not in local["fair2wise_env"]
    # The image's locked values are never contradicted from the role.
    for path in CONFIG_FILES:
        text = path.read_text(encoding="utf-8")
        for key in ("F2W_TILED_URL", "F2W_AUTHORIZATION_MODE", "F2W_DEPLOYMENT_PROFILE", "F2W_TILED_MODE", "local_trusted"):
            assert key not in text, f"{path.name} sets {key}"


def test_stop_timeout_is_120_on_every_stop_or_recreate(als_dev) -> None:
    assert als_dev["fair2wise_stop_timeout"] == 120
    validate = (ROLE / "tasks" / "validate.yml").read_text(encoding="utf-8")
    assert "fair2wise_stop_timeout | int >= 120" in validate
    seen = 0
    for path in TASK_FILES:
        for task in tasks_in(load_yaml(path)):
            if module_of(task) != "community.docker.docker_container":
                continue
            args = task["community.docker.docker_container"]
            if args.get("state") == "started" and "image" not in args:
                continue  # restart of an existing container after a quiesced backup
            assert args.get("stop_timeout") == "{{ fair2wise_stop_timeout }}", f"{path.name}: {task['name']}"
            seen += 1
    assert seen >= 4
    start = next(t for t in load_yaml(ROLE / "tasks" / "container.yml") if t["name"] == "Start the container from the digest")
    assert start["community.docker.docker_container"]["published_ports"] == [
        "{{ fair2wise_gateway_bind }}:{{ fair2wise_gateway_host_port }}:{{ fair2wise_container_port }}"]
    assert als_dev["fair2wise_gateway_bind"] == "127.0.0.1", "the gateway is reached only through the edge"


def test_health_checks_use_the_prefixed_health_urls_and_the_supervisor_status(als_dev) -> None:
    assert als_dev["fair2wise_health_path"] == "/fair2wise/healthz"
    assert als_dev["fair2wise_api_health_path"] == "/fair2wise/api/health"
    tasks = {t["name"]: t for t in load_yaml(ROLE / "tasks" / "health.yml")}
    gw = tasks["Gateway liveness on the host port"]
    assert gw["ansible.builtin.uri"]["url"].endswith("{{ fair2wise_health_path }}") and gw["retries"] and gw["until"]
    api = tasks["API readiness behind the gateway"]
    assert api["ansible.builtin.uri"]["url"].endswith("{{ fair2wise_api_health_path }}")
    edge = tasks["Public HTTPS path through the edge"]
    assert edge["ansible.builtin.uri"]["url"] == "https://{{ fair2wise_fqdn }}{{ fair2wise_health_path }}"
    sup = tasks["Supervisor status reports every required service running"]
    assert sup["community.docker.docker_container_exec"]["argv"] == ["cat", "/run/fair2wise/supervisor.json"]
    text = (ROLE / "tasks" / "health.yml").read_text(encoding="utf-8")
    assert "import" not in text.lower().replace("import_tasks", ""), "health never runs an import"


# ---- writer secret -----------------------------------------------------------------------------
def test_writer_secret_file_is_0600_owned_by_the_container_uid_and_never_logged(als_dev, tmp_path) -> None:
    from app.modules.deployment.tiled_config import TiledConfigError, check_private_file

    tasks = {t["name"]: t for t in load_yaml(ROLE / "tasks" / "prepare.yml")}
    secret = tasks["Tiled writer secret file (0600, owned by uid {{ fair2wise_container_uid }})"]
    copy = secret["ansible.builtin.copy"]
    assert copy["mode"] == "0600" and copy["owner"] == "{{ fair2wise_container_uid }}"
    assert copy["content"] == "{{ fair2wise_tiled_writer_secret }}" and secret["no_log"] is True
    assert secret["when"] == "fair2wise_profile != 'local'"
    assert als_dev["fair2wise_container_uid"] == 10001
    assert copy["dest"] == "{{ fair2wise_secret_host_path }}"
    # What the role produces is what the application's check accepts; one bit more is refused.
    f = tmp_path / "tiled_writer"
    f.write_text("x", encoding="utf-8")
    os.chmod(f, 0o600)
    check_private_file(f)
    os.chmod(f, 0o640)
    with pytest.raises(TiledConfigError):
        check_private_file(f)
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o640


# ---- variables ------------------------------------------------------------------------------------
def _referenced_variables() -> Dict[str, List[str]]:
    refs: Dict[str, List[str]] = {}
    for path in list(TASK_FILES) + PLAYBOOKS + [TEMPLATE]:
        for name in VAR_RE.findall(path.read_text(encoding="utf-8")):
            refs.setdefault(name, []).append(path.name)
    return refs


def _declared_variables() -> Dict[str, str]:
    declared: Dict[str, str] = {k: "defaults" for k in load_yaml(ROLE / "defaults" / "main.yml")}
    for group in ("als_dev", "als_prod"):
        for k in inventory_group_vars(group):
            declared.setdefault(k, "inventory")
    for path in PLAYBOOKS:
        for play in load_yaml(path):
            for k in (play.get("vars") or {}):
                declared.setdefault(k, f"play vars ({path.name})")
    for path in TASK_FILES:
        text = path.read_text(encoding="utf-8")
        for k in re.findall(r"register:\s*(fair2wise_\w+)", text):
            declared.setdefault(k, f"register ({path.name})")
        for k in re.findall(r"^\s+(fair2wise_\w+):", text, re.M):
            declared.setdefault(k, f"set_fact ({path.name})")
        for k in re.findall(r"(fair2wise_\w+) is defined", text):
            declared.setdefault(k, f"optional extra var ({path.name})")
    return declared


def test_every_variable_the_plays_need_is_declared() -> None:
    declared = _declared_variables()
    missing = {k: v for k, v in _referenced_variables().items() if k not in declared}
    assert not missing, missing


def test_operator_inputs_without_defaults_are_in_the_example_inventory_for_both_als_groups() -> None:
    defaults = set(load_yaml(ROLE / "defaults" / "main.yml"))
    required = {"fair2wise_profile", "fair2wise_fqdn", "fair2wise_cors_origins", "fair2wise_image_digest",
                "fair2wise_tiled_writer_secret", "fair2wise_tls_certificate_path", "fair2wise_tls_key_path"}
    assert not required & defaults, "operator inputs must not have silent defaults"
    for group in ("als_dev", "als_prod"):
        assert required <= set(inventory_group_vars(group)), group
    validate = (ROLE / "tasks" / "validate.yml").read_text(encoding="utf-8")
    for name in required - {"fair2wise_profile", "fair2wise_image_digest"}:
        assert f"{name} is defined" in validate, name


# ---- rollout record, rollback, backup, restore ----------------------------------------------------
def test_rollout_records_the_digest_and_rollback_re_points_to_the_recorded_previous_one() -> None:
    record = load_yaml(ROLE / "tasks" / "record.yml")[0]["ansible.builtin.command"]["argv"]
    assert record[1].endswith("f2w_release_record.py") and "record" in record
    assert "{{ fair2wise_image_digest }}" in record and "{{ fair2wise_state_schema }}" in record
    choice = {t["name"]: t for t in load_yaml(ROLE / "tasks" / "rollback_choice.yml")}
    argv = choice["Choose the rollback digest from the host's release record"]["ansible.builtin.command"]["argv"]
    assert "rollback" in argv and "--state-schema" in argv
    assert choice["Use the recorded previous digest for this run"]["ansible.builtin.set_fact"] == {
        "fair2wise_image_digest": "{{ fair2wise_rollback_entry.digest }}"}
    refuse = choice["Refuse when the record refused"]["ansible.builtin.assert"]["that"]
    assert "fair2wise_rollback_entry.digest is match('^sha256:[0-9a-f]{64}$')" in refuse
    play = load_yaml(DEPLOY / "playbooks" / "rollback.yml")[0]
    assert play["vars"]["fair2wise_record_rollout"] is False, "a rollback is recorded once, by rollback_choice"
    steps = [t["ansible.builtin.import_role"] for t in play["tasks"]]
    assert steps == [{"name": "fair2wise", "tasks_from": "rollback_choice"}, {"name": "fair2wise"}]
    main = [t["ansible.builtin.import_tasks"] for t in load_yaml(ROLE / "tasks" / "main.yml")]
    assert main == ["validate.yml", "prepare.yml", "backup.yml", "container.yml", "proxy.yml", "health.yml", "record.yml"]


def test_restore_verifies_profile_and_owner_and_never_replaces_by_default() -> None:
    tasks = {t["name"]: t for t in load_yaml(ROLE / "tasks" / "restore.yml")}
    argv = tasks["Verify and restore the archive"]["ansible.builtin.command"]["argv"]
    assert argv[2] == "restore" and "--expected-profile" in argv and "--owner" in argv
    assert argv[argv.index("--owner") + 1] == "{{ fair2wise_container_uid }}:{{ fair2wise_container_gid }}"
    assert "{{ '--replace' if fair2wise_restore_replace | bool else omit }}" in argv
    play = load_yaml(DEPLOY / "playbooks" / "restore.yml")[0]
    assert play["vars"]["fair2wise_restore_replace"] is False
    assert play["vars"]["fair2wise_backup_before_rollout"] is False, "nothing to back up on a fresh host"
    stop = tasks["The container is not running while its state is replaced"]["community.docker.docker_container"]
    assert stop["state"] == "stopped"


def test_backup_archives_with_manifest_and_verifies_before_success() -> None:
    backup = {t["name"]: t for t in load_yaml(ROLE / "tasks" / "backup.yml")}
    argv = backup["Archive the state tree (consistent SQLite copies, manifest, sha256 sidecar)"]["ansible.builtin.command"]["argv"]
    assert argv[1].endswith("f2w_state_archive.py") and argv[2] == "backup" and "--profile" in argv
    standalone = [t["name"] for t in load_yaml(ROLE / "tasks" / "backup_standalone.yml")]
    assert standalone.index("Archive the state tree") < standalone.index("Verify the archive before reporting success")
    for script in ("f2w_state_archive.py", "f2w_release_record.py"):
        text = (DEPLOY / "scripts" / script).read_text(encoding="utf-8")
        assert "os.environ" not in text and "dotenv" not in text, f"{script} reads no environment"
        assert text.startswith("#!/usr/bin/env python3")


def test_deploy_tree_is_excluded_from_the_image_build_context() -> None:
    ignore = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "deploy" in ignore


def test_readme_names_what_the_operator_supplies_and_what_is_not_here() -> None:
    text = (DEPLOY / "README.md").read_text(encoding="utf-8")
    for phrase in ("inventory.example.yml", "digest", "X-Forwarded-Proto", "/var/lib/fair2wise/state",
                   "--stop-timeout 120", "rollback", "restore", "D3.11", "O07"):
        assert phrase in text, phrase
