"""``tiled_sim`` honours the bl1101 writer switch (H8 P3 gated item 2 follow-up).

``v1``-``v9`` (or an explicit legacy flag) keep the legacy document writer byte for byte; a later version goes
through ``create_snapshot`` and lands in the release-contract layout. Everything is written under ``tmp_path``;
the frozen ``storage/kg/matkg_bl1101_v*.json`` and the terms files are only read. No network, no model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules import tiled_sim
from app.modules.graph import adoption
from app.modules.graph.imports.bl1101 import paths, snapshot
from app.modules.graph.releases import SNAPSHOT_FILES, verify_snapshot_dir
from app.modules.graph.snapshot import SnapshotExistsError

KG = paths.REPO_ROOT / "storage" / "kg"
SOURCE = KG / "matkg_bl1101_v4.json"
COMMIT = "b" * 40
CLOCK = "2026-11-09T00:00:00Z"


def _frozen_mtimes() -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in KG.glob("matkg_bl1101_v*.json")}


@pytest.fixture
def clean_terms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the frozen v4 terms file without its two absolute local paths (``/tmp/...`` and ``file://`` strings
    inside code snippets, which the R4 disclosure rule rejects), served to ``tiled_sim`` as its terms directory."""
    src = paths.TERMS_DIR / "extracted_terms_bl1101_v4.json"
    text = src.read_text(encoding="utf-8")
    assert "/tmp/" in text
    out = tmp_path / "clean_terms"
    out.mkdir()
    (out / src.name).write_text(text.replace("/tmp/", "scratch/").replace("file://", "uri:"), encoding="utf-8")
    monkeypatch.setattr(tiled_sim, "TERMS_DIR", out)
    return out


def test_v10_tiled_sim_build_is_a_release_contract_snapshot(tmp_path: Path, clean_terms: Path) -> None:
    before = _frozen_mtimes()
    kg_dir = tmp_path / "kg"
    result = tiled_sim.run_tiled_sim_promote(
        source_kg=SOURCE, version=10, kg_dir=kg_dir, terms_dir=tmp_path / "terms", code_commit=COMMIT
    )
    assert result["writer"] == "standard" and result["version"] == 10
    snapshot_dir = kg_dir / "bl1101" / "v10"
    assert result["snapshot_dir"] == snapshot_dir and result["kg_path"] == kg_dir / "matkg_bl1101_v10.json"
    assert sorted(p.name for p in snapshot_dir.iterdir()) == sorted(SNAPSHOT_FILES)
    assert result["kg_path"].read_bytes() == (snapshot_dir / "graph.json").read_bytes()

    manifest = verify_snapshot_dir(snapshot_dir).manifest
    assert (manifest.corpus, manifest.snapshot_id) == ("bl1101", "v10")
    assert manifest.validation.status == result["validation_status"] == "pass"
    assert manifest.disclosure.status == "pass" and result["commit_ready"]
    assert manifest.graph.node_count == result["nodes"] > 20

    graph = json.loads(result["kg_path"].read_text(encoding="utf-8"))
    assert graph["metadata"]["graph_snapshot"]["version"] == 10
    assert graph["metadata"]["promote"]["kind"] == "tiled_sim"
    assert not [t for t in graph["things"] if t.get("category") in {"ESAF", "Proposal", "BlueskyRun"}]
    terms = json.loads(result["terms_path"].read_text(encoding="utf-8"))
    assert terms["metadata"]["promote"]["kind"] == "tiled_sim"

    empty = tmp_path / "other_storage_kg"
    empty.mkdir()
    outcome = adoption.adopt_legacy_flat_file(result["kg_path"], storage_kg=empty, code_commit=COMMIT, dry_run=True)
    assert (outcome.status, outcome.corpus, outcome.snapshot_id) == ("would_adopt", "bl1101", "v10")
    assert _frozen_mtimes() == before


def test_v10_build_from_the_frozen_terms_is_flagged_not_commit_ready(tmp_path: Path) -> None:
    """The frozen v4 terms carry ``/tmp/...`` and ``file://`` strings in code snippets: the layout still verifies, and the
    standard writer reports the disclosure failure (R4) instead of hiding it."""
    result = tiled_sim.run_tiled_sim_promote(
        source_kg=SOURCE, version=10, kg_dir=tmp_path / "kg", terms_dir=tmp_path / "terms", code_commit=COMMIT
    )
    assert verify_snapshot_dir(result["snapshot_dir"]).manifest.snapshot_id == "v10"
    assert result["disclosure_status"] == "fail" and result["commit_ready"] is False


def test_v10_standard_build_refuses_sim_nodes_and_never_overwrites(tmp_path: Path) -> None:
    kg_dir = tmp_path / "kg"
    with pytest.raises(ValueError, match="cannot carry simulated nodes"):
        tiled_sim.run_tiled_sim_promote(
            source_kg=SOURCE, version=10, kg_dir=kg_dir, terms_dir=tmp_path / "terms", include_sim_nodes=True
        )
    assert not kg_dir.exists()
    tiled_sim.run_tiled_sim_promote(source_kg=SOURCE, version=10, kg_dir=kg_dir, terms_dir=tmp_path / "terms",
                                    code_commit=COMMIT)
    with pytest.raises(SnapshotExistsError):
        tiled_sim.run_tiled_sim_promote(source_kg=SOURCE, version=10, kg_dir=kg_dir, terms_dir=tmp_path / "terms",
            code_commit=COMMIT)


def test_up_to_v9_and_explicit_legacy_flag_keep_the_legacy_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot, "utc_now", lambda: CLOCK)
    monkeypatch.setattr(snapshot, "_rel_to_repo", lambda path: f"storage/kg/{path.name}")
    kwargs = {"source_kg": SOURCE, "terms_dir": tmp_path / "terms"}
    low = tiled_sim.run_tiled_sim_promote(version=9, kg_dir=tmp_path / "a", **kwargs)
    flagged = tiled_sim.run_tiled_sim_promote(version=10, kg_dir=tmp_path / "b", legacy_writer=True, **kwargs)
    assert low["writer"] == flagged["writer"] == "legacy"
    assert not (tmp_path / "a" / "bl1101").exists() and not (tmp_path / "b" / "bl1101").exists()

    # the document is exactly what the legacy writer makes of the same graph
    expected = json.loads(SOURCE.read_text(encoding="utf-8"))
    expected["metadata"]["promote"] = low["promote"]
    expected["metadata"]["schema"] = str(tiled_sim.SCHEMA_PATH.relative_to(tiled_sim.REPO_ROOT))
    ref = snapshot.write_kg_snapshot(expected, 9, kg_dir=tmp_path / "ref")
    assert low["kg_path"].read_bytes() == ref.read_bytes()
    doc = json.loads(low["kg_path"].read_text(encoding="utf-8"))
    assert len(doc["metadata"]["graph_snapshot"]["sha256"]) == 64
    assert doc["metadata"]["graph_snapshot"]["version"] == 9
    assert snapshot.uses_legacy_writer(9) and not snapshot.uses_legacy_writer(10)


def test_cli_exposes_the_legacy_writer_flag() -> None:
    assert tiled_sim.parse_args([]).legacy_writer is False
    assert tiled_sim.parse_args(["--legacy-writer"]).legacy_writer is True
