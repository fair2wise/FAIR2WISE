#!/usr/bin/env python3
"""Initialize a Splash volume from the repository's SQLite seed database."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(path: Path) -> tuple[int, int]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"SQLite integrity check failed for {path}: {result}")
        entities = connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        links = connection.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    return int(entities), int(links)


def initialize(seed: Path, destination: Path) -> None:
    if not seed.is_file():
        raise FileNotFoundError(f"Splash seed database not found: {seed}")

    seed_entities, seed_links = _validate(seed)
    seed_digest = _sha256(seed)
    marker = destination.parent / ".fair2wise-splash-seed"

    if destination.is_file() and marker.is_file():
        print(f"Using persistent Splash database: {destination}")
        return

    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.is_file():
        if _sha256(destination) == seed_digest:
            marker.write_text(seed_digest + "\n")
            print(f"Existing Splash database already matches seed ({seed_entities} entities, {seed_links} links)")
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = destination.with_name(f"{destination.stem}.pre-seed-{timestamp}{destination.suffix}")
        shutil.copy2(destination, backup)
        print(f"Backed up legacy Splash database to {backup}")

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(seed, temporary)
    _validate(temporary)
    temporary.replace(destination)
    marker.write_text(seed_digest + "\n")
    print(f"Seeded Splash database with {seed_entities} entities and {seed_links} links")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seed", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    initialize(args.seed, args.destination)


if __name__ == "__main__":
    main()
