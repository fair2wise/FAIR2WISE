"""Parity and replay tests for the bl1101 terms/snapshot/cli slice and the legacy shim (T17B/H8 P3 slice b2).

``fixtures/bl1101/terms_golden.json`` was captured from the pre-port ``bl1101_ingest`` module at ``forge_merge``
(large payloads are stored as SHA-256 digests of their JSON). The ``--from-graph`` replay reads the frozen
``storage/kg/matkg_bl1101_v*.json`` files and writes only under ``tmp_path``. No network, no git, no model.

Note on the replay oracle: ``v8`` is not a motor-link replay of ``v7`` (``v8`` is a ``sim-nodes-strip`` promote and
replaying the motor map over ``v7`` links nothing). The reproducible pairs are ``v3 -> v4`` (the map's
``map_version 4`` run) and the ``v7`` no-op, both asserted below.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
from pathlib import Path
import re
import shutil
import types

import pytest

from app.modules import bl1101_ingest as legacy
from app.modules import hybrid_rag, tiled_sim
from app.modules.contracts.json_types import JsonObject, JsonValue
from app.modules.graph.imports.bl1101 import (
    cli,
    common,
    fetch,
    parse_als,
    parse_blueprint,
    paths,
    snapshot,
    specs,
    symbols,
    terms,
)

FIXTURES = Path(__file__).parent / "fixtures" / "bl1101"
GOLDEN: JsonObject = json.loads((FIXTURES / "terms_golden.json").read_text(encoding="utf-8"))
KG = paths.REPO_ROOT / "storage" / "kg"
FROZEN_TIME = "2026-01-01T00:00:00Z"
MODULES = (specs, common, paths, fetch, parse_als, parse_blueprint, symbols, terms, snapshot, cli)


def _default(obj: object) -> JsonValue:
    return dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) and not isinstance(obj, type) else str(obj)  # type: ignore[return-value]


def _plain(value: object) -> JsonValue:
    return json.loads(json.dumps(value, default=_default))  # type: ignore[no-any-return]


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, default=_default).encode()).hexdigest()


def _check(name: str, value: object) -> None:
    """Compare ``value`` with the golden entry: full JSON for small ones, a digest for large ones."""
    expected = GOLDEN[name]
    if isinstance(expected, dict) and "__sha256__" in expected:
        assert _digest(value) == expected["__sha256__"], name
    else:
        assert _plain(value) == expected, name


@pytest.fixture(scope="module")
def als_html() -> str:
    return (FIXTURES / "als_11-0-1-2.html").read_text(encoding="utf-8")


@pytest.fixture
def parsed(als_html: str) -> tuple[dict, dict]:  # type: ignore[type-arg]
    return parse_als.parse_als_page(als_html), parse_blueprint.parse_blueprint_html(paths.BLUEPRINT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "repo", root)
    return root


def test_term_builders_match_golden(parsed: tuple[dict, dict]) -> None:  # type: ignore[type-arg]
    page, blueprint = parsed
    _check("beamline_seed", terms.beamline_seed(page, blueprint, "als-src", "bp-src"))
    _check("software_catalog", terms.software_catalog())
    _check("terms_from_als", terms.terms_from_als(page, "als-src"))
    _check("terms_from_blueprint", terms.terms_from_blueprint(blueprint, "bp-src"))
    _check("terms_from_people", terms.terms_from_people(page, blueprint, "als-src", "bp-src"))
    term = terms.make_term("beamline:t", "T", "Motor", "d", "src", synonyms=["a"],
                           relations=[terms._rel("beamline:t", "partOf", "beamline:u", "ev")])
    _check("make_term", [term, terms._rel("a", "b", "c")])


def test_repo_terms_merge_and_records_match_golden(parsed: tuple[dict, dict], repo: Path, als_html: str) -> None:  # type: ignore[type-arg]
    page, blueprint = parsed
    by_name = {s.name: s for s in specs.REPO_SPECS}
    syms, snips = symbols.extract_python_symbols(repo / "pkg" / "devices.py", "bcs2sim-ophyd", "abc123")
    compose = symbols.parse_compose_services(repo / "docker-compose.yml")
    markdown = [("README.md", symbols.extract_markdown_software(repo / "README.md", "r"))]
    attach = {name: list(terms.attach_repo_terms(spec, repo, "deadbeef", syms, snips, compose, markdown))
              for name, spec in by_name.items()}
    _check("attach_repo_terms", attach)
    base = [terms.beamline_seed(page, blueprint, "als-src", "bp-src"), *terms.software_catalog(),
            *terms.terms_from_als(page, "als-src"), *terms.terms_from_blueprint(blueprint, "bp-src"),
            *terms.terms_from_people(page, blueprint, "als-src", "bp-src")]
    dup = [dict(t) for t in base[:5]]
    _check("merge_terms", terms.merge_terms([base, dup, attach["bcs2sim-ophyd"][0]]))

    mapping = terms.load_motor_stage_map()
    assert sorted(mapping) == GOLDEN["motor_map_keys"] and mapping.get("version") == GOLDEN["motor_map_version"]

    def payload(spec: specs.RepoSpec) -> dict:  # type: ignore[type-arg]
        return {"spec": spec, "root": repo, "sha": "deadbeef", "symbols": syms, "snippets": snips,
                "compose": compose, "markdown": markdown}

    records = cli.ingest_records(als=page, blueprint=blueprint,
                                 repo_payloads=[payload(by_name["bcs2sim-ophyd"]), payload(by_name["bl11012-finch"])])
    _check("ingest_records", records)
    graph = cli.records_to_graph(records, {"schema_version": specs.SCHEMA_VERSION, "kg": "bl1101", "note": "golden"})
    _check("records_to_graph", graph)
    shuffled = {"things": list(reversed(graph["things"])), "associations": list(reversed(graph["associations"])),
                "metadata": graph["metadata"]}
    _check("sort_graph_of_shuffled", terms.sort_graph(shuffled))
    _check("ingest_fixture", cli.ingest_fixture(als_html, [repo / "pkg" / "devices.py"]))


# ----------------------------------------------------------------------------- --from-graph replay
def _promote(source: int, version: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, str, str | None]:  # type: ignore[type-arg]
    """Run the motor-link promote through the legacy path with a frozen clock; return result, kg text, terms text."""
    monkeypatch.setattr(legacy, "utc_now", lambda: FROZEN_TIME)
    out = tmp_path / f"out{source}_{version}"
    result = legacy.run_motor_link_promote(source_kg=KG / f"matkg_bl1101_v{source}.json", version=version,
                                           kg_dir=out / "kg", terms_dir=out / "terms")
    def norm(text: str) -> str:
        # The snapshot's own sha256 covers its (temp) path field, so it is not comparable across directories.
        return re.sub(r'"sha256": "[0-9a-f]{64}"', '"sha256": "<SHA>"', text.replace(str(out), "<TMP>"))

    kg_text = norm(result["kg_path"].read_text(encoding="utf-8"))
    terms_path = result["terms_path"]
    terms_text = norm(terms_path.read_text(encoding="utf-8")) if terms_path else None
    return result, kg_text, terms_text


@pytest.mark.parametrize(("source", "key"), [(3, "replay_v3_to_v10"), (7, "replay_v7_to_v10")])
def test_replay_matches_pre_port_golden(source: int, key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, kg_text, terms_text = _promote(source, 10, tmp_path, monkeypatch)
    golden = GOLDEN[key]
    assert isinstance(golden, dict)
    assert result["stats"] == golden["stats"] and result["version"] == golden["version"]
    assert (result["nodes"], result["edges"]) == (golden["nodes"], golden["edges"])
    assert hashlib.sha256(kg_text.encode()).hexdigest() == golden["kg_sha256"]
    assert (hashlib.sha256(terms_text.encode()).hexdigest() if terms_text else None) == golden["terms_sha256"]
    assert result["sources_landed"] == golden["sources_landed"] and len(kg_text) == golden["kg_text_len"]


def test_replay_of_v3_reproduces_frozen_v4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The motor map applied to v3 yields the committed v4 graph (things and associations, in order)."""
    result, _, _ = _promote(3, 10, tmp_path, monkeypatch)
    frozen = json.loads((KG / "matkg_bl1101_v4.json").read_text(encoding="utf-8"))
    assert result["graph"]["things"] == frozen["things"] and result["graph"]["associations"] == frozen["associations"]
    assert {k: v for k, v in result["graph"]["metadata"].items() if k != "graph_snapshot"} == {
        k: v for k, v in frozen["metadata"].items() if k != "graph_snapshot"}


def test_replay_over_v7_is_a_no_op_on_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, _, _ = _promote(7, 10, tmp_path, monkeypatch)
    frozen = json.loads((KG / "matkg_bl1101_v7.json").read_text(encoding="utf-8"))
    assert result["stats"]["linked"] == 0 and result["stats"]["aliased"] == 0
    assert result["graph"]["things"] == frozen["things"] and result["graph"]["associations"] == frozen["associations"]


def test_cli_from_graph_writes_next_snapshot_only_in_patched_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = {p.name: p.stat().st_mtime_ns for p in KG.glob("matkg_bl1101_v*.json")}
    out = tmp_path / "kg"
    monkeypatch.setattr(legacy, "KG_DIR", out)
    terms_dir = tmp_path / "terms"
    terms_dir.mkdir()
    shutil.copy(paths.TERMS_DIR / "extracted_terms_bl1101_v3.json", terms_dir)  # the promote reads its source terms from TERMS_DIR
    monkeypatch.setattr(legacy, "TERMS_DIR", terms_dir)
    assert legacy.main(["--from-graph", str(KG / "matkg_bl1101_v3.json"), "--snapshot", "4"]) == 0
    assert (out / "matkg_bl1101_v4.json").is_file() and (terms_dir / "extracted_terms_bl1101_v4.json").is_file()
    assert "motor links: +60 part_of, +6 alias related_to" in capsys.readouterr().out
    assert {p.name: p.stat().st_mtime_ns for p in KG.glob("matkg_bl1101_v*.json")} == before


def test_replay_refuses_to_overwrite_its_source(tmp_path: Path) -> None:
    kg_dir = tmp_path / "kg"
    kg_dir.mkdir()
    source = kg_dir / "matkg_bl1101_v1.json"
    shutil.copy(KG / "matkg_bl1101_v3.json", source)
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        snapshot.run_motor_link_promote(source_kg=source, version=1, kg_dir=kg_dir, terms_dir=tmp_path / "t")


def test_snapshot_versions_and_paths(tmp_path: Path) -> None:
    assert snapshot.next_snapshot_version(tmp_path / "missing") == 1
    for name in ("matkg_bl1101_v2.json", "matkg_bl1101_v9.json", "other.json"):
        (tmp_path / name).write_text("{}")
    assert snapshot.next_snapshot_version(tmp_path) == 10
    kg_path, terms_path = snapshot.snapshot_paths(3, kg_dir=tmp_path, terms_dir=tmp_path / "t")
    assert kg_path.name == "matkg_bl1101_v3.json" and terms_path.name == "extracted_terms_bl1101_v3.json"


# ----------------------------------------------------------------------------- fetch hardening
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x", "gopher://h/", "/etc/passwd"])
def test_fetch_url_refuses_non_http_schemes(url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("urlopen must not be reached")

    monkeypatch.setattr(legacy, "urlopen", boom)
    dest = tmp_path / "x" / "p.html"
    out = fetch.fetch_url(url, dest)
    assert out["status"] == "failed" and "scheme" in out["reason"] and not dest.parent.exists()


# ----------------------------------------------------------------------------- shim
def test_legacy_path_reexports_every_name_of_every_module() -> None:
    for module in MODULES:
        tree = ast.parse(Path(str(module.__file__)).read_text(encoding="utf-8"))
        for node in tree.body:
            names = ([node.name] if isinstance(node, ast.FunctionDef | ast.ClassDef) else
                     [t.id for t in node.targets if isinstance(t, ast.Name)] if isinstance(node, ast.Assign) else [])
            for name in names:
                assert getattr(legacy, name) is getattr(module, name), f"{module.__name__}.{name}"


def test_legacy_module_is_a_thin_shim() -> None:
    lines = Path(str(legacy.__file__)).read_text(encoding="utf-8").splitlines()
    defs = [ln for ln in lines if ln.startswith(("def ", "class "))]
    assert defs == []
    assert legacy.main is cli.main and legacy.run_ingest is cli.run_ingest


def test_consumers_use_the_moved_modules() -> None:
    assert tiled_sim.make_term is terms.make_term and tiled_sim.write_kg_snapshot is snapshot.write_kg_snapshot
    assert tiled_sim.KG_DIR is paths.KG_DIR and tiled_sim.BEAMLINE_ID == specs.BEAMLINE_ID
    source = Path(str(hybrid_rag.sources.__file__)).read_text(encoding="utf-8")
    assert "bl1101_ingest" not in source and "imports.bl1101.specs import REPO_SPECS" in source


def test_patching_repo_specs_on_legacy_reaches_hybrid_rag_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy, "REPO_SPECS", ())
    assert specs.REPO_SPECS == ()
    docs = hybrid_rag.sources.collect_ops_docs(root=tmp_path)
    assert all(not str(d["doc_id"]).startswith(("bcs2sim", "bl11012")) for d in docs)


def test_cli_and_snapshot_do_not_touch_the_network() -> None:
    found: dict[str, set[str]] = {}
    for module in MODULES:
        tree = ast.parse(Path(str(module.__file__)).read_text(encoding="utf-8"))
        roots = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        roots |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
        hit = roots & {"urllib", "requests", "httpx", "socket"}
        if hit:
            found[module.__name__.rsplit(".", 1)[1]] = hit
    assert found == {"fetch": {"urllib"}}


def test_module_alias_types() -> None:
    assert isinstance(legacy, types.ModuleType) and legacy.__name__ == "app.modules.bl1101_ingest"
