"""``library_relations``: repair the relations of a bundle on a user schema, no model (owner decision 2026-11-25).

The plecos bundle holds ``Measurement -has_measurement-> Species`` (valid only reversed) and ``Locality -country-> "Brazil"``
(a value filed as a link). The job swaps the first, turns the second into a property, reports counts per pattern, keeps one
previous generation of the files, rebuilds the KG and changes nothing on a second run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.jobs import JobService, JobStore
from app.modules.library import bundles as B
from app.modules.library import library_inputs, register_library_jobs
from app.modules.library import relations_job as RJ
from app.modules.library.handlers import library_handlers
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService
from app.modules.term_extractor.schema import SchemaHelper
from tests.library.test_relation_repairs import SCHEMA as SCHEMA_TEXT
from tests.security.security_fakes import local_settings

USER_SCHEMA = {"id": "user/plecos@v1", "version": "1", "sha256": "abc"}
SHIPPED = {"id": "matkg_schema@0.3.1", "version": "0.3.1", "sha256": "x", "path": "storage/schema/v0.3.1/matkg_schema.yaml"}


def term(name: str, category: str, relations: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"term": name, "definition": f"{name} defined.", "category": category, "pages": [1], "source_papers": ["a.pdf"],
            "context_snippets": [], "relations": list(relations or []), **extra}


def plecos_terms() -> dict[str, Any]:
    evidence = [{"text": "Ancistrus reaches 530 mm in total length.", "source_paper": "a.pdf", "page": 2, "kind": "located", "verified": True}]
    return {"metadata": {}, "code_snippets": [], "terms": [
        term("Ancistrus", "Species"),
        term("530 mm", "Measurement", [{"relation": "has_measurement", "related_term": "Ancistrus", "verified": False, "evidence": evidence}]),
        term("Rio Mogi-Guacu", "Locality", [{"relation": "country", "related_term": "Brazil", "verified": True},
                                             {"relation": "water_body", "related_term": "Rio Mogi-Guacu", "verified": True},
                                             {"relation": "has_measurement", "related_term": "Ancistrus", "verified": False}]),
        term("Brazil", "Thing"),
        term("Orphan", "Species", [{"relation": "has_measurement", "related_term": "Not a term here", "verified": True},
                                   {"relation": "custom_pred", "related_term": "Ancistrus", "verified": False}]),
    ]}


@pytest.fixture
def helper(tmp_path):
    path = tmp_path / "plecos.yaml"
    path.write_text(SCHEMA_TEXT, encoding="utf-8")
    return SchemaHelper(str(path), sanitize_descriptions=True)


def test_repair_terms_swaps_reversed_relations_turns_value_slots_into_properties_and_counts_patterns(helper):
    doc = plecos_terms()
    new, report = RJ.repair_terms(doc, helper)
    assert doc == plecos_terms()   # the input is untouched
    by = {t["term"]: t for t in new["terms"]}
    assert by["530 mm"]["relations"] == []
    (moved,) = by["Ancistrus"]["relations"]
    assert (moved["relation"], moved["related_term"], moved["verified"]) == ("has_measurement", "530 mm", True)
    assert moved["evidence"][0]["text"].startswith("Ancistrus reaches")   # the evidence travelled with it
    place = by["Rio Mogi-Guacu"]
    assert [(p["property"], p["value"]) for p in place["properties"]] == [("country", "Brazil"), ("water_body", "Rio Mogi-Guacu")]
    assert [(r["relation"], r["verified"]) for r in place["relations"]] == [("has_measurement", False)]   # Locality->Species: invalid both ways
    orphan = by["Orphan"]["relations"]
    assert orphan[0]["verified"] is True and orphan[1] == {"relation": "custom_pred", "related_term": "Ancistrus", "verified": False}
    assert report["counts"] == {"swapped": 1, "attributes": 2, "invalid": 1, "unknown_object": 1, "unchanged": 0,
                                "relations": 6, "terms_changed": 3}
    assert report["patterns"][0] == {"outcome": "attributes", "relation": "country", "subject_class": "Locality", "object_class": "", "count": 1} \
        or {"outcome", "count"} <= set(report["patterns"][0])
    assert {(r["outcome"], r["relation"]) for r in report["patterns"]} == {
        ("attributes", "country"), ("attributes", "water_body"), ("swapped", "has_measurement"), ("invalid", "has_measurement"),
        ("unknown_object", "has_measurement")}
    # A second pass over the repaired document changes nothing.
    again, report2 = RJ.repair_terms(new, helper)
    assert again == new and report2["counts"]["swapped"] == 0 and report2["counts"]["attributes"] == 0


class World:
    """A bundle on the user schema with the plecos terms, and the job service with the repair handler."""

    def __init__(self, tmp: Path, *, schema: dict[str, Any] = USER_SCHEMA, terms: dict[str, Any] | None = None) -> None:
        lib = tmp / "papers"
        lib.mkdir()
        self.schema_file = tmp / "plecos.yaml"
        self.schema_file.write_text(SCHEMA_TEXT, encoding="utf-8")
        self.service = LibraryService(lib, tmp / "state" / "library" / "catalog.sqlite", settings=local_settings(),
                                      probe=lambda p: ProbeResult(status="ok", pages=1, page_text="x"))
        self.store = B.BundleStore(tmp / "state" / "kgs")
        self.calls: list[str] = []

        def schema_file_for(bundle: B.Bundle) -> Path:
            self.calls.append(bundle.name)
            if not bundle.schema.startswith("user/"):
                raise ContractError(ErrorCode.INVALID_INPUT, "shipped", reason=RJ.REASON_NOT_USER_SCHEMA)
            return self.schema_file

        self.repair = RJ.LibraryRelations(self.service, self.store, schema_file_for=schema_file_for, clock=lambda: "2026-11-26T00:00:00+00:00")
        settings = local_settings()
        self.jobs = JobService(JobStore(tmp / "jobs.sqlite3"), settings=settings)
        register_library_jobs(self.jobs, settings, handlers=library_handlers(self.service, relations=self.repair))
        self.store.create("kg1", schema=schema, model="m", backend="cborg", papers=[B.PaperRef("a.pdf", "sha", 1)],
                          terms=terms or plecos_terms())

    def run(self, nonce: str = "1") -> tuple[Any, Any]:
        handle = self.jobs.submit(self.service.worker_context("s"), "library_relations",
                                  library_inputs({"bundle": "kg1"}, nonce=nonce, corpus_scope=("kg1",)))
        job_id, state = self.jobs.work_one("w")
        assert job_id == handle.job_id
        return handle, state

    def bundle(self) -> B.Bundle:
        return self.store.get("kg1")

    def terms(self) -> dict[str, dict[str, Any]]:
        return {t["term"]: t for t in json.loads(self.bundle().file(B.TERMS_FILE).read_text())["terms"]}


def test_the_job_repairs_the_files_keeps_one_previous_generation_and_rebuilds_the_kg(tmp_path):
    world = World(tmp_path)
    before_terms = world.bundle().file(B.TERMS_FILE).read_bytes()
    before_kg = world.bundle().file(B.KG_FILE).read_bytes()
    handle, state = world.run()
    assert state is JobState.SUCCEEDED
    terms = world.terms()
    assert terms["Ancistrus"]["relations"][0]["related_term"] == "530 mm" and terms["530 mm"]["relations"] == []
    assert [p["property"] for p in terms["Rio Mogi-Guacu"]["properties"]] == ["country", "water_body"]
    previous = world.bundle().directory / RJ.PREVIOUS_DIRNAME
    assert (previous / B.TERMS_FILE).read_bytes() == before_terms and (previous / B.KG_FILE).read_bytes() == before_kg
    kg = json.loads(world.bundle().file(B.KG_FILE).read_text())
    names = {n["id"]: n["name"] for n in kg["things"]}
    edges = {(names.get(e["subject"]), e["predicate"], names.get(e["object"])) for e in kg["associations"]}
    assert ("Ancistrus", "rel:has_measurement", "530 mm") in edges and ("530 mm", "rel:has_measurement", "Ancistrus") not in edges
    assert not any(p == "rel:country" for _, p, _ in edges)   # the value is a property, not an edge or a node
    node = next(n for n in kg["things"] if n["name"] == "Rio Mogi-Guacu")
    assert [p["property"] for p in node["properties"]] == ["country", "water_body"]
    manifest = world.bundle().manifest["relations_repair"]
    assert manifest["counts"]["swapped"] == 1 and manifest["counts"]["attributes"] == 2 and manifest["changed"] is True
    assert manifest["previous_kept"] is True and manifest["last_run"] == "2026-11-26T00:00:00+00:00"
    logs = [e.message for e in world.jobs.store.logs(handle.job_id)]
    assert any("Species -has_measurement-> Measurement: swapped 1 times" in m for m in logs)
    assert any(m.startswith("relation country: attributes 1 times") for m in logs)
    assert sum("swapped" in m for m in logs) == 2   # one line per pattern plus the done line, not one per occurrence


def test_a_second_run_changes_nothing_and_leaves_the_previous_generation_as_it_was(tmp_path):
    world = World(tmp_path)
    world.run()
    repaired = world.bundle().file(B.TERMS_FILE).read_bytes()
    previous = (world.bundle().directory / RJ.PREVIOUS_DIRNAME / B.TERMS_FILE).read_bytes()
    _, state = world.run(nonce="2")
    assert state is JobState.SUCCEEDED
    assert world.bundle().file(B.TERMS_FILE).read_bytes() == repaired
    assert (world.bundle().directory / RJ.PREVIOUS_DIRNAME / B.TERMS_FILE).read_bytes() == previous   # not overwritten by a no-op
    block = world.bundle().manifest["relations_repair"]
    assert block["changed"] is False and block["previous_kept"] is True and block["counts"]["swapped"] == 0


def test_a_third_run_after_a_real_change_replaces_the_previous_generation_with_the_second(tmp_path):
    world = World(tmp_path)
    world.run()
    second_gen = world.bundle().file(B.TERMS_FILE).read_bytes()
    # A later extraction adds a reversed relation again.
    doc = json.loads(second_gen)
    doc["terms"].append(term("640 mm", "Measurement", [{"relation": "has_measurement", "related_term": "Ancistrus", "verified": False}]))
    world.bundle().file(B.TERMS_FILE).write_text(json.dumps(doc), encoding="utf-8")
    world.run(nonce="3")
    assert (world.bundle().directory / RJ.PREVIOUS_DIRNAME / B.TERMS_FILE).read_text() == json.dumps(doc)   # exactly one back
    assert {r["related_term"] for r in world.terms()["Ancistrus"]["relations"]} == {"530 mm", "640 mm"}


def test_the_start_check_refuses_with_typed_errors(tmp_path):
    world = World(tmp_path)
    with pytest.raises(ContractError) as missing:
        world.repair.check_startable("nope")
    assert missing.value.code is ErrorCode.NOT_FOUND
    world.store.create("shipped", schema=SHIPPED, terms=plecos_terms())
    with pytest.raises(ContractError) as shipped:
        world.repair.check_startable("shipped")
    assert shipped.value.code is ErrorCode.INVALID_INPUT and shipped.value.reason == RJ.REASON_NOT_USER_SCHEMA
    with pytest.raises(ContractError) as busy:
        world.repair.check_startable("kg1", open_jobs=[("library_relations", "j1")])
    assert busy.value.code is ErrorCode.CONFLICT and busy.value.reason == RJ.REASON_IN_PROGRESS
    assert world.repair.check_startable("kg1") == {"name": "kg1", "schema": "user/plecos@v1"}


def test_a_failed_kg_rebuild_leaves_the_old_kg_and_the_previous_copy(tmp_path, monkeypatch):
    from app.modules.graph import kg_update

    world = World(tmp_path)
    old_kg = world.bundle().file(B.KG_FILE).read_bytes()

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(kg_update, "rebuild_kg", boom)
    _, state = world.run()
    assert state is JobState.FAILED
    assert world.bundle().file(B.KG_FILE).read_bytes() == old_kg
    assert (world.bundle().directory / RJ.PREVIOUS_DIRNAME / B.KG_FILE).read_bytes() == old_kg
    assert not list(world.bundle().directory.glob(".kg.json.*"))   # no temp file left behind
