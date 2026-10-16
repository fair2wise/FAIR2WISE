"""T22C acceptance: the 12 tracked versioned KGs validate unchanged under schema set 0.3.0; no KG file changes.

The 12 are the promotion snapshots ``storage/kg/matkg_rsoxs_v1..v3.json`` and ``matkg_bl1101_v1..v9.json``.
Each binds by file name (``multi_kg`` patterns). Under set 0.3.0 the rsoxs graphs bind ``rsoxs_schema@0.3.0``;
the bl1101 graphs keep ``bl1101_schema.yaml`` on the base set, which set 0.3.0 does not contain (bl1101 has
its own ops ``Sample`` class; see docs/migration/reviews/H3/schema_diff_T22C.md).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import json
import pytest

from app.modules.schema.loading import closure_fingerprint, load_helper
from app.modules.schema.validation import categories_of, validate_graph

REPO = Path(__file__).resolve().parents[2]
KG = REPO / "storage" / "kg"
TRACKED = [f"matkg_rsoxs_v{n}.json" for n in range(1, 4)] + [f"matkg_bl1101_v{n}.json" for n in range(1, 10)]
NEW_CLASSES = {"EvidenceQuote", "Instrument", "MeasurementRun", "PaperSection", "QuantityValue", "RunCondition",
               "Sample"}

_HELPERS: dict = {}


def _helper(entry):
    if entry.schema_id not in _HELPERS:
        _HELPERS[entry.schema_id] = load_helper(entry)
    return _HELPERS[entry.schema_id]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report(payload, entry, registry):
    return validate_graph(payload, entry, registry=registry, helper=_helper(entry),
                          fingerprint=closure_fingerprint(entry), selection_source="filename_pattern")


def _lists(payload):
    nodes = payload["things"] if "things" in payload else payload["nodes"]
    edges = payload["associations"] if "associations" in payload else payload["edges"]
    return nodes, edges


def test_the_twelve_tracked_kgs_are_present():
    assert len(TRACKED) == 12
    for name in TRACKED:
        assert (KG / name).is_file(), name


@pytest.fixture(scope="module")
def before_hashes():
    return {name: _sha(KG / name) for name in TRACKED}


@pytest.mark.parametrize("name", TRACKED)
def test_tracked_kg_validates_unchanged_under_the_new_version(registry, before_hashes, name):
    path = KG / name
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    old = registry.for_graph(name)
    new = registry.for_graph(name, schema_set="0.3.0")
    if name.startswith("matkg_rsoxs"):
        assert (old.schema_id, new.schema_id) == ("rsoxs_schema", "rsoxs_schema@0.3.0")
    else:
        assert old.schema_id == new.schema_id == "bl1101_schema"  # not part of set 0.3.0
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


@pytest.mark.parametrize("name", TRACKED)
def test_tracked_kg_against_the_new_base_shows_only_new_class_names(registry, name):
    """Cross-check against matkg 0.3.0 and rsoxs v2 directly: a report differs only where a node's category is a
    new class name. That happens only for bl1101's own ops ``Sample`` nodes, which is why bl1101 stays on the
    base set (its graphs never bind matkg 0.3.0)."""
    payload = json.loads((KG / name).read_bytes().decode("utf-8"))
    nodes, _edges = _lists(payload)
    hits = {c for c in categories_of(nodes) if c in NEW_CLASSES}
    for old_id, new_id in (("matkg_schema", "matkg_schema@0.3.0"), ("rsoxs_schema", "rsoxs_schema@0.3.0")):
        a = _report(payload, registry.get(old_id), registry).categories
        b = _report(payload, registry.get(new_id), registry).categories
        if not hits:
            assert a == b, (name, old_id)
            continue
        assert name.startswith("matkg_bl1101") and hits == {"Sample"}
        gone = set(a["legacy_warning"]["categories_outside_closure"]) - set(b["legacy_warning"]["categories_outside_closure"])
        assert gone <= {"Sample"}
        moved = a["legacy_warning"]["nodes_with_category_outside_closure"] - \
            b["legacy_warning"]["nodes_with_category_outside_closure"]
        assert moved == categories_of(nodes)["Sample"]
        for key in ("contract_error", "incomplete_extraction", "unknown_source_data"):
            assert a[key] == b[key]


def test_no_kg_file_changed_during_the_module(before_hashes):
    assert {name: _sha(KG / name) for name in TRACKED} == before_hashes
