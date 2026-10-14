"""T08A: Tiled 0.2.18 GraphPort against a real disposable server (``tests/fixtures/tiled_server.py``).

Skips unless ``F2W_TILED_PYTHON`` names an isolated tiled[server]==0.2.18 interpreter.
The fixture seed includes a FORGE-owned sentinel ESAF and unmarked ``f2w:test/...``
records; neither may ever appear in a literature scope.
"""
import pytest
from conftest import make_als_ctx, make_ctx
from graph_samples import nodes_payload, seed_graph, things_payload
from tiled_server import start_tiled_instance, tiled_python

from app.modules.contracts.errors import CapabilityError, ContractError, ErrorCode
from app.modules.contracts.ports import GraphPort
from app.modules.graph import identity_adapter as ida
from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.graph.backends.tiled import CAPABILITIES, TiledGraphBackend
from app.modules.graph.backends.tiled_client import TiledGraphClient
from app.modules.graph.ports import TruncatedGraphRead

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")


@pytest.fixture(scope="module")
def seeded(tiled_base):
    inst = start_tiled_instance(tiled_base)
    try:
        sentinel = inst.seed_receipt["sentinel_id"]
        ids_v4 = seed_graph(inst, "rsoxs_v4", things_payload())
        ids_v3 = seed_graph(inst, "rsoxs_v3", nodes_payload())
        # A FORGE link from an in-scope literature entity to the FORGE sentinel.
        inst.graphql("mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }",
                     {"i": {"subjectId": ids_v4["matkg:P3HT"], "predicate": "forge:usedIn", "objectId": sentinel,
                            "properties": {"owner": "forge"}}})
        # A FORGE entity that forges a literature URI but not the owner marker.
        inst.graphql("mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }",
                     {"i": {"entityType": "Material", "name": "P3HT",
                            "uri": ida.entity_uri("rsoxs_v4", "matkg:Fake"),
                            "properties": {"f2w_graph_id": "rsoxs_v4", "f2w_id": "matkg:Fake", "owner": "forge"}}})
        yield inst, ids_v4, ids_v3, sentinel
    finally:
        inst.destroy()


def _backend(inst, **kw):
    return TiledGraphBackend(TiledGraphClient.from_managed(inst.server), **kw)


def test_capabilities_and_unsupported_operations(seeded):
    backend = _backend(seeded[0])
    assert isinstance(backend, GraphPort)
    caps = backend.capabilities()
    assert caps == CAPABILITIES and caps.backend == "tiled"
    assert caps.bounded_traversal and not caps.complete_listing
    assert not any((caps.mutable_entity_properties, caps.batch_writes, caps.compare_and_swap,
                    caps.owned_deletion))
    assert caps.append_only_relations
    for call in (lambda: backend.apply_plan(make_ctx(), None, None), backend.update_entity_properties,
                 backend.compare_and_swap, backend.delete_owned, lambda: backend.list_complete(make_ctx(), "g")):
        with pytest.raises(CapabilityError) as err:
            call()
        assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    with pytest.raises(ContractError) as err:
        backend.client.query("mutation { deleteEntity(id: \"x\") }")
    assert err.value.code is ErrorCode.INVALID_INPUT


def test_reads_match_json_backend(seeded, tmp_path):
    inst, ids_v4, _ids_v3, _ = seeded
    backend = _backend(inst)
    ctx = make_ctx()
    p3ht = backend.get_entity(ctx, "rsoxs_v4", "matkg:P3HT")
    assert p3ht["graph_id"] == "rsoxs_v4" and p3ht["tiled_id"] == ids_v4["matkg:P3HT"]
    from graph_samples import write
    json_backend = JsonGraphBackend([write(tmp_path / "matkg_rsoxs_v4.json", things_payload())])
    expected = json_backend.get_entity(ctx, "rsoxs_v4", "matkg:P3HT")
    for key in ("graph_id", "id", "name", "category", "citations", "data"):
        assert p3ht[key] == expected[key], key
    assert backend.ledger.tiled_id("rsoxs_v4", "matkg:P3HT") == ids_v4["matkg:P3HT"]
    # Second lookup goes through the ledger and still validates identity.
    assert backend.get_entity(ctx, "rsoxs_v4", "matkg:P3HT")["tiled_id"] == ids_v4["matkg:P3HT"]
    assert backend.get_entity(ctx, "rsoxs_v4", "matkg:Missing") is None
    v3 = backend.get_entity(ctx, "rsoxs_v3", "rsoxs:PSbPMMA")
    assert v3["data"]["graph_id"] == "legacy-gid" and v3["name"] == "PS-b-PMMA"


def test_scoping_and_forge_sentinel_isolation(seeded):
    inst, _ids_v4, _ids_v3, sentinel = seeded
    backend = _backend(inst)
    ctx = make_ctx()
    hits, complete = backend.find_entities(ctx, "rsoxs_v4", "", limit=100)
    assert complete
    assert sorted(h["id"] for h in hits) == ["matkg:Isolated", "matkg:OPV", "matkg:P3HT", "matkg:RSoXS"]
    assert all(h["graph_id"] == "rsoxs_v4" for h in hits)
    returned = {h["tiled_id"] for h in hits}
    assert sentinel not in returned
    assert [h["id"] for h in backend.find_entities(ctx, "rsoxs_v4", "P3HT", limit=10)[0]] == ["matkg:P3HT"]
    assert backend.find_entities(ctx, "rsoxs_v4", "FORGE", limit=10) == ([], True)
    assert backend.find_entities(ctx, "rsoxs_v4", "Fake", limit=10) == ([], True)
    assert backend.get_entity(ctx, "rsoxs_v4", "matkg:Fake") is None
    assert [h["id"] for h in backend.find_entities(ctx, "rsoxs_v3", "", limit=10)[0]] == [
        "rsoxs:PSbPMMA", "rsoxs:RSoXS"]
    scoped = make_als_ctx(scope={"rsoxs_v3"})
    with pytest.raises(ContractError) as err:
        backend.find_entities(scoped, "rsoxs_v4", "", limit=10)
    assert err.value.code is ErrorCode.DENIED
    hood = backend.neighborhood(ctx, "rsoxs_v4", "matkg:P3HT", hops=5, max_nodes=100)
    assert sentinel not in {n["tiled_id"] for n in hood["nodes"]}
    assert all(e["predicate"].startswith("rel:") for e in hood["edges"])


def test_neighborhood_matches_json_and_truncates(seeded, tmp_path):
    inst = seeded[0]
    backend = _backend(inst)
    ctx = make_ctx()
    hood = backend.neighborhood(ctx, "rsoxs_v4", "matkg:P3HT", hops=3, max_nodes=100)
    assert [n["id"] for n in hood["nodes"]] == ["matkg:P3HT", "matkg:RSoXS", "matkg:OPV"]
    assert [n["depth"] for n in hood["nodes"]] == [0, 1, 2] and not hood["truncated"]
    from graph_samples import write
    json_backend = JsonGraphBackend([write(tmp_path / "matkg_rsoxs_v4.json", things_payload())])
    jhood = json_backend.neighborhood(ctx, "rsoxs_v4", "matkg:P3HT", hops=3, max_nodes=100)
    key = sorted((e["id"], e["subject"], e["predicate"], e["object"]) for e in hood["edges"])
    jkey = sorted((e["id"], e["subject"], e["predicate"], e["object"]) for e in jhood["edges"]
                  if e["object"] not in jhood["missing"])
    assert key == jkey
    capped = backend.neighborhood(ctx, "rsoxs_v4", "matkg:P3HT", hops=3, max_nodes=2)
    assert len(capped["nodes"]) == 2 and capped["truncated"]
    one = backend.neighborhood(ctx, "rsoxs_v4", "matkg:RSoXS", hops=1, max_nodes=10, direction="out")
    assert [n["id"] for n in one["nodes"]] == ["matkg:RSoXS", "matkg:OPV"]
    # Link page cap: a node with more links than link_limit is flagged as truncated.
    small = _backend(inst, link_limit=1)
    assert small.neighborhood(ctx, "rsoxs_v4", "matkg:RSoXS", hops=1, max_nodes=10)["truncated"]
    # Many hops are many requests; nothing exceeds the server's depth limit of 10.
    assert not backend.neighborhood(ctx, "rsoxs_v4", "matkg:P3HT", hops=20, max_nodes=100)["truncated"]


def test_partial_scan_is_flagged_not_absent(seeded):
    inst = seeded[0]
    backend = _backend(inst, page_size=3, max_scan=4)
    hits, complete = backend.find_entities(make_ctx(), "rsoxs_v4", "", limit=100)
    assert complete is False
    with pytest.raises(TruncatedGraphRead) as err:
        backend.get_entity(make_ctx(), "rsoxs_v4", "matkg:Missing")
    assert err.value.code is ErrorCode.UNAVAILABLE


def test_duplicate_identity_is_a_conflict(tiled_base):
    inst = start_tiled_instance(tiled_base, seed=False)
    try:
        payload = {"things": [{"id": "matkg:A", "name": "A"}], "associations": []}
        seed_graph(inst, "dup", payload)
        seed_graph(inst, "dup", payload)
        with pytest.raises(ContractError) as err:
            _backend(inst).get_entity(make_ctx(), "dup", "matkg:A")
        assert err.value.code is ErrorCode.CONFLICT
    finally:
        inst.destroy()


def test_wrong_credential_is_denied_and_stopped_server_is_unavailable(tiled_base):
    inst = start_tiled_instance(tiled_base, seed=False)
    seed_graph(inst, "g1", {"things": [{"id": "matkg:A", "name": "A"}], "associations": []})
    url = inst.url
    bad = TiledGraphBackend(TiledGraphClient(url, profile="test", credential="wrongkey0"))
    with pytest.raises(ContractError) as err:
        bad.get_entity(make_ctx(), "g1", "matkg:A")
    assert err.value.code is ErrorCode.DENIED
    good = TiledGraphBackend(TiledGraphClient(url, profile="test", credential=inst.writer_key))
    assert good.get_entity(make_ctx(), "g1", "matkg:A")["id"] == "matkg:A"
    inst.destroy()
    for call in (lambda: good.get_entity(make_ctx(), "g1", "matkg:A"),
                 lambda: good.find_entities(make_ctx(), "g1", "", limit=5),
                 lambda: good.neighborhood(make_ctx(), "g1", "matkg:A", hops=1, max_nodes=5)):
        with pytest.raises(ContractError) as err:
            call()
        assert err.value.code is ErrorCode.UNAVAILABLE and err.value.retryable
