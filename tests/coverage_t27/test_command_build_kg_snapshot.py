"""``f2w-kg-snapshot`` (app.modules.commands.build_kg_snapshot): arguments parsed into the request, summary and errors."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import types

import pytest

from app.modules.commands import build_kg_snapshot as cmd


@pytest.fixture(autouse=True)
def restore_root_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
    root.setLevel(level)


def _result(tmp_path: Path):
    return types.SimpleNamespace(
        snapshot_id="v3", corpus="rsoxs", directory=tmp_path / "rsoxs" / "v3", graph_sha256="ab" * 32,
        flat_path=tmp_path / "flat.json", validation_status="valid", disclosure=types.SimpleNamespace(status="clean"),
        commit_ready=True, warnings=["w1"])


def test_required_arguments_and_defaults():
    args = cmd.parse_args(["--corpus", "rsoxs", "--terms", "t.json"])
    assert args.corpus == "rsoxs" and args.terms == Path("t.json") and args.profile == "local"
    assert args.parent_relation == "rebuild" and args.extraction_revision == [] and args.source_sha256 == []
    assert args.storage_kg is None and args.verbose is False


def test_missing_required_and_bad_profile_exit_2(capsys):
    for argv in ([], ["--corpus", "x"], ["--corpus", "x", "--terms", "t", "--profile", "prod"]):
        with pytest.raises(SystemExit) as stop:
            cmd.parse_args(argv)
        assert stop.value.code == 2
    assert "required" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--corpus", "c", "--reconcile-edits", "--parent", "v1", "--journal", "j"],              # no --storage-kg
    ["--corpus", "c", "--reconcile-edits", "--storage-kg", "s", "--journal", "j"],           # no --parent
    ["--corpus", "c", "--reconcile-edits", "--parent", "v1", "--storage-kg", "s"],           # no journal
    ["--corpus", "c", "--reconcile-edits", "--parent", "v1", "--storage-kg", "s", "--state-dir", "d"],  # no target
    ["--corpus", "c", "--reconcile-edits", "--parent", "v1", "--storage-kg", "s", "--journal", "j",
     "--parent-relation", "repair"],                                                          # relation is fixed
    ["--corpus", "c", "--confirm-commit", "abc", "--snapshot-id", "v2", "--repo", "r", "--storage-kg", "s",
     "--journal", "j"],                                                                       # not a full commit
    ["--corpus", "c", "--confirm-commit", "a" * 40, "--repo", "r", "--storage-kg", "s", "--journal", "j"],  # no id
    ["--corpus", "c", "--confirm-commit", "a" * 40, "--snapshot-id", "v2", "--storage-kg", "s", "--journal", "j"],
    ["--corpus", "c", "--reconcile-edits", "--confirm-commit", "a" * 40, "--parent", "v1", "--storage-kg", "s",
     "--journal", "j", "--snapshot-id", "v2", "--repo", "r"],                                 # one mode at a time
])
def test_reconciliation_modes_check_their_own_requirements_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as stop:
        cmd.parse_args(argv)
    assert stop.value.code == 2 and capsys.readouterr().err


def test_reconciliation_modes_parse_without_terms():
    args = cmd.parse_args(["--corpus", "c", "--reconcile-edits", "--parent", "v1", "--storage-kg", "s",
                           "--state-dir", "d", "--target", "http://127.0.0.1:8000"])
    assert args.reconcile_edits and args.terms is None and args.target == "http://127.0.0.1:8000"
    args = cmd.parse_args(["--corpus", "c", "--confirm-commit", "a" * 40, "--snapshot-id", "v2", "--repo", "r",
                           "--storage-kg", "s", "--journal", "j"])
    assert args.confirm_commit == "a" * 40 and not args.reconcile_edits and args.journal == Path("j")


def test_help_exits_0(capsys):
    with pytest.raises(SystemExit) as stop:
        cmd.parse_args(["--help"])
    assert stop.value.code == 0 and "--extraction-revision" in capsys.readouterr().out


@pytest.mark.parametrize(("text", "expected"), [
    ("extract_terms:2.1", {"extractor": "extract_terms", "version": "2.1"}),
    ("extract_terms:2.1:lbl/cborg-chat", {"extractor": "extract_terms", "version": "2.1", "model": "lbl/cborg-chat"}),
    ("x:1:vendor:model:tag", {"extractor": "x", "version": "1", "model": "vendor:model:tag"}),
])
def test_revision_parsing(text, expected):
    assert cmd._revision(text) == expected


def test_revision_without_a_version_is_an_error():
    with pytest.raises(ValueError, match="extractor:version"):
        cmd._revision("onlyname")


def test_main_builds_the_request_from_argv_and_prints_a_json_summary(monkeypatch, tmp_path, capsys):
    seen = []
    monkeypatch.setattr(cmd, "create_snapshot", lambda request: seen.append(request) or _result(tmp_path))
    code = cmd.main(["--corpus", "rsoxs", "--terms", str(tmp_path / "t.json"), "--storage-kg", str(tmp_path),
                     "--snapshot-id", "v3", "--schema", "matkg", "--parent", "v2", "--parent-relation", "update",
                     "--parent-data-commit", "abc123", "--extraction-revision", "ex:1:m", "--extraction-revision", "ex2:2",
                     "--source-sha256", "11" * 32, "--corpus-revision-id", "rev1", "--terms-ref", "private:x",
                     "--code-commit", "def456", "--run-id", "2026-10-14", "--profile", "ci",
                     "--owner-waiver-ref", "waiver-7"])
    assert code == 0
    req = seen[0]
    assert (req.corpus, req.terms_path, req.storage_kg, req.schema_id, req.snapshot_id) == (
        "rsoxs", tmp_path / "t.json", tmp_path, "matkg", "v3")
    assert (req.parent_snapshot_id, req.parent_relation, req.parent_data_commit) == ("v2", "update", "abc123")
    assert req.extraction_revisions == [{"extractor": "ex", "version": "1", "model": "m"},
                                        {"extractor": "ex2", "version": "2"}]
    assert req.source_sha256 == ["11" * 32] and req.corpus_revision_id == "rev1" and req.terms_ref == "private:x"
    assert (req.code_commit, req.run_id, req.profile, req.owner_waiver_ref) == ("def456", "2026-10-14", "ci", "waiver-7")
    summary = json.loads(capsys.readouterr().out)
    assert summary == {"snapshot_id": "v3", "corpus": "rsoxs", "directory": str(tmp_path / "rsoxs" / "v3"),
                       "graph_sha256": "ab" * 32, "flat_copy": str(tmp_path / "flat.json"), "validation": "valid",
                       "disclosure": "clean", "commit_ready": True, "warnings": ["w1"]}


def test_main_verbose_turns_on_debug_logging(monkeypatch, tmp_path):
    monkeypatch.setattr(cmd, "create_snapshot", lambda request: _result(tmp_path))
    monkeypatch.setattr(logging.getLogger(), "handlers", [])  # basicConfig only acts on an unconfigured root
    assert cmd.main(["--corpus", "c", "--terms", "t", "-v"]) == 0
    assert logging.getLogger().level == logging.DEBUG
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    assert cmd.main(["--corpus", "c", "--terms", "t"]) == 0
    assert logging.getLogger().level == logging.WARNING


@pytest.mark.parametrize("failure", [ValueError("bad terms"), LookupError("no such schema"), FileNotFoundError("t.json")])
def test_main_reports_expected_failures_and_returns_1(monkeypatch, capsys, failure):
    def boom(request):
        raise failure

    monkeypatch.setattr(cmd, "create_snapshot", boom)
    assert cmd.main(["--corpus", "c", "--terms", "t"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("ERROR: ") and str(failure) in out


def test_a_bad_extraction_revision_is_reported_before_any_snapshot_is_made(monkeypatch, capsys):
    monkeypatch.setattr(cmd, "create_snapshot", lambda request: pytest.fail("must not be reached"))
    assert cmd.main(["--corpus", "c", "--terms", "t", "--extraction-revision", "nocolon"]) == 1
    assert "extractor:version" in capsys.readouterr().out


def test_unexpected_errors_are_not_swallowed(monkeypatch):
    def boom(request):
        raise RuntimeError("bug")

    monkeypatch.setattr(cmd, "create_snapshot", boom)
    with pytest.raises(RuntimeError):
        cmd.main(["--corpus", "c", "--terms", "t"])
