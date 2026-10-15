"""Helpers for the T08B import tests: snapshot builders, an in-memory Tiled double, clocks."""
from __future__ import annotations

import copy
import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph.backends.tiled_mutations import TiledMutationClient
from app.modules.graph.ports import TruncatedGraphRead

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def snapshot_with_graph(src_dir: Path, dest_root: Path, corpus: str, snapshot_id: str,
                        payload: Dict[str, Any], *, patch_manifest: Optional[Dict[str, Any]] = None) -> Path:
    """Copy a real T23B snapshot and swap in ``payload`` as graph.json, keeping the manifest consistent."""
    import hashlib
    dest = dest_root / "storage" / "kg" / corpus / snapshot_id
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_dir, dest)
    blob = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    (dest / "graph.json").write_bytes(blob)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    manifest["corpus"], manifest["snapshot_id"] = corpus, snapshot_id
    manifest["graph"].update(sha256=hashlib.sha256(blob).hexdigest(), size_bytes=len(blob),
                             node_count=len(payload["things"]), edge_count=len(payload["associations"]))
    manifest["graph"].pop("codesnippet_count", None)
    manifest.pop("compat_paths", None)
    for key, value in (patch_manifest or {}).items():
        manifest[key] = value
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return dest


def tree_bytes(path: Path) -> Dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


class MemoryTiled(TiledMutationClient):
    """In-memory stand-in with the observed 0.2.18 semantics (no upsert, ids are UUIDs, accessBlob required)."""

    def __init__(self, url: str = "http://127.0.0.1:59999") -> None:
        super().__init__(url, profile="test", credential="x")
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.links: Dict[str, Dict[str, Any]] = {}
        self.writes: List[str] = []
        self.down = False
        self.max_rows: Optional[int] = None

    def _guard(self) -> None:
        if self.down:
            raise ContractError(ErrorCode.UNAVAILABLE, "Tiled graph is unavailable (ConnectionError)")

    def scan_entities(self, **_kw):
        self._guard()
        if self.max_rows is not None and len(self.entities) > self.max_rows:
            raise TruncatedGraphRead("tiled.entities scan", self.max_rows)
        return copy.deepcopy(list(self.entities.values()))

    def scan_links(self, **_kw):
        self._guard()
        return copy.deepcopy(list(self.links.values()))

    def create_entity(self, *, entity_type, name, uri, properties, access_blob):
        self._guard()
        assert access_blob is not None
        tid = str(uuid.uuid4())
        self.entities[tid] = {"id": tid, "name": name, "entityType": entity_type, "uri": uri,
                              "properties": copy.deepcopy(properties) or None}
        self.writes.append("createEntity")
        return tid

    def create_link(self, *, subject_id, predicate, object_id, properties, access_blob):
        self._guard()
        assert subject_id in self.entities and object_id in self.entities and access_blob is not None
        tid = str(uuid.uuid4())
        self.links[tid] = {"id": tid, "subjectId": subject_id, "objectId": object_id, "predicate": predicate,
                           "properties": copy.deepcopy(properties) or None, "accessBlob": None}
        self.writes.append("createLink")
        return tid

    def set_entity_uri(self, row, graph_id, uri, access_blob):
        self._guard()
        super_check = row.get("properties", {}).get("f2w_owner") == "f2w-literature"
        assert super_check and access_blob is not None
        self.entities[row["id"]]["uri"] = uri
        self.writes.append("updateEntity")

    def delete_link(self, row, graph_id):
        self._guard()
        assert row.get("properties", {}).get("f2w_owner") == "f2w-literature"
        del self.links[row["id"]]
        self.writes.append("deleteLink")

    def add_forge_sentinel(self) -> None:
        a = str(uuid.uuid4())
        b = str(uuid.uuid4())
        self.entities[a] = {"id": a, "name": "ESAF-1", "entityType": "ESAF", "uri": "forge:esaf/1",
                            "properties": {"owner": "forge"}}
        self.entities[b] = {"id": b, "name": "Sample", "entityType": "Sample", "uri": "forge:sample/1",
                            "properties": {"owner": "forge"}}
        lid = str(uuid.uuid4())
        self.links[lid] = {"id": lid, "subjectId": a, "objectId": b, "predicate": "forge:has", "properties": None,
                           "accessBlob": None}
