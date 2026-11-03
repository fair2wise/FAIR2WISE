#!/usr/bin/env python3
r"""Prepare a frozen KG snapshot for the owner's Git commit, then verify and export what was committed (T26B).

Console script: ``f2w-kg-release`` (``scripts/prepare_kg_release.py`` stays as a launch facade).

The script never commits and never pushes. It reads Git through ``app.modules.graph.releases``, whose Git access
is limited to read-only subcommands, and it prints the exact ``git`` commands the owner runs by hand.

1. Validate a snapshot and write the tested-release record (before the commit exists)::

     python3 scripts/prepare_kg_release.py prepare --snapshot storage/kg/rsoxs/v4 \
         --binary-report <report.json> --binary-report-ref private:binary/rsoxs-v4.json \
         --repository github.com/<org>/<repo> --out <release-dir>

   Checks: schema binding, validation.json and manifest hashes, disclosure, private dependencies, evidence
   availability (``--evidence-inventory`` / ``--evidence-root``), the binary report's binding and verdict, and a
   clean working tree. A PROVISIONAL or NOT_PASS verdict is recorded as such and never becomes PASS.

2. After the owner ran the printed commands, compare the committed bytes with the tested ones and write the T26A
   data-release descriptor::

     python3 scripts/prepare_kg_release.py verify-committed --record <release-dir>/<id>.release_record.json \
         --commit "$(git rev-parse HEAD)" --out <release-dir>

3. Export the selected bytes from the commit to a mount (import/promotion input)::

     python3 scripts/prepare_kg_release.py export --descriptor <release-dir>/<id>.descriptor.json --dest <mount>

Exit codes: 0 success, 2 refused (the reason code is printed), 1 usage or I/O error.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys
from typing import Any

from ..cli_output import output
from ..graph import releases
from ..graph.releases import ReleaseRefused, ReleaseVerificationError

EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Command line. There is intentionally no option that commits, tags or pushes."""
    parser = argparse.ArgumentParser(description="Commit-ready KG release checks (no Git writes, no push)")
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare", help="validate a snapshot and write the tested-release record")
    prep.add_argument("--repo", type=Path, default=Path.cwd(), help="top of the Git working tree (default: cwd)")
    prep.add_argument("--snapshot", required=True, help="repository-relative storage/kg/<corpus>/v<N>")
    prep.add_argument("--binary-report", required=True, type=Path, help="executed evals.binary JSON report")
    prep.add_argument("--binary-report-ref", required=True,
                      help="where the report is kept: repository-relative path or private: reference")
    prep.add_argument("--repository", required=True, help="canonical repository identity, github.com/<org>/<repo>")
    prep.add_argument("--out", required=True, type=Path, help="release directory (outside storage/kg)")
    prep.add_argument("--evidence-inventory", type=Path, default=None,
                      help='JSON {"sources": {<source>: {"ref": "private:...", "sha256": ...}}}')
    prep.add_argument("--evidence-root", type=Path, default=None, help="mounted private store for private: refs")
    prep.add_argument("--allowlist", type=Path, default=None, help="JSON list of reviewed R3/R4 allowlist keys")
    prep.add_argument("--allow-pending-review-schema", action="store_true",
                      help="accept a schema set pending owner review (explicit selection, e.g. 0.3.0)")
    prep.add_argument("--allow-untracked", action="store_true",
                      help="tolerate untracked files outside the candidate (they are never committed)")

    ver = sub.add_parser("verify-committed", help="committed bytes == tested bytes; write the descriptor")
    ver.add_argument("--repo", type=Path, default=Path.cwd())
    ver.add_argument("--record", required=True, type=Path)
    ver.add_argument("--commit", required=True, help="full commit id (branches and tags never select data)")
    ver.add_argument("--out", required=True, type=Path)

    exp = sub.add_parser("export", help="export a descriptor's artifacts from its commit to a mount")
    exp.add_argument("--repo", type=Path, default=Path.cwd())
    exp.add_argument("--descriptor", required=True, type=Path)
    exp.add_argument("--dest", required=True, type=Path)
    return parser.parse_args(argv)


def _load_json(path: Path | None) -> Any:
    return None if path is None else json.loads(path.read_text(encoding="utf-8"))


def _write_once(path: Path, data: bytes) -> str:
    """Create ``path`` exclusively; an identical existing file is accepted, a different one refused."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != data:
            raise ReleaseRefused("output_exists", f"{path.name} already exists with other content")
        return "already_present"
    with path.open("xb") as handle:
        handle.write(data)
    return "written"


def _outside_kg(repo: Path, out: Path) -> None:
    kg = (repo / "storage" / "kg").resolve()
    target = out.resolve()
    if target == kg or kg in target.parents:
        raise ReleaseRefused("bad_path", "release records are never written under storage/kg (R1 allowlist)")


def _refusal(exc: ReleaseVerificationError) -> int:
    output.info(json.dumps({"status": "refused", "code": exc.code, "message": str(exc),
                      "findings": [f.to_dict() for f in getattr(exc, "findings", [])]}, indent=2, sort_keys=True))
    return EXIT_REFUSED


def cmd_prepare(args: argparse.Namespace) -> int:
    """Validate and write ``<release_id>.release_record.json``; print the owner's commands."""
    repo = args.repo.resolve()
    _outside_kg(repo, args.out)
    allowlist = frozenset(_load_json(args.allowlist) or [])
    candidate = releases.validate_commit_candidate(
        repo, args.snapshot, binary_report=args.binary_report.read_bytes(),
        binary_report_ref=args.binary_report_ref, evidence_inventory=_load_json(args.evidence_inventory),
        evidence_root=args.evidence_root, allowlist=allowlist,
        allow_pending_review_schema=args.allow_pending_review_schema, allow_untracked=args.allow_untracked)
    record_path = args.out / f"{candidate.release_id}.release_record.json"
    record = releases.build_release_record(candidate, record_name=f"<release-dir>/{record_path.name}",
                                           repository=args.repository)
    data = releases.canonical_json_bytes(record)
    status = _write_once(record_path, data)
    binary = record["binary_report"]
    output.info(json.dumps({
        "status": "commit_ready", "release_id": record["release_id"], "record": record_path.name,
        "record_sha256": releases.sha256_bytes(data), "record_write": status,
        "binary_verdict": binary["verdict"], "binary_passed": binary["passed"],
        "evidence": {"status": record["evidence"]["status"], "counts": record["evidence"]["counts"],
                     "required_unavailable": record["evidence"]["required_unavailable"]},
        "paths": [a["path"] for a in record["artifacts"]]}, indent=2, sort_keys=True))
    output.info("\nOwner commands (run from the repository root; this tool runs none of them and never pushes):")
    for line in record["owner_commands"]:
        output.info(f"  {line}")
    return EXIT_OK


def cmd_verify_committed(args: argparse.Namespace) -> int:
    """Verify the commit against the record and write ``<release_id>.descriptor.json``."""
    from ..graph.imports.tiled_json import ADAPTER_VERSION

    repo = args.repo.resolve()
    _outside_kg(repo, args.out)
    record = releases.parse_release_record(args.record.read_bytes())
    committed = releases.verify_snapshot_selection(repo, record, args.commit)
    descriptor = releases.build_data_release_descriptor(
        committed, importer_name="app.modules.graph.imports.tiled_json", importer_version=ADAPTER_VERSION)
    data = releases.canonical_json_bytes(descriptor)
    path = args.out / f"{descriptor['release_id']}.descriptor.json"
    status = _write_once(path, data)
    output.info(json.dumps({
        "status": "committed_equals_tested", "release_id": descriptor["release_id"],
        "data_commit": descriptor["data_commit"], "descriptor": path.name,
        "descriptor_sha256": releases.sha256_bytes(data), "descriptor_write": status,
        "state": descriptor["state"], "binary_verdict": record["binary_report"]["verdict"],
        "artifacts": [{"path": a["path"], "git_blob": a["git_blob"]} for a in descriptor["artifacts"]]},
        indent=2, sort_keys=True))
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    """Export a descriptor's pinned artifacts from its commit to ``--dest``."""
    descriptor: dict[str, Any] = _load_json(args.descriptor)
    if not isinstance(descriptor, dict) or descriptor.get("descriptor_version") != "1":
        raise ReleaseRefused("record_invalid", "not a T26A data-release descriptor")
    done: list[dict[str, Any]] = releases.export_snapshot_selection(
        args.repo.resolve(), str(descriptor.get("data_commit")), descriptor.get("artifacts") or [], args.dest)
    output.info(json.dumps({"status": "exported", "release_id": descriptor.get("release_id"),
                      "data_commit": descriptor.get("data_commit"), "files": done}, indent=2, sort_keys=True))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    handlers = {"prepare": cmd_prepare, "verify-committed": cmd_verify_committed, "export": cmd_export}
    try:
        return handlers[args.command](args)
    except ReleaseVerificationError as exc:
        return _refusal(exc)
    except (OSError, ValueError) as exc:
        output.info(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}))
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
