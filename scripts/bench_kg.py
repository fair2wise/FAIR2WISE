"""Time the knowledge-graph reads that a person actually waits on.

    DEV-ONLY (T29 port, X-07). This measures SQLite lock contention. The donor
    opened `catalog.db` read-write and made a scratch table in it; this port
    NEVER touches the database you name: it benchmarks a private COPY in a
    temporary directory, so the competing writer and its scratch table cannot
    reach real data. ``--db`` is required (there is no default path) and the
    tool is meaningless against a Tiled-served catalog (no local file).

    Adapted from als-computing/forge_rsoxs_analysis scripts/bench_kg.py (BSD-3-Clause).


WHY THIS EXISTS BEFORE ANY FIX. "The KG seems slow" is not actionable and
"I added an index" is not evidence. This measures four reads that back real UI,
each **twice**: idle, and against a synthetic writer holding the same lock a
batch ingest holds. The gap between those two numbers IS the finding --
measured at first run: a 9 ms read timing out entirely under load.

THE WRITER IS SYNTHETIC ON PURPOSE. Benchmarking against a live backfill makes
the number depend on which paper it happened to be extracting. A tight
insert/rollback loop on a scratch table reproduces the lock contention
deterministically and touches no real row: every transaction is rolled back,
and the table is dropped at the end.

Usage:
    python scripts/bench_kg.py --db path/to/catalog.db
    python scripts/bench_kg.py --db path/to/catalog.db --json   # for regression gating
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
from pathlib import Path
import shutil
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from typing import Any

#: Repetitions per case. Enough for a median and a p95 to mean something
#: without making a "quick check" take minutes.
REPEATS = 20


def _ro(db: Path) -> sqlite3.Connection:
    """A read-only connection, so a benchmark can never write."""
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)


# --- the four reads a person waits on --------------------------------------

def case_entity_census(con: sqlite3.Connection) -> int:
    """Powers the graph totals on every KG page."""
    return con.execute(
        "SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type"
    ).fetchall().__len__()


def case_latest_publication(con: sqlite3.Connection) -> int:
    """The Ingestion tab's 'latest entry' card -- one publication + its one-hop
    neighbourhood. The shape KG-PERF-003 says the other call sites should copy."""
    row = con.execute(
        "SELECT id FROM entities WHERE entity_type='publication' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if not row:
        return 0
    return len(
        con.execute(
            "SELECT l.predicate, e.entity_type FROM links l "
            "JOIN entities e ON e.id = l.object_id WHERE l.subject_id = ?",
            (row[0],),
        ).fetchall()
    )


def case_forward_walk(con: sqlite3.Connection) -> int:
    """subject_id lookup -- indexed today (links_triple_idx). The control:
    if this degrades too, the cause is the lock, not the index."""
    row = con.execute(
        "SELECT id FROM entities WHERE entity_type='publication' LIMIT 1"
    ).fetchone()
    if not row:
        return 0
    return len(
        con.execute(
            "SELECT object_id FROM links WHERE subject_id = ?", (row[0],)
        ).fetchall()
    )


def case_reverse_walk(con: sqlite3.Connection) -> int:
    """object_id lookup -- SCANs today. This is KG-PERF-002's case, and the
    number that should move when the index lands."""
    row = con.execute(
        "SELECT id FROM entities WHERE entity_type='claim' LIMIT 1"
    ).fetchone()
    if not row:
        return 0
    return len(
        con.execute(
            "SELECT subject_id FROM links WHERE object_id = ?", (row[0],)
        ).fetchall()
    )


CASES: dict[str, Callable[[sqlite3.Connection], int]] = {
    "entity_census": case_entity_census,
    "latest_publication": case_latest_publication,
    "forward_walk_subject_id": case_forward_walk,
    "reverse_walk_object_id": case_reverse_walk,
}


class _Writer(threading.Thread):
    """Holds a write lock the way a batch ingest does, and touches nothing real.

    IT MUST COMMIT, AND THE FIRST VERSION DID NOT. Rolling back takes only a
    RESERVED lock, which readers are explicitly allowed to share -- so the first
    run showed no contention at all and appeared to disprove KG-CONC-001. Only
    the commit path takes EXCLUSIVE, which is what actually blocks readers in
    rollback-journal mode. The rows are inserted and deleted in the same
    scratch table, so the writes are real and the data is still disposable.

    Any failure is recorded rather than swallowed: a writer that quietly died
    would make the contended numbers look identical to the idle ones and invert
    the conclusion.
    """

    def __init__(self, db: Path) -> None:
        super().__init__(daemon=True)
        self.db = db
        self.stop = threading.Event()
        self.error: str = ""
        self.transactions = 0

    def run(self) -> None:
        try:
            con = sqlite3.connect(self.db, timeout=30)
            con.execute("CREATE TABLE IF NOT EXISTS _bench_scratch (v TEXT)")
            con.commit()
            while not self.stop.is_set():
                con.execute("BEGIN IMMEDIATE")
                # A batch of rows, so the commit has real work to flush -- a
                # single-row commit is over too quickly to contend with anything.
                con.executemany(
                    "INSERT INTO _bench_scratch VALUES (?)",
                    [("x" * 512,) for _ in range(200)],
                )
                con.commit()
                con.execute("DELETE FROM _bench_scratch")
                con.commit()
                self.transactions += 1
            con.execute("DROP TABLE IF EXISTS _bench_scratch")
            con.commit()
            con.close()
        except Exception as exc:  # noqa: BLE001 - reported, never hidden
            self.error = f"{type(exc).__name__}: {exc}"


def _time(fn: Callable[[sqlite3.Connection], int], db: Path) -> dict[str, Any]:
    """Median/p95 wall-clock in ms, plus timeouts.

    A NEW CONNECTION PER REPEAT, deliberately: a long-lived connection can hold
    a read snapshot that hides exactly the contention being measured.
    """
    samples: list[float] = []
    timeouts = 0
    for _ in range(REPEATS):
        started = time.perf_counter()
        try:
            con = _ro(db)
            try:
                fn(con)
            finally:
                con.close()
        except sqlite3.OperationalError:
            timeouts += 1
            continue
        samples.append((time.perf_counter() - started) * 1000)
    if not samples:
        return {"median_ms": None, "p95_ms": None, "timeouts": timeouts}
    ordered = sorted(samples)
    return {
        "median_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)], 2),
        "timeouts": timeouts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n\n")[0])
    parser.add_argument("--db", type=Path, required=True, help="catalog.db to measure (copied, never modified)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.db.is_file():
        print(f"  no database at {args.db}")
        return 2
    with tempfile.TemporaryDirectory(prefix="bench_kg_") as scratch:
        copy = Path(scratch) / "catalog_copy.db"
        shutil.copyfile(args.db, copy)
        args.db = copy
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    """Benchmark ``args.db`` (already a private copy) idle and under a synthetic writer."""

    con = _ro(args.db)
    journal = con.execute("PRAGMA journal_mode").fetchone()[0]
    entities = con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    links = con.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    con.close()

    result: dict[str, Any] = {
        "journal_mode": journal,
        "entities": entities,
        "links": links,
        "idle": {},
        "contended": {},
    }

    for name, fn in CASES.items():
        result["idle"][name] = _time(fn, args.db)

    writer = _Writer(args.db)
    writer.start()
    time.sleep(0.3)  # let it acquire the lock at least once
    for name, fn in CASES.items():
        result["contended"][name] = _time(fn, args.db)
    writer.stop.set()
    writer.join(timeout=10)
    result["writer_transactions"] = writer.transactions
    result["writer_error"] = writer.error

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"  journal_mode={journal}  {entities} entities / {links} links")
    if writer.error:
        print(f"  WRITER FAILED: {writer.error} -- contended numbers are meaningless")
    elif writer.transactions == 0:
        print("  WRITER NEVER COMMITTED a transaction -- contention not reproduced")
    else:
        print(f"  writer held {writer.transactions} transactions during the run")
    print(f"\n  {'case':26s} {'idle med':>9s} {'idle p95':>9s} "
          f"{'load med':>9s} {'load p95':>9s} {'t/o':>4s}")
    for name in CASES:
        i, c = result["idle"][name], result["contended"][name]
        print(
            f"  {name:26s} {str(i['median_ms']):>9s} {str(i['p95_ms']):>9s} "
            f"{str(c['median_ms']):>9s} {str(c['p95_ms']):>9s} "
            f"{c['timeouts']:>4d}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
