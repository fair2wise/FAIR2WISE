#!/usr/bin/env python3
"""Consistent backup and verified restore of the FAIR2WISE mounted state (T24E, DEP-17).

The ``/var/lib/fair2wise/state`` mount (bind-mounted from ``fair2wise_state_host_path`` by
the Ansible role) holds the job/curation/audit SQLite databases, session files and
traces. A mounted volume is durable storage, not a backup
(``docs/migration/companion/DEPLOYMENT_STRATEGY.md`` section 6): this tool produces an
archive that can be restored on a new disposable host and proves what it restored.

``backup``
    Walks the state root and archives every file. A SQLite database with no ``-wal``/``-shm``
    sidecar (no open connection: the container is stopped or the store is idle) is copied
    byte for byte; one with sidecars (a writer is open) is copied through the ``sqlite3``
    online backup API, which folds the WAL into a consistent copy (the sidecars themselves are
    never archived). Every other file is copied byte for byte. Every copy of a database must
    pass ``PRAGMA integrity_check``. The result is one ``.tar.gz`` whose first member is
    ``manifest.json``: schema, creation time, deployment profile, image digest (if given), and
    the kind, size and sha256 of every archived file. A sidecar ``<archive>.sha256`` carries
    the archive's own sha256.

``verify``
    Checks the sidecar, the manifest and every member hash without writing anything.

``restore``
    Refuses (exit 3) an archive whose sidecar, manifest or any member does not match, a
    member path that escapes the destination, a manifest for another profile (unless
    ``--allow-profile-change``) and a non-empty destination (unless ``--replace``). Only
    then does it write, and it writes nothing before the whole archive has been verified.
    With ``--owner UID:GID`` (root only) the restored tree is given to the container uid.

Standard library only: this file is copied to the deployment host by the role and runs
with the host's ``python3``. It never reads environment variables, ``.env`` files or
secrets; the state tree holds none of the writer secret (that is ``/run/secrets``).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = "f2w.state-archive/v1"
MANIFEST_NAME = "manifest.json"
STATE_PREFIX = "state"
SQLITE_SUFFIXES = (".sqlite3", ".sqlite", ".db")
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
PROFILES = ("local", "als-dev", "als-prod")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3


class ArchiveError(Exception):
    """A refused backup or restore; the message names the reason, never file contents."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_sqlite(path: Path) -> bool:
    return path.suffix in SQLITE_SUFFIXES


def is_sidecar(path: Path) -> bool:
    """``x.sqlite3-wal`` and friends: folded into the database copy, never archived alone."""
    name = path.name
    return any(name.endswith(s) and Path(name[: -len(s)]).suffix in SQLITE_SUFFIXES for s in SIDECAR_SUFFIXES)


def _relative_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames) + sorted(filenames):
            full = Path(dirpath) / name
            if full.is_symlink():
                raise ArchiveError(f"refusing symlink inside the state tree: {full.relative_to(root)}")
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if is_sidecar(full):
                continue
            files.append(full.relative_to(root))
    return files


def _has_sidecars(source: Path) -> bool:
    return any(source.with_name(source.name + s).exists() for s in SIDECAR_SUFFIXES)


def _consistent_sqlite_copy(source: Path, target: Path) -> str:
    """Copy a database consistently; return the copy kind recorded in the manifest.

    No sidecar: nothing has the database open, so a plain byte copy is both consistent and
    byte-identical (``sqlite-raw``). Sidecars present: a writer may be active, so the online
    backup API copies pages under the database's own locking (``sqlite-backup``).
    """
    if not _has_sidecars(source):
        shutil.copyfile(source, target)
        kind = "sqlite-raw"
    else:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30.0)
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        kind = "sqlite-backup"
    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        row = check.execute("PRAGMA integrity_check").fetchone()
    finally:
        check.close()
    if row is None or row[0] != "ok":
        raise ArchiveError(f"integrity_check failed on the copy of {source.name}")
    return kind


def build_manifest(*, profile: str, state_root: str, files: Sequence[Dict[str, Any]],
                   image_digest: Optional[str], note: str) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile": profile,
        "state_root": state_root,
        "image_digest": image_digest,
        "note": note,
        "file_count": len(files),
        "total_bytes": sum(int(f["size"]) for f in files),
        "files": list(files),
    }


def backup(state_root: Path, archive: Path, *, profile: str, image_digest: Optional[str] = None,
           note: str = "") -> Dict[str, Any]:
    """Write ``archive`` (``.tar.gz``) and ``archive.sha256``; return the manifest."""
    if profile not in PROFILES:
        raise ArchiveError(f"unknown profile {profile!r}; expected one of {', '.join(PROFILES)}")
    if image_digest is not None and not _is_digest(image_digest):
        raise ArchiveError("image digest must look like sha256:<64 hex>")
    if not state_root.is_dir():
        raise ArchiveError(f"state root {state_root} is not a directory")
    archive.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="f2w-state-backup-", dir=str(archive.parent)))
    entries: List[Dict[str, Any]] = []
    try:
        for rel in _relative_files(state_root):
            source = state_root / rel
            staged = staging / rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            if is_sqlite(source):
                kind = _consistent_sqlite_copy(source, staged)
            else:
                shutil.copyfile(source, staged)
                kind = "file"
            entries.append({"path": rel.as_posix(), "kind": kind, "size": staged.stat().st_size,
                            "sha256": sha256_file(staged), "mode": f"{source.stat().st_mode & 0o777:04o}"})
        manifest = build_manifest(profile=profile, state_root=str(state_root), files=entries,
                                  image_digest=image_digest, note=note)
        tmp_archive = archive.with_name(archive.name + ".partial")
        with tarfile.open(tmp_archive, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            data = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
            info = tarfile.TarInfo(MANIFEST_NAME)
            info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
            tar.addfile(info, io.BytesIO(data))
            for entry in entries:
                member = f"{STATE_PREFIX}/{entry['path']}"
                tar.add(staging / entry["path"], arcname=member, recursive=False)
        os.replace(tmp_archive, archive)
        sidecar_for(archive).write_text(f"{sha256_file(archive)}  {archive.name}\n", encoding="utf-8")
        return manifest
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def sidecar_for(archive: Path) -> Path:
    return archive.with_name(archive.name + ".sha256")


def _is_digest(value: str) -> bool:
    return len(value) == 71 and value.startswith("sha256:") and all(c in "0123456789abcdef" for c in value[7:])


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("..", "") for part in path.parts) or not path.parts:
        raise ArchiveError(f"refusing archive member with an unsafe path: {name!r}")
    return path


def read_manifest(tar: tarfile.TarFile) -> Dict[str, Any]:
    """The manifest, which must be the first member (the rest are ``state/...`` files)."""
    members = tar.getmembers()
    first = members[0] if members else None
    if first is None or first.name != MANIFEST_NAME or not first.isfile():
        raise ArchiveError(f"the first archive member must be {MANIFEST_NAME}")
    fh = tar.extractfile(first)
    assert fh is not None
    try:
        manifest = json.loads(fh.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"manifest is not valid JSON: {exc}") from None
    if manifest.get("schema") != SCHEMA:
        raise ArchiveError(f"manifest schema {manifest.get('schema')!r} is not {SCHEMA}")
    if manifest.get("profile") not in PROFILES:
        raise ArchiveError(f"manifest names an unknown profile {manifest.get('profile')!r}")
    if not isinstance(manifest.get("files"), list):
        raise ArchiveError("manifest has no file list")
    return manifest


def verify(archive: Path, *, require_sidecar: bool = True) -> Tuple[Dict[str, Any], List[tarfile.TarInfo]]:
    """Check sidecar, manifest and every member; return (manifest, members) without writing."""
    if not archive.is_file():
        raise ArchiveError(f"archive {archive} is missing")
    sidecar = sidecar_for(archive)
    if sidecar.is_file():
        recorded = sidecar.read_text(encoding="utf-8").split()[0] if sidecar.read_text(encoding="utf-8").split() else ""
        if recorded != sha256_file(archive):
            raise ArchiveError("archive sha256 does not match its .sha256 sidecar")
    elif require_sidecar:
        raise ArchiveError(f"sidecar {sidecar.name} is missing (pass --no-sidecar to accept an unsigned archive)")
    try:
        tar = tarfile.open(archive, "r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveError(f"archive cannot be opened: {exc}") from None
    with tar:
        manifest = read_manifest(tar)
        expected = {f["path"]: f for f in manifest["files"]}
        seen: Dict[str, str] = {}
        members: List[tarfile.TarInfo] = []
        for member in tar.getmembers()[1:]:
            rel = _safe_member(member.name)
            if rel.parts[0] != STATE_PREFIX or len(rel.parts) < 2:
                raise ArchiveError(f"unexpected archive member outside {STATE_PREFIX}/: {member.name}")
            if not member.isfile():
                raise ArchiveError(f"archive member {member.name} is not a regular file")
            key = PurePosixPath(*rel.parts[1:]).as_posix()
            entry = expected.get(key)
            if entry is None:
                raise ArchiveError(f"archive member {key} is not in the manifest")
            if key in seen:
                raise ArchiveError(f"archive member {key} appears twice")
            fh = tar.extractfile(member)
            assert fh is not None
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
            if size != int(entry["size"]) or digest.hexdigest() != entry["sha256"]:
                raise ArchiveError(f"archive member {key} does not match the manifest (size or sha256)")
            seen[key] = entry["sha256"]
            members.append(member)
        missing = sorted(set(expected) - set(seen))
        if missing:
            raise ArchiveError(f"{len(missing)} manifest file(s) are missing from the archive: {missing[:3]}")
    return manifest, members


def _dir_is_empty(path: Path) -> bool:
    return not any(path.iterdir())


def restore(archive: Path, destination: Path, *, expected_profile: Optional[str] = None,
            allow_profile_change: bool = False, replace: bool = False, require_sidecar: bool = True,
            owner: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
    """Verify, then extract ``archive`` into ``destination``; return the manifest."""
    manifest, _members = verify(archive, require_sidecar=require_sidecar)
    if expected_profile is not None and manifest["profile"] != expected_profile and not allow_profile_change:
        raise ArchiveError(f"archive was taken from profile {manifest['profile']!r}, this host runs "
                           f"{expected_profile!r}; pass --allow-profile-change to override")
    destination = destination.resolve()
    if destination.exists():
        if not destination.is_dir():
            raise ArchiveError(f"destination {destination} is not a directory")
        if not _dir_is_empty(destination):
            if not replace:
                raise ArchiveError(f"destination {destination} is not empty; pass --replace to clear it first")
            for child in destination.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    else:
        destination.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        read_manifest(tar)
        for member in tar.getmembers()[1:]:
            rel = PurePosixPath(*_safe_member(member.name).parts[1:])
            target = destination / Path(*rel.parts)
            if destination not in target.resolve().parents:
                raise ArchiveError(f"archive member {member.name} would land outside the destination")
            target.parent.mkdir(parents=True, exist_ok=True)
            fh = tar.extractfile(member)
            assert fh is not None
            with target.open("wb") as out:
                shutil.copyfileobj(fh, out)
            entry = next(f for f in manifest["files"] if f["path"] == rel.as_posix())
            os.chmod(target, int(entry.get("mode", "0600"), 8))
    for entry in manifest["files"]:
        target = destination / Path(*PurePosixPath(entry["path"]).parts)
        if sha256_file(target) != entry["sha256"]:
            raise ArchiveError(f"restored file {entry['path']} does not match the manifest after writing")
    if owner is not None:
        uid, gid = owner
        for dirpath, dirnames, filenames in os.walk(destination):
            os.chown(dirpath, uid, gid)
            for name in filenames:
                os.chown(os.path.join(dirpath, name), uid, gid)
        os.chown(destination, uid, gid)
    return manifest


def _parse_owner(value: str) -> Tuple[int, int]:
    try:
        uid, gid = value.split(":", 1)
        return int(uid), int(gid)
    except ValueError:
        raise argparse.ArgumentTypeError("owner must be UID:GID") from None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backup", help="archive the state tree with a manifest and sha256 sidecar")
    b.add_argument("--state-root", type=Path, required=True)
    b.add_argument("--archive", type=Path, required=True, help="output .tar.gz path")
    b.add_argument("--profile", required=True, choices=PROFILES)
    b.add_argument("--image-digest", default=None, help="sha256:<hex> of the running image, for the manifest")
    b.add_argument("--note", default="")

    v = sub.add_parser("verify", help="check an archive without writing")
    v.add_argument("--archive", type=Path, required=True)
    v.add_argument("--no-sidecar", action="store_true")

    r = sub.add_parser("restore", help="verify, then restore into an empty destination")
    r.add_argument("--archive", type=Path, required=True)
    r.add_argument("--destination", type=Path, required=True)
    r.add_argument("--expected-profile", choices=PROFILES, default=None)
    r.add_argument("--allow-profile-change", action="store_true")
    r.add_argument("--replace", action="store_true", help="clear a non-empty destination first")
    r.add_argument("--no-sidecar", action="store_true")
    r.add_argument("--owner", type=_parse_owner, default=None, help="UID:GID to give the restored tree")

    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            manifest = backup(args.state_root, args.archive, profile=args.profile,
                              image_digest=args.image_digest, note=args.note)
            result = {"result": "ok", "archive": str(args.archive), "sidecar": str(sidecar_for(args.archive)),
                      "file_count": manifest["file_count"], "total_bytes": manifest["total_bytes"],
                      "profile": manifest["profile"]}
        elif args.command == "verify":
            manifest, members = verify(args.archive, require_sidecar=not args.no_sidecar)
            result = {"result": "ok", "archive": str(args.archive), "file_count": len(members),
                      "profile": manifest["profile"], "created_at": manifest["created_at"]}
        else:
            manifest = restore(args.archive, args.destination, expected_profile=args.expected_profile,
                               allow_profile_change=args.allow_profile_change, replace=args.replace,
                               require_sidecar=not args.no_sidecar, owner=args.owner)
            result = {"result": "ok", "destination": str(args.destination), "file_count": manifest["file_count"],
                      "profile": manifest["profile"], "created_at": manifest["created_at"],
                      "image_digest": manifest.get("image_digest")}
    except ArchiveError as exc:
        print(json.dumps({"result": "refused", "reason": str(exc)}), file=sys.stdout)
        return EXIT_REFUSED
    print(json.dumps(result))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
