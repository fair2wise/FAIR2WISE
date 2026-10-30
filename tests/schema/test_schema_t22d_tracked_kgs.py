"""T22D acceptance: every tracked KG under ``storage/kg`` validates under schema set 0.3.1; no KG file changes.

The 12 promotion snapshots (``matkg_rsoxs_v1..v3``, ``matkg_bl1101_v1..v9``) must give a report identical to
their base-set report: under 0.3.1 the rsoxs graphs bind ``rsoxs_schema@0.3.1`` and, unlike 0.3.0, the bl1101
graphs bind ``bl1101_schema@0.3.1`` (bl1101_v3), whose own ops ``Sample`` shadows the imported literature one, so
nothing is retyped. Every other tracked graph (the LLM extraction graphs, ``matkg_with_code``, the xray demo)
has no overlay of its own and is checked against ``matkg_schema@0.3.1``: it must pass with no contract error.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app.modules.schema.loading import closure_fingerprint, load_helper
from app.modules.schema.validation import categories_of, validate_graph

REPO = Path(__file__).resolve().parents[2]
KG = REPO / "storage" / "kg"
VERSIONED = [f"matkg_rsoxs_v{n}.json" for n in range(1, 4)] + [f"matkg_bl1101_v{n}.json" for n in range(1, 10)]
SET = "0.3.1"


def _tracked_graphs():
    """Tracked ``storage/kg/*.json`` (``git ls-files``); outside a checkout, every ``*.json`` in the directory."""
    try:
        out = subprocess.run(["git", "ls-files", "storage/kg"], cwd=REPO, capture_output=True, text=True, check=True)
        names = sorted(Path(line).name for line in out.stdout.split() if line.endswith(".json"))
    except (OSError, subprocess.CalledProcessError):
        names = sorted(p.name for p in KG.glob("*.json"))
    return [n for n in names if (KG / n).is_file()]


TRACKED = _tracked_graphs()
OTHERS = [n for n in TRACKED if n not in VERSIONED]

_HELPERS: dict = {}
_FPS: dict = {}


def _helper(entry):
    if entry.schema_id not in _HELPERS:
        _HELPERS[entry.schema_id] = load_helper(entry)
        _FPS[entry.schema_id] = closure_fingerprint(entry)
    return _HELPERS[entry.schema_id]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report(payload, entry, registry):
    helper = _helper(entry)
    return validate_graph(payload, entry, registry=registry, helper=helper, fingerprint=_FPS[entry.schema_id],
                          selection_source="filename_pattern")


def _lists(payload):
    nodes = payload["things"] if "things" in payload else payload["nodes"]
    edges = payload["associations"] if "associations" in payload else payload["edges"]
    return nodes, edges


def test_the_tracked_graphs_are_present():
    assert len(VERSIONED) == 12 and set(VERSIONED) <= set(TRACKED)
    assert len(TRACKED) >= 50 and OTHERS  # the LLM extraction graphs and demos are tracked too


@pytest.fixture(scope="module")
def before_hashes():
    return {name: _sha(KG / name) for name in TRACKED}


@pytest.mark.parametrize("name", VERSIONED)
def test_versioned_kg_validates_unchanged_under_031(registry, before_hashes, name):
    path = KG / name
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    old = registry.for_graph(name)
    new = registry.for_graph(name, schema_set=SET)
    if name.startswith("matkg_rsoxs"):
        assert (old.schema_id, new.schema_id) == ("rsoxs_schema", "rsoxs_schema@0.3.1")
    else:
        assert (old.schema_id, new.schema_id) == ("bl1101_schema", "bl1101_schema@0.3.1")  # bl1101_v3 (D1.3)
    nodes, edges = _lists(payload)
    cats = categories_of(nodes)
    a, b = _report(payload, old, registry), _report(payload, new, registry)
    for report in (a, b):
        assert report.status == "pass" and report.categories["contract_error"]["count"] == 0
        assert report.fingerprint_status == "legacy_unverified"  # tracked KGs carry no fingerprint
        assert (report.counts["nodes"], report.counts["edges"]) == (len(nodes), len(edges))
    assert a.categories == b.categories  # every diagnostic identical: nothing reclassified, nothing gained
    assert categories_of(_lists(payload)[0]) == cats  # the payload was not retyped
    assert path.read_bytes() == raw and _sha(path) == before_hashes[name]  # file unchanged


@pytest.mark.parametrize("name", OTHERS)
def test_other_tracked_kg_passes_under_031(registry, before_hashes, name):
    path = KG / name
    payload = json.loads(path.read_bytes().decode("utf-8"))
    base = registry.for_graph(name) or registry.get("matkg_schema")
    new = registry.for_graph(name, schema_set=SET) or registry.get("matkg_schema@0.3.1")
    assert new.schema_set in (SET, None)
    nodes, edges = _lists(payload)
    cats = categories_of(nodes)
    a, b = _report(payload, base, registry), _report(payload, new, registry)
    for report in (a, b):
        assert report.status == "pass" and report.categories["contract_error"]["count"] == 0
        assert (report.counts["nodes"], report.counts["edges"]) == (len(nodes), len(edges))
    # a literature graph may already use a 0.3.x class name as a category (then it is now inside the closure);
    # nothing else may differ, and no node or edge count changes
    for key in ("contract_error", "incomplete_extraction", "unknown_source_data"):
        assert a.categories[key] == b.categories[key]
    assert b.categories["legacy_warning"]["nodes_with_category_outside_closure"] <= \
        a.categories["legacy_warning"]["nodes_with_category_outside_closure"]
    assert categories_of(_lists(payload)[0]) == cats
    assert _sha(path) == before_hashes[name]


def test_bl1101_sample_nodes_stay_in_the_ops_class_under_bl1101_v3(registry):
    """bl1101's ops Sample shadows the literature Sample in the bl1101_v3 closure; Detector and Endstation keep their
    classes. So the bl1101 graphs gain nothing and lose nothing under 0.3.1 (the mapping is applied only by
    ``bl1101_mapping`` on request, never by validation)."""
    payload = json.loads((KG / "matkg_bl1101_v9.json").read_bytes().decode("utf-8"))
    nodes, _ = _lists(payload)
    cats = categories_of(nodes)
    assert cats["Sample"] and cats["Detector"] and cats["Endstation"]
    helper = _helper(registry.get("bl1101_schema@0.3.1"))
    assert {"Sample", "Detector", "Endstation", "Instrument", "QuantityValue"} <= set(helper.classes)
    a = _report(payload, registry.get("bl1101_schema"), registry).categories["legacy_warning"]
    b = _report(payload, registry.get("bl1101_schema@0.3.1"), registry).categories["legacy_warning"]
    assert a == b


def test_no_kg_file_changed_during_the_module(before_hashes):
    assert {name: _sha(KG / name) for name in TRACKED} == before_hashes
