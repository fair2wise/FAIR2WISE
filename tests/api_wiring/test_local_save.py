"""T18D on ``:local``: the UI Save target resolved lazily after the first publication (owner decision 2026-11-04).

Pins :mod:`app.api.local_save` and its wiring through ``create_api_app(local_publication=True)``:

* before the first publication Save is the typed 501 (``UNSUPPORTED_CAPABILITY``, owner wording "JSON available,
  Tiled unavailable"), ``can_save`` is false, no journal entry exists and no Splash call is made;
* after a publication through the real publication job the target resolves from the active pointer (corpus,
  snapshot, recorded sha256, per-corpus journal under the importer state dir) and a Save reads back on Tiled;
* a Save from an out-of-date copy is :class:`StaleSave` (409); a base graph that no longer matches its recorded
  hash is the legacy_save ``CONFLICT`` (502, never 500);
* a pointer outside the corpus scope, a release record that disagrees with the pointer or a missing snapshot is
  unavailable (typed), and a later publication is resolved anew;
* the writer credential never appears in a ``repr``, an error text or a log record.

The "server" variant of the wiring test runs the same path against a real disposable Tiled 0.2.18 on
127.0.0.1 (``tests/fixtures/tiled_server.py``); without ``F2W_TILED_PYTHON`` it skips.
"""
import hashlib
import json
import logging
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "graph_import", REPO / "tests" / "security", REPO / "tests" / "fixtures",
              REPO / "tests" / "contracts" / "legacy_ui" / "closure", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from _wiring_support import make_cfg  # noqa: E402
from closure_support import loopback_guard  # noqa: E402
from import_fakes import MemoryTiled, snapshot_with_graph  # noqa: E402
from tiled_server import tiled_base, tiled_python, tiled_seeded_pool  # noqa: E402,F401

from app.api import local_publication as lp  # noqa: E402
from app.api import local_save as ls  # noqa: E402
from app.api.main import create_api_app  # noqa: E402
from app.api.operations import build_operations  # noqa: E402
from app.modules.contracts.errors import ContractError, ErrorCode  # noqa: E402
from app.modules.contracts.jobs import JobState  # noqa: E402
from app.modules.deployment.tiled_config import write_private_file  # noqa: E402
from app.modules.graph import identity_adapter as ida  # noqa: E402
from app.modules.graph.backends.tiled_mutations import TiledMutationClient as RealTiledMutationClient  # noqa: E402
from app.modules.graph.imports import corpus_scope as cs  # noqa: E402
from app.modules.graph.legacy_save import StaleSave, TiledSaveBackend  # noqa: E402
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot  # noqa: E402
from app.modules.jobs.snapshot_source import DirectorySnapshotSource  # noqa: E402
from app.modules.schema.registry import SchemaRegistry  # noqa: E402
from app.modules.security.authorization import DeploymentSettings  # noqa: E402
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS  # noqa: E402

LOCAL = "http://127.0.0.1:8765"
WRITER = "f2w-writer"
SCOPE = "contract"
NODE = "matkg:TermA"
KEY = "WRITERKEYabc123def456ghi789jkl012mno345pqr678"


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("kg") / "storage" / "kg"
    root.mkdir(parents=True)
    create_snapshot(SnapshotRequest(corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json", storage_kg=root,
                                    terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT,
                                    registry=SchemaRegistry.discover()))
    return root / "synthetic" / "v1"


def live_nodes(client):
    return {ida.parse_entity_uri(e["uri"])[1]: e for e in client.scan_entities()
            if ida.literature_entity_key(e) and ida.literature_entity_key(e)[0] == SCOPE}


class World:
    """The legacy app with ``local_publication=True`` over one Tiled (memory double or a real server) and its stores.

    Nothing is published at construction: that is the state of a fresh image.
    """

    def __init__(self, monkeypatch, tmp_path, template, *, instance=None, corpus_scope=frozenset(), kg_mode="json"):
        self.ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
        monkeypatch.delenv("F2W_RUNTIME", raising=False)
        self.payload = json.loads((self.ctx.workspace / "storage" / "kg" / H.GRAPH_FILENAME).read_text(encoding="utf-8"))
        self.template, self.tmp = template, tmp_path
        if instance is None:
            self.tiled_dir = tmp_path / "tiled"
            self.tiled_dir.mkdir()
            write_private_file(self.tiled_dir / "secrets" / "writer.key", KEY + "\n")
            self.target, port, python = LOCAL, 8765, "/nonexistent/python"
            self.mem = MemoryTiled(LOCAL)            # in-memory double: no socket is ever opened
            self.mem.add_forge_sentinel()
            # Both the publication and the Save provider build the writer client by this name.
            monkeypatch.setattr(lp, "TiledMutationClient", lambda *_a, **_kw: self.mem)
            monkeypatch.setattr(ls, "TiledMutationClient", lambda *_a, **_kw: self.mem)
        else:
            loopback_guard(monkeypatch)              # 127.0.0.1 only, never the developer's servers
            self.tiled_dir = Path(instance.root)
            self.target, port, python = instance.url, int(instance.url.rsplit(":", 1)[1]), str(tiled_python())
        self.settings = DeploymentSettings(profile="local", writer_identity=WRITER, tiled_target=self.target,
                                           corpus_scope=frozenset(corpus_scope))
        self.startup = {"F2W_DEPLOYMENT_PROFILE": "local", "F2W_TILED_DATA_DIR": str(self.tiled_dir),
                        "F2W_TILED_PYTHON": python, "F2W_TILED_PORT": str(port), "F2W_TILED_WRITER_IDENTITY": WRITER}
        self.ops_root = tmp_path / "ops"
        self.cfg = make_cfg(self.ctx, kg_mode=kg_mode)
        self.app = create_api_app(self.cfg, deployment=self.settings, startup_config=self.startup,
                                  operations_root=self.ops_root, local_publication=True)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.ops = self.app.state.f2w_operations
        self.service = self.app.state.f2w_agent_service
        self.provider = self.service.tiled_save_provider
        self.storage_kg = Path(self.cfg.workdir) / lp.SNAPSHOTS_DIRNAME
        self.state_dir = self.ops_root / lp.STATE_DIRNAME

    def tiled(self):
        """The writer client the provider uses (direct row access on the memory double)."""
        return self.provider.backend().t.client

    def freeze(self, snapshot_id, payload):
        """Freeze ``payload`` as ``contract/<snapshot_id>`` under the deployment's snapshot root."""
        src = snapshot_with_graph(self.template, self.tmp / "snap" / snapshot_id, SCOPE, snapshot_id, payload)
        dest = self.storage_kg / SCOPE / snapshot_id
        shutil.copytree(src, dest)
        return dest

    def publish(self, snapshot_id="v1"):
        """Submit, review and run the real publication job for ``contract/<snapshot_id>``."""
        ctx = self.ops.cli_context()
        handle = self.ops.submit_publication(ctx, f"{SCOPE}/{snapshot_id}", self.target)
        assert self.ops.jobs.work_one("w1") == (handle.job_id, JobState.AWAITING_REVIEW)
        plan = self.ops.current_plan(ctx, handle.job_id)
        self.ops.approve_publication(ctx, handle.job_id, plan.plan_sha256)
        assert self.ops.jobs.work_one("w1") == (handle.job_id, JobState.SUCCEEDED)
        return self.ops.publication_pointer(ctx, self.target)

    def editable(self):
        resp = self.client.put("/settings", json={"graph_source": "splash"})
        assert resp.status_code == 200, resp.text

    def can_save(self):
        return self.client.get("/v1/capabilities").json()["can_save"]

    def journal_path(self):
        return cs.scope_dir(self.state_dir, self.target, SCOPE) / ls.JOURNAL_FILENAME

    def splash_calls(self):
        return {k: v for k, v in self.ctx.calls.items() if k.startswith("splash.")}


@pytest.fixture
def w(monkeypatch, tmp_path, template):
    return World(monkeypatch, tmp_path, template)


# --------------------------------------------------------------------------------------------- before a publication


def test_before_the_first_publication_save_is_the_typed_501_and_can_save_is_false(w):
    assert isinstance(w.provider, ls.LocalSaveProvider) and w.provider.target == w.target
    assert w.service.tiled_save is None                       # nothing is bound at startup
    assert not w.provider.available() and not w.service.save_available()
    with pytest.raises(ls.SaveUnavailable) as exc:
        w.provider.resolve()
    assert exc.value.reason == "no_publication" and exc.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert str(exc.value).startswith(ls.UNAVAILABLE_INDICATOR)
    w.editable()
    assert w.can_save() is False
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "too early"})
    assert resp.status_code == 501, resp.text
    assert ls.UNAVAILABLE_INDICATOR in resp.json()["detail"] and "Traceback" not in resp.text
    assert not w.journal_path().exists()
    assert w.splash_calls() == {}, "no Splash call on :local"


def test_json_mode_keeps_its_read_only_400_whatever_the_publication_state(w):
    assert w.client.get("/settings").json()["graph_source"] == "json"
    assert w.can_save() is False
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "x"})
    assert resp.status_code == 400 and resp.json()["detail"] == "Editing node properties requires splash graph mode"


# --------------------------------------------------------------------------------------------- after a publication


def test_after_the_first_publication_the_target_resolves_from_the_pointer_without_a_restart(w):
    snap = w.freeze("v1", w.payload)
    pointer = w.publish("v1")
    assert pointer["snapshot_ref"] == f"{SCOPE}/v1"
    target = w.provider.resolve()
    assert (target.scope, target.base_snapshot_id) == (SCOPE, "v1")
    assert target.base_graph_path == snap / "graph.json"
    assert target.base_graph_sha256 == pointer["snapshot_sha256"] == hashlib.sha256((snap / "graph.json").read_bytes()).hexdigest()
    assert target.journal.path == w.journal_path()
    assert target.target == w.target and target.settings == w.settings
    assert w.provider.available() and w.service.save_available()
    backend = w.provider.backend()
    assert isinstance(backend, TiledSaveBackend) and w.provider.backend() is backend   # one lock per publication
    assert set(backend.base_graph()["things"][0]) >= {"id"}


def test_save_after_a_lazy_resolution_reads_back_on_tiled_and_journals_per_corpus(w):
    w.freeze("v1", w.payload)
    w.publish("v1")
    w.editable()                                            # the session graph becomes the effective record
    assert w.can_save() is True
    graph = w.client.get("/graph").json()
    assert {n["id"] for n in graph["nodes"]} >= {NODE, "matkg:Other"}
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "Term A edited lazily",
                                                        "description": "Saved after the first publication."})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == NODE and resp.json()["label"] == "Term A edited lazily"
    assert live_nodes(w.tiled())[NODE]["name"] == "Term A edited lazily"      # the Tiled effect exists (Q12)
    assert w.client.get(f"/graph/node/{NODE}").json()["label"] == "Term A edited lazily"
    records = [json.loads(line) for line in w.journal_path().read_text(encoding="utf-8").splitlines()]
    assert records[-1]["state"] == "applied" and records[-1]["scope"] == [SCOPE]
    assert records[-1]["base_snapshot_id"] == "v1" and records[-1]["actor"] == "local-operator"
    assert w.splash_calls() == {}, "no Splash call"
    # The import-time conflict block reads the same journal: the next publication of this corpus is refused
    # until the edit is reconciled or a reviewed resolution is attached (owner: that block is sufficient).
    changed = json.loads(json.dumps(w.payload))
    changed["things"][0]["description"] = "re-extracted"
    w.freeze("v2", changed)
    with pytest.raises(ContractError) as refused:
        w.ops.publication.deps.step.plan(w.ops.cli_context(), DirectorySnapshotSource(w.storage_kg).load(f"{SCOPE}/v2"),
                                         w.target, operation_id="plan-v2")
    assert refused.value.code is ErrorCode.CONFLICT and "lose" in str(refused.value)


def test_a_later_publication_is_resolved_anew(w):
    w.freeze("v1", w.payload)
    w.publish("v1")
    first = w.provider.backend()
    changed = json.loads(json.dumps(w.payload))
    changed["things"][0]["description"] = "version two"
    snap2 = w.freeze("v2", changed)
    w.publish("v2")
    target = w.provider.resolve()
    assert target.base_snapshot_id == "v2" and target.base_graph_path == snap2 / "graph.json"
    assert target.base_graph_sha256 == hashlib.sha256((snap2 / "graph.json").read_bytes()).hexdigest()
    assert w.provider.backend() is not first


# --------------------------------------------------------------------------------------------- refusals


def test_a_save_from_an_out_of_date_copy_is_stale_save_409(w):
    w.freeze("v1", w.payload)
    w.publish("v1")
    w.editable()
    row = live_nodes(w.mem)[NODE]
    w.mem.entities[row["id"]]["properties"]["description"] = "changed by another writer"
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "from a stale tab"})
    assert resp.status_code == 409, resp.text
    assert not w.journal_path().exists()
    backend = w.provider.backend()
    before = backend.base_graph()
    after = json.loads(json.dumps(before))
    next(t for t in after["things"] if t["id"] == NODE)["description"] = "direct"
    with pytest.raises(StaleSave):
        backend.save(w.ops.cli_context(), before, after)


def test_a_base_graph_that_no_longer_matches_its_recorded_hash_is_the_typed_conflict_never_500(w):
    snap = w.freeze("v1", w.payload)
    w.publish("v1")
    w.editable()
    (snap / "graph.json").write_text(json.dumps(w.payload, indent=1), encoding="utf-8")   # same graph, other bytes
    with pytest.raises(ContractError) as exc:
        w.provider.backend().base_graph()
    assert exc.value.code is ErrorCode.CONFLICT and "recorded hash" in str(exc.value)
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "on a tampered base"})
    assert resp.status_code == 502 and "Traceback" not in resp.text
    assert not w.journal_path().exists()


def test_a_pointer_outside_the_corpus_scope_is_unavailable(w):
    # The publication job itself refuses a corpus outside F2W_CORPUS_SCOPE, so the provider's check is defence in
    # depth: a pointer left by an earlier, differently scoped run must not become a Save target.
    w.freeze("v1", w.payload)
    w.publish("v1")
    scoped = DeploymentSettings(profile="local", writer_identity=WRITER, tiled_target=w.target,
                                corpus_scope=frozenset({"other"}))
    provider = ls.LocalSaveProvider(settings=scoped, managed=lp.managed_tiled_from(w.startup), target=w.target,
                                    storage_kg=w.storage_kg, state_dir=w.state_dir, store=w.ops.publication.store,
                                    clock=w.provider.clock)
    with pytest.raises(ls.SaveUnavailable) as exc:
        provider.resolve()
    assert exc.value.reason == "corpus_out_of_scope" and not provider.available()
    w.service.attach_tiled_save_provider(provider)
    w.editable()
    assert w.can_save() is False
    assert w.client.patch(f"/graph/node/{NODE}", json={"label": "x"}).status_code == 501
    assert not w.journal_path().exists()


def test_a_release_record_that_disagrees_with_the_pointer_or_a_missing_snapshot_is_unavailable(w):
    snap = w.freeze("v1", w.payload)
    w.publish("v1")
    release = cs.ReleaseState(cs.scope_dir(w.state_dir, w.target, SCOPE) / "release.json")
    record = release.read()
    release._write({**record, "live_snapshot_id": "v9"})
    with pytest.raises(ls.SaveUnavailable) as exc:
        w.provider.resolve()
    assert exc.value.reason == "release_mismatch"
    release._write(record)
    assert w.provider.available()
    (snap / "graph.json").unlink()
    with pytest.raises(ls.SaveUnavailable) as exc2:
        w.provider.resolve()
    assert exc2.value.reason == "snapshot_missing" and not w.provider.available()


# --------------------------------------------------------------------------------------------- the credential


def test_the_writer_credential_is_read_lazily_and_never_shown(monkeypatch, tmp_path, template, caplog):
    w = World(monkeypatch, tmp_path, template)
    monkeypatch.setattr(ls, "TiledMutationClient", RealTiledMutationClient)   # the real writer client (no socket yet)
    managed = lp.managed_tiled_from(w.startup)
    provider = ls.LocalSaveProvider(settings=w.settings, managed=managed, target=w.target, storage_kg=w.storage_kg,
                                    state_dir=w.state_dir, store=w.ops.publication.store, clock=w.provider.clock)
    assert provider._client._credential() == KEY            # the managed lifecycle's reader, called per request
    with caplog.at_level(logging.DEBUG):
        assert not provider.available()
        with pytest.raises(ls.SaveUnavailable) as exc:
            provider.resolve()
    shown = " ".join([repr(provider), repr(provider._client), str(exc.value), repr(exc.value), repr(w.provider),
                      *(r.getMessage() for r in caplog.records)])
    assert KEY not in shown
    w.freeze("v1", w.payload)
    w.publish("v1")
    target = provider.resolve()
    assert KEY not in repr(target) and KEY not in repr(provider.backend())
    assert KEY not in json.dumps(w.client.get("/v1/capabilities").json())


# --------------------------------------------------------------------------------------------- real Tiled


def test_wiring_against_a_real_loopback_tiled(monkeypatch, tmp_path, template, request):
    if tiled_python() is None:
        pytest.skip("F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")
    inst = request.getfixturevalue("tiled_seeded_pool").acquire()
    w = World(monkeypatch, tmp_path, template, instance=inst)
    w.editable()
    assert w.can_save() is False
    assert w.client.patch(f"/graph/node/{NODE}", json={"label": "too early"}).status_code == 501
    w.freeze("v1", w.payload)
    assert w.publish("v1")["snapshot_ref"] == f"{SCOPE}/v1"
    w.editable()
    assert w.can_save() is True
    resp = w.client.patch(f"/graph/node/{NODE}", json={"label": "Term A on the image's Tiled"})
    assert resp.status_code == 200, resp.text
    names = {e["name"] for e in inst.graphql("{ entities(limit: 500, offset: 0) { id name } }")["entities"]}
    assert "Term A on the image's Tiled" in names
    assert w.journal_path().exists() and w.splash_calls() == {}


# ------------------------------------------------------------- the corpus shown in the graph view (owner, 2026-11-09)

OTHER = "othercorpus"


def freeze_as(w, corpus, snapshot_id, payload):
    """Freeze ``payload`` as ``<corpus>/<snapshot_id>`` (a second corpus next to ``contract``)."""
    src = snapshot_with_graph(w.template, w.tmp / "snap" / corpus / snapshot_id, corpus, snapshot_id, payload)
    shutil.copytree(src, w.storage_kg / corpus / snapshot_id)


def publish_as(w, corpus, snapshot_id="v1"):
    ctx = w.ops.cli_context()
    handle = w.ops.submit_publication(ctx, f"{corpus}/{snapshot_id}", w.target)
    assert w.ops.jobs.work_one("w1") == (handle.job_id, JobState.AWAITING_REVIEW)
    w.ops.approve_publication(ctx, handle.job_id, w.ops.current_plan(ctx, handle.job_id).plan_sha256)
    assert w.ops.jobs.work_one("w1") == (handle.job_id, JobState.SUCCEEDED)


def renamed(payload, prefix):
    """The payload with every node id prefixed (so a node of one corpus is not a node of the other)."""
    data = json.loads(json.dumps(payload))
    rename = {t["id"]: f"{prefix}:{t['id'].split(':', 1)[-1]}" for t in data["things"]}
    for t in data["things"]:
        t["id"] = rename[t["id"]]
    for a in data.get("associations", []):
        a["subject"], a["object"] = rename.get(a["subject"], a["subject"]), rename.get(a["object"], a["object"])
    return data


def journal_records(w, corpus):
    path = cs.scope_dir(w.state_dir, w.target, corpus) / ls.JOURNAL_FILENAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def sources(w):
    return {s["id"]: s for s in w.client.get("/v1/graph/sources").json()["sources"]}


def test_before_a_publication_a_tiled_source_is_read_only_not_501(w):
    w.editable()
    resp = w.client.patch(f"/graph/node/{NODE}", params={"source": f"tiled:{SCOPE}"}, json={"label": "x"})
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "read_only_source"
    assert w.client.get("/v1/graph/sources").json()["tiled"]["status"] == "ok"
    assert w.client.patch(f"/graph/node/{NODE}", params={"corpus": SCOPE}, json={"label": "x"}).status_code == 501


def test_a_tiled_source_whose_corpus_is_live_is_editable_and_save_edits_that_corpus(w):
    w.freeze("v1", w.payload)
    w.publish("v1")
    freeze_as(w, OTHER, "v1", renamed(w.payload, OTHER))
    publish_as(w, OTHER)
    assert w.provider.resolve().scope == OTHER, "the pointer is 'last activated': the editable default shows B"
    w.editable()
    listed = sources(w)
    assert listed[f"tiled:{SCOPE}"]["read_only"] is False and listed[f"tiled:{OTHER}"]["read_only"] is False
    assert all(s["read_only"] is True for sid, s in listed.items() if sid.startswith("json:"))
    shown_before = w.client.get("/graph").json()
    assert {n["id"] for n in shown_before["nodes"]} >= {f"{OTHER}:TermA"}

    # Save with the Tiled source of corpus A shown: A's live version is edited, A's journal records it.
    resp = w.client.patch(f"/graph/node/{NODE}", params={"source": f"tiled:{SCOPE}"},
                          json={"label": "Term A via its source"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == NODE and resp.json()["label"] == "Term A via its source"
    assert live_nodes(w.tiled())[NODE]["name"] == "Term A via its source"
    assert journal_records(w, SCOPE)[-1]["scope"] == [SCOPE] and journal_records(w, OTHER) == []
    # The Tiled source shows the edit at once; the editable default (corpus B) is untouched.
    picked = w.client.get("/graph", params={"source": f"tiled:{SCOPE}"}).json()
    assert {n["id"]: n for n in picked["nodes"]}[NODE]["label"] == "Term A via its source"
    assert w.client.get("/graph").json() == shown_before
    assert w.splash_calls() == {}

    # The node of corpus A is not a node of corpus B's source (the legacy 404), and vice versa.
    assert w.client.patch(f"/graph/node/{NODE}", params={"source": f"tiled:{OTHER}"},
                          json={"label": "wrong corpus"}).status_code == 404
    resp = w.client.patch(f"/graph/node/{OTHER}:TermA", params={"source": f"tiled:{OTHER}"},
                          json={"label": "Term A of B"})
    assert resp.status_code == 200, resp.text
    assert journal_records(w, OTHER)[-1]["scope"] == [OTHER]
    assert {n["id"]: n for n in w.client.get("/graph").json()["nodes"]}[f"{OTHER}:TermA"]["label"] == "Term A of B"

    # The explicit corpus parameter names a live corpus directly; it must agree with a source when both are given.
    resp = w.client.patch(f"/graph/node/{NODE}", params={"corpus": SCOPE}, json={"label": "Term A by corpus"})
    assert resp.status_code == 200 and live_nodes(w.tiled())[NODE]["name"] == "Term A by corpus"
    assert w.client.patch(f"/graph/node/{NODE}", params={"source": f"tiled:{SCOPE}", "corpus": OTHER},
                          json={"label": "x"}).status_code == 400
    assert w.client.patch(f"/graph/node/{NODE}", params={"corpus": "../etc"}, json={"label": "x"}).status_code == 400
    assert w.client.patch(f"/graph/node/{NODE}", params={"corpus": "neverlive"},
                          json={"label": "x"}).status_code == 501
    assert w.can_save() is True


def test_json_and_non_live_tiled_sources_stay_read_only(w):
    w.freeze("v1", w.payload)
    w.publish("v1")
    w.editable()
    listed = sources(w)
    for sid in [s for s in listed if s.startswith("json:")] + ["tiled:notlive", "tiled:../x"]:
        resp = w.client.patch(f"/graph/node/{NODE}", params={"source": sid}, json={"label": "x"})
        assert resp.status_code == 400 and resp.json()["detail"]["code"] == "read_only_source", sid
    assert journal_records(w, SCOPE) == [] and live_nodes(w.tiled())[NODE]["name"] != "x"
