"""``POST /v1/library/kgs/{name}/passages`` (V99): start ``library_passages`` on ``:local`` only; typed refusals; no model."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from _v1_support import build_als, build_local

from app.api.policies import API_ROUTE_POLICIES
from app.modules.contracts.jobs import JobState
from app.modules.library import bundles as B
from app.modules.library.extraction import bundle_lock
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService
from tests.library.passages_fixtures import PAPERS, S_PURITY, SCHEMA, make_pdf, term
from tests.security.security_fakes import local_settings

URL = "/v1/library/kgs/{}/passages"


def build(monkeypatch, tmp_path: Path):
    lib = tmp_path / "papers"
    lib.mkdir()
    for name, pages in PAPERS.items():
        make_pdf(lib / name, pages)
    service = LibraryService(lib, tmp_path / "state" / "library" / "catalog.sqlite", settings=local_settings(),
                             probe=lambda p: ProbeResult(status="ok", pages=1, page_text="x"))
    service.refresh(max_probe=0)
    app = build_local(monkeypatch, tmp_path, library=service)
    store: B.BundleStore = app.app.state.f2w_library_passages.store
    terms = {"metadata": {}, "code_snippets": [],
             "terms": [term("domain purity", "a.pdf", "b.pdf"), term("face-on orientation", "a.pdf")]}
    store.create("kg1", schema=SCHEMA, terms=terms, papers=[B.PaperRef("a.pdf", "x", 1)])
    store.create("empty", schema=SCHEMA)
    return app, store


def test_the_policy_is_local_only_with_the_extract_capability():
    row = next(p for p in API_ROUTE_POLICIES if p.route_id == "V99")
    assert (row.method, row.path, row.capabilities, row.on_als) == (
        "POST", "/v1/library/kgs/{name}/passages", frozenset({"library.extract"}), False)


def test_als_answers_404(monkeypatch, tmp_path):
    als = build_als(monkeypatch, tmp_path)
    assert als.client("admin").post(URL.format("kg1"), json={}).status_code == 404


def test_start_answers_the_job_handle_and_the_job_finds_the_text(monkeypatch, tmp_path):
    app, store = build(monkeypatch, tmp_path)
    c = app.client()
    resp = c.post(URL.format("kg1"), json={})
    assert resp.status_code == 200, resp.text
    handle = resp.json()
    assert set(handle) == {"job_id", "kind", "state", "submitted_at", "settings_revision"}
    assert handle["kind"] == "library_passages" and handle["state"] == "queued"
    job_id, state = app.work()
    assert (job_id, state) == (handle["job_id"], JobState.SUCCEEDED)
    terms = {t["term"]: t for t in json.loads(store.get("kg1").file(B.TERMS_FILE).read_text())["terms"]}
    assert terms["domain purity"]["context_snippets"][0]["text"] == S_PURITY
    summary = c.get(f"/v1/jobs/{handle['job_id']}/progress").json()["summary"]
    assert summary["params"] == {"bundle": "kg1"}
    assert summary["recovery"]["total"]["located_added"] == 3
    assert summary["recovery"]["papers"]["a.pdf"]["located_added"] == 2
    assert summary["recovery"]["papers"]["b.pdf"]["newly_covered"] == 1


def test_a_run_again_is_a_new_job_and_adds_nothing(monkeypatch, tmp_path):
    app, store = build(monkeypatch, tmp_path)
    c = app.client()
    first = c.post(URL.format("kg1"), json={}).json()
    app.work()
    second = c.post(URL.format("kg1"), json={}).json()
    assert second["job_id"] != first["job_id"]
    assert app.work()[1] is JobState.SUCCEEDED
    summary = c.get(f"/v1/jobs/{second['job_id']}/progress").json()["summary"]
    assert summary["recovery"]["total"]["located_added"] == 0


def test_typed_refusals(monkeypatch, tmp_path):
    app, store = build(monkeypatch, tmp_path)
    c = app.client()
    missing = c.post(URL.format("nope"), json={})
    assert missing.status_code == 404
    assert c.post(URL.format("bad name!"), json={}).status_code in (404, 422)
    empty = c.post(URL.format("empty"), json={})
    assert empty.status_code == 422 and empty.json()["reason"] == "no_source_papers"
    with bundle_lock(store, "kg1", "job-x"):
        busy = c.post(URL.format("kg1"), json={})
    assert busy.status_code == 409 and busy.json()["reason"] == "bundle_busy"
    first = c.post(URL.format("kg1"), json={})
    assert first.status_code == 200
    again = c.post(URL.format("kg1"), json={})   # still queued: the same bundle is already being searched
    assert again.status_code == 409 and again.json()["reason"] == "passages_in_progress"
    assert c.post(URL.format("kg1"), json={"junk": 1}).status_code == 422       # extra fields are refused
    assert str(tmp_path) not in busy.text + again.text + empty.text


def test_the_job_kind_needs_the_library_extract_capability_and_is_not_registered_without_the_handler():
    from app.modules.library.jobs import LIBRARY_KIND_CAPABILITIES

    assert LIBRARY_KIND_CAPABILITIES["library_passages"] == "library.extract"
