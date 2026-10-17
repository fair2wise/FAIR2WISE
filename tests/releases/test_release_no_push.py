"""T26B acceptance "no automatic Git push": no push code path exists, and an attempted push is refused.

Three independent proofs:

1. Structural: the only process the release module starts is ``git`` with a subcommand from the read-only
   allowlist; ``push`` (and every other writing subcommand) is refused before a process starts.
2. Behavioural: a full prepare -> owner commit -> verify -> export -> lookup run, with a bare remote configured as
   ``origin``, issues only allowlisted read-only Git commands and leaves the remote empty.
3. Static: neither the module nor the script contains a Git write invocation, and the script cannot start a
   process at all (it does not import subprocess/os process APIs) and has no push/commit option.
"""
from __future__ import annotations

import ast
import subprocess

import pytest

from app.modules.graph import releases
from app.modules.graph.releases import (
    GIT_READ_ONLY_COMMANDS,
    ReleaseVerificationError,
    build_data_release_descriptor,
    build_release_record,
    export_snapshot_selection,
    lookup_snapshot,
    validate_commit_candidate,
    verify_snapshot_selection,
)
from tests.releases.conftest import REPOSITORY, git
from tests.snapshots.snapshot_fakes import REPO

MODULE = REPO / "app/modules/graph/releases.py"
SCRIPT = REPO / "scripts/prepare_kg_release.py"
WRITING = ("push", "commit", "add", "tag", "fetch", "pull", "remote", "update-ref", "update-index", "reset",
           "checkout", "merge", "rebase", "config", "send-pack", "gc", "notes", "stash", "rm", "mv", "am")


def test_the_read_only_allowlist_contains_no_writing_subcommand():
    assert GIT_READ_ONLY_COMMANDS == {"cat-file", "ls-tree", "rev-parse", "status", "check-ignore",
                                      "hash-object", "ls-files"}
    assert not set(WRITING) & GIT_READ_ONLY_COMMANDS


ATTEMPTS = [("push",), ("push", "origin", "main"), ("commit", "-m", "x"), ("add", "."), ("tag", "v1"), ("fetch",),
            ("remote", "add", "o", "x"), ("hash-object", "-w", "f"), ("hash-object", "--write", "f"),
            ("-c", "x=y", "push"), ()]


@pytest.mark.parametrize("args", ATTEMPTS)
def test_an_attempted_push_or_other_write_is_refused_before_any_process_starts(tmp_path, monkeypatch, args):
    started = []
    monkeypatch.setattr(releases.subprocess, "run", lambda *a, **k: started.append(a) or None)
    with pytest.raises(ReleaseVerificationError) as err:
        releases._git(tmp_path, *args)
    assert err.value.code == "git_write_refused" and started == []


def test_a_full_release_run_issues_only_read_only_git_and_never_touches_the_remote(fx, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    fx.git("remote", "add", "origin", str(remote))
    calls = []
    real = subprocess.run

    def spy(argv, **kwargs):
        calls.append(list(argv))
        return real(argv, **kwargs)

    monkeypatch.setattr(releases.subprocess, "run", spy)
    candidate = validate_commit_candidate(fx.root, fx.rel, binary_report=fx.report().read_bytes(),
                                          binary_report_ref="private:binary/r.json")
    record = build_release_record(candidate, record_name="<release-dir>/r.json", repository=REPOSITORY)
    monkeypatch.setattr(releases.subprocess, "run", real)
    commit = fx.run_owner_commands(record)  # the owner commits by hand; still no push anywhere
    monkeypatch.setattr(releases.subprocess, "run", spy)
    committed = verify_snapshot_selection(fx.root, record, commit)
    descriptor = build_data_release_descriptor(committed, importer_name="i", importer_version="1")
    export_snapshot_selection(fx.root, commit, descriptor["artifacts"], tmp_path / "mount")
    lookup_snapshot(fx.root, commit, "synthetic", "v1")

    assert calls, "the release path reads Git"
    for argv in calls:
        assert argv[:2] == ["git", "-C"] and argv[3] == "--no-pager", argv
        assert argv[4] in GIT_READ_ONLY_COMMANDS, argv
        assert not set(argv) & {"push", "commit", "add", "fetch", "-w", "--write"}, argv
    assert git(remote, "for-each-ref") == "" and git(remote, "count-objects").startswith("0 objects")
    assert not any("push" in line.split() for line in record["owner_commands"])


def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def test_static_no_git_write_or_push_call_in_the_module_or_the_script():
    module = ast.parse(MODULE.read_text("utf-8"))
    for call in _calls(module):
        name = getattr(call.func, "attr", getattr(call.func, "id", ""))
        if name == "_git":  # every Git call names its subcommand literally, and it is read-only
            sub = call.args[1]
            assert isinstance(sub, ast.Constant) and sub.value in GIT_READ_ONLY_COMMANDS, ast.dump(sub)
        if name == "run":  # the single subprocess.run is inside _git and starts "git" only
            first = call.args[0]
            assert isinstance(first, ast.List) and first.elts[0].value == "git"
    spawners = ("run", "Popen", "call", "check_call", "check_output", "system", "spawnv")
    runs = [c for c in _calls(module) if getattr(c.func, "attr", "") in spawners]
    assert len(runs) == 1
    script = ast.parse(SCRIPT.read_text("utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(script) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names} | {n.module.split(".")[0] for n in ast.walk(script)
                                     if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"subprocess", "pty", "pexpect", "git", "dulwich", "pygit2", "shutil"}
    assert not any(getattr(c.func, "attr", "") in ("system", "popen", "execv", "spawnv") for c in _calls(script))


def test_the_cli_has_no_push_commit_or_tag_option(capsys):
    from scripts import prepare_kg_release as cli

    for argv in (["push"], ["commit"], ["prepare", "--push"], ["verify-committed", "--push"], ["export", "--tag"]):
        with pytest.raises(SystemExit) as exit_:
            cli.parse_args(argv)
        assert exit_.value.code == 2
    capsys.readouterr()
