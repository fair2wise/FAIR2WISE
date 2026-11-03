#!/usr/bin/env python3
r"""Adopt legacy flat KG files as nested releases and print data-release descriptors (T26A, owner decision O-R1 Q2).

Console script: ``f2w-kg-adopt`` (``scripts/adopt_legacy_kg.py`` stays as a launch facade).

No Git writes, no network, no Tiled. An existing frozen directory is never overwritten.

  python3 scripts/adopt_legacy_kg.py adopt [--storage-kg storage/kg] [--code-commit <sha>] [--created-at <UTC>] [--dry-run]
      Runs the release_contract.md section 4 checks on every top-level storage/kg entry and writes
      storage/kg/<corpus>/v<N>/{graph.json,manifest.json,validation.json} for each that passes. Prints a JSON report
      of adopted and skipped entries with the exact rule. Nothing is allowlisted here.

  python3 scripts/adopt_legacy_kg.py descriptor --commit <full sha> --corpus <c> --snapshot v1 \
      --repository github.com/<org>/<repo> [--out <path outside the repository>]
      Builds a state=candidate descriptor from the objects at the commit and validates it against
      schemas/data_release_descriptor.schema.json. Default output is stdout. Writing inside the repository is
      refused: where descriptors live is owner question Q6.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime
import json
from pathlib import Path
import sys

from ..cli_output import output
from ..graph import adoption
from ..graph.releases import ReleaseVerificationError
from ..graph.snapshot import SnapshotError
from ..resources import resource_root

REPO_ROOT = resource_root() or Path.cwd()

EXIT_OK, EXIT_ERROR = 0, 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the ``adopt`` / ``descriptor`` command line."""
    parser = argparse.ArgumentParser(description="Adopt legacy flat KG files; build release descriptors")
    sub = parser.add_subparsers(dest="command", required=True)
    ad = sub.add_parser("adopt", help="adopt every passing flat file under storage/kg")
    ad.add_argument("--storage-kg", type=Path, default=REPO_ROOT / "storage" / "kg")
    ad.add_argument("--only", action="append", default=None, help="restrict to this file name (repeatable)")
    ad.add_argument("--code-commit", default=None, help="adopting revision (default: HEAD, read without Git)")
    ad.add_argument("--created-at", default=None, help="manifest created.at, ISO-8601 UTC (default: now)")
    ad.add_argument("--run-id", default=None)
    ad.add_argument("--dry-run", action="store_true", help="run every check, write nothing")
    de = sub.add_parser("descriptor", help="build and validate a state=candidate descriptor")
    de.add_argument("--repo", type=Path, default=REPO_ROOT)
    de.add_argument("--commit", required=True)
    de.add_argument("--corpus", required=True)
    de.add_argument("--snapshot", required=True)
    de.add_argument("--repository", required=True)
    de.add_argument("--importer-name", default="json_to_tiled")
    de.add_argument("--importer-version", default="unknown")
    de.add_argument("--out", type=Path, default=None, help="file outside the repository (default: stdout)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: adopt flat KG files or print an adoption descriptor; exit status 0 or 1."""
    args = parse_args(argv)
    try:
        if args.command == "adopt":
            now = datetime.fromisoformat(args.created_at.replace("Z", "+00:00")) if args.created_at else None
            outcomes = adoption.adopt_legacy_directory(
                args.storage_kg, only=args.only, code_commit=args.code_commit, now=now, run_id=args.run_id,
                dry_run=args.dry_run)
            output.info(json.dumps({"outcomes": [o.to_dict() for o in outcomes]}, indent=2, sort_keys=True))
            return EXIT_OK
        out = args.out.resolve() if args.out else None
        if out is not None and (out == args.repo.resolve() or args.repo.resolve() in out.parents):
            output.info("ERROR: --out must be outside the repository (descriptor location is owner question Q6)")
            return EXIT_ERROR
        descriptor = adoption.build_adoption_descriptor(
            args.repo, args.commit, args.corpus, args.snapshot, repository=args.repository,
            importer_name=args.importer_name, importer_version=args.importer_version)
        data = adoption.descriptor_bytes(descriptor)
        if out is None:
            sys.stdout.write(data.decode("utf-8"))
        else:
            with out.open("xb") as handle:
                handle.write(data)
        return EXIT_OK
    except (SnapshotError, ReleaseVerificationError, ValueError, OSError) as exc:
        output.info(f"ERROR: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
