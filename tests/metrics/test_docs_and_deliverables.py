"""Drift tests: the docs list every variable and instrument the metrics package defines."""

from __future__ import annotations

from pathlib import Path
import re

from app.modules.metrics import catalog
from app.modules.metrics import config as cfg

REPO = Path(__file__).resolve().parents[2]
PAGE = (REPO / "mkdocs" / "docs" / "observability.md").read_text(encoding="utf-8")
PKG = REPO / "app" / "modules" / "metrics"


def test_every_variable_the_module_reads_is_documented() -> None:
    for name in cfg.ENV_VARS:
        assert f"`{name}`" in PAGE, f"{name} is read by app/modules/metrics but missing from observability.md"


def test_every_literal_variable_name_in_the_package_is_declared() -> None:
    literal = re.compile(r"""["']((?:F2W_METRICS_|F2W_OTEL_|OTEL_)[A-Z0-9_]+)["']""")
    used = {m for path in PKG.glob("*.py") for m in literal.findall(path.read_text(encoding="utf-8"))}
    assert used <= set(cfg.ENV_VARS), sorted(used - set(cfg.ENV_VARS))


def test_documented_metrics_variables_are_known() -> None:
    documented = set(re.findall(r"^\| `(F2W_METRICS_[A-Z0-9_]+)`", PAGE, flags=re.MULTILINE))
    assert documented == {cfg.ENABLED_VAR, cfg.INTERVAL_VAR}


def test_every_instrument_is_documented_with_its_prometheus_name() -> None:
    for spec in catalog.SPECS:
        assert f"`{spec.name}`" in PAGE, spec.name
        prom = spec.name.replace(".", "_")
        if spec.kind == "counter":
            prom += "_total"
        elif spec.unit == "s":
            prom += "_seconds"
        assert prom in PAGE, (spec.name, prom)


def test_collector_requirements_are_recorded() -> None:
    for needle in ("4318", "resource_constant_labels", "metric_expiration", "NO_PROXY", "NetworkPolic",
                   "Single replica assumed"):
        assert needle in PAGE, needle
