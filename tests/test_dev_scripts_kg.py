"""T29 B3: X-05 seed_synthetic_kg and X-07 bench_kg developer tools (offline, no real graph or catalog)."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_dev_{name}", SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


seed = load("seed_synthetic_kg")
bench = load("bench_kg")


def test_graph_shape_is_synthetic_reproducible_and_acyclic():
    graph = seed.build_graph(40, seed=3)
    again = seed.build_graph(40, seed=3)
    assert graph.counts() == again.counts() and graph.nodes == again.nodes
    assert seed.build_graph(40, seed=4).counts() != graph.counts()
    ids = {n.node_id for n in graph.nodes}
    assert len(ids) == len(graph.nodes)
    assert all(e.source in ids and e.target in ids for e in graph.edges)
    assert all(n.properties["synthetic"] is True for n in graph.nodes)
    assert not any("doi" in n.properties or "openalex_id" in n.properties for n in graph.nodes)
    cites = [(e.source, e.target) for e in graph.edges if e.relation == "cites"]
    assert cites and all(s > t for s, t in cites)         # citations only point at earlier papers
    assert {"has_section", "has_figure", "authored_by", "mentions", "cites"} <= set(graph.counts()["by_relation"])


def test_default_size_is_about_the_documented_budget():
    counts = seed.build_graph().counts()
    assert 8_000 <= counts["entities"] <= 12_000 and 20_000 <= counts["links"] <= 32_000


class FakeStore:
    def __init__(self) -> None:
        self.nodes: list[str] = []
        self.edges: list[tuple[str, str, str]] = []
        self.batches = 0

    @contextmanager
    def batch_writes(self):
        self.batches += 1
        yield

    def upsert_node(self, node_id: str, entity_type: str, *, name: str, source: str, properties: dict) -> bool:
        assert source == "synthetic_seed"
        self.nodes.append(node_id)
        return True

    def upsert_edge(self, source: str, target: str, relation: str, *, confidence: float, evidence: str) -> bool:
        assert source in self.nodes and target in self.nodes   # parents before children
        self.edges.append((source, target, relation))
        return True


def test_write_graph_to_an_injected_store():
    graph = seed.build_graph(10, seed=1)
    store = FakeStore()
    written = seed.write_graph(store, graph, chunk=50)
    assert written == {"nodes": len(graph.nodes), "edges": len(graph.edges)} and store.batches > 2


def test_cli_dry_run_writes_nothing_and_out_writes_json(tmp_path: Path):
    out = tmp_path / "g.json"
    assert seed.main(["--papers", "5", "--dry-run", "--out", str(out)]) == 0 and not out.exists()
    assert seed.main(["--papers", "5"]) == 0 and not list(tmp_path.iterdir())
    assert seed.main(["--papers", "5", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["source"] == "synthetic_seed" and data["nodes"] and data["edges"]


def make_catalog(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE entities (id TEXT, entity_type TEXT, created_at TEXT);"
        "CREATE TABLE links (subject_id TEXT, predicate TEXT, object_id TEXT);"
        "INSERT INTO entities VALUES ('p1','publication','1'),('c1','claim','2');"
        "INSERT INTO links VALUES ('p1','makes','c1');")
    con.commit()
    con.close()


def test_bench_runs_on_a_copy_and_never_modifies_the_database(tmp_path: Path, monkeypatch, capsys):
    db = tmp_path / "catalog.db"
    make_catalog(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    monkeypatch.setattr(bench, "REPEATS", 3)
    assert bench.main(["--db", str(db), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["entities"] == 2 and result["links"] == 1
    assert set(result["idle"]) == set(bench.CASES) == set(result["contended"])
    assert result["writer_error"] == "" and result["writer_transactions"] > 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before          # byte-identical
    assert sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE name='_bench_scratch'").fetchall() == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["catalog.db"]   # the temp copy is gone


def test_bench_requires_an_existing_db_argument(tmp_path: Path, capsys):
    assert bench.main(["--db", str(tmp_path / "missing.db")]) == 2
    with pytest.raises(SystemExit):
        bench.main([])
