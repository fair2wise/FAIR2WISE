"""Drift tests: the docs list what the code reads, and the example files say what the task promises."""

from __future__ import annotations

from pathlib import Path
import re

import yaml

from app.modules.tracing import config as cfg
from app.modules.tracing import defaults

REPO = Path(__file__).resolve().parents[2]
PAGE = (REPO / "mkdocs" / "docs" / "observability.md").read_text(encoding="utf-8")
PKG = REPO / "app" / "modules" / "tracing"


def test_every_variable_the_module_reads_is_documented_and_declared() -> None:
    for name in cfg.ENV_VARS:
        assert f"`{name}`" in PAGE, f"{name} is read by app/modules/tracing but missing from observability.md"
    literal = re.compile(r"""["']((?:F2W_OTEL_|OTEL_)[A-Z0-9_]+)["']""")
    used = {m for path in PKG.glob("*.py") for m in literal.findall(path.read_text(encoding="utf-8"))}
    assert used <= set(cfg.ENV_VARS), sorted(used - set(cfg.ENV_VARS))


def test_every_documented_variable_is_known() -> None:
    documented = set(re.findall(r"^\| `((?:F2W_OTEL_|OTEL_)[A-Z0-9_]+)`", PAGE, flags=re.MULTILINE))
    assert documented and documented <= set(cfg.ENV_VARS), sorted(documented - set(cfg.ENV_VARS))


def test_the_page_lists_the_exact_default_function_list() -> None:
    for target in defaults.DEFAULT_TRACE_FUNCTIONS:
        assert f"`{target}`" in PAGE, target
    rows = re.findall(r"^\| `(app\.modules\.[^`]+:[^`]+)` \|", PAGE, flags=re.MULTILINE)
    assert tuple(rows) == defaults.DEFAULT_TRACE_FUNCTIONS


def test_the_page_is_in_the_navigation() -> None:
    nav = (REPO / "mkdocs" / "mkdocs.yml").read_text(encoding="utf-8")
    assert "observability.md" in nav


def test_cluster_pages_carry_the_k3s_env_fragment() -> None:
    for page in (REPO / "docs" / "cluster-profile.md", REPO / "mkdocs" / "docs" / "cluster-image.md"):
        text = page.read_text(encoding="utf-8")
        for needle in ("F2W_OTEL_ENABLED", "OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger.observability.svc:4318",
                       "NO_PROXY", "deploy/k3s/jaeger"):
            assert needle in text, (page.name, needle)


# ---------------------------------------------------------------- hub compose fragment


def test_hub_compose_fragment_has_the_promised_shape() -> None:
    raw = (REPO / "deploy" / "hub" / "compose.f2w.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    svc = doc["services"]["f2w"]
    assert svc["image"] == "f2w:cluster" and svc["profiles"] == ["f2w"]
    assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"] and svc["user"] == "10001:10001"
    assert svc["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in svc  # F2W has no sign-in: reachable only through Traefik
    assert set(svc["networks"]) == {"observability", "web"} and doc["networks"]["web"] == {"name": "web"}
    assert {t.split(":")[0] for t in svc["tmpfs"]} == {"/tmp", "/run/fair2wise", "/var/lib/fair2wise/cache"}
    assert {v.split(":")[1] for v in svc["volumes"]} == {"/var/lib/fair2wise/state", "/var/lib/fair2wise/tiled"}
    assert set(doc["volumes"]) == {"f2w-state", "f2w-tiled"}
    env = svc["environment"]
    assert env["F2W_GATEWAY"] == "off" and env["OTEL_SERVICE_NAME"] == "f2w"
    assert env["F2W_OTEL_ENABLED"] == "${F2W_OTEL_ENABLED:-false}"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "${F2W_OTEL_ENDPOINT:-http://otel-collector:4318}"
    assert env["CBORG_API_KEY"] is None  # taken from the host environment, never a literal
    assert "/fair2wise/api/readyz" in " ".join(svc["healthcheck"]["test"])
    labels = "\n".join(svc["labels"])
    assert "Host(`f2w.localhost`)" in labels and "entrypoints=web" in labels
    assert "loadbalancer.server.port=8080" in labels and "redirectregex" in labels and "/fair2wise/" in labels
    assert not re.search(r"sk-[A-Za-z0-9]{8,}", raw)


def test_hub_readme_covers_what_the_maintainer_needs() -> None:
    text = (REPO / "deploy" / "hub" / "README.md").read_text(encoding="utf-8")
    for needle in ("otel-collector:4318", "jaeger", "service **`f2w`**", "spanmetrics", "config/otel/collector.yaml",
                   "F2W_OTEL_ENABLED", "no sign-in", "docker build --platform linux/arm64 --target cluster",
                   "--profile f2w --profile observe", "f2w.localhost", "/fair2wise/"):
        assert needle in text, needle


# ---------------------------------------------------------------- K3s manifests


def test_k3s_manifests_are_restricted_pss_and_bounded() -> None:
    root = REPO / "deploy" / "k3s" / "jaeger"
    docs = {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in root.glob("*.yaml")}
    kust = docs.pop("kustomization.yaml")
    assert sorted(kust["resources"]) == sorted(docs)
    assert docs["namespace.yaml"]["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    pod = docs["deployment.yaml"]["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    sc = container["securityContext"]
    assert pod["securityContext"]["runAsUser"] == 10001 and pod["securityContext"]["runAsNonRoot"] is True
    assert sc["readOnlyRootFilesystem"] is True and sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"] == {"drop": ["ALL"]} and sc["seccompProfile"] == {"type": "RuntimeDefault"}
    assert re.fullmatch(r"jaegertracing/jaeger@sha256:[0-9a-f]{64}", container["image"])
    assert docs["deployment.yaml"]["spec"]["replicas"] == 1
    assert {"requests", "limits"} <= set(container["resources"])
    config = yaml.safe_load(docs["configmap.yaml"]["data"]["config.yaml"])
    assert config["extensions"]["jaeger_storage"]["backends"]["main_store"]["memory"]["max_traces"] > 0
    assert {p["name"]: p["port"] for p in docs["service.yaml"]["spec"]["ports"]} == {
        "otlp-grpc": 4317, "otlp-http": 4318, "ui": 16686}
    rules = docs["networkpolicy.yaml"]["spec"]["ingress"]
    assert [sorted(p["port"] for p in r["ports"]) for r in rules] == [[4317, 4318], [16686]]
    middleware = docs["ingressroute.yaml"]["spec"]["routes"][0]["middlewares"][0]["name"]
    assert middleware.startswith("REPLACE_WITH_")  # a marked placeholder; no invented middleware name


def test_the_jaeger_image_digest_is_the_same_in_the_manifest_and_the_comment() -> None:
    text = (REPO / "deploy" / "k3s" / "jaeger" / "deployment.yaml").read_text(encoding="utf-8")
    assert "# jaegertracing/jaeger:2.22.0" in text and "sha256:836b967b5abcf1f7758066aa6b348619267a2f1459f72d472840570f9bd88a17" in text
