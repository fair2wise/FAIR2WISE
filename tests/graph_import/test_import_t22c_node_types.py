"""T22C: a snapshot bound to schema set 0.3.0 round-trips its new node types through the Tiled import (memory
double and the disposable Tiled 0.2.18 server). Publishing it needs an explicit supported fingerprint: the
default supported set excludes a schema set pending owner review."""
import hashlib
import json

import pytest
from import_fakes import snapshot_with_graph, tree_bytes

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph import identity_adapter as ida
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
from app.modules.schema.loading import closure_fingerprint
from app.modules.schema.validation import validate_graph
from tests.schema.t22c_fixtures import mapped_graph
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS

NEW_ID = "rsoxs_schema@0.3.0"
NEW_TYPES = {"Sample", "MeasurementRun", "Instrument", "RunCondition", "QuantityValue", "EvidenceQuote",
             "PaperSection"}


@pytest.fixture(scope="module")
def t22c_snapshot(tmp_path_factory, registry):
    """A real snapshot bound to rsoxs_schema@0.3.0 whose graph is the mapped T22C fixture, validated against it."""
    base = tmp_path_factory.mktemp("t22c-base") / "storage" / "kg"
    base.mkdir(parents=True)
    create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json", storage_kg=base,
                                    terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT, registry=registry,
                                    schema_id=NEW_ID, allow_pending_review_schema=True))
    graph = mapped_graph(registry)[2]
    entry = registry.get(NEW_ID)
    report = validate_graph(graph, entry, registry=registry, selection_source="caller")
    assert report.status == "pass"
    dest = snapshot_with_graph(base / "synthetic" / "v1", tmp_path_factory.mktemp("t22c"), "synthetic", "v1", graph)
    (dest / "validation.json").write_bytes(report.canonical_bytes())
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    manifest["validation"]["report_sha256"] = hashlib.sha256(report.canonical_bytes()).hexdigest()
    assert manifest["schema"]["name"] == NEW_ID
    assert manifest["schema"]["import_fingerprint"] == closure_fingerprint(entry)
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return dest


def _owned(env):
    ents, links = env.scan()
    return ([e for e in ents if (e.get("properties") or {}).get(ida.OWNER_KEY) == ida.LITERATURE_OWNER],
            [lk for lk in links if (lk.get("properties") or {}).get(ida.OWNER_KEY) == ida.LITERATURE_OWNER])


def test_default_publish_set_refuses_the_pending_schema(env, t22c_snapshot):
    with pytest.raises(ContractError) as err:
        env.plan(t22c_snapshot)
    assert err.value.code is ErrorCode.INVALID_INPUT and "supported set" in str(err.value)
    assert env.writes() == []


def test_new_node_types_round_trip_through_the_tiled_import(env, t22c_snapshot, registry):
    graph = json.loads((t22c_snapshot / "graph.json").read_text(encoding="utf-8"))
    assert NEW_TYPES <= {n["category"] for n in graph["things"]}
    before = tree_bytes(t22c_snapshot)
    env.fingerprints = frozenset({closure_fingerprint(registry.get(NEW_ID))})  # explicit selection
    plan = env.plan(t22c_snapshot)
    assert plan.change.creates == len(graph["things"]) and plan.change.links == len(graph["associations"])
    assert plan.summary["dangling"] == []
    out = env.apply(plan, env.approve(plan), t22c_snapshot)
    assert out.status == "applied" and out.receipt.receipt.verified
    gid = plan.detail["graph_id"]
    ents, links = _owned(env)
    by_node = {e["properties"][ida.ENTITY_KEY]: e for e in ents}
    assert set(by_node) == {n["id"] for n in graph["things"]}
    for node in graph["things"]:
        row = by_node[node["id"]]
        assert row["entityType"] == node["category"] and row["name"] == node["name"]
        assert row["properties"] == ida.entity_properties(gid, node["id"], node)
        assert ida.strip_reserved(row["properties"]) == node
        assert row["uri"] == ida.entity_uri(gid, node["id"])
    tid_to_node = {e["id"]: e["properties"][ida.ENTITY_KEY] for e in ents}
    got = sorted((tid_to_node[lk["subjectId"]], lk["predicate"], tid_to_node[lk["objectId"]]) for lk in links)
    assert got == sorted((a["subject"], a["predicate"], a["object"]) for a in graph["associations"])
    assert tree_bytes(t22c_snapshot) == before
    assert env.plan(t22c_snapshot).is_noop

    if env.instance is None:
        return
    from app.modules.graph.backends.json import JsonGraphBackend
    from app.modules.graph.backends.tiled import TiledGraphBackend
    from app.modules.graph.backends.tiled_mutations import TiledMutationClient

    backend = TiledGraphBackend(TiledMutationClient.from_managed(env.instance.server))
    json_backend = JsonGraphBackend({gid: t22c_snapshot / "graph.json"})
    for node in graph["things"]:
        rec = backend.get_entity(env.ctx, gid, node["id"])
        expected = json_backend.get_entity(env.ctx, gid, node["id"])
        for key in ("graph_id", "id", "name", "category", "citations", "data"):
            assert rec[key] == expected[key], (node["id"], key)
    run = next(n["id"] for n in graph["things"] if n["category"] == "MeasurementRun")
    hood = backend.neighborhood(env.ctx, gid, run, hops=1, max_nodes=100)
    assert {"Sample", "QuantityValue", "EvidenceQuote"} <= {n["category"] for n in hood["nodes"]}
