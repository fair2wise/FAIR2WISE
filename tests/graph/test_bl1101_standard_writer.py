"""bl1101 snapshot writer switch (owner decision 2026-11-09, seventeenth set; H8 P3 gated item 2).

``v1``-``v9`` stay byte-identical through the legacy document writer: replaying ``v3`` with the motor map at the
frozen clock reproduces the committed ``v4`` file byte for byte. A new version (``v10`` on) goes through
``graph.snapshot.create_snapshot``: the release-contract layout ``storage/kg/bl1101/v10/`` plus the flat copy,
which ``releases.verify_snapshot_dir`` accepts and an ``f2w-kg-adopt`` dry run would adopt. Everything is written
under ``tmp_path``; the frozen ``storage/kg/matkg_bl1101_v*.json`` are only read. No network, no git, no model.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shutil

import pytest

from app.modules import bl1101_ingest as legacy
from app.modules.graph import adoption
from app.modules.graph.imports.bl1101 import cli, paths, snapshot, specs
from app.modules.graph.releases import SNAPSHOT_FILES, verify_snapshot_dir
from app.modules.graph.snapshot import SnapshotExistsError

KG = paths.REPO_ROOT / "storage" / "kg"
COMMIT = "a" * 40
V4_WRITTEN_AT = "2026-09-16T20:29:02Z"


def _frozen_mtimes() -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in KG.glob("matkg_bl1101_v*.json")}


# ----------------------------------------------------------------------------- the switch
def test_writer_rule_legacy_through_v9_standard_from_v10() -> None:
    assert snapshot.LEGACY_WRITER_MAX_VERSION == 9
    assert all(snapshot.uses_legacy_writer(v) for v in range(1, 10))
    assert not snapshot.uses_legacy_writer(10) and not snapshot.uses_legacy_writer(11)
    assert snapshot.uses_legacy_writer(10, legacy_writer=True)
    assert legacy.uses_legacy_writer is snapshot.uses_legacy_writer
    assert legacy.write_standard_snapshot is snapshot.write_standard_snapshot


# ----------------------------------------------------------------------------- v1-v9: byte-identical history
_SELF_DIGEST = re.compile(rb'("graph_snapshot": \{[^}]*"sha256": ")([0-9a-f]{64})(")')


def test_replay_of_v3_reproduces_frozen_v4_byte_for_byte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The legacy writer, at v4's clock and repo-relative path, writes the committed v4 bytes.

    Every byte matches except the 64 hex characters of ``graph_snapshot.sha256``: that field is the document's
    digest of its own pre-stamp text, and the frozen v4 was stamped by an earlier revision of the writer whose
    pre-stamp text is not recorded, so its value cannot be re-derived from the file (the pre-port tests masked it
    too). The test pins that this is the *only* difference.
    """
    before = _frozen_mtimes()
    out = tmp_path / "kg"
    real_rel = snapshot._rel_to_repo

    def rel(path: Path) -> str:  # the written file sits in tmp; v4 recorded the repo-relative path
        return f"storage/kg/{path.name}" if path.resolve().is_relative_to(out.resolve()) else real_rel(path)

    monkeypatch.setattr(legacy, "utc_now", lambda: V4_WRITTEN_AT)
    monkeypatch.setattr(legacy, "_rel_to_repo", rel)
    result = legacy.run_motor_link_promote(source_kg=KG / "matkg_bl1101_v3.json", version=4, kg_dir=out,
                                           terms_dir=tmp_path / "terms")
    assert (result["stats"]["linked"], result["stats"]["aliased"]) == (60, 6)
    ours, frozen = result["kg_path"].read_bytes(), (KG / "matkg_bl1101_v4.json").read_bytes()
    assert len(ours) == len(frozen)
    assert len(_SELF_DIGEST.findall(ours)) == 1 and len(_SELF_DIGEST.findall(frozen)) == 1
    assert _SELF_DIGEST.sub(rb"\1<SELF>\3", ours) == _SELF_DIGEST.sub(rb"\1<SELF>\3", frozen)
    differing = [i for i, (a, b) in enumerate(zip(ours, frozen, strict=True)) if a != b]
    span = _SELF_DIGEST.search(frozen)
    assert span is not None and differing and all(span.start(2) <= i < span.end(2) for i in differing)
    assert not (out / "bl1101").exists()  # the legacy writer makes no nested layout
    assert _frozen_mtimes() == before


# ----------------------------------------------------------------------------- v10: the standard writer
@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Redirect every output directory of the ingest into tmp; v1-v9 exist as placeholders so the next is v10."""
    root = tmp_path / "out"
    blueprint = root / "papers/rsoxs/beamline_blueprint" / paths.BLUEPRINT_PATH.name
    schema = root / "storage/schema/bl1101_schema.yaml"
    for src, dst in ((paths.BLUEPRINT_PATH, blueprint), (paths.SCHEMA_PATH, schema)):
        dst.parent.mkdir(parents=True)
        shutil.copy(src, dst)
    monkeypatch.setattr(legacy, "REPO_ROOT", root)
    monkeypatch.setattr(legacy, "BLUEPRINT_PATH", blueprint)
    monkeypatch.setattr(legacy, "SCHEMA_PATH", schema)
    dirs = {
        "KG_DIR": root / "storage/kg",
        "TERMS_DIR": root / "storage/terminology",
        "CACHE_DIR": root / ".cache/bl1101",
        "REPOS_DIR": root / ".cache/bl1101/repos",
        "READMES_DIR": root / ".cache/bl1101/readmes",
        "PAGES_DIR": root / ".cache/bl1101/pages",
        "WORK_DIR": root / ".cache/bl1101/work",
    }
    for name, value in dirs.items():
        monkeypatch.setattr(legacy, name, value)
    monkeypatch.setattr(legacy, "REPO_SPECS", ())
    dirs["KG_DIR"].mkdir(parents=True)
    for n in range(1, 10):
        (dirs["KG_DIR"] / f"matkg_bl1101_v{n}.json").write_text("{}", encoding="utf-8")
    return dirs


def test_v10_build_is_a_release_contract_snapshot(sandbox: dict[str, Path], tmp_path: Path) -> None:
    before = _frozen_mtimes()
    result = cli.run_ingest(offline=True, code_commit=COMMIT)
    assert result["version"] == 10 and result["writer"] == "standard"
    kg_dir = sandbox["KG_DIR"]
    snapshot_dir = kg_dir / "bl1101" / "v10"
    assert result["snapshot_dir"] == snapshot_dir and result["kg_path"] == kg_dir / "matkg_bl1101_v10.json"
    assert sorted(p.name for p in snapshot_dir.iterdir()) == sorted(SNAPSHOT_FILES)
    assert result["kg_path"].read_bytes() == (snapshot_dir / "graph.json").read_bytes()
    assert not result["kg_path"].is_symlink()

    verified = verify_snapshot_dir(snapshot_dir)
    manifest = verified.manifest
    assert (manifest.corpus, manifest.snapshot_id) == ("bl1101", "v10")
    assert manifest.schema_block.name == "bl1101_schema"  # bound by the matkg_bl1101_* file-name pattern
    assert verified.compat_paths == ("storage/kg/matkg_bl1101_v10.json",)
    assert manifest.extraction.revisions[0].extractor == snapshot.EXTRACTOR
    assert manifest.extraction.revisions[0].version == specs.INGEST_VERSION
    assert manifest.inputs[0].role == "terms_snapshot"
    assert manifest.inputs[0].ref == "storage/terminology/extracted_terms_bl1101_v10.json"
    assert manifest.graph.node_count == result["nodes"] == len(result["graph"]["things"]) > 20
    assert manifest.validation.status == result["validation_status"] == "pass"
    assert manifest.disclosure.status == result["disclosure_status"] == "pass" and result["commit_ready"]

    graph = json.loads((snapshot_dir / "graph.json").read_text(encoding="utf-8"))
    assert graph["metadata"]["kg"] == "bl1101" and graph["metadata"]["graph_snapshot"]["version"] == 10
    assert graph["metadata"]["ingest_version"] == specs.INGEST_VERSION
    assert "sha256" not in graph["metadata"]["graph_snapshot"]  # the manifest carries the hash, not the document
    assert result["terms_path"] == sandbox["TERMS_DIR"] / "extracted_terms_bl1101_v10.json"
    terms = json.loads(result["terms_path"].read_text(encoding="utf-8"))
    assert manifest.inputs[0].sha256 == hashlib.sha256(result["terms_path"].read_bytes()).hexdigest()
    assert terms["metadata"]["graph_snapshot"]["version"] == 10
    assert _frozen_mtimes() == before


def test_v10_flat_copy_passes_the_adoption_dry_run(sandbox: dict[str, Path], tmp_path: Path) -> None:
    """``f2w-kg-adopt adopt --dry-run`` would adopt the flat copy into an empty storage/kg, and reports the
    nested snapshot as already present in its own one."""
    result = cli.run_ingest(offline=True, code_commit=COMMIT)
    flat = result["kg_path"]
    empty = tmp_path / "other_storage_kg"
    empty.mkdir()
    outcome = adoption.adopt_legacy_flat_file(flat, storage_kg=empty, code_commit=COMMIT, dry_run=True)
    assert (outcome.status, outcome.corpus, outcome.snapshot_id) == ("would_adopt", "bl1101", "v10")
    assert outcome.graph_sha256 == verify_snapshot_dir(result["snapshot_dir"]).graph_sha256
    assert "bl1101_schema" in outcome.detail
    assert not any(empty.iterdir())
    outcomes = adoption.adopt_legacy_directory(sandbox["KG_DIR"], only=["matkg_bl1101_v10.json"], code_commit=COMMIT,
                                               dry_run=True)
    assert [(o.flat_name, o.status, o.rule) for o in outcomes] == [
        ("matkg_bl1101_v10.json", "skipped", adoption.RULE_TARGET_EXISTS)]


def test_v10_is_never_overwritten(sandbox: dict[str, Path]) -> None:
    first = cli.run_ingest(offline=True, code_commit=COMMIT)
    second = cli.run_ingest(offline=True, code_commit=COMMIT)
    assert (first["version"], second["version"]) == (10, 11) and (sandbox["KG_DIR"] / "bl1101" / "v11").is_dir()
    with pytest.raises(SnapshotExistsError):
        snapshot.write_standard_snapshot(first["terms_path"], 10, kg_dir=sandbox["KG_DIR"], code_commit=COMMIT)


def test_legacy_writer_flag_keeps_the_flat_document_at_v10(sandbox: dict[str, Path]) -> None:
    result = cli.run_ingest(offline=True, legacy_writer=True)
    assert result["version"] == 10 and result["writer"] == "legacy"
    data = json.loads(result["kg_path"].read_text(encoding="utf-8"))
    snap = data["metadata"]["graph_snapshot"]
    assert snap["name"] == "matkg_bl1101_v10" and len(snap["sha256"]) == 64 and snap["nodes"] == result["nodes"]
    assert not (sandbox["KG_DIR"] / "bl1101").exists()


def test_write_standard_snapshot_records_source_hashes_and_time(sandbox: dict[str, Path], tmp_path: Path) -> None:
    result = cli.run_ingest(offline=True, legacy_writer=True)  # v10 legacy: gives us a terms file to freeze again
    when = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    page_sha = "b" * 64
    frozen = snapshot.write_standard_snapshot(
        result["terms_path"], 11, kg_dir=sandbox["KG_DIR"], code_commit=COMMIT, now=when,
        sources_landed=[{"name": "als_public_page", "sha256": page_sha}, {"name": "repo", "sha": "c" * 40}],
    )
    assert frozen.snapshot_id == "v11" and frozen.manifest.created.at == "2026-11-09T12:00:00Z"
    assert frozen.manifest.created.code_commit == COMMIT
    assert frozen.manifest.corpus_revision.source_count == 1  # git shas are not source hashes
    verify_snapshot_dir(frozen.directory)


# ----------------------------------------------------------------------------- CLI
def test_cli_offline_at_v10_uses_the_standard_writer(sandbox: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    assert legacy.main(["--offline", "--code-commit", COMMIT]) == 0
    out = capsys.readouterr().out
    assert "bl1101 v10:" in out and "snapshot: " in out and "validation pass" in out
    assert (sandbox["KG_DIR"] / "bl1101" / "v10" / "manifest.json").is_file()


def test_cli_from_graph_past_v9_requires_the_legacy_writer_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _frozen_mtimes()
    out = tmp_path / "kg"
    monkeypatch.setattr(legacy, "KG_DIR", out)
    monkeypatch.setattr(legacy, "TERMS_DIR", tmp_path / "terms")
    source = str(KG / "matkg_bl1101_v3.json")
    assert legacy.main(["--from-graph", source, "--snapshot", "10"]) == 1
    assert "--legacy-writer" in capsys.readouterr().out and not out.exists()
    assert legacy.main(["--from-graph", source, "--snapshot", "10", "--legacy-writer"]) == 0
    assert (out / "matkg_bl1101_v10.json").is_file() and not (out / "bl1101").exists()
    assert legacy.main(["--from-graph", source, "--snapshot", "4"]) == 0  # inside the legacy range: no flag needed
    assert (out / "matkg_bl1101_v4.json").is_file()
    assert _frozen_mtimes() == before


def test_parse_args_writer_flags() -> None:
    args = cli.parse_args(["--legacy-writer", "--code-commit", COMMIT])
    assert args.legacy_writer and args.code_commit == COMMIT
    default = cli.parse_args([])
    assert not default.legacy_writer and default.code_commit is None
