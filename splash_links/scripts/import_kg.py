#!/usr/bin/env python3
"""
Import a matkg JSON knowledge graph file into splash-links.

Each KG file has:
    { "things": [...], "associations": [...] }

Things become entities; associations become links.

Usage:
    # Single file
    pixi run python scripts/import_kg.py /path/to/matkg_file.json

    # Multiple files
    pixi run python scripts/import_kg.py /path/to/*.json

    # Custom server URL
    pixi run python scripts/import_kg.py --url http://localhost:8081 /path/to/file.json

    # Dry run (validate without importing)
    pixi run python scripts/import_kg.py --dry-run /path/to/file.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from splash_links.client.base import LinksClient, from_uri

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _build_entity_properties(thing: dict) -> dict:
    """Extract all non-core fields into a properties dict for the entity.

    Core fields handled separately by create_entity (id, name, category/entityType, uri)
    are excluded; everything else is preserved so that CodeSnippet fields like
    code_snippet, code_language, function_name, domain_features, paper_title, doi, etc.
    survive the round-trip through splash-links.
    """
    CORE_KEYS = {"id", "name", "category", "type", "uri"}
    props: dict = {}
    for key, value in thing.items():
        if key in CORE_KEYS:
            continue
        if value is None:
            continue
        props[key] = value
    return props


def import_kg_file(
    client: LinksClient,
    path: Path,
    *,
    dry_run: bool = False,
    skip_existing: bool = True,
) -> tuple[int, int, int]:
    """
    Import one KG JSON file into splash-links.

    Returns:
        (entities_created, links_created, links_skipped)
    """
    logger.info("Loading %s", path)
    with open(path) as f:
        data = json.load(f)

    things = data.get("things", [])
    associations = data.get("associations", [])
    source_file = path.name

    if not things and not associations:
        logger.warning("File %s is empty — skipping", path.name)
        return 0, 0, 0

    logger.info("Found %d things, %d associations in %s", len(things), len(associations), path.name)

    if dry_run:
        logger.info("[DRY RUN] Would create %d entities and up to %d links", len(things), len(associations))
        return 0, 0, 0

    # ------------------------------------------------------------------
    # Phase 1: Create entities, build matkg_id -> splash_uuid mapping
    # ------------------------------------------------------------------
    id_map: dict[str, str] = {}  # matkg_id -> splash entity UUID
    entities_created = 0

    for i, thing in enumerate(things, 1):
        matkg_id = thing["id"]
        name = thing.get("name", matkg_id)
        category = thing.get("category", "Thing")
        properties = _build_entity_properties(thing)
        properties["matkg_id"] = matkg_id
        properties["source_file"] = source_file

        entity = client.create_entity(
            entity_type=category,
            name=name,
            uri=matkg_id,
            properties=properties,
        )
        id_map[matkg_id] = entity.id
        entities_created += 1

        if i % 500 == 0:
            logger.info("  entities: %d / %d", i, len(things))

    logger.info("Created %d entities", entities_created)

    # ------------------------------------------------------------------
    # Phase 2: Create links using the id mapping
    # ------------------------------------------------------------------
    links_created = 0
    links_skipped = 0

    for i, assoc in enumerate(associations, 1):
        subject_matkg = assoc["subject"]
        object_matkg = assoc["object"]
        predicate = assoc.get("predicate", "rel:related_to")

        subject_uuid = id_map.get(subject_matkg)
        object_uuid = id_map.get(object_matkg)

        if not subject_uuid or not object_uuid:
            logger.debug(
                "Skipping link %s -> %s: missing entity (subject=%s, object=%s)",
                subject_matkg, object_matkg,
                "found" if subject_uuid else "MISSING",
                "found" if object_uuid else "MISSING",
            )
            links_skipped += 1
            continue

        link_props: dict = {"source_file": source_file}
        if assoc.get("has_evidence"):
            link_props["has_evidence"] = assoc["has_evidence"]

        client.create_link(
            subject_id=subject_uuid,
            predicate=predicate,
            object_id=object_uuid,
            properties=link_props,
        )
        links_created += 1

        if i % 500 == 0:
            logger.info("  links: %d / %d (skipped %d)", links_created, i, links_skipped)

    logger.info("Created %d links, skipped %d", links_created, links_skipped)
    return entities_created, links_created, links_skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import matkg JSON knowledge graph files into splash-links.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Path(s) to matkg JSON file(s)",
    )
    parser.add_argument(
        "--url",
        default="splash://localhost:8081",
        help="splash-links server URI (default: splash://localhost:8081)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and count without importing",
    )
    args = parser.parse_args()

    client = from_uri(args.url)

    total_entities = 0
    total_links = 0
    total_skipped = 0

    for path in args.files:
        if not path.exists():
            logger.error("File not found: %s", path)
            sys.exit(1)
        if not path.suffix == ".json":
            logger.warning("Skipping non-JSON file: %s", path)
            continue

        entities, links, skipped = import_kg_file(client, path, dry_run=args.dry_run)
        total_entities += entities
        total_links += links
        total_skipped += skipped

    logger.info("=== TOTALS: %d entities, %d links created, %d links skipped ===",
                total_entities, total_links, total_skipped)


if __name__ == "__main__":
    main()
