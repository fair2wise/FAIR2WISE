"""T18D: the legacy ``PATCH /graph/node`` as a journaled multi-write on Tiled, through the real legacy app.

The app runs the T18A harness (fake models and providers, sockets blocked) with a Tiled save target over the
in-memory Tiled double (the server-backed variants of the same sequences are in
``tests/graph_import/test_legacy_save.py``). The harness's in-memory Splash fake stays installed so every test can
prove that **no Splash call is made**.

Acceptance covered: UI04 (editable mode works with no Splash service), UI15 (node fields), UI16 (relationship add
and remove), UI17 (snippet upsert and the implicit unlink), same id read back, old citations resolve, Q8 (502
during a switch), Q10 (409), Q12 (acknowledged only after the Tiled effect; a partial failure is a 502 and a
``failed`` entry that the retry's recovery completes), and TIL-19 (ALS non-admin: 403 with no journal entry).
"""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "graph_import", REPO / "tests" / "security", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from _wiring_support import FORGED_HEADERS, make_cfg, other_user  # noqa: E402,F401
from import_fakes import FakeClock as _FakeClock, MemoryTiled, snapshot_with_graph  # noqa: E402
from security_fakes import (  # noqa: E402
    STAGING, FakeTiledIdentityPort, ForbiddenIdentityPort, InMemoryCredentialStore, admin_user, local_settings,
    plain_user, staging_settings)

from app.api.auth import SESSION_COOKIE  # noqa: E402
from app.modules.contracts.errors import ContractError, ErrorCode  # noqa: E402
from app.modules.graph import edit_journal as ej  # noqa: E402
from app.modules.graph import identity_adapter as ida  # noqa: E402
from app.modules.graph.imports import corpus_scope as cs  # noqa: E402
from app.modules.graph.imports import tiled_json as tj  # noqa: E402
from app.modules.graph.legacy_save import SaveTarget  # noqa: E402
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot  # noqa: E402
from app.modules.schema.registry import SchemaRegistry  # noqa: E402
from app.modules.security.authorization import DeploymentSettings, create_approval, local_operator_context  # noqa: E402
from app.modules.security.tiled_identity import TiledCredential  # noqa: E402
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS  # noqa: E402

REPO_ROOT = REPO


class FakeClock(_FakeClock):
    """Every reading is one second after the last, as a real clock would be between two journal writes."""

    def __call__(self):
        self.advance(1)
        return self.now


SCOPE = "contract"
NODE = "matkg:TermA"


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("kg") / "storage" / "kg"
    root.mkdir(parents=True)
    create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json", storage_kg=root,
                                    terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT,
                                    registry=SchemaRegistry.discover()))
    return root / "synthetic" / "v1"


class World:
    """One legacy app, one Tiled double and the journal, all under ``tmp_path``."""

    def __init__(self, monkeypatch, tmp_path, template, *, als=False):
        self.ctx = H.install(monkeypatch, tmp_path / "ws", REPO_ROOT)
        monkeypatch.delenv("F2W_RUNTIME", raising=False)
        payload = json.loads((self.ctx.workspace / "storage" / "kg" / H.GRAPH_FILENAME).read_text(encoding="utf-8"))
        self.base_payload = payload
        self.snap = snapshot_with_graph(template, tmp_path / "snaproot", SCOPE, "v1", payload)
        loopback = "http://127.0.0.1:59999"
        self.mem = MemoryTiled(loopback)   # an in-memory double: no socket is ever opened
        self.mem.add_forge_sentinel()
        self.clock = FakeClock()
        self.state_dir = tmp_path / "state"
        # Seed the corpus as the local operator would publish it; the ALS variant then re-labels the double as
        # the staging Tiled (the double never opens a socket).
        seed_settings = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target=loopback)
        seeder = local_operator_context(seed_settings, request_id="seed")
        fps = tj.default_supported_schema_fingerprints(SchemaRegistry.discover())
        plan = cs.plan_replacement(self.snap, client=self.mem, settings=seed_settings,
                                   supported_schema_fingerprints=fps, state_dir=self.state_dir)
        approval = create_approval(seeder, plan.change, seed_settings, clock=self.clock,
                                   approval_id="appr-" + plan.change.plan_sha256[:8])
        cs.apply_replacement(seeder, plan, approval, source=self.snap, client=self.mem, settings=seed_settings,
                             supported_schema_fingerprints=fps, state_dir=self.state_dir, clock=self.clock)
        if als:
            self.mem.base_url, self.mem.profile = STAGING, "als-dev"
            self.settings = staging_settings(tiled_target=STAGING)
        else:
            self.settings = seed_settings
        self.mem.writes.clear()
        self.journal = ej.EditJournal(tmp_path / "edits.jsonl")
        sha = hashlib.sha256((self.snap / "graph.json").read_bytes()).hexdigest()
        self.target = SaveTarget(client=self.mem, settings=self.settings, state_dir=self.state_dir, clock=self.clock,
                                 scope=SCOPE, journal=self.journal, base_graph_path=self.snap / "graph.json",
                                 base_snapshot_id="v1", base_graph_sha256=sha)

    def splash_calls(self):
        return {k: v for k, v in self.ctx.calls.items() if k.startswith("splash.")}


def build_local(monkeypatch, tmp_path, template):
    from app.modules.f2w_agent import api as api_mod

    w = World(monkeypatch, tmp_path, template)
    cfg = make_cfg(w.ctx)
    cfg.tiled_save = w.target
    w.app = api_mod.create_app(cfg, deployment=local_settings(), identity_port=ForbiddenIdentityPort())
    w.client = TestClient(w.app, raise_server_exceptions=False)
    return w


def build_als(monkeypatch, tmp_path, template):
    from app.modules.f2w_agent import api as api_mod

    w = World(monkeypatch, tmp_path, template, als=True)
    port = FakeTiledIdentityPort(tiled_target=STAGING)
    store = InMemoryCredentialStore()
    for who, user in (("user", plain_user()), ("admin", admin_user())):
        port.tokens[f"token-{who}"] = user
        store.sessions[f"cookie-{who}"] = TiledCredential(f"token-{who}", tiled_target=STAGING)
    cfg = make_cfg(w.ctx)
    cfg.tiled_save = w.target
    w.app = api_mod.create_app(cfg, deployment=staging_settings(), identity_port=port,
                               credential_store=store, clock=w.clock)
    return w


@pytest.fixture
def w(monkeypatch, tmp_path, template):
    return build_local(monkeypatch, tmp_path, template)


def live_nodes(mem):
    return {ida.parse_entity_uri(e["uri"])[1]: e for e in mem.entities.values()
            if ida.literature_entity_key(e) and ida.literature_entity_key(e)[0] == SCOPE}


def owned_triples(mem):
    out = set()
    for lk in mem.links.values():
        if (lk.get("properties") or {}).get("f2w_graph_id") == SCOPE:
            s, o = mem.entities[lk["subjectId"]], mem.entities[lk["objectId"]]
            out.add((s["properties"]["f2w_id"], lk["predicate"], o["properties"]["f2w_id"]))
    return out


def states(journal):
    return sorted((r.operation, r.state) for r in journal.latest().values())


# --------------------------------------------------------------------------------------------- the happy path


def test_ui04_ui15_ui16_ui17_save_reads_back_on_tiled_with_no_splash_call(w):
    c = w.client
    before_graph = c.get("/graph").json()
    assert {n["id"] for n in before_graph["nodes"]} >= {NODE, "matkg:Other"}
    foreign = [json.dumps(r, sort_keys=True) for r in [*w.mem.entities.values(), *w.mem.links.values()]
               if (r.get("properties") or {}).get("f2w_owner") != "f2w-literature"]
    body = {
        "label": "Term A edited", "description": "Edited in the frozen UI against Tiled.",
        "relationship_updates": [{"action": "add", "source": NODE, "predicate": "rel:affects",
                                  "target": "matkg:Other"}],
        "linked_code_snippets": [
            {"id": "matkg:Code1", "label": "gamma", "function_name": "gamma", "code_language": "python",
             "code_snippet": "def gamma():\n    return 3", "_action": "upsert"},
            {"label": "delta snippet", "function_name": "delta", "code_language": "python",
             "code_snippet": "def delta():\n    return 4", "_action": "upsert"}],
    }
    resp = c.patch(f"/graph/node/{NODE}", json=body)
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert saved["id"] == NODE and saved["label"] == "Term A edited"
    assert sorted(s["function_name"] for s in saved["linked_code_snippets"]) == ["delta", "gamma"]
    # The Tiled effect exists before the response was returned (Q12) and the journal says so.
    assert live_nodes(w.mem)[NODE]["name"] == "Term A edited"
    assert (NODE, "rel:affects", "matkg:Other") in owned_triples(w.mem)
    assert {op for op, st in states(w.journal)} == {"node_patch", "relationship_add", "snippet_upsert"}
    assert {st for _op, st in states(w.journal)} == {"applied"}
    assert w.splash_calls() == {}, "no Splash call"
    assert [json.dumps(r, sort_keys=True) for r in [*w.mem.entities.values(), *w.mem.links.values()]
            if (r.get("properties") or {}).get("f2w_owner") != "f2w-literature"] == foreign
    # Same id read back through every reader: detail, search, graph, neighbourhood is the same file.
    assert c.get(f"/graph/node/{NODE}").json()["label"] == "Term A edited"
    graph = c.get("/graph").json()
    assert {"source": NODE, "target": "matkg:Other", "predicate": "rel:affects"} in graph["edges"]
    assert len(graph["nodes"]) == len(before_graph["nodes"]) + 1
    found = c.post("/graph/nodes/search", json={"query": "Term A edited"}).json()
    assert NODE in {r["node"]["id"] for r in found["results"]}
    # UI17 inverse: omit the new snippet (implicit unlink) and remove the relationship.
    keep = [s for s in saved["linked_code_snippets"] if s["function_name"] == "gamma"]
    resp2 = c.patch(f"/graph/node/{NODE}", json={
        "label": "Term A", "description": "Synthetic contract node A (resonant scattering).",
        "relationship_updates": [{"action": "remove", "source": NODE, "predicate": "rel:affects",
                                  "target": "matkg:Other"}],
        "linked_code_snippets": [{"id": s["id"], "_action": "upsert", "label": s["label"],
                                  "function_name": s["function_name"], "code_language": s["code_language"],
                                  "code_snippet": s["code_snippet"]} for s in keep]})
    assert resp2.status_code == 200, resp2.text
    after = c.get(f"/graph/node/{NODE}").json()
    assert after["label"] == "Term A" and [s["function_name"] for s in after["linked_code_snippets"]] == ["gamma"]
    assert not [e for e in c.get("/graph").json()["edges"] if e["predicate"] == "rel:affects"]
    assert (NODE, "rel:affects", "matkg:Other") not in owned_triples(w.mem)
    snippet = [n for n in live_nodes(w.mem) if n.startswith("matkg:snippetdelta")]
    assert snippet, "an implicit unlink keeps the snippet node"
    assert w.splash_calls() == {}


def test_a_retry_of_a_saved_request_is_answered_with_the_same_readback_and_writes_nothing(w):
    body = {"label": "Term A once", "description": "Same request twice."}
    first = w.client.patch(f"/graph/node/{NODE}", json=body)
    writes = len(w.mem.writes)
    again = w.client.patch(f"/graph/node/{NODE}", json=body)
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert len(w.mem.writes) == writes and len(w.journal.latest()) == 1


# --------------------------------------------------------------------------------------------- refusals


def test_q8_a_save_during_a_switch_is_the_legacy_502_and_leaves_nothing(w):
    cs.ReleaseState(cs.scope_dir(w.target.state_dir, w.target.target, SCOPE) / "release.json").mark_switching("ab")
    n = len(w.mem.writes)
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "during a switch"})
    assert resp.status_code == 502 and resp.json()["detail"].startswith("Splash update failed: ")
    assert "republished" in resp.json()["detail"]
    assert not w.journal.path.exists() and len(w.mem.writes) == n
    assert w.client.get(f"/graph/node/{NODE}").json()["label"] == "Term A"


def test_q10_a_save_from_an_out_of_date_copy_is_409_with_no_journal_entry(w):
    row = live_nodes(w.mem)[NODE]
    w.mem.entities[row["id"]]["properties"]["description"] = "changed by another writer"
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "from a stale tab"})
    assert resp.status_code == 409, resp.text
    assert not w.journal.path.exists()


def test_q9_removing_a_link_f2w_does_not_own_is_the_legacy_400(w):
    graph = w.client.get("/graph").json()
    edge = next(e for e in graph["edges"] if e["source"] == NODE and e["predicate"] != "rel:has_code_snippet")
    s, o = live_nodes(w.mem)[edge["source"]]["id"], live_nodes(w.mem)[edge["target"]]["id"]
    for lid in [lid for lid, lk in w.mem.links.items() if lk["subjectId"] == s and lk["objectId"] == o
                and lk["predicate"] == edge["predicate"]]:
        del w.mem.links[lid]
    w.mem.links["foreign-1"] = {"id": "foreign-1", "subjectId": s, "objectId": o, "predicate": edge["predicate"],
                                "properties": {"owner": "forge"}, "accessBlob": None}
    resp = w.client.patch(f"/graph/node/{NODE}", json={"relationship_updates": [
        {"action": "remove", "source": edge["source"], "predicate": edge["predicate"], "target": edge["target"]}]})
    assert resp.status_code == 400 and "F2W" in resp.json()["detail"]
    assert "foreign-1" in w.mem.links and not w.journal.path.exists()


def test_json_graph_mode_is_unchanged_read_only_400(w):
    set_json = w.client.put("/settings", json={"graph_source": "json", "json_graph_path": f"storage/kg/{H.GRAPH_FILENAME}",
                                               "json_graph_paths": [f"storage/kg/{H.GRAPH_FILENAME}"]})
    assert set_json.status_code == 200
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "x"})
    assert resp.status_code == 400 and resp.json()["detail"] == "Editing node properties requires splash graph mode"
    assert not w.journal.path.exists()


# --------------------------------------------------------------------------------------------- partial failure


def test_q12_a_partial_failure_is_502_and_failed_and_the_retry_completes_with_the_same_readback(w):
    calls = {"n": 0}

    def fault(stage, i):
        if stage == "promote_new_effect":
            calls["n"] += 1
            if calls["n"] == 1:
                raise ContractError(ErrorCode.UNAVAILABLE, "Tiled graph is unavailable (ConnectionError)")
    w.target.fault = fault
    body = {"label": "Term A partial", "description": "partial then retried"}
    resp = w.client.patch(f"/graph/node/{NODE}", json=body)
    assert resp.status_code == 502 and resp.json()["detail"].startswith("Splash update failed: ")
    assert [st for _op, st in states(w.journal)] == ["failed"]
    w.clock.advance(1000)                                   # the failed Save's lease has expired
    retry = w.client.patch(f"/graph/node/{NODE}", json=body)    # first runs the recovery job, then answers
    assert retry.status_code == 200, retry.text
    assert retry.json()["label"] == "Term A partial" and retry.json()["id"] == NODE
    assert len(w.journal.latest()) == 1 and [st for _op, st in states(w.journal)] == ["applied"]
    assert live_nodes(w.mem)[NODE]["name"] == "Term A partial"
    assert len(live_nodes(w.mem)) == len(w.base_payload["things"])
    assert w.splash_calls() == {}


# --------------------------------------------------------------------------------------------- ALS


def test_til19_als_non_admin_forged_save_is_403_with_no_journal_entry_and_no_tiled_access(monkeypatch, tmp_path,
                                                                                          template):
    d = build_als(monkeypatch, tmp_path, template)
    n_writes = len(d.mem.writes)
    for headers in (None, FORGED_HEADERS):
        user = TestClient(d.app, cookies={SESSION_COOKIE: "cookie-user"}, headers=headers,
                          raise_server_exceptions=False)
        resp = user.patch(f"/graph/node/{NODE}", json={"label": "forged by a user"})
        assert resp.status_code == 403 and resp.json()["code"] == "denied", resp.text
    anon = TestClient(d.app, raise_server_exceptions=False).patch(f"/graph/node/{NODE}", json={"label": "anon"})
    assert anon.status_code in (401, 403)
    assert not d.journal.path.exists(), "no journal entry"
    assert len(d.mem.writes) == n_writes, "no Tiled access by the writer"
    assert d.splash_calls() == {}
    admin = TestClient(d.app, cookies={SESSION_COOKIE: "cookie-admin"}, raise_server_exceptions=False)
    ok = admin.patch(f"/graph/node/{NODE}", json={"label": "by an admin"})
    assert ok.status_code == 200, ok.text
    rec = next(iter(d.journal.latest().values()))
    assert rec.actor == "uuid-admin" and rec.state == "applied" and rec.profile == "als-dev"
    assert live_nodes(d.mem)[NODE]["name"] == "by an admin"
