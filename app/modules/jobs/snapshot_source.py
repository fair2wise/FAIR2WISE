"""Adapters from the merged T23B snapshots and T05 staging to the publication ports.

* :class:`DirectorySnapshotSource` reads ``storage/kg/<corpus>/v<N>`` through
  ``graph.releases.verify_snapshot_dir``: the bytes must hash to the manifest, otherwise the load
  raises (tampering after freezing is detected before planning and again before import).
* :func:`staged_packages_check` reports staged packages (T05 ``paper.json``) that are incomplete or
  failed, each with a reason a person can act on, for the quarantine step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from ..contracts.errors import ContractError, ErrorCode
from ..graph.releases import ReleaseVerificationError, verify_snapshot_dir
from ..graph.snapshot_manifest import to_contract_manifest
from .publish_ports import FrozenSnapshot, PackageProblem


class DirectorySnapshotSource:
    """Frozen snapshots under a ``storage/kg`` root; refs are ``<corpus>/<snapshot id>``."""

    def __init__(self, storage_kg: Path) -> None:
        self.root = Path(storage_kg)

    def load(self, ref: str) -> FrozenSnapshot:
        """Load and verify the frozen snapshot at ``<corpus>/<snapshot id>``.

        Raises:
            ContractError: ``INVALID_INPUT`` if ``ref`` is malformed or the snapshot fails verification.
        """
        corpus, _, snapshot_id = ref.partition("/")
        if not corpus or not snapshot_id or "/" in snapshot_id or ".." in ref:
            raise ContractError(ErrorCode.INVALID_INPUT, "snapshot ref must be '<corpus>/<snapshot id>'")
        try:
            verified = verify_snapshot_dir(self.root / corpus / snapshot_id)
        except ReleaseVerificationError as exc:
            raise ContractError(ErrorCode.INVALID_INPUT, f"snapshot failed verification: {exc}") from exc
        return FrozenSnapshot(
            ref=ref,
            corpus=corpus,
            sha256=verified.graph_sha256,
            manifest=to_contract_manifest(verified.manifest),
        )


def staged_packages_check(root: Path):
    """A ``PackageCheck`` over staged packages: every ``package_keys`` entry must be a complete ``paper.json``."""

    def check(snapshot: FrozenSnapshot) -> Sequence[PackageProblem]:
        problems: list[PackageProblem] = []
        for key in snapshot.package_keys:
            path = Path(root) / key / "paper.json"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                problems.append(PackageProblem(key, "package is missing from staging"))
                continue
            except (OSError, ValueError):
                problems.append(PackageProblem(key, "package is unreadable or corrupt"))
                continue
            if payload.get("complete") is not True:
                stages = payload.get("stages") or {}
                open_stages = sorted(k for k, v in stages.items() if v not in ("complete", "not_run"))
                chunks = len(payload.get("incomplete_chunks") or [])
                reason = f"incomplete stage(s): {', '.join(open_stages) or 'unknown'}"
                if chunks:
                    reason += f"; {chunks} extraction chunk(s) failed"
                problems.append(PackageProblem(key, reason, {"stages": stages, "incomplete_chunks": chunks}))
        return problems

    return check


__all__ = ("DirectorySnapshotSource", "staged_packages_check")
