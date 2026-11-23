"""Pilot P0: versioned snapshots of one graph id must not make the directory listing refuse.

The image ships ``storage/kg/matkg_bl1101_v1.json`` ... ``v9.json``; all carry the id
``bl1101``. ``storage_graph_files`` lists only the newest, while two genuinely different
files claiming one id are still refused by the JSON backend ("graphs are never merged").
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from app.modules.contracts import ContractError, ErrorCode
from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.operations.knowledge import KnowledgeOperations, storage_graph_files

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "graph"))

from graph_samples import things_payload, write  # noqa: E402


def _kg(tmp_path: Path, *names: str) -> Path:
    kg = tmp_path / "storage" / "kg"
    kg.mkdir(parents=True)
    for name in names:
        write(kg / name, things_payload())
    return kg


def test_shipped_bl1101_versions_list_only_the_newest(tmp_path):
    names = [f"matkg_bl1101_v{n}.json" for n in range(1, 10)] + ["matkg_rsoxs_v1.json"]
    _kg(tmp_path, *names)
    listed = [Path(p).name for p in storage_graph_files(tmp_path)]
    assert listed == ["matkg_bl1101_v9.json", "matkg_rsoxs_v1.json"]
    ops = KnowledgeOperations.from_storage(tmp_path)
    assert ops.graph.port.graph_ids() == ["bl1101", "rsoxs_v1"]


def test_version_order_is_numeric(tmp_path):
    _kg(tmp_path, "matkg_bl1101_v9.json", "matkg_bl1101_v10.json")
    assert [Path(p).name for p in storage_graph_files(tmp_path)] == ["matkg_bl1101_v10.json"]


def test_backend_still_refuses_two_files_claiming_one_id(tmp_path):
    kg = _kg(tmp_path, "matkg_bl1101_v2.json", "matkg_bl1101_v3.json")
    with pytest.raises(ContractError) as err:
        JsonGraphBackend([str(kg / "matkg_bl1101_v2.json"), str(kg / "matkg_bl1101_v3.json")])
    assert err.value.code is ErrorCode.INVALID_INPUT
    assert "never merged" in str(err.value)


def test_non_version_collision_is_not_deduplicated(tmp_path, monkeypatch):
    _kg(tmp_path, "matkg_bl1101_v2.json", "other.json")
    monkeypatch.setattr("app.modules.graph.snapshots.graph_id_for_snapshot", lambda p: "same")
    assert len(storage_graph_files(tmp_path)) == 2  # kept, so the backend refuses them
