"""bl1101 ingest: Python/Markdown/compose extraction, term builders, motor-stage linking and snapshots."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from app.modules import bl1101_ingest as bi
from tests.coverage_t27.test_bl1101_parsing import ALS_HTML, BLUEPRINT_HTML

SOURCE_PY = '''
"""module docstring"""
import numpy as np


class SimMotor(Device):
    """sim"""
    def move(self):
        return 1


class CustomDetector:
    pass


class HelperError(Exception):
    pass


class Plain:
    pass


class ScanPlan:
    pass


class Endstation(Device):
    pass


def load_sim_devices(cfg):
    return cfg


def _private_scan():
    yield


@bp.plan
def decorated(x):
    yield x


def plain_helper(x):
    return x


def my_scan(det):
    yield det


async def async_thing():
    pass
'''


def _py(tmp_path, text=SOURCE_PY, name="mod.py"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_extract_python_symbols_classifies_classes_and_plans(tmp_path):
    symbols, snippets = bi.extract_python_symbols(_py(tmp_path), "repo", commit="abc123")
    by_name = {s["name"]: s for s in symbols}
    assert set(by_name) == {"SimMotor", "CustomDetector", "ScanPlan", "Endstation", "load_sim_devices",
                            "decorated", "my_scan"}
    assert by_name["SimMotor"]["category"] == "OphydDevice" and by_name["CustomDetector"]["category"] == "Detector"
    assert by_name["ScanPlan"]["category"] == "BlueskyPlan" and by_name["Endstation"]["category"] == "Endstation"
    assert by_name["load_sim_devices"]["category"] == "Method" and by_name["my_scan"]["category"] == "BlueskyPlan"
    assert by_name["load_sim_devices"]["id"] == "beamline:fn-load-sim-devices"
    assert by_name["my_scan"]["id"] == "beamline:plan-my-scan"
    snip = next(s for s in snippets if s["function_name"] == "my_scan")
    assert snip["source_file_url"].startswith("https://github.com/als-computing/repo/blob/abc123/mod.py#L")
    assert snip["code_snippet"].startswith("def my_scan") and snip["source_paper"] == "repo:mod.py:my_scan"
    _, no_commit = bi.extract_python_symbols(_py(tmp_path, name="n.py"), "repo")
    assert no_commit[0]["source_file_url"] == ""


def test_extract_python_symbols_truncates_long_bodies_and_skips_bad_files(tmp_path):
    body = "def long_scan():\n" + "".join(f"    x{i} = {i}\n" for i in range(200)) + "    yield\n"
    _, snips = bi.extract_python_symbols(_py(tmp_path, body, "long.py"), "r")
    assert len(snips[0]["code_snippet"].splitlines()) == bi.MAX_SNIPPET_LINES
    assert bi.extract_python_symbols(_py(tmp_path, "def broken(:\n", "bad.py"), "r") == ([], [])
    assert bi.extract_python_symbols(tmp_path / "missing.py", "r") == ([], [])


def test_compose_services_from_yaml_and_regex_fallback(tmp_path):
    ok = tmp_path / "docker-compose.yml"
    ok.write_text("services:\n  web:\n    image: x\n  db:\n    image: y\nvolumes:\n  v: {}\n")
    assert bi.parse_compose_services(ok) == [{"name": "web", "file": ok.name}, {"name": "db", "file": ok.name}]
    bad = tmp_path / "broken.yml"
    bad.write_text("services:\n  api:\n  worker:\n  volumes:\n  bad: [unclosed\n")
    assert [s["name"] for s in bi.parse_compose_services(bad)] == ["api", "worker"]
    nomap = tmp_path / "list.yml"
    nomap.write_text("- a\n- b\n")
    assert bi.parse_compose_services(nomap) == []


def test_markdown_summary_and_ts_fence_extraction(tmp_path):
    md = tmp_path / "README.md"
    md.write_text("preamble\n# Title\nFirst line.\n\n```py\ncode\n```\nMore text.\n", encoding="utf-8")
    summary = bi.extract_markdown_software(md, "r")
    assert summary.startswith("# Title First line.") and "preamble" not in summary and "code" in summary
    assert bi.extract_markdown_software(md, "r", limit=10) == "# Title Fi"
    assert bi.extract_markdown_software(tmp_path / "none.md", "r") == ""

    ts = tmp_path / "ts.md"
    ts.write_text("intro\n```ts\nconst other = 1\n```\n\n```tsx\nconst cfg: FinchConfig = {}\n```\n```ts\nFinchConfig 2\n```\n")
    (snip,) = bi.extract_ts_fences(ts, "finch", commit="c0")
    assert snip["code_snippet"] == "const cfg: FinchConfig = {}" and snip["page"] == 6
    assert snip["code_language"] == "typescript" and snip["repo_commit_sha"] == "c0"
    assert bi.extract_ts_fences(tmp_path / "none.md", "r") == []
    plain = tmp_path / "plain.md"
    plain.write_text("```ts\nconst nothing = 1\n```\n")
    assert bi.extract_ts_fences(plain, "r") == []


# ----------------------------------------------------------------------------- term builders
@pytest.fixture(scope="module")
def als():
    return bi.parse_als_page(ALS_HTML)


@pytest.fixture(scope="module")
def blueprint():
    return bi.parse_blueprint_html(BLUEPRINT_HTML)


def test_energy_definition_uses_page_values_and_blueprint_note():
    label, definition = bi._energy_from_sources({"facts": {"Minimum energy (eV)": "100", "Maximum energy (eV)": "2,000"}},
                                                {"beam_note": "Range ~ 200 – 1500 eV at mono"})
    assert label == "100–2,000 eV" and "Blueprint beam note: ~ 200 – 1500 eV." in definition
    label, definition = bi._energy_from_sources({}, {})
    assert label == "165–1,500 eV" and "E/ΔE 4000" in definition


def test_terms_from_als_cover_techniques_contacts_and_source(als):
    terms = bi.terms_from_als(als, "als-src")
    by_id = {t["id"]: t for t in terms}
    assert "beamline:technique-RSoXS" in by_id and by_id["beamline:technique-RSoXS"]["category"] == "ExperimentalTechnique"
    assert len(terms) == 7 + 12 + 2
    assert all(t["source_papers"] == ["als-src"] for t in terms)
    epu = by_id["beamline:source-EPU5"]
    assert "Contacts: " in epu["definition"] and epu["category"] == "BeamlineStage"
    assert by_id["beamline:PhotonEnergy-soft-xray"]["photon_energy_eV"] == {"min": 165, "max": 1500}
    bare = {t["id"]: t for t in bi.terms_from_als({}, "s")}
    assert "Contacts" not in bare["beamline:source-EPU5"]["definition"]


def test_terms_from_blueprint_wires_devices_pvs_stages_and_beam_path(blueprint):
    terms = bi.terms_from_blueprint(blueprint, "bp")
    by_id = {}
    for t in terms:
        by_id.setdefault(t["id"], []).append(t)
    motor = by_id["beamline:Motor-m1"][0]
    rel_pairs = {(r["relation"], r["related_id"]) for r in motor["relations"]}
    assert ("part_of", bi.BEAMLINE_ID) in rel_pairs and ("hasPV", "beamline:PV-X-M1") in rel_pairs
    assert ("part_of", "beamline:stage-Mono-101") in rel_pairs and ("related_to", "beamline:ophyd-EpicsMotor") in rel_pairs
    assert motor["definition"].endswith('Handle d.motors["m1"].') and motor["pv"] == "X:M1"
    assert by_id["beamline:PV-X-M1"][0]["category"] == "ProcessVariable"
    assert by_id["beamline:ophyd-EpicsMotor"][0]["category"] == "OphydDevice"
    assert by_id["beamline:Detector-AXIS-SXR-40"] and by_id["beamline:Endstation-Reflectometer"]
    assert by_id["beamline:rule-Safety"][0]["category"] == "Condition"
    # one directed beam-path edge per relation name per step; every stage gets an index
    steps = len(blueprint["beam_path"]) - 1
    edges = [t for t in terms if t["definition"] == "" and t["relations"]]
    assert len(edges) == steps * len(bi.BEAM_PATH_RELATIONS)
    idx = {t["name"]: t["beam_path_index"] for t in terms if t.get("beam_path_index")}
    assert idx["EPU"] == 1 and idx["Detector"] == len(blueprint["beam_path"])


def test_beam_path_stage_terms_need_two_stages_and_reuse_known_stage_ids():
    assert bi._beam_path_stage_terms({"beam_path": ["EPU"]}, "s", {}) == []
    ids = {"epu": "beamline:stage-EPU"}
    terms = bi._beam_path_stage_terms({"beam_path": ["EPU", "M101"]}, "s", ids)
    first = [t for t in terms if t["id"] == "beamline:stage-EPU"][0]
    assert first["definition"] == "Optical beam-path stage 1: EPU." and "m101" in ids
    assert any(t["id"] == "beamline:stage-M101" and "Sheet A" in t["definition"] for t in terms)


def test_beamline_seed_links_catalogs_and_software(als, blueprint):
    seed = bi.beamline_seed(als, blueprint, "als", "bp")
    assert seed["id"] == bi.BEAMLINE_ID and seed["source_papers"] == ["als", "bp"]
    kinds = {r["relation"] for r in seed["relations"]}
    assert {"hasEndstation", "hasDetector", "runsSoftware", "usesTechnique", "supportsGeometry"} <= kinds
    assert len([r for r in seed["relations"] if r["relation"] == "runsSoftware"]) == 7
    assert [t["name"] for t in bi.software_catalog()][:2] == ["Bluesky", "ophyd-async"] and len(bi.software_catalog()) == 10


def test_attach_repo_terms_for_each_repo_flavour():
    spec = bi.RepoSpec(name="bcs2sim-ophyd", url="https://x/y", mode="full")
    symbols = [
        {"id": "beamline:plan-scan", "name": "scan", "category": "BlueskyPlan", "file": "p.py", "snippet_paper": "r:p.py:scan",
         "start": 3, "end": 9},
        {"id": "beamline:ophyd-Dev", "name": "Dev", "category": "OphydDevice", "file": "p.py"},
    ]
    terms, snippets = bi.attach_repo_terms(spec, Path("."), "a" * 40, symbols, [{"s": 1}],
                                           [{"name": "web", "file": "compose.yml"}],
                                           [("docs/guide.md", "Summary"), ("docs/empty.md", "")])
    ids = {t["id"]: t for t in terms}
    assert snippets == [{"s": 1}] and ids["beamline:repo-bcs2sim-ophyd"]["repo_commit_sha"] == "a" * 40
    plan_rels = {(r["relation"], r["related_id"]) for r in ids["beamline:plan-scan"]["relations"]}
    assert ("has_code_snippet", "beamline:snippet-bcs2sim-ophyd-scan-3") in plan_rels
    assert ("runsSoftware", "beamline:Software-ophyd-async") in plan_rels
    assert "beamline:service-bcs2sim-ophyd-web" in ids and "beamline:doc-bcs2sim-ophyd-guide" in ids
    assert "beamline:doc-bcs2sim-ophyd-empty" not in ids and "beamline:config-sim-config" in ids
    for name, expect in (("bl11012-scan-replay", "beamline:DataPath-labview-replay-bundle"),
                         ("bl11012-bluesky", "beamline:DataPath-tiled-compose")):
        extra, _ = bi.attach_repo_terms(bi.RepoSpec(name=name, url="u", mode="docs"), Path("."), "", [], [], [], [])
        assert expect in {t["id"] for t in extra}


def test_merge_terms_keeps_first_unions_relations_and_longest_definition():
    a = bi.make_term("beamline:X", "X", "Motor", "short", "s1", [bi._rel("X", "part_of", "A")])
    b = bi.make_term("beamline:X", "X", "Motor", "a longer definition", "s2",
                     [bi._rel("X", "part_of", "A"), bi._rel("X", "hasPV", "P")], pv="X:1")
    c = bi.make_term("beamline:A", "A", "Stage", "", "s1")
    merged = bi.merge_terms([[a, c], [b]])
    assert [t["id"] for t in merged] == ["beamline:A", "beamline:X"]
    x = merged[1]
    assert x["definition"] == "a longer definition" and x["source_papers"] == ["s1", "s2"] and x["pv"] == "X:1"
    assert [(r["relation"], r["related_id"]) for r in x["relations"]] == [("part_of", "A"), ("hasPV", "P")]


def test_sort_graph_is_deterministic():
    g = bi.sort_graph({"things": [{"id": "b"}, {"id": "a"}, {}],
                       "associations": [{"subject": "b", "predicate": "p", "object": "x"},
                                        {"subject": "a", "predicate": "q", "object": "x"},
                                        {"subject": "a", "predicate": "p", "object": "y"}]})
    assert [t.get("id") for t in g["things"]] == [None, "a", "b"]
    assert [(e["subject"], e["predicate"]) for e in g["associations"]] == [("a", "p"), ("a", "q"), ("b", "p")]
    assert bi.sort_graph({}) == {"things": [], "associations": []}


# ----------------------------------------------------------------------------- motor-stage map
MAP_YAML = """
version: 9
source: test-map
motors:
  m_a: Mono 101
  m_b: {stage: Exit slits}
  m_c: nowhere
  m_d: ""
aliases:
  m_a_galil: m_a
  ghost: m_a
leave_on_beamline: [x, y]
"""


def test_load_motor_stage_map_normalises_shapes(tmp_path):
    p = tmp_path / "map.yaml"
    p.write_text(MAP_YAML)
    m = bi.load_motor_stage_map(p)
    assert m["version"] == 9 and m["motors"]["m_a"] == "Mono 101" and m["motors"]["m_b"] == "Exit slits"
    assert "m_d" not in m["motors"] and m["aliases"]["m_a_galil"] == "m_a" and m["leave_on_beamline"] == {"x": "", "y": ""}
    assert m["source"] == "test-map" and m["path"]
    p.write_text("leave_on_beamline:\n  k: why\n")
    assert bi.load_motor_stage_map(p)["leave_on_beamline"] == {"k": "why"}
    assert bi.load_motor_stage_map(tmp_path / "missing.yaml") == {"version": 0, "motors": {}, "aliases": {},
                                                                  "leave_on_beamline": {}}


def _graph():
    return {
        "things": [
            {"id": "s_mono", "category": "BeamlineStage", "name": "Mono 101"},
            {"id": "s_exit", "category": "BeamlineStage", "name": "Exit slits"},
            {"id": "s_blob", "category": "BeamlineStage", "name": "a b c d e f g"},
            {"id": "s_noname", "category": "BeamlineStage", "name": ""},
            {"id": "mot_a", "category": "Motor", "ophyd_name": "m_a"},
            {"id": "mot_b", "category": "Motor", "ophyd_name": "m_b"},
            {"id": "mot_alias", "category": "Motor", "ophyd_name": "m_a_galil"},
            {"id": "mot_free", "category": "Motor", "ophyd_name": ""},
        ],
        "associations": [{"subject": "mot_a", "predicate": "rel:part_of", "object": "s_mono"}],
    }


@pytest.fixture
def stage_map(tmp_path):
    p = tmp_path / "map.yaml"
    p.write_text(MAP_YAML)
    return bi.load_motor_stage_map(p)


def test_apply_motor_stage_map_to_graph_links_aliases_and_counts_unknowns(stage_map):
    graph, stats = bi.apply_motor_stage_map_to_graph(_graph(), stage_map)
    edges = {(e["subject"], e["predicate"], e["object"]) for e in graph["associations"]}
    assert ("mot_b", "rel:part_of", "s_exit") in edges
    assert ("mot_alias", "rel:related_to", "mot_a") in edges and ("mot_alias", "rel:part_of", "s_mono") in edges
    # m_a is already linked (no new edge); m_c has no stage node and alias "ghost" has no motor node
    assert stats == {"linked": 2, "aliased": 1, "skipped_unknown": 2, "motors": 3}


def test_apply_motor_stage_map_to_graph_does_not_mutate_input_and_is_idempotent(stage_map):
    original = _graph()
    graph, first = bi.apply_motor_stage_map_to_graph(original, stage_map)
    assert len(original["associations"]) == 1
    _, second = bi.apply_motor_stage_map_to_graph(graph, stage_map)
    assert second["linked"] == 0 and second["aliased"] == 0


def test_apply_motor_stage_map_to_terms_adds_relations_once(stage_map):
    terms = [
        {"id": "s_mono", "category": "BeamlineStage", "name": "Mono 101"},
        {"id": "s_exit", "category": "BeamlineStage", "name": "Exit slits"},
        {"id": "mot_a", "category": "Motor", "ophyd_name": "m_a", "relations": []},
        {"id": "mot_alias", "category": "Motor", "ophyd_name": "m_a_galil"},
        {"id": "mot_ghost_alias", "category": "Motor", "ophyd_name": "ghost"},
    ]
    added = bi.apply_motor_stage_map_to_terms(terms, stage_map)
    # m_a part_of mono; each of the two aliases gets related_to + part_of (ghost is a motor here too)
    assert added == 5
    alias = terms[3]
    assert {(r["relation"], r["related_id"]) for r in alias["relations"]} == {("related_to", "mot_a"),
                                                                              ("part_of", "s_mono")}
    assert bi.apply_motor_stage_map_to_terms(terms, stage_map) == 0
    assert ("related_to", "mot_a") in {(r["relation"], r["related_id"]) for r in terms[4]["relations"]}
    assert bi._stage_id_by_name([{"category": "Motor", "id": "m"}]) == {}


# ----------------------------------------------------------------------------- snapshots / promotion
def test_write_kg_snapshot_stamps_metadata_and_hash(tmp_path):
    graph = {"things": [{"id": "a"}], "associations": []}
    path = bi.write_kg_snapshot(graph, 5, kg_dir=tmp_path / "kg")
    assert path.name == "matkg_bl1101_v5.json" and not path.with_suffix(".json.tmp").exists()
    data = json.loads(path.read_text())
    snap = data["metadata"]["graph_snapshot"]
    assert snap["name"] == "matkg_bl1101_v5" and snap["nodes"] == 1 and snap["edges"] == 0 and len(snap["sha256"]) == 64
    assert data["metadata"]["ingest_version"] == bi.INGEST_VERSION


def test_promote_copies_graph_applies_map_and_refuses_to_overwrite_source(tmp_path, stage_map, monkeypatch):
    src_dir = tmp_path / "kg"
    src_dir.mkdir()
    src = src_dir / "matkg_bl1101_v3.json"
    src.write_text(json.dumps(_graph()))
    terms_dir = tmp_path / "terms"
    terms_dir.mkdir()
    monkeypatch.setattr(bi, "TERMS_DIR", terms_dir)
    (terms_dir / "extracted_terms_bl1101_v3.json").write_text(json.dumps({"terms": _graph()["things"]}))
    map_path = tmp_path / "map.yaml"
    map_path.write_text(MAP_YAML)
    result = bi.run_motor_link_promote(source_kg=src, map_path=map_path, kg_dir=src_dir, terms_dir=terms_dir)
    assert result["version"] == 4 and result["stats"]["linked"] == 2
    new = json.loads(result["kg_path"].read_text())
    assert new["metadata"]["promote"]["kind"] == "motor_stage_links" and new["metadata"]["promote"]["map_version"] == 9
    terms = json.loads(result["terms_path"].read_text())
    assert terms["metadata"]["promote"]["linked"] == 2 and terms["metadata"]["graph_snapshot"]["version"] == 4
    assert json.loads(src.read_text()) == _graph()  # the source snapshot is untouched
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        bi.run_motor_link_promote(source_kg=src, version=3, map_path=map_path, kg_dir=src_dir, terms_dir=terms_dir)
    with pytest.raises(FileNotFoundError):
        bi.run_motor_link_promote(source_kg=tmp_path / "none.json", map_path=map_path)


def test_promote_without_a_terms_snapshot_still_writes_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(bi, "TERMS_DIR", tmp_path / "no_terms")
    src = tmp_path / "matkg_custom.json"
    src.write_text(json.dumps(_graph()))
    result = bi.run_motor_link_promote(source_kg=src, version=2, map_path=tmp_path / "missing.yaml",
                                       kg_dir=tmp_path / "out", terms_dir=tmp_path / "out_terms")
    assert result["terms_path"] is None and result["stats"]["linked"] == 0
    assert bi._terms_snapshot_for_kg(src).name == "extracted_terms_custom.json"
    assert bi._terms_snapshot_for_kg(Path("matkg_bl1101_v12.json")).name == "extracted_terms_bl1101_v12.json"


# ----------------------------------------------------------------------------- people and records
def test_terms_from_people_dedupes_across_sources_and_links_roles(als, blueprint):
    terms = bi.terms_from_people(als, blueprint, "als-src", "bp.html")
    ids = {t["id"]: t for t in terms}
    assert "beamline:Role-BeamlineScientist" in ids
    cheng = ids["beamline:Person-Cheng-Wang"]
    assert "cwang@lbl.gov" in cheng["definition"] and cheng["email"] == "cwang@lbl.gov"
    assert {r["relation"] for r in cheng["relations"]} == {"hasRole", "supports"}
    assert sum(1 for t in terms if t["category"] == "Person") == 2  # Cheng Wang and Thomas Ferron, the latter named by both sources but kept once
    ferron = ids["beamline:Person-Thomas-Ferron"]
    assert "source_url" in ferron


def test_terms_from_people_falls_back_to_facts_and_returns_empty_without_people():
    out = bi.terms_from_people({"facts": {"Primary contact(s)": "Ada Lovelace"}}, {"header": {}}, "a", "b")
    assert [t["name"] for t in out if t["category"] == "Person"] == ["Ada Lovelace"]
    assert bi.terms_from_people({}, {}, "a", "b") == []


def test_ingest_records_wires_motors_detectors_plans_and_resolves_bcs2sim_beam_path(als, blueprint, tmp_path):
    root = tmp_path / "bcs"
    root.mkdir()
    (root / "bl11012_knowledge.md").write_text("Mono → M103 → exit slits → sample")
    bp = {k: v for k, v in blueprint.items() if k not in ("beam_path", "beam_path_svg", "beam_path_caption")}
    payload = {
        "spec": bi.RepoSpec(name="bcs2sim-ophyd", url="https://x", mode="full"), "root": root, "sha": "s",
        "symbols": [{"id": "beamline:plan-my-scan", "name": "my_scan", "category": "BlueskyPlan", "file": "p.py"}],
        "snippets": [{"id": "snip"}], "compose": [], "markdown": [],
    }
    rec = bi.ingest_records(als=als, blueprint=bp, repo_payloads=[payload])
    ids = {t["id"]: t for t in rec["terms"]}
    beam = ids[bi.BEAMLINE_ID]
    rels = {(r["relation"], r["related_id"]) for r in beam["relations"]}
    assert ("hasMotor", "beamline:Motor-m1") in rels and ("hasDetector", "beamline:Detector-cam") in rels
    assert ("implementsPlan", "beamline:plan-my-scan") in rels
    assert rec["code_snippets"] == [{"id": "snip"}]
    assert "beamline:stage-M103" in ids  # path taken from the bcs2sim knowledge file
    assert [t["id"] for t in rec["terms"]] == sorted(ids)


def test_records_to_graph_and_fixture_round_trip(tmp_path):
    py = _py(tmp_path)
    rec = bi.ingest_fixture("<html><body><p>nothing</p></body></html>", [py])
    graph = bi.records_to_graph(rec, {"graph_snapshot": {"version": 1}})
    ids = {t["id"] for t in graph["things"]}
    assert bi.BEAMLINE_ID in ids and "beamline:Person-Cheng-Wang" in ids and "beamline:plan-my-scan" in ids
    assert graph["metadata"] == {"graph_snapshot": {"version": 1}}
    assert any(e["subject"] == bi.BEAMLINE_ID and "hasContact" in e["predicate"] or e["object"] == bi.BEAMLINE_ID
               for e in graph["associations"])
    full = bi.ingest_fixture(ALS_HTML, [])
    assert "beamline:technique-RSoXS" in {t["id"] for t in full["terms"]}


# ----------------------------------------------------------------------------- CLI / run_ingest
@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect every module-level output path of the ingest into tmp."""
    root = tmp_path / "repo"
    paths = {
        "REPO_ROOT": root,
        "KG_DIR": root / "storage/kg",
        "TERMS_DIR": root / "storage/terminology",
        "CACHE_DIR": root / ".cache/bl1101",
        "REPOS_DIR": root / ".cache/bl1101/repos",
        "READMES_DIR": root / ".cache/bl1101/readmes",
        "PAGES_DIR": root / ".cache/bl1101/pages",
        "WORK_DIR": root / ".cache/bl1101/work",
        "SCHEMA_PATH": root / "storage/schema/bl1101_schema.yaml",
        "BLUEPRINT_PATH": root / "papers/rsoxs/beamline_blueprint/blueprint.html",
    }
    for name, value in paths.items():
        monkeypatch.setattr(bi, name, value)
    paths["BLUEPRINT_PATH"].parent.mkdir(parents=True)
    paths["BLUEPRINT_PATH"].write_text(BLUEPRINT_HTML, encoding="utf-8")
    monkeypatch.setattr(bi, "MOTOR_STAGE_MAP_PATH", root / "none.yaml")
    spec_full = bi.RepoSpec(name="fullrepo", url="https://x/full", mode="full", python_globs=("src/*.py",),
                            md_globs=("README.md",), compose_globs=("compose.yml",))
    spec_finch = bi.RepoSpec(name="bl11012-finch", url="https://x/finch", mode="docs", md_globs=("README.md",))
    spec_ro = bi.RepoSpec(name="readmeonly", url="https://x/ro", mode="readme", readme_only=True)
    monkeypatch.setattr(bi, "REPO_SPECS", (spec_full, spec_finch, spec_ro))
    return SimpleNamespace(root=root, paths=paths)


def _make_clone(sb, name="fullrepo"):
    clone = sb.paths["REPOS_DIR"] / name
    (clone / "src").mkdir(parents=True)
    (clone / "src" / "plans.py").write_text("def my_scan(det):\n    yield det\n")
    (clone / "README.md").write_text("# Title\nHello\n```ts\nFinchConfig = 1\n```\n")
    (clone / "compose.yml").write_text("services:\n  api: {}\n")
    return clone


def test_ingest_repos_offline_skips_uncached_and_reads_cached_clones(sandbox, monkeypatch):
    clone = _make_clone(sandbox)
    finch = sandbox.paths["REPOS_DIR"] / "bl11012-finch"
    finch.mkdir(parents=True)
    (finch / "README.md").write_text("# Finch\n```tsx\nconst c: FinchConfig = {}\n```\n")
    (finch / ".git").mkdir()
    monkeypatch.setattr(bi, "git_sha", lambda p: "f" * 40)
    landed, skipped, payloads = bi._ingest_repos(offline=True)
    assert [x["name"] for x in landed] == ["fullrepo", "bl11012-finch"]
    assert skipped == [{"name": "readmeonly", "url": "https://x/ro", "status": "skipped", "reason": "offline, no cache"}]
    full = payloads[0]
    assert full["sha"] == "" and [s["name"] for s in full["symbols"]] == ["my_scan"]
    assert full["symbols"][0]["file"] == "src/plans.py" and full["compose"] == [{"name": "api", "file": "compose.yml"}]
    assert full["markdown"][0][0] == "README.md"
    assert payloads[1]["sha"] == "f" * 40 and payloads[1]["snippets"][0]["function_name"] == "FinchConfigProvider"


def test_ingest_repos_offline_git_failure_marks_skipped(sandbox, monkeypatch):
    clone = _make_clone(sandbox)
    (clone / ".git").mkdir()

    def fail(path):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(bi, "git_sha", fail)
    landed, skipped, _ = bi._ingest_repos(offline=True)
    assert "fullrepo" in [s["name"] for s in skipped] and landed == []


def test_ingest_repos_online_uses_sync_repo_and_skips_failures(sandbox, monkeypatch):
    def fake_sync(spec, dest):
        if spec.name == "fullrepo":
            _make_clone(sandbox)
            return {"name": spec.name, "url": spec.url, "status": "ok", "sha": "a" * 40, "mode": spec.mode}
        return {"name": spec.name, "url": spec.url, "status": "skipped", "reason": "private", "mode": spec.mode}

    monkeypatch.setattr(bi, "sync_repo", fake_sync)
    landed, skipped, payloads = bi._ingest_repos(offline=False)
    assert [x["name"] for x in landed] == ["fullrepo"] and {s["name"] for s in skipped} == {"bl11012-finch", "readmeonly"}
    assert payloads[0]["sha"] == "a" * 40


def test_run_ingest_offline_without_cache_falls_back_to_builtin_als_facts(sandbox, monkeypatch):
    result = bi.run_ingest(offline=True)
    assert result["version"] == 1 and result["kg_path"].name == "matkg_bl1101_v1.json" and result["nodes"] > 20
    skipped = {s["name"] for s in result["sources_skipped"]}
    assert "als_public_page" in skipped and "fullrepo" in skipped
    data = json.loads(result["kg_path"].read_text())
    assert data["metadata"]["graph_snapshot"]["sha256"] and data["metadata"]["corpus_revision"]["git_shas"] == {}
    ids = {t["id"] for t in data["things"]}
    assert "beamline:Person-Cheng-Wang" in ids  # built-in contacts
    assert result["terms_path"].exists() and (sandbox.paths["WORK_DIR"] / "extracted_terms.json").exists()
    again = bi.run_ingest(offline=True, from_scratch=True)
    assert again["version"] == 2  # the next unused vN; existing snapshots are kept
    assert result["kg_path"].exists()


def test_run_ingest_online_fetches_page_and_uses_it(sandbox, monkeypatch):
    def fake_fetch(url, dest, timeout=45):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(ALS_HTML, encoding="utf-8")
        return {"url": url, "status": "ok", "fetched_at": "t", "path": "p", "sha256": "s", "http_status": 200, "bytes": 1}

    monkeypatch.setattr(bi, "fetch_url", fake_fetch)
    monkeypatch.setattr(bi, "sync_repo", lambda spec, dest: {"name": spec.name, "url": spec.url, "status": "skipped",
                                                              "reason": "private", "mode": spec.mode})
    result = bi.run_ingest()
    assert [s["name"] for s in result["sources_landed"]] == ["als_public_page", "gabe_blueprint"]
    page_meta = result["graph"]["metadata"]["corpus_revision"]["page_snapshots"]["als_public"]
    assert page_meta["status"] == "ok" and page_meta["sha256"] == "s"


def test_run_ingest_offline_uses_cached_als_page_and_reports_missing_blueprint(sandbox, monkeypatch):
    sandbox.paths["PAGES_DIR"].mkdir(parents=True)
    (sandbox.paths["PAGES_DIR"] / "als_11-0-1-2.html").write_text(ALS_HTML, encoding="utf-8")
    result = bi.run_ingest(offline=True)
    landed = {s["name"]: s for s in result["sources_landed"]}
    assert landed["als_public_page"]["offline"] is True
    sandbox.paths["BLUEPRINT_PATH"].unlink()
    with pytest.raises(FileNotFoundError, match="Blueprint not found"):
        bi.run_ingest(offline=True)


def test_run_ingest_online_fetch_failure_is_recorded_as_skipped(sandbox, monkeypatch):
    monkeypatch.setattr(bi, "fetch_url", lambda url, dest, timeout=45: {"url": url, "status": "failed", "reason": "dns"})
    monkeypatch.setattr(bi, "sync_repo", lambda spec, dest: {"name": spec.name, "url": spec.url, "status": "skipped",
                                                              "reason": "x", "mode": spec.mode})
    result = bi.run_ingest()
    assert any(s["name"] == "als_public_page" and s["reason"] == "dns" for s in result["sources_skipped"])


def test_parse_args_and_main_dispatch(sandbox, monkeypatch, capsys):
    args = bi.parse_args(["--offline", "--from-scratch", "-v", "--snapshot", "9", "--from-graph", "g.json"])
    assert args.offline and args.from_scratch and args.verbose and args.snapshot == 9 and args.from_graph == Path("g.json")
    assert bi.parse_args([]).motor_stage_map is None
    lines = []
    monkeypatch.setattr(bi.output, "info", lambda msg, *a, **k: lines.append(msg))
    assert bi.main(["--offline"]) == 0
    assert lines[0].startswith("bl1101 v1:") and lines[1].startswith("landed:") and lines[2].startswith("skipped:")
    kg = sandbox.paths["KG_DIR"] / "matkg_bl1101_v1.json"
    lines.clear()
    assert bi.main(["--from-graph", str(kg), "--snapshot", "7"]) == 0
    assert lines[0].startswith("bl1101 v7:") and lines[1].startswith("motor links: +")
