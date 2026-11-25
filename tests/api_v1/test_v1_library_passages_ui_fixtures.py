"""The UI's passage fixtures (``ui/src/app/components/__fixtures__/passages_*.json``) pin the shapes Find supporting text reads.

Each fixture is compared with what the real routes answer (the start route, ``GET /v1/jobs/{id}/progress`` of a
``library_passages`` job, and the run summary of an extraction with passage counters): same keys at every level and the
same JSON types for every value that is not ``null`` on either side. A change to a route the panel reads fails here, so
the fixture and the TypeScript types in ``extractionApi.ts`` change together.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.modules.jobs.run_summary import run_summary
from tests.api_v1.test_v1_library_passages import URL, build
from tests.api_v1.test_v1_library_schemas_ui_fixtures import same_shape
from tests.library.passages_fixtures import SCHEMA, term

FIXTURES = Path(__file__).resolve().parents[2] / "ui" / "src" / "app" / "components" / "__fixtures__"


def fixture(name: str) -> Any:  # noqa: ANN401
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_the_start_handle_and_the_recovery_summary_match_the_fixtures(monkeypatch, tmp_path):
    app, store = build(monkeypatch, tmp_path)
    store.create("kg2", schema=SCHEMA, terms={"metadata": {}, "code_snippets": [], "terms": [
        term("domain purity", "a.pdf"), term("gone term", "gone.pdf"), term("blank page term", "blank.pdf")]})
    c = app.client()
    handle = c.post(URL.format("kg2"), json={}).json()
    same_shape(handle, fixture("passages_job_handle.json"))
    assert handle["kind"] == "library_passages"
    app.work()
    summary = c.get(f"/v1/jobs/{handle['job_id']}/progress").json()["summary"]
    pinned = fixture("passages_recovery_summary.json")
    same_shape(summary, pinned)
    assert set(summary["recovery"]["papers"]) == set(pinned["recovery"]["papers"]) == {"a.pdf", "gone.pdf", "blank.pdf"}
    assert summary["recovery"]["papers"]["gone.pdf"]["reason"] == pinned["recovery"]["papers"]["gone.pdf"]["reason"] == "missing"
    assert summary["recovery"]["papers"]["blank.pdf"]["reason"] == pinned["recovery"]["papers"]["blank.pdf"]["reason"]


def test_the_extraction_summary_passages_block_matches_the_fixture(tmp_path):
    from tests.library.test_library_extract import World, _three

    def hook(orch: Any, name: str) -> None:  # noqa: ANN401
        orch.store.note_passage(name, f"{name}-t1", kind="quoted")
        orch.store.note_passage(name, f"{name}-t2", kind="located")
        orch.store.note_passage(name, f"{name}-t3", kind=None, rejected=1)

    world = World(tmp_path, hook=hook)
    names = _three(world)
    handle, _ = world.run(world.params(names))
    job = world.jobs.store.get(handle.job_id)
    summary = run_summary(world.jobs.store, job)
    pinned = fixture("passages_extraction_summary.json")
    same_shape(summary, pinned)
    assert set(summary["passages"]["papers"]) == set(pinned["passages"]["papers"]) == {"a.pdf", "b.pdf", "c.pdf"}
    assert summary["passages"]["total"] == {"terms": 9, "with_passage": 6, "quoted": 3, "located": 3, "rejected": 3, "uncovered": 3,
                                          "relations": 0, "relations_with_evidence": 0, "evidence_sentences": 0, "relations_without": 0}
