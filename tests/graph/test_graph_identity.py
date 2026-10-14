"""T08A: identity mapping F2W <-> Tiled (no display-name joins)."""
import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph import identity_adapter as ida


@pytest.mark.parametrize("graph_id,node_id", [
    ("rsoxs_v4", "matkg:P3HT"), ("bl1101", "beamline:SynthMotor"), ("g/x", "a/b:c%d"), ("x", "matkg:")])
def test_uri_round_trip(graph_id, node_id):
    uri = ida.entity_uri(graph_id, node_id)
    assert ida.parse_entity_uri(uri) == (graph_id, node_id)


def test_ids_with_slashes_cannot_forge_another_graph():
    uri = ida.entity_uri("a", "b/node/c")
    assert ida.parse_entity_uri(uri) == ("a", "b/node/c")
    assert ida.parse_entity_uri("f2w:kg/a/node/b/node/c") is None
    assert ida.parse_entity_uri("f2w:test/paper/0") is None and ida.parse_entity_uri(None) is None


def _entity(graph_id, node_id, **over):
    row = {"id": "uuid", "uri": ida.entity_uri(graph_id, node_id),
           "properties": ida.entity_properties(graph_id, node_id, {"name": "x"})}
    row.update(over)
    return row


def test_ownership_requires_owner_uri_and_properties_to_agree():
    assert ida.literature_entity_key(_entity("g", "n")) == ("g", "n")
    forged = _entity("g", "n")
    forged["properties"] = dict(forged["properties"], f2w_owner="forge")
    assert ida.literature_entity_key(forged) is None
    mismatch = _entity("g", "n", uri=ida.entity_uri("other", "n"))
    assert ida.literature_entity_key(mismatch) is None
    assert ida.literature_entity_key({"uri": "forge:sentinel/esaf/1", "properties": {"owner": "forge"}}) is None
    with pytest.raises(ContractError):
        ida.entity_properties("g", "n", {"f2w_id": "spoof"})


def test_edge_ids_follow_triple_identity():
    edges = [{"subject": "a", "predicate": "rel:X", "object": "b"},
             {"subject": "a", "predicate": "rel:X", "object": "b"},
             {"subject": "a", "predicate": "rel:Y", "object": "b"}]
    ids = [eid for eid, _ in ida.assign_edge_ids(edges)]
    assert ids[0] == ida.edge_id("a", "rel:X", "b") and ids[1] == ids[0] + "#1" and ids[2] != ids[0]
    props = ida.link_properties("g", ids[0], {**edges[0], "has_evidence": "e"})
    assert ida.literature_link_key({"properties": props}) == ("g", ids[0])
    assert "subject" not in props and props["has_evidence"] == "e"


def test_ledger_never_rebinds_and_round_trips():
    ledger = ida.IdentityLedger()
    ledger.bind("g", "n", "u1")
    ledger.bind("g", "n", "u1")
    for args in (("g", "n", "u2"), ("g", "m", "u1")):
        with pytest.raises(ContractError) as err:
            ledger.bind(*args)
        assert err.value.code is ErrorCode.CONFLICT
    again = ida.IdentityLedger.from_json(ledger.to_json())
    assert again.tiled_id("g", "n") == "u1" and again.f2w_key("u1") == ("g", "n")
    assert again.sha256() == ledger.sha256() and len(again) == 1
