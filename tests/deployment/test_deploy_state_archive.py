"""Restore and rollback evidence without a container (T24E, DEP-17 and DEP-21).

``deploy/scripts/f2w_state_archive.py`` and ``deploy/scripts/f2w_release_record.py`` are the
host tools the Ansible plays call. Here they run against a temporary directory shaped like
the ``/var/lib/fair2wise/state`` mount: a real job database written by the application's own
``JobStore`` (``session/operations/jobs.sqlite3``), session files and traces. The tests prove:

* a backup restores byte for byte (a quiescent database is copied raw);
* a database with an open writer is copied consistently (rows equal, integrity ok);
* a tampered member, manifest or sidecar is refused with nothing written;
* a member path that escapes the destination is refused;
* an archive from another profile is refused unless explicitly allowed;
* the release record keeps previous and new digest on every rollout and rollback, and
  refuses tags, unknown digests, profile/repository changes and a state-schema mismatch.

Restore onto a real new host and the running container's behaviour on the restored state
still need D3.11 (see ``tests/deployment/README.md``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Dict

import pytest

from app.modules.jobs.keys import OperationInputs
from app.modules.jobs.store import JobStore

REPO = Path(__file__).resolve().parents[2]
ARCHIVE_TOOL = REPO / "deploy" / "scripts" / "f2w_state_archive.py"
RECORD_TOOL = REPO / "deploy" / "scripts" / "f2w_release_record.py"
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
REPO_NAME = "ghcr.io/fair2wise/fair2wise"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


archive = _load(ARCHIVE_TOOL)
release = _load(RECORD_TOOL)


def tree_hashes(root: Path) -> Dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def make_state(root: Path) -> Path:
    """A state mount as the images lay it out (profile JSON: F2W_WORKDIR, F2W_JOB_DB, F2W_TRACE_DIR)."""
    state = root / "state"
    ops = state / "session" / "operations"
    ops.mkdir(parents=True)
    store = JobStore(ops / "jobs.sqlite3")
    store.submit("build_json", OperationInputs(corpus_scope=("paper-a", "paper-b")), created_by="local-operator")
    store.submit("import_tiled", OperationInputs(corpus_scope=("paper-a",)), created_by="local-operator")
    store.db.close()
    (state / "session" / "sessions").mkdir()
    (state / "session" / "sessions" / "s1.json").write_text(json.dumps({"session": "s1", "turns": 3}), encoding="utf-8")
    (state / "session" / "approvals.json").write_text("[]\n", encoding="utf-8")
    (state / "traces").mkdir()
    (state / "traces" / "2026-10-29.jsonl").write_text('{"event": "chat"}\n{"event": "complete"}\n', encoding="utf-8")
    (state / "traces" / "empty.jsonl").write_bytes(b"")
    return state


def run_tool(tool: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(tool), *args], capture_output=True, text=True,
                          env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})


def retar(src: Path, dst: Path, mutate) -> None:
    """Rewrite ``src`` as ``dst`` letting ``mutate(member, data) -> data`` change member bytes."""
    with tarfile.open(src, "r:gz") as tin, tarfile.open(dst, "w:gz", format=tarfile.PAX_FORMAT) as tout:
        for member in tin.getmembers():
            fh = tin.extractfile(member)
            data = fh.read() if fh is not None else b""
            data = mutate(member, data)
            info = tarfile.TarInfo(member.name)
            info.size, info.mode, info.mtime = len(data), member.mode, member.mtime
            tout.addfile(info, io.BytesIO(data))


def sign(path: Path) -> None:
    archive.sidecar_for(path).write_text(f"{archive.sha256_file(path)}  {path.name}\n", encoding="utf-8")


# ---- backup / restore -------------------------------------------------------------------------
def test_backup_then_restore_is_byte_identical_and_the_manifest_describes_every_file(tmp_path) -> None:
    state = make_state(tmp_path)
    before = tree_hashes(state)
    out = tmp_path / "backups" / "state-als-dev-1.tar.gz"
    proc = run_tool(ARCHIVE_TOOL, "backup", "--state-root", str(state), "--archive", str(out),
                    "--profile", "als-dev", "--image-digest", D1, "--note", "pre-rollout")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    receipt = json.loads(proc.stdout)
    assert receipt["result"] == "ok" and receipt["file_count"] == len(before) == 5
    assert archive.sidecar_for(out).read_text(encoding="utf-8").split()[0] == archive.sha256_file(out)

    with tarfile.open(out, "r:gz") as tar:
        names = [m.name for m in tar.getmembers()]
        manifest = json.loads(tar.extractfile(tar.getmembers()[0]).read())
    assert names[0] == "manifest.json" and all(n.startswith("state/") for n in names[1:])
    assert manifest["schema"] == "f2w.state-archive/v1" and manifest["profile"] == "als-dev"
    assert manifest["image_digest"] == D1 and manifest["note"] == "pre-rollout"
    by_path = {f["path"]: f for f in manifest["files"]}
    assert by_path["session/operations/jobs.sqlite3"]["kind"] == "sqlite-raw", "no open writer: copied raw"
    assert {f["path"] for f in manifest["files"]} == set(before)
    assert all(f["sha256"] == before[f["path"]] for f in manifest["files"])

    dest = tmp_path / "new-host" / "state"
    proc = run_tool(ARCHIVE_TOOL, "restore", "--archive", str(out), "--destination", str(dest),
                    "--expected-profile", "als-dev")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["image_digest"] == D1
    assert tree_hashes(dest) == before, "restore is byte-identical"
    # The restored job store opens and holds the same jobs.
    store = JobStore(dest / "session" / "operations" / "jobs.sqlite3")
    try:
        assert sorted(j.kind for j in store.list_jobs()) == ["build_json", "import_tiled"]
    finally:
        store.db.close()


def test_a_database_with_an_open_writer_is_copied_consistently(tmp_path) -> None:
    state = make_state(tmp_path)
    db = state / "session" / "operations" / "jobs.sqlite3"
    live = JobStore(db)  # WAL mode; -wal/-shm sidecars exist while it is open
    live.submit("build_json", OperationInputs(corpus_scope=("paper-c",)), created_by="x")
    try:
        assert db.with_name("jobs.sqlite3-wal").exists()
        out = tmp_path / "live.tar.gz"
        manifest = archive.backup(state, out, profile="als-prod")
        kinds = {f["path"]: f["kind"] for f in manifest["files"]}
        assert kinds["session/operations/jobs.sqlite3"] == "sqlite-backup"
        assert not any(p.endswith(("-wal", "-shm")) for p in kinds), "sidecars are folded in, never archived"
        dest = tmp_path / "restored"
        archive.restore(out, dest, expected_profile="als-prod")
        restored = JobStore(dest / "session" / "operations" / "jobs.sqlite3")
        try:
            assert sorted(j.job_id for j in restored.list_jobs()) == sorted(j.job_id for j in live.list_jobs())
            assert len(restored.list_jobs()) == 3
        finally:
            restored.db.close()
        con = sqlite3.connect(str(dest / "session" / "operations" / "jobs.sqlite3"))
        try:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            con.close()
    finally:
        live.db.close()


def test_a_tampered_member_is_refused_and_nothing_is_written(tmp_path) -> None:
    state = make_state(tmp_path)
    good = tmp_path / "good.tar.gz"
    archive.backup(state, good, profile="als-dev")

    bad = tmp_path / "bad.tar.gz"

    def flip(member, data):
        if member.name == "state/traces/2026-10-29.jsonl":
            return data.replace(b"complete", b"COMPLETE")  # same size, different bytes
        return data

    retar(good, bad, flip)
    sign(bad)  # the attacker can re-sign the archive; the manifest hashes still disagree
    dest = tmp_path / "restored"
    proc = run_tool(ARCHIVE_TOOL, "restore", "--archive", str(bad), "--destination", str(dest))
    assert proc.returncode == 3
    assert json.loads(proc.stdout) == {"result": "refused", "reason":
                                       "archive member traces/2026-10-29.jsonl does not match the manifest (size or sha256)"}
    assert not dest.exists(), "refused before anything was written"
    # verify alone reports the same without a destination
    proc = run_tool(ARCHIVE_TOOL, "verify", "--archive", str(bad))
    assert proc.returncode == 3


def test_sidecar_mismatch_and_missing_sidecar_are_refused(tmp_path) -> None:
    state = make_state(tmp_path)
    out = tmp_path / "s.tar.gz"
    archive.backup(state, out, profile="als-dev")
    sidecar = archive.sidecar_for(out)
    sidecar.write_text("0" * 64 + f"  {out.name}\n", encoding="utf-8")
    with pytest.raises(archive.ArchiveError, match="sidecar"):
        archive.verify(out)
    sidecar.unlink()
    with pytest.raises(archive.ArchiveError, match="sidecar .* is missing"):
        archive.verify(out)
    manifest, members = archive.verify(out, require_sidecar=False)  # members are still hash-checked
    assert manifest["file_count"] == len(members) == 5


def test_a_rewritten_manifest_is_refused_by_hash_and_a_foreign_profile_by_policy(tmp_path) -> None:
    state = make_state(tmp_path)
    good = tmp_path / "good.tar.gz"
    archive.backup(state, good, profile="als-prod")

    # 1. Changing the manifest without re-signing: the sidecar catches it.
    swapped = tmp_path / "swapped.tar.gz"

    def relabel(member, data):
        if member.name == "manifest.json":
            m = json.loads(data)
            m["profile"] = "als-dev"
            return json.dumps(m).encode("utf-8")
        return data

    retar(good, swapped, relabel)
    archive.sidecar_for(swapped).write_text(archive.sidecar_for(good).read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(archive.ArchiveError, match="sidecar"):
        archive.restore(swapped, tmp_path / "r1", expected_profile="als-dev")

    # 2. A genuine als-prod archive is not restored onto an als-dev host unless told so.
    with pytest.raises(archive.ArchiveError, match="taken from profile 'als-prod'"):
        archive.restore(good, tmp_path / "r2", expected_profile="als-dev")
    assert not (tmp_path / "r2").exists()
    archive.restore(good, tmp_path / "r2", expected_profile="als-dev", allow_profile_change=True)
    assert tree_hashes(tmp_path / "r2") == tree_hashes(state)

    # 3. A manifest claiming a file the archive lacks, or an extra member, is refused.
    missing = tmp_path / "missing.tar.gz"
    with tarfile.open(good, "r:gz") as tin, tarfile.open(missing, "w:gz") as tout:
        for member in tin.getmembers():
            if member.name.endswith("approvals.json"):
                continue
            tout.addfile(member, tin.extractfile(member))
    sign(missing)
    with pytest.raises(archive.ArchiveError, match="missing from the archive"):
        archive.verify(missing)


def test_a_member_escaping_the_destination_is_refused(tmp_path) -> None:
    state = make_state(tmp_path)
    good = tmp_path / "good.tar.gz"
    archive.backup(state, good, profile="local")
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(good, "r:gz") as tin, tarfile.open(evil, "w:gz") as tout:
        for member in tin.getmembers():
            tout.addfile(member, tin.extractfile(member))
        payload = b"owned"
        info = tarfile.TarInfo("state/../../escaped.txt")
        info.size = len(payload)
        tout.addfile(info, io.BytesIO(payload))
    sign(evil)
    with pytest.raises(archive.ArchiveError, match="unsafe path"):
        archive.restore(evil, tmp_path / "dest" / "state", expected_profile="local")
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path / "dest").exists()


def test_restore_refuses_a_non_empty_destination_unless_replace(tmp_path) -> None:
    state = make_state(tmp_path)
    out = tmp_path / "a.tar.gz"
    archive.backup(state, out, profile="als-dev")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "leftover.txt").write_text("old", encoding="utf-8")
    with pytest.raises(archive.ArchiveError, match="not empty"):
        archive.restore(out, dest, expected_profile="als-dev")
    assert (dest / "leftover.txt").exists()
    archive.restore(out, dest, expected_profile="als-dev", replace=True)
    assert not (dest / "leftover.txt").exists() and tree_hashes(dest) == tree_hashes(state)


def test_backup_refuses_symlinks_and_bad_inputs(tmp_path) -> None:
    state = make_state(tmp_path)
    (state / "link").symlink_to(state / "traces")
    with pytest.raises(archive.ArchiveError, match="symlink"):
        archive.backup(state, tmp_path / "x.tar.gz", profile="als-dev")
    (state / "link").unlink()
    with pytest.raises(archive.ArchiveError, match="digest"):
        archive.backup(state, tmp_path / "x.tar.gz", profile="als-dev", image_digest="ghcr.io/x:als-dev")
    proc = run_tool(ARCHIVE_TOOL, "backup", "--state-root", str(state), "--archive", str(tmp_path / "y.tar.gz"),
                    "--profile", "staging")
    assert proc.returncode == 2, "argparse refuses an unknown profile"


# ---- release record: rollout and rollback -----------------------------------------------------
def test_release_record_keeps_previous_and_new_digest_through_rollout_and_rollback(tmp_path) -> None:
    rec = tmp_path / "releases.json"
    first = release.record(rec, profile="als-dev", repository=REPO_NAME, digest=D1, state_schema="f2w.state/v1",
                           reason="first rollout", actor="ansible")
    assert first["kind"] == "rollout" and first["digest"] == D1 and first["previous_digest"] is None
    second = release.record(rec, profile="als-dev", repository=REPO_NAME, digest=D2, state_schema="f2w.state/v1",
                            reason="second rollout")
    assert second["previous_digest"] == D1 and second["digest"] == D2
    assert release.current(release.load(rec))["digest"] == D2

    back = release.rollback(rec, profile="als-dev", reason="bad release", state_schema="f2w.state/v1")
    assert back["kind"] == "rollback"
    assert back["digest"] == D1, "re-pointed to the recorded previous digest"
    assert back["previous_digest"] == D2, "the digest that was in service is recorded too"
    assert back["rollback_of"] == second["id"]
    assert release.current(release.load(rec))["digest"] == D1
    data = json.loads(rec.read_text(encoding="utf-8"))
    assert data["schema"] == "f2w.release-record/v1" and [e["digest"] for e in data["entries"]] == [D1, D2, D1]
    assert all(DIGEST.match(e["digest"]) for e in data["entries"] for DIGEST in [release.DIGEST_RE])
    # A second rollback goes to the most recent *different* digest, not to D1 again.
    again = release.rollback(rec, profile="als-dev", reason="and back")
    assert again["digest"] == D2 and again["previous_digest"] == D1


def test_release_record_refusals(tmp_path) -> None:
    rec = tmp_path / "releases.json"
    with pytest.raises(release.RecordError, match="never a tag"):
        release.record(rec, profile="als-dev", repository=REPO_NAME, digest="als-dev", state_schema="s", reason="")
    with pytest.raises(release.RecordError, match="without a tag or digest"):
        release.record(rec, profile="als-dev", repository=REPO_NAME + ":als-dev", digest=D1, state_schema="s", reason="")
    with pytest.raises(release.RecordError, match="nothing has been recorded"):
        release.rollback(rec, profile="als-dev", reason="")
    release.record(rec, profile="als-dev", repository=REPO_NAME, digest=D1, state_schema="f2w.state/v1", reason="")
    with pytest.raises(release.RecordError, match="only one digest"):
        release.rollback(rec, profile="als-dev", reason="")
    with pytest.raises(release.RecordError, match="belongs to profile 'als-dev'"):
        release.record(rec, profile="als-prod", repository=REPO_NAME, digest=D2, state_schema="f2w.state/v1", reason="")
    with pytest.raises(release.RecordError, match="repository changed"):
        release.record(rec, profile="als-dev", repository="ghcr.io/other/image", digest=D2, state_schema="f2w.state/v1", reason="")
    release.record(rec, profile="als-dev", repository=REPO_NAME, digest=D2, state_schema="f2w.state/v2", reason="")
    with pytest.raises(release.RecordError, match="state schema"):
        release.rollback(rec, profile="als-dev", reason="", state_schema="f2w.state/v2")
    with pytest.raises(release.RecordError, match="never recorded"):
        release.rollback(rec, profile="als-dev", reason="", to_digest=D3)
    chosen = release.rollback(rec, profile="als-dev", reason="", to_digest=D1)
    assert chosen["digest"] == D1 and chosen["previous_digest"] == D2


def test_release_record_cli_exit_codes_and_json(tmp_path) -> None:
    rec = tmp_path / "releases.json"
    proc = run_tool(RECORD_TOOL, "--record-file", str(rec), "record", "--profile", "als-prod", "--repository", REPO_NAME,
                    "--digest", "ghcr.io/fair2wise/fair2wise:als-prod", "--state-schema", "f2w.state/v1")
    assert proc.returncode == 3 and json.loads(proc.stdout)["result"] == "refused"
    assert not rec.exists()
    for digest in (D1, D2):
        proc = run_tool(RECORD_TOOL, "--record-file", str(rec), "record", "--profile", "als-prod", "--repository",
                        REPO_NAME, "--digest", digest, "--state-schema", "f2w.state/v1", "--reason", "rollout")
        assert proc.returncode == 0, proc.stdout
    proc = run_tool(RECORD_TOOL, "--record-file", str(rec), "rollback", "--profile", "als-prod",
                    "--state-schema", "f2w.state/v1", "--reason", "ansible rollback")
    assert proc.returncode == 0
    entry = json.loads(proc.stdout)
    assert (entry["kind"], entry["digest"], entry["previous_digest"]) == ("rollback", D1, D2)
    proc = run_tool(RECORD_TOOL, "--record-file", str(rec), "current")
    assert json.loads(proc.stdout)["digest"] == D1
    assert oct(rec.stat().st_mode & 0o777) == "0o640"
