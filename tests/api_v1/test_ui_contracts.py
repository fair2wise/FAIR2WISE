"""UI contracts: every ``/v1`` request the UI makes, answered by the real routes and pinned for the UI's own parsers (Tier 2 D).

Four pilot bugs of 2026-11-25 had one cause: a test used an assumed shape, not the real one (``job_id`` versus ``id``, a bare-hex run
id versus ``job-...``, a dialog's position, an image reference). This module drives the real ``:local`` app (``build_local``, a
library of generated PDFs, a user schema, two bundles, a harvest run, finished jobs, a staged publication) through the routes the
UI reads and records the real answers, with nothing hand-written, in ``ui/src/app/components/__fixtures__/ui_contracts.json``
(``F2W_WRITE_FIXTURES=1`` writes it; a normal run compares the live answer's shape with the pinned one). The companion
``ui/src/app/components/uiContracts.test.ts`` feeds every pinned answer to the UI's parser for that route and fails when the
parser falls back to a default (an empty id, an unknown kind, a missing field).

Routes the UI reaches that are not ``/v1`` (``/chat``, ``/graph``, ``/settings``, ``/sessions``) are pinned byte for byte by
``tests/contracts/legacy_ui``. ``GET /v1/ollama/models`` and the ``GET /v1/operations/*`` routes need their own fakes and are covered by their route tests;
the Ollama listing is pinned here as the local app answers it.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

from _v1_support import build_local

from app.modules.contracts.jobs import JobState
from app.modules.jobs.keys import OperationInputs
from app.modules.library import bundles as B
from app.modules.library.harvest import Candidate, Harvester
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService
from app.modules.literature.openalex import QuotaBreaker
from tests.api_v1.test_v1_approvals import _stage_plan
from tests.api_v1.test_v1_library_schemas_ui_fixtures import same_shape
from tests.library.passages_fixtures import PAPERS, SCHEMA, make_pdf, term
from tests.library.test_library_service import FakeProbe, pdf
from tests.library.test_relation_repairs import SCHEMA as USER_SCHEMA_TEXT
from tests.library.test_relations_job import plecos_terms
from tests.security.security_fakes import local_settings

FIXTURE = Path(__file__).resolve().parents[2] / "ui" / "src" / "app" / "components" / "__fixtures__" / "ui_contracts.json"


def _world(monkeypatch, tmp_path: Path) -> dict[str, Any]:
    """The seeded ``:local`` app: everything the UI's routes need to answer with real content."""
    lib = tmp_path / "papers"
    lib.mkdir()
    for name, pages in PAPERS.items():
        make_pdf(lib / name, pages)
    service = LibraryService(lib, tmp_path / "state" / "library" / "catalog.sqlite", settings=local_settings(),
                             probe=FakeProbe(default=ProbeResult(status="ok", pages=2, page_text="x", title="A real title")),
                             breaker=QuotaBreaker())
    service.refresh(max_probe=0)
    app = build_local(monkeypatch, tmp_path, library=service)
    c = app.client()
    kg = app.app.state.f2w_kg_library
    saved = kg.user_schemas.save("plecos", USER_SCHEMA_TEXT)
    store: B.BundleStore = app.app.state.f2w_library_passages.store
    store.create("kg1", schema=SCHEMA, model="m", backend="cborg", papers=[B.PaperRef("a.pdf", "x", 1)],
                 terms={"metadata": {}, "code_snippets": [], "terms": [term("domain purity", "a.pdf", "b.pdf"), term("face-on orientation", "a.pdf")]})
    store.create("plecos", schema={"id": saved.id, "version": str(saved.version), "sha256": saved.sha256}, terms=plecos_terms(),
                 papers=[B.PaperRef("a.pdf", "x", 1)])
    harvester: Harvester = app.app.state.f2w_harvest
    run_id = OperationInputs(corpus_scope=("rsoxs",), params={"topic": "rsoxs"}).job_identity("library_harvest")
    harvester.store.create_run(run_id, topic="resonant soft x-ray scattering", category="rsoxs", schema=None, sources=["openalex"], max_downloads=5)
    cid = harvester.store.save_candidate(run_id, Candidate(title="Paywalled study", source="openalex", year=2020, doi="10.1/p",
                                                           landing_url="https://doi.org/10.1/p"), status="paywalled")
    harvester.store.save_candidate(run_id, Candidate(title="Fetched study", source="openalex", year=2021, doi="10.1/f"), status="downloaded")
    # Finished jobs: supporting text for kg1, a relation repair for plecos.
    passages_job = c.post("/v1/library/kgs/kg1/passages", json={}).json()["job_id"]
    assert app.work()[1] is JobState.SUCCEEDED
    relations_job = c.post("/v1/library/kgs/plecos/relations", json={}).json()["job_id"]
    assert app.work()[1] is JobState.SUCCEEDED
    plan_job = _stage_plan(app)
    upload = c.post("/v1/library/papers?folder=rsoxs", files={"file": ("new.pdf", io.BytesIO(pdf("new")), "application/pdf")}).json()
    return {"app": app, "c": c, "run_id": run_id, "cid": cid, "passages_job": passages_job, "relations_job": relations_job,
            "plan_job": plan_job, "paper_id": upload["paper"]["id"], "schema_id": saved.id}


def _requests(w: dict[str, Any]) -> list[tuple[str, str, str, dict[str, Any] | None]]:
    """``(contract id, method, path, json body)`` for every /v1 request the UI makes (``paths`` in the UI's *Api.ts modules)."""
    j, r = w["passages_job"], w["relations_job"]
    return [
        ("capabilities", "GET", "/v1/capabilities", None),
        ("approvals.pending", "GET", "/v1/approvals/pending", None),
        ("jobs.list", "GET", "/v1/jobs?detail=summary&limit=5", None),
        ("jobs.list.kind", "GET", f"/v1/jobs?kind=library_passages&detail=summary&limit=5", None),
        ("jobs.one", "GET", f"/v1/jobs/{j}", None),
        ("jobs.progress", "GET", f"/v1/jobs/{j}/progress", None),
        ("jobs.progress.relations", "GET", f"/v1/jobs/{r}/progress", None),
        ("jobs.logs", "GET", f"/v1/jobs/{j}/logs?after=-1", None),
        ("jobs.cancel.finished", "POST", f"/v1/jobs/{j}/cancel", None),
        ("library.status", "GET", "/v1/library", None),
        ("library.papers", "GET", "/v1/library/papers", None),
        ("library.papers.folder", "GET", "/v1/library/papers?folder=rsoxs", None),
        ("library.categories", "GET", "/v1/library/categories", None),
        ("library.verify", "POST", f"/v1/library/papers/{w['paper_id']}/verify", None),
        ("library.kgs", "GET", "/v1/library/kgs", None),
        ("library.schemas", "GET", "/v1/library/schemas", None),
        ("library.models", "GET", "/v1/library/models", None),
        ("library.estimate", "POST", "/v1/library/estimate", {"paper_ids": [w["paper_id"]], "model": "lbl/cborg-chat", "schema": SCHEMA["id"]}),
        ("library.kgs.export", "POST", "/v1/library/kgs/kg1/export", {"confirm": True}),
        ("library.kgs.publish.preview", "POST", "/v1/library/kgs/kg1/publish", {"confirm": False}),
        ("library.kgs.continue", "POST", "/v1/library/kgs/continue", {"name": "kg1_child", "parent": "kg1"}),
        ("library.kgs.passages.start", "POST", "/v1/library/kgs/kg1_child/passages", {}),
        ("library.kgs.relations.refused", "POST", "/v1/library/kgs/kg1/relations", {}),
        ("harvest.runs", "GET", "/v1/library/harvest/runs", None),
        ("harvest.candidates", "GET", f"/v1/library/harvest/runs/{w['run_id']}/candidates", None),
        ("harvest.dismiss", "POST", f"/v1/library/harvest/candidates/{w['cid']}/dismiss", None),
        ("harvest.undo.refused", "POST", f"/v1/library/harvest/candidates/{w['cid']}/undo", None),
        ("schemas.validate", "POST", "/v1/library/schemas/validate", {"text": USER_SCHEMA_TEXT}),
        ("schemas.insert_core", "POST", "/v1/library/schemas/insert-core", {"text": "id: https://example.org/user/x\nname: x\n"}),
        ("schemas.user.list", "GET", "/v1/library/schemas/user", None),
        ("schemas.user.read", "GET", f"/v1/library/schemas/user/{w['schema_id'].removeprefix('user/')}", None),   # as the UI sends it
        ("schemas.user.diff", "GET", "/v1/library/schemas/user/plecos/diff?from=1&to=1", None),
        ("schemas.save.invalid", "POST", "/v1/library/schemas", {"name": "eels", "text": USER_SCHEMA_TEXT}),
        ("schemas.generate.cancel", "POST", "/v1/library/schemas/generate/cancel", {"request_id": "ui-contract"}),
        ("judge_model", "GET", "/v1/validation/judge-model", None),
        ("graph.sources", "GET", "/v1/graph/sources", None),
        ("ollama.models", "GET", "/v1/ollama/models", None),
    ]


def _record(w: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cid, method, path, body in _requests(w):
        resp = w["c"].request(method, path, json=body) if body is not None else w["c"].request(method, path)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"_text": resp.text[:500]}
        out[cid] = {"method": method, "path": path.split("?")[0], "status": resp.status_code, "body": _stable(payload)}
    return out


def _stable(payload: Any) -> Any:  # noqa: ANN401
    """Lists of records in a deterministic order (kind, state, name, id), so the shape of ``[0]`` does not depend on job order."""
    if isinstance(payload, dict):
        return {k: _stable(v) for k, v in payload.items()}
    if isinstance(payload, list) and payload and all(isinstance(item, dict) for item in payload):
        def key(item: dict[str, Any]) -> tuple[str, ...]:
            return tuple(str(item.get(k, "")) for k in ("kind", "state", "name", "id", "job_id"))
        return [_stable(item) for item in sorted(payload, key=key)]
    return payload


def test_every_ui_request_is_answered_by_the_real_route_and_the_pinned_shape_matches(monkeypatch, tmp_path):
    w = _world(monkeypatch, tmp_path)
    live = _record(w)
    expected_ok = {cid for cid, *_ in _requests(w)} - {
        "library.kgs.relations.refused", "harvest.undo.refused", "schemas.save.invalid", "jobs.cancel.finished",
        "ollama.models", "graph.sources"}
    for cid in expected_ok:
        assert live[cid]["status"] == 200, (cid, live[cid])
    assert live["library.kgs.relations.refused"]["status"] == 422 and live["library.kgs.relations.refused"]["body"]["reason"] == "not_user_schema"
    assert live["schemas.save.invalid"]["status"] == 422 and live["schemas.save.invalid"]["body"]["reason"] == "schema_invalid"
    if os.environ.get("F2W_WRITE_FIXTURES") == "1":
        FIXTURE.write_text(json.dumps(live, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    pinned = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(pinned) == set(live), sorted(set(pinned) ^ set(live))
    for cid, entry in live.items():
        assert entry["status"] == pinned[cid]["status"], (cid, entry["status"], pinned[cid]["status"])
        same_shape(entry["body"], pinned[cid]["body"], f"$.{cid}")
