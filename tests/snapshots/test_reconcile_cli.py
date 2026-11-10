"""``f2w-kg-snapshot --reconcile-edits`` / ``--confirm-commit`` (H5 rehearsal finding 2).

The operator entry point for edit reconciliation: the live snapshot plus the per-corpus Save journal
(``<state_dir>/<target>/<corpus>/edits.jsonl``, the file ``app.api.local_save`` writes) become ``v<N+1>`` with
``parent.relation == "edit_reconciliation"``, written exactly as ``create_snapshot`` writes (manifest, validation
report, flat copy). Journal entries are marked ``reconciled`` only after the frozen bytes were re-read and
hash-checked; the commit is recorded later, by the second explicit step, after the owner committed the version.
"""
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest

from app.modules.commands import build_kg_snapshot as cmd
from app.modules.graph import edit_journal as ej
from app.modules.graph import snapshot as snapshot_module
from app.modules.graph.imports.corpus_scope import journal_path
from app.modules.graph.releases import ReleaseVerificationError, sha256_bytes, verify_snapshot_dir
from app.modules.graph.snapshot import ReconcileRequest, SnapshotError, reconcile_snapshot
from app.modules.graph.snapshot_manifest import parse_manifest
from tests.releases.conftest import git
from tests.snapshots.snapshot_fakes import COMMIT, REPO

TARGET = "http://127.0.0.1:8000"
CORPUS = "synthetic"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
MANIFEST_SCHEMA = json.loads(
    (REPO / "docs/migration/data_releases/schemas/kg_snapshot_manifest.schema.json").read_text(encoding="utf-8"))


def _record(edit_id: str, operation: str, ids: tuple[str, ...], before: dict, after: dict, base_sha: str,
            state: str = "acknowledged", at: datetime = NOW) -> ej.EditRecord:
    return ej.EditRecord(edit_id=edit_id, seq=1, state=state, target=TARGET, scope=(CORPUS,), base_snapshot_id="v1",
                         base_graph_sha256=base_sha, profile="local", mode="local_trusted", actor="local-operator",
                         operation=operation, ids=ids, before=before, after=after, recorded_at=at)


@pytest.fixture()
def world(make, storage_kg, tmp_path):
    """v1 frozen, plus a per-corpus journal under a publication-state directory with two acknowledged edits."""
    v1 = make(CORPUS)
    graph = json.loads(v1.graph_path.read_text("utf-8"))
    node = next(t for t in graph["things"] if t["id"] == "matkg:SynthPolymerA")
    other = next(t["id"] for t in graph["things"] if t["id"] not in (node["id"], "matkg:SynthDevice"))
    state_dir = tmp_path / "ops" / "publication-state"
    journal = ej.EditJournal(journal_path(state_dir, TARGET, CORPUS))
    journal.append(_record("e-patch", "node_patch", (node["id"],), {"description": node.get("description")},
                           {"description": "edited in the UI"}, v1.graph_sha256))
    journal.append(_record("e-link", "relationship_add", (node["id"], other), {},
                           {"edge": {"subject": node["id"], "predicate": "rel:affects", "object": other}},
                           v1.graph_sha256, at=NOW.replace(minute=1)))
    return {"v1": v1, "storage_kg": storage_kg, "state_dir": state_dir, "journal": journal, "node": node["id"],
            "other": other, "graph": graph}


def _reconcile_argv(w, **extra):
    argv = ["--corpus", CORPUS, "--reconcile-edits", "--parent", "v1", "--storage-kg", str(w["storage_kg"]),
            "--code-commit", COMMIT]
    for key, value in extra.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    return argv


def _out(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_reconcile_edits_freezes_the_next_version_from_the_live_snapshot_and_the_journal(world, capsys):
    w = world
    journal_sha_before = w["journal"].sha256()
    assert cmd.main(_reconcile_argv(w, state_dir=w["state_dir"], target=TARGET)) == 0
    out = _out(capsys)
    assert out["snapshot_id"] == "v2" and out["relation"] == "edit_reconciliation" and out["parent"] == "v1"
    assert out["target"] == TARGET and out["journal"] == str(w["journal"].path)
    assert sorted(out["reconciled_edits"]) == ["e-link", "e-patch"] and out["edit_conflicts"] == []
    assert out["validation"] == "pass" and out["commit_ready"] is True and "--confirm-commit" in out["next"]

    final = w["storage_kg"] / CORPUS / "v2"
    assert Path(out["directory"]) == final
    verified = verify_snapshot_dir(final)  # graph, manifest, validation report and flat copy, all hash-checked
    assert verified.graph_sha256 == out["graph_sha256"] == sha256_bytes(Path(out["flat_copy"]).read_bytes())
    assert sha256_bytes(Path(out["manifest_path"]).read_bytes()) == out["manifest_sha256"]
    assert Path(out["validation_path"]).is_file()
    manifest = verified.manifest
    jsonschema.validate(json.loads(final.joinpath("manifest.json").read_text("utf-8")), MANIFEST_SCHEMA)
    assert manifest.parent is not None and manifest.parent.relation == "edit_reconciliation"
    assert manifest.parent.snapshot_id == "v1" and manifest.parent.graph_sha256 == w["v1"].graph_sha256
    assert manifest.edit_reconciliation is not None
    assert manifest.edit_reconciliation.base_snapshot_id == "v1"
    assert manifest.edit_reconciliation.applied_edit_count == 2
    assert manifest.edit_reconciliation.journal_sha256 == journal_sha_before, "the journal as replayed"
    assert manifest.edit_reconciliation.journal_ref == f"private:publication-state/{CORPUS}/edits.jsonl"
    assert {i.role for i in manifest.inputs} == {"seed_graph", "edit_journal"}
    assert manifest.schema_block == w["v1"].manifest.schema_block, "the schema is the parent's"
    assert manifest.extraction == w["v1"].manifest.extraction and manifest.corpus_revision == w["v1"].manifest.corpus_revision
    graph = json.loads(verified.graph_bytes.decode("utf-8"))
    assert next(t for t in graph["things"] if t["id"] == w["node"])["description"] == "edited in the UI"
    assert {"subject": w["node"], "predicate": "rel:affects", "object": w["other"]} in graph["associations"]
    assert len(graph["things"]) == len(w["graph"]["things"]), "a JSON patch, not a rebuild"

    latest = w["journal"].latest()
    assert {r.state for r in latest.values()} == {"reconciled"}
    assert {r.reconciled_in for r in latest.values()} == {"v2"}
    assert all(r.data_commit is None for r in latest.values()), "no commit exists yet; none is claimed"


def test_the_target_can_be_read_from_the_journal_and_the_journal_named_directly(world, capsys):
    w = world
    assert cmd.main(_reconcile_argv(w, journal=w["journal"].path)) == 0
    assert _out(capsys)["target"] == TARGET


def test_nothing_to_reconcile_is_a_typed_refusal_with_no_version_written(world, capsys):
    w = world
    assert cmd.main(_reconcile_argv(w, journal=w["journal"].path)) == 0
    capsys.readouterr()
    assert cmd.main(_reconcile_argv(w, journal=w["journal"].path, parent="v2")) == 1
    assert "nothing to reconcile" in capsys.readouterr().out
    assert sorted(p.name for p in (w["storage_kg"] / CORPUS).iterdir()) == ["v1", "v2"]
    empty = ej.EditJournal(w["state_dir"] / "empty.jsonl")
    empty.path.parent.mkdir(parents=True, exist_ok=True)
    empty.path.write_text("", encoding="utf-8")
    assert cmd.main(_reconcile_argv(w, journal=empty.path, target=TARGET)) == 1
    assert "nothing to reconcile" in capsys.readouterr().out


def test_journal_entries_are_not_marked_reconciled_until_the_frozen_bytes_verify(world, monkeypatch, capsys):
    """The re-read of the frozen directory is what advances the journal; if it fails, nothing is marked."""
    w = world
    real = snapshot_module.verify_snapshot_dir

    def failing(path):
        if Path(path).name == "v2":
            raise ReleaseVerificationError("tampered", "simulated read-back failure")
        return real(path)

    monkeypatch.setattr(snapshot_module, "verify_snapshot_dir", failing)
    assert cmd.main(_reconcile_argv(w, journal=w["journal"].path)) == 1
    assert "does not read back" in capsys.readouterr().out
    assert {r.state for r in w["journal"].latest().values()} == {"acknowledged"}, "journal untouched"
    assert all(r.reconciled_in is None for r in w["journal"].latest().values())


def test_create_snapshot_with_a_journal_also_verifies_before_marking(make, world, monkeypatch):
    w = world
    monkeypatch.setattr(snapshot_module, "verify_snapshot_dir",
                        lambda path: (_ for _ in ()).throw(ReleaseVerificationError("tampered", "simulated")))
    with pytest.raises(SnapshotError, match="does not read back"):
        make(CORPUS, edit_journal=w["journal"], journal_target=TARGET)
    assert {r.state for r in w["journal"].latest().values()} == {"acknowledged"}


def test_a_live_snapshot_that_does_not_verify_or_an_unknown_target_is_refused(world, capsys):
    w = world
    graph = w["v1"].graph_path
    graph.chmod(0o644)
    original = graph.read_bytes()
    graph.write_bytes(original + b" ")
    assert cmd.main(_reconcile_argv(w, journal=w["journal"].path)) == 1
    assert "does not verify" in capsys.readouterr().out
    graph.write_bytes(original)
    assert cmd.main(_reconcile_argv(w, journal=w["journal"].path, target="http://127.0.0.1:9999")) == 1
    assert "nothing to reconcile" in capsys.readouterr().out
    assert cmd.main(_reconcile_argv(w, journal=w["state_dir"] / "missing.jsonl")) == 1
    assert "does not exist" in capsys.readouterr().out
    assert {r.state for r in w["journal"].latest().values()} == {"acknowledged"}


def test_conflicting_edits_are_journaled_and_do_not_stop_the_carried_ones(world, capsys):
    w = world
    w["journal"].append(_record("e-gone", "node_patch", ("matkg:NoSuchNode",), {"description": "a"},
                                {"description": "b"}, w["v1"].graph_sha256, at=NOW.replace(minute=2)))
    assert cmd.main(_reconcile_argv(w, journal=w["journal"].path)) == 0
    out = _out(capsys)
    assert sorted(out["reconciled_edits"]) == ["e-link", "e-patch"] and out["edit_conflicts"] == ["e-gone"]
    assert any("e-gone" in warning for warning in out["warnings"])
    latest = w["journal"].latest()
    assert latest["e-gone"].state == "conflict" and latest["e-patch"].state == "reconciled"


def test_reconcile_request_refuses_a_version_that_would_not_change_the_graph(world):
    """Edits already present in the live bytes do not justify a new, byte-identical version."""
    w = world
    graph = json.loads(w["v1"].graph_path.read_text("utf-8"))
    node = next(t for t in graph["things"] if t["id"] == w["node"])
    present = ej.EditJournal(w["state_dir"] / "present.jsonl")
    present.append(_record("e-same", "node_patch", (w["node"],), {"description": "x"},
                           {"description": node.get("description")}, w["v1"].graph_sha256))
    with pytest.raises(SnapshotError, match="no version is created"):
        reconcile_snapshot(ReconcileRequest(corpus=CORPUS, storage_kg=w["storage_kg"], parent_snapshot_id="v1",
                                            edit_journal=present, journal_target=TARGET, journal_ref="private:j",
                                            code_commit=COMMIT))
    assert present.latest()["e-same"].state == "acknowledged"


# ------------------------------------------------------------------------------------------- step 2: the commit

def _commit_version(tmp_path: Path, storage_kg: Path, snapshot_id: str) -> tuple[Path, str, str]:
    """A throwaway repository where the fixture owner commits storage/kg/<corpus>/<vN> (test code only)."""
    repo = tmp_path / "owner-repo"
    (repo / "storage" / "kg").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    shutil.copytree(storage_kg / CORPUS / snapshot_id, repo / "storage" / "kg" / CORPUS / snapshot_id)
    shutil.copy2(storage_kg / f"matkg_{CORPUS}_{snapshot_id}.json", repo / "storage" / "kg")
    git(repo, "add", "-f", "storage/kg")
    git(repo, "commit", "-q", "-m", f"data: {CORPUS}/{snapshot_id}")
    return repo, base, git(repo, "rev-parse", "HEAD")


def _confirm_argv(w, repo: Path, commit: str, **extra):
    argv = ["--corpus", CORPUS, "--confirm-commit", commit, "--snapshot-id", "v2", "--repo", str(repo),
            "--storage-kg", str(w["storage_kg"]), "--journal", str(w["journal"].path)]
    for key, value in extra.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    return argv


def test_confirm_commit_records_the_verified_commit_on_the_reconciled_entries_and_is_idempotent(world, tmp_path, capsys):
    w = world
    assert cmd.main(_reconcile_argv(w, journal=w["journal"].path)) == 0
    capsys.readouterr()
    repo, base, commit = _commit_version(tmp_path, w["storage_kg"], "v2")

    assert cmd.main(_confirm_argv(w, repo, base)) == 1, "a commit without the version is refused"
    assert "does not hold" in capsys.readouterr().out
    assert all(r.data_commit is None for r in w["journal"].latest().values())

    assert cmd.main(_confirm_argv(w, repo, commit)) == 0
    out = _out(capsys)
    assert out["data_commit"] == commit and sorted(out["recorded"]) == ["e-link", "e-patch"]
    assert out["already_recorded"] == [] and out["snapshot_id"] == "v2"
    latest = w["journal"].latest()
    assert {(r.state, r.reconciled_in, r.data_commit) for r in latest.values()} == {("reconciled", "v2", commit)}
    assert all(r.seq == 3 for r in latest.values()), "one appended record per edit; nothing rewritten"

    assert cmd.main(_confirm_argv(w, repo, commit)) == 0
    out = _out(capsys)
    assert out["recorded"] == [] and sorted(out["already_recorded"]) == ["e-link", "e-patch"]
    assert all(r.seq == 3 for r in w["journal"].latest().values())

    # another commit holding other bytes for the same id is refused: the local v2 is the reference
    flat = repo / "storage" / "kg" / f"matkg_{CORPUS}_v2.json"
    graph_in_repo = repo / "storage" / "kg" / CORPUS / "v2" / "graph.json"
    for path in (flat, graph_in_repo):
        path.chmod(0o644)
        path.write_bytes(path.read_bytes().replace(b"edited in the UI", b"edited elsewhere"))
    git(repo, "add", "-f", "storage/kg")
    git(repo, "commit", "-q", "-m", "tamper")
    tampered = git(repo, "rev-parse", "HEAD")
    assert cmd.main(_confirm_argv(w, repo, tampered)) == 1
    assert "does not hold" in capsys.readouterr().out
    assert {r.data_commit for r in w["journal"].latest().values()} == {commit}


def test_confirm_commit_refuses_a_different_commit_once_one_is_recorded(world, tmp_path, capsys):
    w = world
    assert cmd.main(_reconcile_argv(w, journal=w["journal"].path)) == 0
    repo, _base, commit = _commit_version(tmp_path, w["storage_kg"], "v2")
    assert cmd.main(_confirm_argv(w, repo, commit)) == 0
    (repo / "note.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "note.txt")
    git(repo, "commit", "-q", "-m", "same bytes, later commit")
    later = git(repo, "rev-parse", "HEAD")
    capsys.readouterr()
    assert cmd.main(_confirm_argv(w, repo, later)) == 1
    assert "another commit" in capsys.readouterr().out
    assert {r.data_commit for r in w["journal"].latest().values()} == {commit}


def test_confirm_commit_refuses_when_no_entry_is_reconciled_into_the_version(world, tmp_path, capsys):
    w = world
    assert cmd.main(_reconcile_argv(w, journal=w["journal"].path)) == 0
    repo, _base, commit = _commit_version(tmp_path, w["storage_kg"], "v2")
    foreign = ej.EditJournal(w["state_dir"] / "foreign.jsonl")
    foreign.append(_record("e-x", "node_patch", (w["node"],), {}, {"description": "y"}, w["v1"].graph_sha256))
    capsys.readouterr()
    argv = _confirm_argv(w, repo, commit)
    argv[argv.index("--journal") + 1] = str(foreign.path)
    assert cmd.main(argv) == 1
    assert "no journal entry is reconciled" in capsys.readouterr().out


def test_manifest_parses_without_the_block_and_old_journal_lines_without_data_commit(world):
    manifest = json.loads(world["v1"].manifest_path.read_text("utf-8"))
    assert "edit_reconciliation" not in manifest and parse_manifest(world["v1"].manifest_path.read_bytes())
    line = json.loads(world["journal"].path.read_text("utf-8").splitlines()[0])
    line.pop("data_commit", None)
    assert ej.EditRecord.model_validate(line).data_commit is None
