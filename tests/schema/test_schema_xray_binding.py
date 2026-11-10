"""H3 S5: x-ray graphs bind ``storage/schema/xray_schema.yaml`` by file name; every other binding is unchanged.

x-ray graphs are recognised by name only (``matkg_xray_vN.json`` snapshots and ``matkg_xray_<name>.json`` such
as the demo graph). A snapshot's own manifest already records the schema it was frozen with, so no manifest
lookup is needed here. ``_legacy_schema_path`` is the pre-S5 rule, kept verbatim so the tests can prove that
only x-ray graphs changed binding.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import pytest

from app.modules.f2w_agent.multi_kg import XRAY_DEMO_NAME, is_xray_graph, schema_path_for_graph
from app.modules.schema.loading import closure_fingerprint, load_helper
from app.modules.schema.validation import validate_graph

REPO = Path(__file__).resolve().parents[2]
KG = REPO / "storage" / "kg"
TRACKED = [f"matkg_rsoxs_v{n}.json" for n in range(1, 4)] + [f"matkg_bl1101_v{n}.json" for n in range(1, 10)]
XRAY = "storage/schema/xray_schema.yaml"


def _legacy_schema_path(path: str) -> Optional[str]:
    """``multi_kg.schema_path_for_graph`` before H3 S5 (frozen copy)."""
    name = Path(str(path).replace("\\", "/").strip()).name
    by_name = {"matkg_rsoxs_v1.json": "storage/schema/rsoxs_schema.yaml",
               "matkg_bl1101_v1.json": "storage/schema/bl1101_schema.yaml"}
    if name in by_name:
        return by_name[name]
    if re.match(r"^matkg_bl1101_v(\d+)\.json$", name) or name.startswith("matkg_bl1101"):
        return "storage/schema/bl1101_schema.yaml"
    if re.match(r"^matkg_rsoxs_v(\d+)\.json$", name) or name.startswith("matkg_rsoxs"):
        return "storage/schema/rsoxs_schema.yaml"
    return None


@pytest.mark.parametrize("name", [XRAY_DEMO_NAME, "matkg_xray_v1.json", "matkg_xray_v12.json",
                                  "storage/kg/matkg_xray_v3.json", "storage\\kg\\matkg_xray_papers_x.json"])
def test_xray_graphs_bind_the_xray_schema(registry, name):
    assert is_xray_graph(name)
    assert schema_path_for_graph(name) == XRAY
    assert registry.for_graph(name) is registry.get("xray_schema")
    # x-ray is not part of the pending 0.3.0 set: an explicit set selection keeps the base overlay
    assert registry.for_graph(name, schema_set="0.3.0") is registry.get("xray_schema")
    assert "graph-view" in registry.get("xray_schema").selectable_for


@pytest.mark.parametrize("name", ["matkg_xray_papers_cborg_chat.json.bak", "xray_demo.json", "matkg_xrayish.json",
                                  "matkg_rsoxs_xray.json", "matkg_xray_.json"])
def test_names_that_are_not_xray_graphs_are_not_bound_to_it(name):
    assert not is_xray_graph(name)
    assert schema_path_for_graph(name) == _legacy_schema_path(name)


@pytest.mark.parametrize("name,schema_id", [
    ("matkg_rsoxs_v1.json", "rsoxs_schema"), ("matkg_rsoxs_v3.json", "rsoxs_schema"),
    ("storage/kg/matkg_rsoxs_v9.json", "rsoxs_schema"),
    ("matkg_bl1101_v1.json", "bl1101_schema"), ("matkg_bl1101_v9.json", "bl1101_schema"),
])
def test_rsoxs_and_bl1101_graphs_keep_their_overlay(registry, name, schema_id):
    assert registry.for_graph(name) is registry.get(schema_id)
    assert schema_path_for_graph(name) == _legacy_schema_path(name)


@pytest.mark.parametrize("name", ["matkg_with_code.json", "matkg_qwen3_235b_580papers.json",
                                  "matkg_google_gemini-flash-lite_25_20251008_185312.json",
                                  "matkg_resources_saxs_algorithms_20260603_181120.json"])
def test_matkg_graphs_keep_no_overlay_and_default_to_matkg(registry, name):
    assert schema_path_for_graph(name) is None and registry.for_graph(name) is None
    # callers that need a schema then use the matkg default (snapshot._choose_schema)
    from app.modules.graph.snapshot import _choose_schema

    corpus = name[len("matkg_"):-len(".json")].split("_")[0].lower()
    entry, source = _choose_schema(None, corpus, registry, allow_pending_review=False)
    assert (entry.schema_id, source) == ("matkg_schema", "default")


def test_only_xray_graph_files_under_storage_kg_change_binding():
    files = sorted(p.name for p in KG.glob("*.json"))
    assert set(TRACKED) <= set(files)
    changed = {n: (_legacy_schema_path(n), schema_path_for_graph(n)) for n in files
               if _legacy_schema_path(n) != schema_path_for_graph(n)}
    assert all(is_xray_graph(n) and new == XRAY and old is None for n, (old, new) in changed.items()), changed
    assert XRAY_DEMO_NAME in changed  # the tracked x-ray demo graph is the one that now binds an overlay


def _report(registry, payload, entry):
    return validate_graph(payload, entry, registry=registry, helper=load_helper(entry),
                          fingerprint=closure_fingerprint(entry), selection_source="filename_pattern")


@pytest.mark.parametrize("name", TRACKED)
def test_tracked_kg_binding_and_diagnostics_are_unchanged(registry, name):
    assert not is_xray_graph(name)
    old, new = registry.resolve(_legacy_schema_path(name)), registry.for_graph(name)
    assert old is new and old is not None
    payload = json.loads((KG / name).read_bytes().decode("utf-8"))
    assert _report(registry, payload, new).to_dict() == _report(registry, payload, old).to_dict()


def test_xray_demo_graph_validates_under_the_xray_schema(registry):
    payload = json.loads((KG / XRAY_DEMO_NAME).read_bytes().decode("utf-8"))
    report = _report(registry, payload, registry.for_graph(XRAY_DEMO_NAME))
    assert report.status == "pass" and report.categories["contract_error"]["count"] == 0
    assert report.schema["name"] == "xray_schema"
