"""The UI's relation-repair fixtures (``ui/src/app/components/__fixtures__/relations_*.json``) pin the shapes Repair relations reads.

Compared with the real start route and the real ``GET /v1/jobs/{id}/progress`` of a ``library_relations`` job (same keys at
every level, same JSON types for every non-null value). Regenerate with ``F2W_WRITE_FIXTURES=1`` (the values of the fixture
are then the real route's answer, with nothing hand-written).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tests.api_v1.test_v1_library_relations import URL, build
from tests.api_v1.test_v1_library_schemas_ui_fixtures import same_shape

FIXTURES = Path(__file__).resolve().parents[2] / "ui" / "src" / "app" / "components" / "__fixtures__"


def fixture(name: str, actual: Any) -> Any:  # noqa: ANN401
    path = FIXTURES / name
    if os.environ.get("F2W_WRITE_FIXTURES") == "1":
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_start_handle_and_the_repair_summary_match_the_fixtures(monkeypatch, tmp_path):
    app, _ = build(monkeypatch, tmp_path)
    c = app.client()
    handle = c.post(URL.format("kg1"), json={}).json()
    same_shape(handle, fixture("relations_job_handle.json", handle))
    assert handle["kind"] == "library_relations"
    app.work()
    summary = c.get(f"/v1/jobs/{handle['job_id']}/progress").json()["summary"]
    pinned = fixture("relations_repair_summary.json", summary)
    same_shape(summary, pinned)
    assert {k: summary["counters"][k] for k in ("repair_swapped", "repair_attributes", "repair_invalid", "repair_relations")} == {
        "repair_swapped": 1, "repair_attributes": 2, "repair_invalid": 1, "repair_relations": 6}
    assert pinned["counters"]["repair_swapped"] == 1
