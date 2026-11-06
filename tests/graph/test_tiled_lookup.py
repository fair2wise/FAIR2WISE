"""T17B (H8): ``graph.backends.tiled_lookup``, the real port of the legacy live Tiled lookup.

Unit tests drive the lookup and the Settings probe through a fake transport (no network, no
environment credential); the last test runs the same code against a disposable Tiled 0.2.18
server (skips unless ``F2W_TILED_PYTHON`` is set). The legacy import path is pinned as an alias
of the new module so attribute patches at either path keep applying to both.
"""
from __future__ import annotations

import importlib
import sys

import pytest
import requests
from tiled_server import start_tiled_instance, tiled_python

from app.modules.graph.backends import tiled_lookup as tl

URI = "http://127.0.0.1:8765"
ESAF = {"id": "e1", "entityType": "ESAF", "name": "ESAF 2026-00043", "uri": "beamline:ESAF-2026-00043",
        "nodeId": None, "properties": {"esaf_number": "2026-00043", "scientist": "A. Scientist"},
        "outgoingLinks": [{"predicate": "rel:hasSample",
                           "object": {"id": "s1", "name": "P3HT blend 7", "entityType": "Sample",
                                      "uri": "beamline:Sample-P3HT-blend-7"}}]}
SAMPLE = {"id": "s1", "entityType": "Sample", "name": "P3HT blend 7", "uri": "beamline:Sample-P3HT-blend-7",
          "nodeId": None, "properties": {"sample_code": "P3HT-blend-7"}, "outgoingLinks": []}


class FakeTransport:
    """``LookupTransport`` answering by entity type; records every document and variables."""

    def __init__(self, by_type=None, *, probe_rows=None, fail=None):
        self.by_type = by_type or {}
        self.probe_rows = probe_rows
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    def query(self, document, variables=None):
        self.calls.append((document, dict(variables or {})))
        if self.fail is not None:
            raise self.fail
        if "__typename" in document:
            return {"__typename": "Query"}
        if "entities(limit: 1)" in document:
            return {"entities": self.probe_rows if self.probe_rows is not None else []}
        rows = self.by_type.get(variables["entityType"], [])
        off, lim = variables["offset"], variables["limit"]
        return {"entities": rows[off:off + lim]}


@pytest.fixture(autouse=True)
def _no_network_no_key(monkeypatch):
    monkeypatch.delenv("TILED_API_KEY", raising=False)
    monkeypatch.delenv("TILED_URI", raising=False)
    monkeypatch.delenv("F2W_LIVE_TILED", raising=False)
    monkeypatch.delenv("TILED_GRAPH_ENABLED", raising=False)

    def refuse(*a, **k):  # pragma: no cover - guards against accidental network use
        raise AssertionError("network call attempted")

    monkeypatch.setattr(requests, "post", refuse)


# ---------------------------------------------------------------- import surface


def test_import_has_no_side_effects_and_legacy_path_is_an_alias():
    assert tl.process_api_key() == ""  # read at call time, never cached at import
    legacy = importlib.import_module("app.modules.tiled_graph")
    assert legacy is tl
    assert sys.modules["app.modules.tiled_graph"] is sys.modules["app.modules.graph.backends.tiled_lookup"]
    for name in ("TiledLookupGraph", "TiledLookupResult", "lookup_experiment_identity", "probe_tiled_status",
                 "load_tiled_config", "coerce_local_tiled_uri", "is_experiment_identity_question",
                 "resolve_tiled_node_id", "tiled_neighborhood_ids", "tiled_graphql", "TiledGraphQLError"):
        assert name in tl.__all__ and getattr(legacy, name) is getattr(tl, name)


def test_a_patch_at_the_legacy_path_reaches_the_new_module(monkeypatch):
    from app.modules import tiled_graph as legacy

    monkeypatch.setattr(legacy, "tiled_graphql", lambda *a, **k: {"entities": []})
    out = tl.lookup_experiment_identity("Which samples are on ESAF 2026-00043?", uri=URI, enabled=True,
                                        include_catalog=False)
    assert out.status == "unsigned"  # the fake answered empty lists without a key


# ---------------------------------------------------------------- client


def test_client_never_exposes_the_credential_and_sends_it_only_as_a_header():
    seen = {}

    class Session:
        def post(self, url, *, json, headers, timeout):
            seen.update(url=url, json=json, headers=headers, timeout=timeout)

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"data": {"ok": True}}
            return R()

    client = tl.TiledLookupClient(URI + "/api/graphql", credential=lambda: "SECRET", timeout=2.5, session=Session())
    assert "SECRET" not in repr(client) and "SECRET" not in str(vars(client).values())
    assert client.query("query { ok }", {"a": 1}) == {"ok": True}
    assert seen["url"] == URI + "/api/graphql" and seen["timeout"] == 2.5
    assert seen["headers"]["Authorization"] == "Apikey SECRET" and seen["json"]["variables"] == {"a": 1}


def test_client_raises_graphql_errors_structurally():
    class Session:
        def post(self, url, *, json, headers, timeout):
            assert "Authorization" not in headers  # no credential configured -> unsigned request

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"errors": [{"message": "Cannot query field 'nodeId'", "path": ["entities", "nodeId"]}]}
            return R()

    client = tl.TiledLookupClient(URI, credential=lambda: "", session=Session())
    with pytest.raises(tl.TiledGraphQLError) as err:
        client.query("query { entities { nodeId } }")
    assert err.value.mentions_field("nodeId") and not err.value.mentions_field("uri")


# ---------------------------------------------------------------- lookup through a fake transport


def test_lookup_pages_entities_builds_the_overlay_graph_and_scores_hits():
    fake = FakeTransport({"ESAF": [ESAF], "Sample": [SAMPLE]})
    out = tl.lookup_experiment_identity("Which samples are on ESAF 2026-00043?", uri=URI, enabled=True,
                                        include_catalog=False, client=fake)
    assert out.status == "ok" and out.fallback == "" and out.source == "graphql"
    assert out.entity_counts == {"ESAF": 1, "Proposal": 0, "Sample": 1, "BlueskyRun": 0}
    ids = {h.id for h in out.hits}
    assert {"beamline:ESAF-2026-00043", "beamline:Sample-P3HT-blend-7"} <= ids
    assert all(h.graph_id == tl.TILED_GRAPH_ID for h in out.hits)
    # One page per cookbook type, offset 0, no nodeId fallback needed.
    assert [(v["entityType"], v["offset"]) for _, v in fake.calls] == [(t, 0) for t in tl.ENTITY_TYPES]
    # Overlay graph: the ESAF's outgoing link became an edge the cite-click BFS can follow.
    near = tl.tiled_neighborhood_ids(out.graph, ["tiled:beamline:ESAF-2026-00043"], hops=1)
    assert near[0] == "beamline:ESAF-2026-00043" and "beamline:Sample-P3HT-blend-7" in near
    assert out.graph.nodes["beamline:ESAF-2026-00043"]["description"].startswith("ESAF 2026-00043")
    meta = out.meta()
    assert meta["used"] is True and meta["graph_id"] == "tiled" and meta["fallback"] is None


def test_lookup_falls_back_to_the_no_nodeid_query_when_the_server_lacks_the_field():
    class NoNodeId(FakeTransport):
        def query(self, document, variables=None):
            if "nodeId" in document:
                self.calls.append((document, dict(variables or {})))
                raise tl.TiledGraphQLError([{"message": "Cannot query field 'nodeId' on type 'Entity'"}])
            return super().query(document, variables)

    fake = NoNodeId({"ESAF": [ESAF]})
    out = tl.lookup_experiment_identity("esaf 2026-00043", uri=URI, enabled=True, include_catalog=False, client=fake)
    assert out.status == "ok"
    docs = [d for d, _ in fake.calls]
    assert "nodeId" in docs[0] and "nodeId" not in docs[1]


def test_lookup_statuses_disabled_skipped_invalid_unreachable_unsigned_empty(monkeypatch):
    fake = FakeTransport()
    assert tl.lookup_experiment_identity("esaf 1", enabled=False, client=fake).status == "disabled"
    assert tl.lookup_experiment_identity("what is RSoXS?", uri=URI, enabled=True, client=fake).status == "skipped"
    # A malformed URI is coerced to the default (baseline behaviour: ``invalid_uri`` is not reachable this way).
    bad = tl.lookup_experiment_identity("esaf 1", uri="ftp://x", enabled=True, include_catalog=False, client=fake)
    assert bad.status == "unsigned" and tl.load_tiled_config(uri="ftp://x").uri == tl.DEFAULT_TILED_URI
    down = tl.lookup_experiment_identity("esaf 1", uri=URI, enabled=True, include_catalog=False,
                                         client=FakeTransport(fail=requests.ConnectionError("refused")))
    assert down.status == "unreachable" and down.error.startswith("ConnectionError") and down.fallback == "json_kg"
    unsigned = tl.lookup_experiment_identity("esaf 1", uri=URI, enabled=True, include_catalog=False, client=fake)
    assert unsigned.status == "unsigned" and "TILED_API_KEY" in unsigned.error
    monkeypatch.setenv("TILED_API_KEY", "k")
    signed = tl.lookup_experiment_identity("which proposals", uri=URI, enabled=True, include_catalog=False,
                                           client=FakeTransport({"ESAF": [ESAF]}))
    # Signed, entities exist, question matches the synthetic inventory nodes -> ok (count hits).
    assert signed.status == "ok" and any(h.id.startswith("tiled:count:") for h in signed.hits)


def test_lookup_redacts_the_process_key_from_error_text(monkeypatch):
    monkeypatch.setenv("TILED_API_KEY", "TOPSECRET")
    fake = FakeTransport(fail=requests.HTTPError("401 for Apikey TOPSECRET"))
    out = tl.lookup_experiment_identity("esaf 1", uri=URI, enabled=True, include_catalog=False, client=fake)
    assert out.status == "unreachable" and "TOPSECRET" not in out.error and "<redacted>" in out.error


# ---------------------------------------------------------------- probe through a fake transport


def test_probe_statuses(monkeypatch):
    assert tl.probe_tiled_status(uri=URI, enabled=False, client=FakeTransport())["tiled_status"] == "disabled"
    coerced = tl.probe_tiled_status(uri="nope", enabled=True, client=FakeTransport(probe_rows=[{"name": "x"}]))
    assert coerced["tiled_status"] == "ok" and coerced["tiled_uri"] == tl.DEFAULT_TILED_URI
    down = tl.probe_tiled_status(uri=URI, enabled=True, client=FakeTransport(fail=requests.Timeout("t")))
    assert down["tiled_status"] == "unreachable" and down["tiled_error"] == "Timeout: t"
    unsigned = tl.probe_tiled_status(uri=URI, enabled=True, client=FakeTransport(probe_rows=[]))
    assert unsigned["tiled_status"] == "unsigned" and unsigned["tiled_api_key_set"] is False
    ok = tl.probe_tiled_status(uri=URI, enabled=True, client=FakeTransport(probe_rows=[{"name": "x"}]))
    assert ok["tiled_status"] == "ok" and ok["tiled_error"] is None and ok["tiled_uri"] == URI
    monkeypatch.setenv("TILED_API_KEY", "k")
    empty = tl.probe_tiled_status(uri=URI, enabled=True, client=FakeTransport(probe_rows=[]))
    assert empty["tiled_status"] == "empty" and empty["tiled_api_key_set"] is True
    assert set(ok) == {"use_live_tiled", "tiled_uri", "tiled_api_key_set", "tiled_status", "tiled_error"}


def test_default_transport_routes_through_the_module_function(monkeypatch):
    calls = []

    def fake_graphql(query, variables=None, *, uri=None, api_key=None, timeout=8.0):
        calls.append((uri, api_key, timeout))
        return {"__typename": "Query"} if "__typename" in query else {"entities": [{"name": "x"}]}

    monkeypatch.setattr(tl, "tiled_graphql", fake_graphql)
    assert tl.probe_tiled_status(uri=URI, enabled=True, timeout=1.5)["tiled_status"] == "ok"
    assert calls == [(URI, None, 1.5), (URI, None, 1.5)]  # the probe never passes an explicit key


# ---------------------------------------------------------------- against a disposable Tiled 0.2.18


@pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")
def test_lookup_and_probe_against_a_disposable_tiled_server(tiled_base, monkeypatch):
    monkeypatch.setattr(requests, "post", requests.Session().post)  # re-enable loopback HTTP for this test
    inst = start_tiled_instance(tiled_base, seed=False)
    try:
        create = "mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }"
        esaf = inst.graphql(create, {"i": {"entityType": "ESAF", "name": "ESAF 2026-00043",
                                           "uri": "beamline:ESAF-2026-00043",
                                           "properties": {"esaf_number": "2026-00043"}}})["createEntity"]["id"]
        sample = inst.graphql(create, {"i": {"entityType": "Sample", "name": "P3HT blend 7",
                                             "uri": "beamline:Sample-P3HT-blend-7",
                                             "properties": {"sample_code": "P3HT-blend-7"}}})["createEntity"]["id"]
        inst.graphql("mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }",
                     {"i": {"subjectId": esaf, "predicate": "rel:hasSample", "objectId": sample, "properties": {}}})
        # Injected client: the writer key reaches the server as a header only, nothing read from os.environ.
        client = tl.TiledLookupClient(inst.url, credential=lambda: inst.writer_key)
        pack = tl.lookup_experiment_identity("Which samples are on ESAF 2026-00043?", uri=inst.url, enabled=True,
                                             include_catalog=False, client=client)
        assert pack.status == "ok"
        assert {"beamline:ESAF-2026-00043", "beamline:Sample-P3HT-blend-7"} <= {h.id for h in pack.hits}
        assert "beamline:Sample-P3HT-blend-7" in tl.tiled_neighborhood_ids(pack.graph, ["beamline:ESAF-2026-00043"],
                                                                           hops=1)
        # Default transport (the retained contract's path): process key from the environment.
        monkeypatch.setenv("TILED_API_KEY", inst.writer_key)
        status = tl.probe_tiled_status(uri=inst.url, enabled=True)
        assert status["tiled_status"] == "ok" and status["tiled_api_key_set"] is True
        again = tl.lookup_experiment_identity("Which samples are on ESAF 2026-00043?", uri=inst.url, enabled=True,
                                              include_catalog=False)
        assert again.status == "ok" and {h.id for h in again.hits} == {h.id for h in pack.hits}
    finally:
        inst.destroy()
