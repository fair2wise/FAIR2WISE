"""Per-target import receipts (T08B; json_kg_contract.md 5.5, release_contract.md section 8).

A receipt is a separate private artifact, never written into the snapshot. It wraps the shared
:class:`~app.modules.contracts.changes.ImportReceipt` and adds the fields section 5.5 requires that
the shared model does not carry (manifest hash, graph id, scope, requested/approved by, base and
resulting revision). It is immutable once written and valid only for the target and revision it names.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from app.modules.contracts.changes import ImportReceipt

from ..ports import ContractError, ErrorCode


class ReceiptExtras(BaseModel):
    """Section 5.5 fields beyond :class:`ImportReceipt`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_sha256: str
    snapshot_id: str
    graph_id: str
    corpus_scope: List[str]
    requested_by: str
    approved_by: str
    profile: str
    authorization_mode: str
    base_revision: str
    resulting_revision: str
    data_commit: Optional[str] = None
    operation_counts: Dict[str, int] = {}
    dangling_unimported: int = 0


class ReceiptArtifact(BaseModel):
    """What is stored: receipt + extras + ``receipt_sha256`` over both."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt: ImportReceipt
    extras: ReceiptExtras
    receipt_sha256: str


def _digest(receipt: ImportReceipt, extras: ReceiptExtras) -> str:
    body = {"receipt": receipt.model_dump(mode="json"), "extras": extras.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def make_artifact(receipt: ImportReceipt, extras: ReceiptExtras) -> ReceiptArtifact:
    """Combine and hash."""
    return ReceiptArtifact(receipt=receipt, extras=extras, receipt_sha256=_digest(receipt, extras))


def verify_artifact(artifact: ReceiptArtifact) -> bool:
    """The stored hash matches the content."""
    return artifact.receipt_sha256 == _digest(artifact.receipt, artifact.extras)


def receipt_valid_for(artifact: ReceiptArtifact, *, target: str, revision: str) -> bool:
    """A receipt covers only its own target and the revision it verified (staging never covers production)."""
    return (verify_artifact(artifact) and artifact.receipt.verified and artifact.receipt.target == target
            and artifact.extras.resulting_revision == revision)


class ReceiptStore:
    """Directory of immutable receipt files (``<receipt_id>.json``)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, receipt_id: str) -> Path:
        if not re.match(r"^[A-Za-z0-9._-]+$", receipt_id):
            raise ContractError(ErrorCode.INVALID_INPUT, "bad receipt id")
        return self.root / f"{receipt_id}.json"

    def write(self, artifact: ReceiptArtifact) -> Path:
        """Write atomically; an existing receipt with different content is never replaced."""
        if not verify_artifact(artifact):
            raise ContractError(ErrorCode.INVALID_INPUT, "receipt hash does not match its content")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(artifact.receipt.receipt_id)
        text = artifact.model_dump_json(indent=2)
        if path.exists():
            if self.read(artifact.receipt.receipt_id).receipt_sha256 != artifact.receipt_sha256:
                raise ContractError(ErrorCode.CONFLICT, "a different receipt with this id already exists")
            return path
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        return path

    def read(self, receipt_id: str) -> ReceiptArtifact:
        """Load and verify one receipt."""
        try:
            artifact = ReceiptArtifact.model_validate_json(self._path(receipt_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ContractError(ErrorCode.NOT_FOUND, f"no receipt {receipt_id!r}") from None
        if not verify_artifact(artifact):
            raise ContractError(ErrorCode.INTERNAL, f"receipt {receipt_id!r} fails its own hash")
        return artifact

    def all(self) -> List[ReceiptArtifact]:
        """Every readable, self-consistent receipt."""
        out = []
        for path in sorted(self.root.glob("*.json")) if self.root.exists() else []:
            try:
                out.append(self.read(path.stem))
            except ContractError:
                continue
        return out

    def find(self, **match: Any) -> List[ReceiptArtifact]:
        """Receipts whose receipt/extras fields equal ``match`` (for example ``plan_sha256=...``)."""
        out = []
        for art in self.all():
            fields = {**art.receipt.model_dump(), **art.extras.model_dump()}
            if all(fields.get(k) == v for k, v in match.items()):
                out.append(art)
        return out
