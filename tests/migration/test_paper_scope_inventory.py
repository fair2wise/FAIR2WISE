"""T17: structure of docs/migration/paper_scope_inventory.json and existence of every cited test.

The inventory is the machine-readable paper scope ledger. These checks keep it honest when code or
tests move: every catalog port and every inventoried donor route has exactly one entry, every open
item names a blocking task, nothing is "covered" without tests, and every cited test file exists
and defines the cited test function. (Whether the tests pass is checked by the full suite itself.)
"""
from __future__ import annotations

import ast
import collections
import json
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "docs" / "migration"
INVENTORY = json.loads((MIGRATION / "paper_scope_inventory.json").read_text(encoding="utf-8"))
COMPANION = MIGRATION / "companion"

STATUSES = {
    "covered", "retained_in_place", "partial", "pending", "gap_needs_owner",
    "proposed_exception", "remains_in_forge",
}
OPEN = {"partial", "pending", "gap_needs_owner", "proposed_exception"}
CAP_KEYS = {
    "id", "kind", "t17_port_id", "donor", "task_graph_owners", "f2w_locations", "tests",
    "status", "demonstrated", "blocking_task", "follow_on_task", "notes",
}
ROUTE_KEYS = {
    "id", "method", "path", "source", "source_function", "catalog_disposition", "v4_exposure",
    "status", "f2w_equivalent", "f2w_exposure", "tests", "blocking_task", "notes",
}
CAPS = INVENTORY["capabilities"]
ROUTES = INVENTORY["routes"]


def _all_rows():
    return [("cap", row) for row in CAPS] + [("route", row) for row in ROUTES]


@lru_cache(maxsize=None)
def _test_names(path: str) -> frozenset:
    """Names of test functions and ``Class::method`` pairs defined in a test file."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(f"{node.name}::{sub.name}")
                    names.add(sub.name)
    return frozenset(names)


def test_top_level_shape_and_vocabulary() -> None:
    assert INVENTORY["task"] == "T17"
    assert set(INVENTORY["status_vocabulary"]) == STATUSES
    for row in CAPS:
        assert set(row) >= CAP_KEYS, row["id"]
    for row in ROUTES:
        assert set(row) == ROUTE_KEYS, row["id"]


def test_ids_are_unique() -> None:
    ids = [row["id"] for _, row in _all_rows()]
    assert len(ids) == len(set(ids))


def test_every_catalog_port_has_exactly_one_entry() -> None:
    catalog = [e["id"] for e in json.loads((COMPANION / "port_manifest.json").read_text())["entries"]]
    mine = [row["id"] for row in CAPS if row["kind"] in ("donor_port", "donor_operator")]
    assert sorted(mine) == sorted(catalog)


def test_t17_port_ids_are_flagged() -> None:
    tasks = {t["id"]: t for t in json.loads((COMPANION / "task_graph.json").read_text())["tasks"]}
    flagged = {row["id"] for row in CAPS if row["t17_port_id"]}
    assert flagged == set(tasks["T17"]["port_ids"])


def test_every_inventoried_route_has_exactly_one_disposition() -> None:
    routes = json.loads((COMPANION / "paper_route_inventory.json").read_text())["routes"]
    expected = collections.Counter((r["method"], r["path"], r["source_function"]) for r in routes)
    mine = collections.Counter((r["method"], r["path"], r["source_function"]) for r in ROUTES)
    assert mine == expected


def test_asset_review_items_are_all_resolved_to_an_entry() -> None:
    items = json.loads((COMPANION / "paper_source_coverage.json").read_text())["items"]
    assets = {i["path"].rsplit("/", 1)[-1] for i in items if i["disposition"] == "asset_or_support_review"}
    cited = " ".join(str(row["donor"]) for row in CAPS if row["kind"].startswith("donor_asset"))
    for name in assets:
        assert name in cited, name


@pytest.mark.parametrize("kind,row", _all_rows(), ids=lambda v: v["id"] if isinstance(v, dict) else v)
def test_row_status_rules(kind, row) -> None:
    assert row["status"] in STATUSES
    if row["status"] in OPEN:
        assert row["blocking_task"], "an open item must name a blocking task or owner decision"
    if row["status"] in ("covered", "retained_in_place"):
        assert row["tests"], "covered without tests"
        assert row["blocking_task"] is None


@pytest.mark.parametrize("kind,row", _all_rows(), ids=lambda v: v["id"] if isinstance(v, dict) else v)
def test_cited_paths_exist(kind, row) -> None:
    for node in row["tests"]:
        path, _, name = node.partition("::")
        assert (ROOT / path).is_file(), node
        if name:
            assert name in _test_names(path), node
    for loc in row.get("f2w_locations", []):
        assert (ROOT / loc.rstrip("/")).exists(), loc


def test_summary_counts_match_rows() -> None:
    summary = INVENTORY["summary"]
    assert summary["capabilities"] == len(CAPS)
    assert summary["routes"] == len(ROUTES)
    assert summary["capabilities_by_status"] == dict(collections.Counter(r["status"] for r in CAPS))
    assert summary["routes_by_status"] == dict(collections.Counter(r["status"] for r in ROUTES))
