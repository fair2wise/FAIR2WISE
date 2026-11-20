"""Image profiles: tracing is off in all four, the published three are byte-identical, NO_PROXY follows the collector."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
PROFILES = REPO / "docker" / "profiles"

#: sha256 of the three published profiles at the commit before the OpenTelemetry work (they must not change).
PUBLISHED_SHA256 = {
    "local.json": "9e8e76a87bcd296dcfe684b798bc6d577e031f3cfbd8914f311ef69abcb881fb",
    "als-dev.json": "b857c2acb51b06cf6716787dbba1d5afc0a179cbe8f3f77e6fc6e41e6e11ac2e",
    "als-prod.json": "7958682ad2f5f3630785ea24ad052ded7f6d785a75ea66097177758af1e41622",
}


def _load(name: str):  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(f"otel_{name}", REPO / "docker" / "entrypoint" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SUP = _load("f2w_supervisor")


@pytest.mark.parametrize("name,digest", sorted(PUBLISHED_SHA256.items()))
def test_published_profiles_are_unchanged(name: str, digest: str) -> None:
    assert hashlib.sha256((PROFILES / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("name", ["local", "als-dev", "als-prod", "cluster"])
def test_tracing_is_off_by_default_in_every_profile(name: str) -> None:
    data = json.loads((PROFILES / f"{name}.json").read_text(encoding="utf-8"))
    for section in ("env", "locked_env"):
        assert not any(k.startswith(("F2W_OTEL", "OTEL_")) for k in data.get(section, {})), (name, section)


def test_cluster_documents_the_optional_otel_settings_without_setting_them() -> None:
    data = json.loads((PROFILES / "cluster.json").read_text(encoding="utf-8"))
    optional = data["optional_env"]
    assert {"F2W_OTEL_ENABLED", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_SERVICE_NAME", "F2W_OTEL_TRACE_FUNCTIONS"} <= set(optional)
    assert set(optional).isdisjoint(data["env"]) and set(optional).isdisjoint(data["locked_env"])
    assert set(optional).isdisjoint(data["forbidden_env"])
    for other in ("local", "als-dev", "als-prod"):
        assert "optional_env" not in json.loads((PROFILES / f"{other}.json").read_text(encoding="utf-8"))


def resolved(environ: dict[str, str]) -> dict[str, str]:
    profile = SUP.load_profile(PROFILES / "cluster.json")
    return SUP.resolve_environment(environ, profile)[0]


def test_nothing_changes_while_tracing_is_off() -> None:
    env = resolved({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318"})
    assert env["NO_PROXY"] == "127.0.0.1,localhost,::1"  # only the profile's own entries
    assert SUP.otel_no_proxy_host({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318"}) is None


@pytest.mark.parametrize("endpoint,host", [
    ("http://otel-collector:4318", "otel-collector"),                           # hub: single-label compose name
    ("http://jaeger-collector.observability.svc:4318", "jaeger-collector.observability.svc"),
    ("http://jaeger-collector.observability.svc.cluster.local:4318", "jaeger-collector.observability.svc.cluster.local"),
    ("http://10.43.2.17:4318", "10.43.2.17"),
    ("http://[fd00::5]:4318", "fd00::5"),
    ("http://localhost:4318", "localhost"),
])
def test_internal_collector_is_added_to_no_proxy(endpoint: str, host: str) -> None:
    env = resolved({"F2W_OTEL_ENABLED": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
                    "NO_PROXY": "kubernetes.default.svc", "HTTPS_PROXY": "http://squid:3128"})
    entries = env["NO_PROXY"].split(",")
    assert host in entries and "kubernetes.default.svc" in entries and "127.0.0.1" in entries
    assert env["no_proxy"] == env["NO_PROXY"] and env["HTTPS_PROXY"] == "http://squid:3128"


@pytest.mark.parametrize("endpoint", [
    "https://otlp.example.org:4318", "http://8.8.8.8:4318", "https://jaeger.lbl.gov/otlp",
])
def test_public_collector_is_left_to_the_proxy(endpoint: str) -> None:
    assert SUP.otel_no_proxy_host({"F2W_OTEL_ENABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint}) is None


def test_alias_default_endpoint_and_traces_endpoint_variable() -> None:
    assert SUP.otel_no_proxy_host({"OTEL_ENABLED": "yes"}) == "localhost"
    env = {"F2W_OTEL_ENABLED": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": "https://public.example.org",
           "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318/v1/traces"}
    assert SUP.otel_no_proxy_host(env) == "collector"
    assert SUP.otel_no_proxy_host({"F2W_OTEL_ENABLED": "0", "OTEL_ENABLED": "1"}) is None


def test_other_profiles_gain_the_entry_only_when_tracing_is_on() -> None:
    profile = SUP.load_profile(PROFILES / "local.json")
    off = SUP.resolve_environment({}, profile)[0]
    on = SUP.resolve_environment({"F2W_OTEL_ENABLED": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318"},
                                 profile)[0]
    assert "NO_PROXY" not in off and "otel-collector" in on["NO_PROXY"].split(",")


def test_the_merged_no_proxy_makes_requests_skip_the_proxy_for_the_collector_only(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The OTLP exporter posts with ``requests``; its proxy decision reads exactly the variables the supervisor sets."""
    from requests.utils import get_environ_proxies

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://squid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://squid:3128")
    collector = "http://jaeger-collector.observability.svc:4318/v1/traces"
    env = resolved({"F2W_OTEL_ENABLED": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://jaeger-collector.observability.svc:4318"})
    assert get_environ_proxies(collector, no_proxy=env["NO_PROXY"]) == {}                      # direct
    assert get_environ_proxies("https://api.cborg.lbl.gov/v1", no_proxy=env["NO_PROXY"]) != {}  # still proxied
    assert get_environ_proxies(collector, no_proxy="127.0.0.1,localhost,::1") != {}           # without the entry: proxied


def test_check_output_shows_no_proxy_when_the_collector_added_it(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("F2W_TILED_PYTHON", raising=False)  # the test recipe sets it; the :local profile locks its own
    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    assert SUP.main(["--profile-file", str(PROFILES / "local.json"), "--check"]) == 0
    shown = json.loads(capsys.readouterr().out)["env"]
    assert "otel-collector" in shown["NO_PROXY"]
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in shown  # the endpoint itself is not echoed
