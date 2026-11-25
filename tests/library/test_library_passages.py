"""``library_passages``: find supporting text for an existing KG bundle from generated PDFs (no model, no network)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.graph import kg_update
from app.modules.library import bundles as B
from app.modules.library import passages_job as PJ
from tests.jobs.jobs_fakes import Clock, Crash
from tests.library.passages_fixtures import S_DUP, S_ORIENT, S_PURITY, S_PURITY_B, World, make_pdf, term


def located(record: dict, paper: str) -> list[dict]:
    return [s for s in record["context_snippets"] if s["source_paper"] == paper and s.get("kind") == "located"]


def test_end_to_end_attaches_located_passages_with_the_right_pages(tmp_path):
    world = World(tmp_path)
    handle, state = world.run()
    assert state is JobState.SUCCEEDED
    terms = world.terms()
    (a,) = located(terms["domain purity"], "a.pdf")
    assert a["page"] == 1 and a["verified"] is True and a["text"] == S_PURITY
    (b,) = located(terms["domain purity"], "b.pdf")
    assert b["page"] == 1 and b["text"] == S_PURITY_B
    (o,) = located(terms["face-on orientation"], "a.pdf")
    assert o["page"] == 2 and o["text"] == S_ORIENT
    assert terms["unfindable notion"]["context_snippets"] == []     # nothing invented
    # the KG was rebuilt from the new terms: the node carries the passages with their kind
    kg = json.loads(world.bundle().file(B.KG_FILE).read_text())
    node = next(n for n in kg["things"] if n["name"] == "domain purity")
    assert {s["source_paper"] for s in node["context_snippets"]} == {"a.pdf", "b.pdf"}
    assert all(s["kind"] == "located" for s in node["context_snippets"])
    # manifest: per-paper entries (absolute for a KG that had none) and the recovery info
    manifest = world.bundle().manifest
    assert manifest["passages"]["a.pdf"] == {"terms": 3, "with_passage": 2, "quoted": 0, "located": 2, "rejected": 0,
                                             "uncovered": 1}
    assert manifest["passages"]["b.pdf"]["located"] == 1 and "blank.pdf" not in manifest["passages"]
    recovery = manifest["recovery"]
    assert recovery["located_added"] == 3 and recovery["terms_newly_covered"] == 3 and recovery["papers_skipped"] == 2
    # terms.json metadata carries the same coverage for later extractions
    meta = json.loads(world.bundle().file(B.TERMS_FILE).read_text())["metadata"]
    assert meta["passages"]["a.pdf"]["located"] == 2 and meta["processed_files"] == 3


def test_result_block_reports_every_paper_with_reasons(tmp_path):
    world = World(tmp_path)
    handle, _ = world.run()
    units = world.units(handle)
    assert units["a.pdf"]["result"]["recovery"] == {"terms": 3, "with_passage_before": 0, "located_added": 2,
                                                     "newly_covered": 2, "still_uncovered": 1}
    assert units["b.pdf"]["result"]["recovery"]["located_added"] == 1
    assert units["blank.pdf"]["result"]["recovery"]["reason"] == PJ.REASON_NO_TEXT
    assert units["gone.pdf"]["result"]["recovery"]["reason"] == PJ.REASON_MISSING
    block = PJ.recovery_block({k: v["result"]["recovery"] for k, v in units.items()})
    assert block["total"] == {"terms": 4, "with_passage_before": 0, "located_added": 3, "newly_covered": 3,
                              "still_uncovered": 1, "papers_processed": 2, "papers_skipped": 2}


def test_a_second_run_adds_nothing_and_changes_no_file(tmp_path):
    world = World(tmp_path)
    world.run()
    files = {n: world.bundle().file(n).read_bytes() for n in (B.TERMS_FILE, B.KG_FILE)}
    manifest_before = world.bundle().manifest["passages"]
    handle, state = world.run(nonce="2")
    assert state is JobState.SUCCEEDED
    assert {n: world.bundle().file(n).read_bytes() for n in (B.TERMS_FILE, B.KG_FILE)} == files
    assert world.bundle().manifest["passages"] == manifest_before
    assert world.bundle().manifest["recovery"]["located_added"] == 0
    assert world.units(handle)["a.pdf"]["result"]["recovery"]["located_added"] == 0


def test_existing_passages_are_kept_and_never_overwritten(tmp_path):
    quoted = {"text": "A quoted sentence the model gave.", "source_paper": "a.pdf", "page": 1, "verified": True}
    world = World(tmp_path, extra_terms=[term("kept term", "a.pdf", snippets=[quoted])])
    world.run()
    assert world.terms()["kept term"]["context_snippets"] == [quoted]      # has a passage from a.pdf: untouched
    entry = world.bundle().manifest["passages"]["a.pdf"]
    assert entry["quoted"] == 1 and entry["with_passage"] == 3 and entry["terms"] == 4


def test_a_term_with_text_from_another_paper_gets_the_missing_paper_added_after_the_old_one(tmp_path):
    first = {"text": "From a.pdf originally here.", "source_paper": "a.pdf", "page": 1}
    (tmp_path / "w2").mkdir()
    world2 = World(tmp_path / "w2")
    snippets = json.loads(world2.bundle().file(B.TERMS_FILE).read_text())
    snippets["terms"][0]["context_snippets"] = [first]
    (world2.bundle().file(B.TERMS_FILE)).write_text(json.dumps(snippets))
    world2.run()
    got = world2.terms()["domain purity"]["context_snippets"]
    assert got[0] == first and [s["source_paper"] for s in got[1:]] == ["b.pdf"]


def test_an_ambiguous_name_is_reported_not_guessed_and_the_manifest_sha_settles_a_tie(tmp_path):
    world = World(tmp_path, extra_terms=[term("detector distance", "dup.pdf")])
    make_pdf(world.lib / "x" / "dup.pdf", [[S_DUP]])
    make_pdf(world.lib / "y" / "dup.pdf", [[S_DUP + " Another copy of it."]])
    world.service.refresh(max_probe=0)
    handle, _ = world.run()
    assert world.units(handle)["dup.pdf"]["result"]["recovery"]["reason"] == PJ.REASON_AMBIGUOUS
    assert world.terms()["detector distance"]["context_snippets"] == []
    # the manifest records the bytes of x/dup.pdf: now the tie is settled
    sha = next(r["sha256"] for r in world.service.catalog.rows() if r["relpath"] == "x/dup.pdf")
    manifest = dict(world.bundle().manifest)
    manifest["papers"] = [*manifest["papers"], {"name": "dup.pdf", "sha256": sha, "pages": 1}]
    B._write_json(world.bundle().directory / B.MANIFEST_FILE, manifest)
    handle, _ = world.run(nonce="2")
    assert world.units(handle)["dup.pdf"]["result"]["recovery"]["located_added"] == 1
    assert world.terms()["detector distance"]["context_snippets"][0]["text"] == S_DUP


def test_a_failed_terms_write_leaves_the_old_files(tmp_path, monkeypatch):
    world = World(tmp_path)
    before = {n: world.bundle().file(n).read_bytes() for n in (B.TERMS_FILE, B.KG_FILE)}

    def boom(data, handle, **kw):
        handle.write("{ partial")
        raise OSError("disk full")

    monkeypatch.setattr(PJ.json, "dump", boom)
    handle, state = world.run()
    monkeypatch.undo()
    assert state is JobState.FAILED
    assert {n: world.bundle().file(n).read_bytes() for n in (B.TERMS_FILE, B.KG_FILE)} == before
    assert not [p for p in world.bundle().directory.iterdir() if p.name.endswith(".tmp")]
    assert "passages" not in world.bundle().manifest


def test_a_failed_kg_rebuild_leaves_the_old_kg(tmp_path, monkeypatch):
    world = World(tmp_path)
    kg_before = world.bundle().file(B.KG_FILE).read_bytes()

    def broken(terms_json, kg_json):
        Path(kg_json).write_text("{ half a file")
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(kg_update, "rebuild_kg", broken)
    _, state = world.run()
    monkeypatch.undo()
    assert state is JobState.FAILED
    assert world.bundle().file(B.KG_FILE).read_bytes() == kg_before
    assert not [p for p in world.bundle().directory.iterdir() if p.name.endswith(".tmp")]
    # the terms were written paper by paper and are kept: the next run only has to rebuild
    assert located(world.terms()["domain purity"], "a.pdf")
    _, state = world.run(nonce="2")
    assert state is JobState.SUCCEEDED
    assert any(n["name"] == "domain purity" and n["context_snippets"]
               for n in json.loads(world.bundle().file(B.KG_FILE).read_text())["things"])


def test_cancel_stops_between_papers_keeps_the_finished_ones_and_rebuilds_the_kg(tmp_path, monkeypatch):
    world = World(tmp_path)
    holder = {}
    original = world.recovery.resolve_pdf

    def watching(name, sha):
        if name == "b.pdf":
            world.jobs.store.request_stop(holder["id"], actor="op")
        return original(name, sha)

    monkeypatch.setattr(world.recovery, "resolve_pdf", watching)
    handle = world.submit()
    holder["id"] = handle.job_id
    assert world.jobs.work_one("w") == (handle.job_id, JobState.CANCELLED)
    terms = world.terms()
    assert located(terms["domain purity"], "a.pdf") and not located(terms["domain purity"], "b.pdf")
    node = next(n for n in json.loads(world.bundle().file(B.KG_FILE).read_text())["things"] if n["name"] == "domain purity")
    assert node["context_snippets"]                       # the KG shows what finished


def test_a_killed_worker_resumes_without_redoing_finished_papers(tmp_path, monkeypatch):
    clock = Clock()
    world = World(tmp_path, clock=clock)
    calls: list[str] = []
    original = world.recovery.resolve_pdf
    crashed: list[int] = []

    def counting(name, sha):
        calls.append(name)
        if name == "b.pdf" and not crashed:
            crashed.append(1)
            raise Crash
        return original(name, sha)

    monkeypatch.setattr(world.recovery, "resolve_pdf", counting)
    handle = world.submit()
    with pytest.raises(Crash):
        world.jobs.work_one("w")
    first = list(calls)
    clock.advance(500)
    job_id, state = world.jobs.work_one("w2")
    assert (job_id, state) == (handle.job_id, JobState.SUCCEEDED)
    assert first[:2] == ["a.pdf", "b.pdf"] and calls.count("a.pdf") == 1      # a.pdf was not redone
    assert located(world.terms()["domain purity"], "b.pdf")
    assert world.bundle().manifest["passages"]["a.pdf"]["located"] == 2        # kept from before the kill


def test_the_start_check_refuses_with_typed_errors(tmp_path):
    world = World(tmp_path)
    r = world.recovery
    with pytest.raises(ContractError) as err:
        r.check_startable("nope")
    assert err.value.code is ErrorCode.NOT_FOUND
    world.store.create("empty", schema=world.bundle().manifest["schema"], terms={"metadata": {}, "terms": [], "code_snippets": []})
    with pytest.raises(ContractError) as err:
        r.check_startable("empty")
    assert (err.value.code, err.value.reason) == (ErrorCode.INVALID_INPUT, PJ.REASON_NO_PAPERS)
    with pytest.raises(ContractError) as err:
        r.check_startable("kg1", open_jobs=[("library_passages", "job-1")])
    assert (err.value.code, err.value.reason, err.value.retryable) == (ErrorCode.CONFLICT, PJ.REASON_IN_PROGRESS, False)
    assert r.check_startable("kg1") == {"name": "kg1", "papers": 4}
    from app.modules.library.extraction import bundle_lock

    with bundle_lock(world.store, "kg1", "job-x"):
        assert PJ.bundle_is_locked(world.store, "kg1")
        with pytest.raises(ContractError) as err:
            r.check_startable("kg1")
        assert err.value.reason == PJ.REASON_BUSY
    assert not PJ.bundle_is_locked(world.store, "kg1")


def test_relation_evidence_hook_exists_and_does_nothing():
    assert PJ.recover_relation_evidence([], {}) == 0
