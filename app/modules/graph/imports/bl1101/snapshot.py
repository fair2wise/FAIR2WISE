"""Versioned ``matkg_bl1101_vN.json`` snapshot paths and writer, and the JSON-first motor-stage promote."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import LOGGER, _rel_to_repo, sha256_file, utc_now
from .paths import KG_DIR, MOTOR_STAGE_MAP_PATH, TERMS_DIR
from .specs import INGEST_VERSION, SCHEMA_VERSION, SNAPSHOT_RE
from .terms import apply_motor_stage_map_to_graph, apply_motor_stage_map_to_terms, load_motor_stage_map


def next_snapshot_version(kg_dir: Path) -> int:
    """Return the next vN that does not yet exist (1 if none)."""
    versions: list[int] = []
    if kg_dir.exists():
        for path in kg_dir.iterdir():
            match = SNAPSHOT_RE.match(path.name)
            if match:
                versions.append(int(match.group(1)))
    return (max(versions) + 1) if versions else 1


def snapshot_paths(
    version: int,
    *,
    kg_dir: Path | None = None,
    terms_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Return the on-disk paths of one graph snapshot version.

    Args:
        version: Snapshot number N.
        kg_dir: Directory for the graph file. Defaults to ``KG_DIR``.
        terms_dir: Directory for the extracted-terms file. Defaults to ``TERMS_DIR``.

    Returns:
        ``(kg_path, terms_path)`` for ``matkg_bl1101_vN.json`` and
        ``extracted_terms_bl1101_vN.json``. Nothing is created or checked.
    """
    kg_dir = kg_dir or KG_DIR
    terms_dir = terms_dir or TERMS_DIR
    return (
        kg_dir / f"matkg_bl1101_v{version}.json",
        terms_dir / f"extracted_terms_bl1101_v{version}.json",
    )


def _terms_snapshot_for_kg(kg_path: Path) -> Path:
    match = SNAPSHOT_RE.match(kg_path.name)
    if not match:
        return TERMS_DIR / f"{kg_path.stem.replace('matkg_', 'extracted_terms_')}.json"
    return TERMS_DIR / f"extracted_terms_bl1101_v{match.group(1)}.json"


def write_kg_snapshot(
    graph: dict[str, Any],
    version: int,
    *,
    kg_dir: Path | None = None,
) -> Path:
    """Write ``graph`` as the ``matkg_bl1101_v<version>.json`` snapshot.

    Updates ``graph['metadata']`` in place with snapshot info (name, version,
    timestamp, repo-relative path, sha256, node and edge counts) and the ingest
    version, then writes through a temporary file that is moved into place.

    Args:
        graph: Graph dict with ``things`` and ``associations``. Mutated.
        version: Snapshot number N.
        kg_dir: Output directory. Defaults to ``KG_DIR``.

    Returns:
        Path of the written file.
    """
    kg_path, _ = snapshot_paths(version, kg_dir=kg_dir)
    kg_path.parent.mkdir(parents=True, exist_ok=True)
    meta = graph.setdefault("metadata", {})
    snap = meta.setdefault("graph_snapshot", {})
    snap.update(
        {
            "name": f"matkg_bl1101_v{version}",
            "version": version,
            "written_at": utc_now(),
            "path": _rel_to_repo(kg_path),
        }
    )
    meta["ingest_version"] = INGEST_VERSION
    meta["extraction_version"] = INGEST_VERSION
    tmp = kg_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    digest = sha256_file(tmp)
    snap["sha256"] = digest
    snap["nodes"] = len(graph.get("things") or [])
    snap["edges"] = len(graph.get("associations") or [])
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(kg_path)
    return kg_path


def run_motor_link_promote(
    *,
    source_kg: Path,
    version: int | None = None,
    map_path: Path | None = None,
    kg_dir: Path | None = None,
    terms_dir: Path | None = None,
) -> dict[str, Any]:
    """Copy an existing ops KG, apply the motor-stage map, write vN. Does not touch literature."""
    source_kg = source_kg.resolve()
    if not source_kg.exists():
        raise FileNotFoundError(source_kg)
    out_kg_dir = kg_dir or KG_DIR
    out_terms_dir = terms_dir or TERMS_DIR
    version = version if version is not None else next_snapshot_version(out_kg_dir)
    dest_kg, dest_terms = snapshot_paths(version, kg_dir=out_kg_dir, terms_dir=out_terms_dir)
    if dest_kg.resolve() == source_kg:
        raise ValueError(f"Refusing to overwrite source snapshot {source_kg}")
    mapping = load_motor_stage_map(map_path)
    graph = json.loads(source_kg.read_text(encoding="utf-8"))
    graph, stats = apply_motor_stage_map_to_graph(graph, mapping)
    meta = graph.setdefault("metadata", {})
    meta["schema_version"] = meta.get("schema_version") or SCHEMA_VERSION
    promote = {
        "kind": "motor_stage_links",
        "source_graph": _rel_to_repo(source_kg),
        "motor_stage_map": mapping.get("path") or _rel_to_repo(MOTOR_STAGE_MAP_PATH),
        "map_version": mapping.get("version"),
        "linked": stats["linked"],
        "aliased": stats["aliased"],
    }
    meta["promote"] = promote
    kg_path = write_kg_snapshot(graph, version, kg_dir=out_kg_dir)

    src_terms = _terms_snapshot_for_kg(source_kg)
    if src_terms.exists():
        records = json.loads(src_terms.read_text(encoding="utf-8"))
        terms = list(records.get("terms") or [])
        apply_motor_stage_map_to_terms(terms, mapping)
        records["terms"] = terms
        rec_meta = records.setdefault("metadata", {})
        rec_meta["ingest_version"] = INGEST_VERSION
        rec_meta["promote"] = promote
        rec_meta["graph_snapshot"] = dict(graph.get("metadata", {}).get("graph_snapshot") or {})
        dest_terms.parent.mkdir(parents=True, exist_ok=True)
        dest_terms.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    LOGGER.info(
        "Promoted %s → %s (+%d part_of, +%d alias related_to)",
        source_kg,
        kg_path,
        stats["linked"],
        stats["aliased"],
    )
    return {
        "kg_path": kg_path,
        "terms_path": dest_terms if dest_terms.exists() else None,
        "version": version,
        "nodes": len(graph.get("things") or []),
        "edges": len(graph.get("associations") or []),
        "stats": stats,
        "graph": graph,
        "sources_landed": [promote],
        "sources_skipped": [],
    }
