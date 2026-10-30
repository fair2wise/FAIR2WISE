#!/usr/bin/env python3
"""Per-host record of which image digest FAIR2WISE runs, for rollout and rollback (T24E, DEP-16/21).

The Ansible role never deploys a tag: every container is created from
``<repository>@sha256:<digest>`` (DEPLOYMENT_STRATEGY.md section 7, ANSIBLE_HANDOFF.md
"image_ref"). This file keeps, on the deployment host, the ordered history of digests the
role put in service so that a rollback is a re-point to a recorded previous digest, not a
guess, and so that both digests are in the receipt. Image rollback and graph/JSON data
rollback are different operations (D31, DEP-21); this record is only about the code digest
and the state-format it was approved for.

``record``   append a rollout: ``{profile, digest, previous_digest, repository, state_schema,
             reason, at, kind: "rollout"}``; refuses a tag, a digest for another profile's
             file, and a repository change.
``rollback`` append ``{kind: "rollback", digest: <the previous entry's digest>,
             previous_digest: <the digest in service>, rollback_of: <its entry id>}``
             and print the digest to re-point to; refuses when there is nothing earlier.
``current``  print the entry in service (what the container should be running).

Standard library only; copied to the host by the role. Records are JSON, never secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCHEMA = "f2w.release-record/v1"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROFILES = ("local", "als-dev", "als-prod")
EXIT_REFUSED = 3


class RecordError(Exception):
    """A refused record operation."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"schema": SCHEMA, "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecordError(f"release record {path} is unreadable: {exc}") from None
    if data.get("schema") != SCHEMA or not isinstance(data.get("entries"), list):
        raise RecordError(f"release record {path} has schema {data.get('schema')!r}, expected {SCHEMA}")
    return data


def save(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


def current(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return data["entries"][-1] if data["entries"] else None


def _check_digest(digest: str) -> str:
    if not DIGEST_RE.match(digest or ""):
        raise RecordError("image reference must be an immutable digest sha256:<64 hex>, never a tag")
    return digest


def _check_profile(data: Dict[str, Any], profile: str, repository: str) -> None:
    if profile not in PROFILES:
        raise RecordError(f"unknown profile {profile!r}")
    if ":" in repository.rsplit("/", 1)[-1] or "@" in repository:
        raise RecordError("repository must be the bare image repository, without a tag or digest")
    head = current(data)
    if head is not None:
        if head["profile"] != profile:
            raise RecordError(f"this record belongs to profile {head['profile']!r}, not {profile!r}")
        if head["repository"] != repository:
            raise RecordError(f"repository changed from {head['repository']!r} to {repository!r}; a new record "
                              "file is needed for a different repository")


def record(path: Path, *, profile: str, repository: str, digest: str, state_schema: str, reason: str,
           actor: str = "") -> Dict[str, Any]:
    data = load(path)
    _check_digest(digest)
    _check_profile(data, profile, repository)
    head = current(data)
    entry = {
        "id": uuid.uuid4().hex,
        "kind": "rollout",
        "at": _now(),
        "profile": profile,
        "repository": repository,
        "digest": digest,
        "previous_digest": head["digest"] if head else None,
        "state_schema": state_schema,
        "reason": reason,
        "actor": actor,
        "rollback_of": None,
    }
    data["entries"].append(entry)
    save(path, data)
    return entry


def rollback(path: Path, *, profile: str, reason: str, actor: str = "", to_digest: Optional[str] = None,
             state_schema: Optional[str] = None) -> Dict[str, Any]:
    data = load(path)
    head = current(data)
    if head is None:
        raise RecordError("nothing has been recorded on this host; there is no previous digest to roll back to")
    if head["profile"] != profile:
        raise RecordError(f"this record belongs to profile {head['profile']!r}, not {profile!r}")
    if to_digest is not None:
        _check_digest(to_digest)
        candidates = [e for e in data["entries"] if e["digest"] == to_digest and e is not head]
        if not candidates:
            raise RecordError(f"{to_digest} was never recorded on this host; rollback re-points only to a "
                              "recorded digest")
        target = candidates[-1]
    else:
        earlier = [e for e in data["entries"][:-1] if e["digest"] != head["digest"]]
        if not earlier:
            raise RecordError("only one digest has ever been in service here; nothing to roll back to")
        target = earlier[-1]
    if state_schema is not None and target.get("state_schema") != state_schema:
        raise RecordError(f"previous digest was approved for state schema {target.get('state_schema')!r}, the "
                          f"mounted state is {state_schema!r}; validate compatibility before rolling back")
    entry = {
        "id": uuid.uuid4().hex,
        "kind": "rollback",
        "at": _now(),
        "profile": profile,
        "repository": head["repository"],
        "digest": target["digest"],
        "previous_digest": head["digest"],
        "state_schema": target.get("state_schema"),
        "reason": reason,
        "actor": actor,
        "rollback_of": head["id"],
    }
    data["entries"].append(entry)
    save(path, data)
    return entry


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record-file", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("record")
    r.add_argument("--profile", required=True, choices=PROFILES)
    r.add_argument("--repository", required=True)
    r.add_argument("--digest", required=True)
    r.add_argument("--state-schema", required=True, help="state format the digest was approved for")
    r.add_argument("--reason", default="")
    r.add_argument("--actor", default="")
    b = sub.add_parser("rollback")
    b.add_argument("--profile", required=True, choices=PROFILES)
    b.add_argument("--to-digest", default=None, help="a recorded digest; default: the previous one")
    b.add_argument("--state-schema", default=None, help="refuse unless the target was approved for this schema")
    b.add_argument("--reason", default="")
    b.add_argument("--actor", default="")
    sub.add_parser("current")
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            out = record(args.record_file, profile=args.profile, repository=args.repository, digest=args.digest,
                         state_schema=args.state_schema, reason=args.reason, actor=args.actor)
        elif args.command == "rollback":
            out = rollback(args.record_file, profile=args.profile, reason=args.reason, actor=args.actor,
                           to_digest=args.to_digest, state_schema=args.state_schema)
        else:
            out = current(load(args.record_file)) or {}
    except RecordError as exc:
        print(json.dumps({"result": "refused", "reason": str(exc)}))
        return EXIT_REFUSED
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
