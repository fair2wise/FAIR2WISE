#!/usr/bin/env python3
r"""Freeze an extracted-terms JSON into an immutable versioned KG snapshot (T23B), or reconcile edits (H5).

Console script: ``f2w-kg-snapshot`` (``scripts/build_kg_snapshot.py`` stays as a launch facade).

Default mode calls ``app.modules.graph.snapshot.create_snapshot``: json2kg builds the graph, the bytes are frozen
under ``storage/kg/<corpus>/<snapshot-id>/`` with a sidecar manifest plus the byte-identical flat compatibility
copy. No Tiled, network, shell or Git process is used, and an existing snapshot is never overwritten.

  python3 scripts/build_kg_snapshot.py --corpus rsoxs --terms storage/terminology/extracted_terms_rsoxs_v1.json \
      --extraction-revision extract_terms:2.1:lbl/cborg-chat --run-id 2026-10-14

Edit reconciliation (two explicit steps; json_kg_contract.md 6.2.2, plan 4.14, H5 rehearsal finding 2):

1. ``--reconcile-edits`` freezes ``v<N+1>`` from the live snapshot plus the per-corpus Save journal, relation
   ``edit_reconciliation`` (``snapshot.reconcile_snapshot``). The journal entries are marked ``reconciled`` only
   after the frozen files were re-read and hash-checked. The journal is the one Save writes and the publication
   step checks: ``<ops-root>/publication-state/<target>/<corpus>/edits.jsonl`` (``--state-dir`` + ``--target``,
   or ``--journal`` with the file itself). Nothing is written to Tiled or Git; the next publication imports
   the new version.

     f2w-kg-snapshot --corpus rsoxs --reconcile-edits --parent v2 --storage-kg <workdir>/storage_kg \
         --state-dir <ops-root>/publication-state --target http://127.0.0.1:8000

2. After the owner committed the version (``f2w-kg-release prepare`` / ``verify-committed`` and the printed Git
   commands), ``--confirm-commit <40-hex>`` verifies that the commit holds exactly the local bytes (read-only
   Git) and records the commit on those journal entries (``data_commit``), so a cross-environment promotion
   can check "reconciled into a new committed version" from the journal itself.

     f2w-kg-snapshot --corpus rsoxs --confirm-commit <DATA_COMMIT> --snapshot-id v3 --repo <repo> \
         --storage-kg <workdir>/storage_kg --journal <ops-root>/publication-state/<target>/<corpus>/edits.jsonl
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import hashlib
import json
import logging
from pathlib import Path
import re
import sys

from ..cli_output import output
from ..graph.edit_journal import EditJournal
from ..graph.imports.corpus_scope import journal_path
from ..graph.ports import ContractError
from ..graph.snapshot import (
    ReconcileRequest,
    SnapshotRequest,
    SnapshotResult,
    create_snapshot,
    reconcile_snapshot,
    record_reconciliation_commit,
)
from ..security.tiled_identity import normalize_tiled_target

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the snapshot command line."""
    parser = argparse.ArgumentParser(description="Create an immutable versioned JSON KG snapshot")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--terms", type=Path, default=None,
                        help="extracted-terms JSON (read only); required unless reconciling or confirming")
    parser.add_argument("--storage-kg", type=Path, default=None, help="default: <repo>/storage/kg")
    parser.add_argument("--snapshot-id", default=None, help="v<N>; default: next free number")
    parser.add_argument("--schema", default=None, help="registered schema id (default: chosen from the corpus)")
    parser.add_argument("--parent", default=None, help="parent snapshot id (the live snapshot when reconciling)")
    parser.add_argument("--parent-relation", default="rebuild")
    parser.add_argument("--parent-data-commit", default=None)
    parser.add_argument("--extraction-revision", action="append", default=[],
                        help="extractor:version[:model] (repeatable)")
    parser.add_argument("--source-sha256", action="append", default=[], help="per-source sha256 (repeatable)")
    parser.add_argument("--corpus-revision-id", default=None)
    parser.add_argument("--terms-ref", default=None, help="repo-relative path or private: reference")
    parser.add_argument("--code-commit", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--profile", default="local", choices=["local", "als-dev", "als-prod", "ci"])
    parser.add_argument("--owner-waiver-ref", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")

    mode = parser.add_argument_group("edit reconciliation (H5)")
    exclusive = mode.add_mutually_exclusive_group()
    exclusive.add_argument("--reconcile-edits", action="store_true",
                           help="freeze v<N+1> from the live snapshot (--parent) plus the edit journal; no terms")
    exclusive.add_argument("--confirm-commit", default=None, metavar="COMMIT",
                           help="record the full 40-hex commit that holds --snapshot-id on its reconciled edits")
    mode.add_argument("--journal", type=Path, default=None,
                      help="the per-corpus edit journal file (edits.jsonl); or derive it with --state-dir/--target")
    mode.add_argument("--state-dir", type=Path, default=None,
                      help="importer state directory (<ops-root>/publication-state) holding the journals")
    mode.add_argument("--target", default=None,
                      help="Tiled target the edits were journaled against (default: the journal's only target)")
    mode.add_argument("--journal-ref", default=None,
                      help="reference recorded for the journal (default private:publication-state/<corpus>/edits.jsonl)")
    mode.add_argument("--repo", type=Path, default=None,
                      help="--confirm-commit: Git repository holding the committed storage/kg (read-only)")
    args = parser.parse_args(argv)
    if args.reconcile_edits or args.confirm_commit:
        if args.storage_kg is None:
            parser.error("--storage-kg is required when reconciling or confirming")
        if args.journal is None and (args.state_dir is None or args.target is None):
            parser.error("name the journal: --journal FILE, or --state-dir DIR together with --target URL")
    else:
        if args.terms is None:
            parser.error("the following arguments are required: --terms")
    if args.reconcile_edits:
        if args.parent is None:
            parser.error("--reconcile-edits requires --parent (the live snapshot id)")
        if args.parent_relation not in ("rebuild", "edit_reconciliation"):
            parser.error("--reconcile-edits always records parent relation edit_reconciliation")
    if args.confirm_commit is not None:
        if not _COMMIT.match(args.confirm_commit):
            parser.error("--confirm-commit must be a full lowercase 40-hex Git commit id")
        if args.snapshot_id is None or args.repo is None:
            parser.error("--confirm-commit requires --snapshot-id and --repo")
    return args


def _revision(text: str) -> dict[str, str]:
    parts = text.split(":", 2)
    if len(parts) < 2:
        raise ValueError(f"--extraction-revision must be extractor:version[:model], got {text!r}")
    out = {"extractor": parts[0], "version": parts[1]}
    if len(parts) == 3:
        out["model"] = parts[2]
    return out


def _journal_and_target(args: argparse.Namespace) -> tuple[EditJournal, str]:
    """The journal file and the normalized target; the target may come from the journal's own records."""
    target = normalize_tiled_target(args.target) if args.target else None
    if args.journal is not None:
        journal = EditJournal(Path(args.journal))
    else:
        assert target is not None  # parse_args: --state-dir needs --target
        journal = EditJournal(journal_path(Path(args.state_dir), target, args.corpus))
    if not journal.path.is_file():
        raise ValueError(f"edit journal {journal.path} does not exist")
    if target is None:
        targets = sorted({r.target for r in journal.latest().values()})
        if len(targets) != 1:
            raise ValueError(f"--target is required: the journal names {len(targets)} target(s) {targets}")
        target = targets[0]
    return journal, target


def _summary(result: SnapshotResult) -> dict[str, object]:
    return {
        "snapshot_id": result.snapshot_id, "corpus": result.corpus, "directory": str(result.directory),
        "graph_sha256": result.graph_sha256, "flat_copy": str(result.flat_path),
        "validation": result.validation_status, "disclosure": result.disclosure.status,
        "commit_ready": result.commit_ready, "warnings": result.warnings}


def _run_build(args: argparse.Namespace) -> dict[str, object]:
    result = create_snapshot(SnapshotRequest(
        corpus=args.corpus, terms_path=args.terms, storage_kg=args.storage_kg, schema_id=args.schema,
        snapshot_id=args.snapshot_id, parent_snapshot_id=args.parent, parent_relation=args.parent_relation,
        parent_data_commit=args.parent_data_commit,
        extraction_revisions=[_revision(r) for r in args.extraction_revision],
        source_sha256=args.source_sha256, corpus_revision_id=args.corpus_revision_id,
        terms_ref=args.terms_ref, code_commit=args.code_commit, run_id=args.run_id, profile=args.profile,
        owner_waiver_ref=args.owner_waiver_ref))
    return _summary(result)


def _run_reconcile(args: argparse.Namespace) -> dict[str, object]:
    journal, target = _journal_and_target(args)
    result = reconcile_snapshot(ReconcileRequest(
        corpus=args.corpus, storage_kg=args.storage_kg, parent_snapshot_id=args.parent, edit_journal=journal,
        journal_target=target, journal_ref=args.journal_ref or f"private:publication-state/{args.corpus}/edits.jsonl",
        snapshot_id=args.snapshot_id, parent_data_commit=args.parent_data_commit, code_commit=args.code_commit,
        run_id=args.run_id, profile=args.profile, owner_waiver_ref=args.owner_waiver_ref))
    manifest_sha = hashlib.sha256(result.manifest_path.read_bytes()).hexdigest()
    return {**_summary(result), "relation": "edit_reconciliation", "parent": args.parent, "target": target,
            "journal": str(journal.path), "manifest_path": str(result.manifest_path), "manifest_sha256": manifest_sha,
            "validation_path": str(result.validation_path), "reconciled_edits": list(result.reconciled_edits),
            "edit_conflicts": list(result.edit_conflicts),
            "next": "commit the version (f2w-kg-release prepare / verify-committed), then "
                    f"f2w-kg-snapshot --corpus {args.corpus} --confirm-commit <COMMIT> --snapshot-id "
                    f"{result.snapshot_id} --repo <repo> --storage-kg {args.storage_kg} --journal {journal.path}"}


def _run_confirm(args: argparse.Namespace) -> dict[str, object]:
    journal, _target = _journal_and_target(args)
    done = record_reconciliation_commit(journal=journal, storage_kg=args.storage_kg, corpus=args.corpus,
                                        snapshot_id=args.snapshot_id, repo=args.repo, data_commit=args.confirm_commit)
    return {"corpus": done.corpus, "snapshot_id": done.snapshot_id, "data_commit": done.data_commit,
            "graph_sha256": done.graph_sha256, "journal": str(journal.path),
            "recorded": list(done.recorded), "already_recorded": list(done.already_recorded)}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: freeze the snapshot and print a JSON summary; exit status 0 or 1."""
    args = parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s: %(message)s")
    try:
        if args.reconcile_edits:
            summary = _run_reconcile(args)
        elif args.confirm_commit:
            summary = _run_confirm(args)
        else:
            summary = _run_build(args)
    except (ValueError, LookupError, OSError, ContractError) as exc:
        output.info(f"ERROR: {exc}")
        return 1
    output.info(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
