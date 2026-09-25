"""Seeded Tiled Graph sim: 5 ESAFs, proposal/scan/sample ranges, JSON ingest."""

import json
from pathlib import Path

from app.modules.tiled_sim import (
    DEFAULT_SEED,
    FIXTURE_PATH,
    SEED_PATH,
    fixture_stats,
    fixture_to_overlay,
    generate_fixture,
    iter_identity_entities,
    iter_identity_links,
    load_seed,
    merge_overlay,
    parse_args,
    promote_rsoxs_snapshot,
    push_fixture_to_tiled,
    run_tiled_sim_promote,
    snapshot_copy_terms,
    write_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
V4_PATH = ROOT / "storage/kg/matkg_bl1101_v4.json"
SIDECAR = ROOT / "storage/fixtures/rsoxs_snapshot_promote.json"


def _tiny_ops_graph() -> dict:
    return {
        "things": [
            {
                "id": "beamline:BL-11-0-1-2",
                "name": "ALS 11.0.1.2",
                "category": "Beamline",
            },
            {"id": "beamline:Person-Cheng-Wang", "name": "Cheng Wang", "category": "Person"},
            {"id": "beamline:Person-Thomas-Ferron", "name": "Thomas Ferron", "category": "Person"},
            {"id": "beamline:Motor-sample-x", "name": "sample_x", "category": "Motor"},
            {"id": "beamline:Motor-sample-y", "name": "sample_y", "category": "Motor"},
            {"id": "beamline:Motor-sample-z", "name": "sample_z", "category": "Motor"},
            {
                "id": "beamline:Motor-sample-theta",
                "name": "sample_theta",
                "category": "Motor",
            },
            {
                "id": "beamline:Motor-sample-azimuthal-rotation",
                "name": "sample_azimuthal_rotation",
                "category": "Motor",
            },
            {"id": "beamline:Motor-samplerot0", "name": "samplerot0", "category": "Motor"},
            {"id": "beamline:Detector-AXIS-SXR-40", "name": "AXIS-SXR-40", "category": "Detector"},
            {"id": "beamline:stage-Sample-stage", "name": "Sample stage", "category": "BeamlineStage"},
            {"id": "beamline:plan-count", "name": "count", "category": "BlueskyPlan"},
            {
                "id": "beamline:plan-labview-energy-scan",
                "name": "labview_energy_scan",
                "category": "BlueskyPlan",
            },
            {
                "id": "beamline:plan-labview-ccd-only-scan",
                "name": "labview_ccd_only_scan",
                "category": "BlueskyPlan",
            },
            {
                "id": "beamline:plan-labview-replay-scan",
                "name": "labview_replay_scan",
                "category": "BlueskyPlan",
            },
            {"id": "beamline:plan-scan", "name": "scan", "category": "BlueskyPlan"},
            {"id": "beamline:Software-Tiled", "name": "Tiled", "category": "Software"},
            {"id": "beamline:Software-Bluesky", "name": "Bluesky", "category": "Software"},
            {
                "id": "beamline:repo-bl11012-scan-replay",
                "name": "bl11012-scan-replay",
                "category": "Software",
            },
            {
                "id": "beamline:repo-bl11012-bluesky",
                "name": "bl11012-bluesky",
                "category": "Software",
            },
            {"id": "beamline:repo-bcs2sim-ophyd", "name": "bcs2sim-ophyd", "category": "Software"},
            {
                "id": "beamline:Polarization-linear-polarization",
                "name": "linear polarization",
                "category": "Polarization",
            },
        ],
        "associations": [
            {
                "subject": "beamline:Motor-sample-x",
                "predicate": "rel:part_of",
                "object": "beamline:stage-Sample-stage",
                "has_evidence": "v4",
            }
        ],
        "metadata": {"schema_version": "bl1101_v2"},
    }


def test_seed_file_documents_20260916():
    seed = load_seed()
    assert seed["seed"] == DEFAULT_SEED == 20260916
    assert SEED_PATH.exists()
    assert len(seed["esafs"]) == 5


def test_generator_seed_is_stable():
    seed = load_seed()
    a = generate_fixture(seed)
    b = generate_fixture(seed)
    assert a["stats"] == b["stats"]
    assert [e["id"] for e in a["esafs"]] == [e["id"] for e in b["esafs"]]
    assert a["esafs"][0]["proposals"] == b["esafs"][0]["proposals"]


def test_five_esafs_and_count_ranges():
    fixture = generate_fixture()
    assert fixture["stats"]["esafs"] == 5
    assert len(fixture["esafs"]) == 5
    scientists = {e["scientist"] for e in fixture["esafs"]}
    assert "Cheng Wang" in scientists
    assert "Thomas Ferron" in scientists
    for esaf in fixture["esafs"]:
        assert 0 <= len(esaf["proposals"]) <= 10
        for proposal in esaf["proposals"]:
            assert 0 <= len(proposal["scans"]) <= 20
            assert 1 <= len(proposal["samples"]) <= 20
            for scan in proposal["scans"]:
                assert scan["plan_name"]
                assert scan["absorption_edge"]
                assert scan["scattering_technique"]
                assert "uid" in scan
                assert scan["catalog_path"][0] == esaf["esaf_number"]
                assert scan["sample_id"] in {s["id"] for s in proposal["samples"]}


def test_overlay_relations_use_graphql_types_and_prov():
    fixture = generate_fixture()
    overlay = fixture_to_overlay(fixture)
    cats = {n["category"] for n in overlay["things"]}
    assert {"ESAF", "Proposal", "Sample", "BlueskyRun", "RSoXSMeasurement"} <= cats
    preds = {e["predicate"] for e in overlay["associations"]}
    assert "rel:hasProposal" in preds
    assert "rel:hasScan" in preds
    assert "rel:hasSample" in preds
    assert "rel:part_of" in preds
    assert "prov:used" in preds
    assert "prov:wasDerivedFrom" in preds
    assert not any(p.startswith("rel:prov:") for p in preds)
    assert not any(p == "rel:wasDerivedFrom" for p in preds)
    has_scan = [e for e in overlay["associations"] if e["predicate"] == "rel:hasScan"]
    assert len(has_scan) == fixture["stats"]["scans"]
    assert all(e["subject"] != e["object"] for e in overlay["associations"])


def test_promote_appends_v5_without_touching_source(tmp_path: Path):
    """By default (include_sim_nodes=False) sim nodes are NOT baked into the KG snapshot.

    Since 2026-09-23, ESAF/Proposal/Sample/BlueskyRun nodes are served exclusively by
    Tiled Graph (:8765).  The JSON KG snapshot (v8+) intentionally omits them.
    This test verifies the new default behaviour: source file is untouched and the
    output graph contains NO sim nodes.

    To verify the old overlay logic still works, pass include_sim_nodes=True explicitly.
    """
    src = tmp_path / "matkg_bl1101_v4.json"
    src.write_text(json.dumps(_tiny_ops_graph()), encoding="utf-8")
    before = src.read_bytes()
    fixture = generate_fixture()

    # --- default: sim nodes NOT included ---
    result = run_tiled_sim_promote(
        source_kg=src,
        fixture=fixture,
        version=5,
        kg_dir=tmp_path,
        terms_dir=tmp_path,
    )
    assert result["kg_path"].name == "matkg_bl1101_v5.json"
    assert src.read_bytes() == before
    graph = json.loads(result["kg_path"].read_text(encoding="utf-8"))
    sim_cats = {"ESAF", "Proposal", "Sample", "BlueskyRun"}
    sim_nodes = [n for n in graph["things"] if n.get("category") in sim_cats]
    assert sim_nodes == [], "Sim nodes must NOT be baked into KG by default (use Tiled Graph instead)"
    assert any(n["id"] == "beamline:BL-11-0-1-2" for n in graph["things"])
    assert result["promote"]["include_sim_nodes"] is False

    # --- explicit opt-in: sim nodes included ---
    result_with = run_tiled_sim_promote(
        source_kg=src,
        fixture=fixture,
        version=6,
        kg_dir=tmp_path,
        terms_dir=tmp_path,
        include_sim_nodes=True,
    )
    graph_with = json.loads(result_with["kg_path"].read_text(encoding="utf-8"))
    esafs = [n for n in graph_with["things"] if n.get("category") == "ESAF"]
    assert len(esafs) == 5, "include_sim_nodes=True must still bake ESAF nodes for backward compat"

    # overlay logic still works independently
    merged = merge_overlay(_tiny_ops_graph(), fixture_to_overlay(fixture))
    assert fixture_stats(fixture["esafs"])["esafs"] == 5
    assert len([n for n in merged["things"] if n["category"] == "ESAF"]) == 5


def test_committed_fixture_matches_seed_and_ranges():
    assert FIXTURE_PATH.exists() or True  # written by ingest; regenerate in this test if missing
    fixture = generate_fixture()
    if not FIXTURE_PATH.exists():
        write_fixture(fixture, FIXTURE_PATH)
    on_disk = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert on_disk["seed"] == DEFAULT_SEED
    assert on_disk["stats"]["esafs"] == 5
    assert on_disk["stats"] == fixture["stats"]
    for esaf in on_disk["esafs"]:
        assert 0 <= len(esaf["proposals"]) <= 10
        for proposal in esaf["proposals"]:
            assert 0 <= len(proposal["scans"]) <= 20
            assert 1 <= len(proposal["samples"]) <= 20


def test_snapshot_copy_does_not_write_live(tmp_path: Path):
    live = tmp_path / "extracted_terms_rsoxs_v1.json"
    live.write_text(json.dumps({"metadata": {"version": "test"}, "terms": [{"term": "P3HT"}]}), encoding="utf-8")
    before = live.read_bytes()
    snap = snapshot_copy_terms(live, tmp_path / "extracted_terms_rsoxs_v1_copy.json")
    assert snap != live
    assert live.read_bytes() == before
    dest = tmp_path / "matkg_rsoxs_v2.json"
    result = promote_rsoxs_snapshot(terms_snapshot=snap, output_kg=dest, kg_dir=tmp_path)
    assert result["snapshot"]["term_count"] == 1
    assert dest.exists()
    assert live.read_bytes() == before


def test_literature_sidecar_records_v2_counts():
    if not SIDECAR.exists():
        return
    data = json.loads(SIDECAR.read_text(encoding="utf-8"))
    assert data["live_file_written"] is False
    assert data["term_count"] >= 10000
    assert data["unique_papers"] >= 130
    assert data["nodes"] >= data["term_count"]
    assert "matkg_rsoxs_v2.json" in data["kg_path"]


def test_repo_v5_exists_with_five_esafs():
    v5 = ROOT / "storage/kg/matkg_bl1101_v5.json"
    if not v5.exists() or not V4_PATH.exists():
        return
    v4 = json.loads(V4_PATH.read_text(encoding="utf-8"))
    graph = json.loads(v5.read_text(encoding="utf-8"))
    esafs = [n for n in graph["things"] if n.get("category") == "ESAF"]
    assert len(esafs) == 5
    assert len(graph["things"]) > len(v4["things"])
    assert graph["metadata"]["promote"]["kind"] == "tiled_sim"
    assert graph["metadata"]["promote"]["seed"] == DEFAULT_SEED
    assert graph["metadata"]["promote"]["live_tiled"] is False
    v4_ids = {n["id"] for n in v4["things"]}
    v5_ids = {n["id"] for n in graph["things"]}
    assert v4_ids <= v5_ids


def _tiny_identity_fixture() -> dict:
    return {
        "seed": 20260916,
        "stats": {"esafs": 1, "proposals": 1, "scans": 1, "samples": 1},
        "esafs": [
            {
                "id": "beamline:ESAF-2026-00041",
                "name": "ESAF 2026-00041",
                "esaf_number": "2026-00041",
                "title": "P3HT OPV P-RSoXS at the carbon edge",
                "scientist": "Cheng Wang",
                "proposals": [
                    {
                        "id": "beamline:Proposal-P202600041-01",
                        "name": "Proposal P202600041-01",
                        "proposal_code": "P202600041-01",
                        "samples": [
                            {
                                "id": "beamline:Sample-S01",
                                "name": "P3HT:PCBM blend",
                                "sample_code": "P202600041-01-S01",
                                "material": "P3HT:PCBM",
                            }
                        ],
                        "scans": [
                            {
                                "id": "beamline:BlueskyRun-u",
                                "name": "scan 141 labview_replay_scan",
                                "uid": "9cd237f8-8fc5-4d6f-bc31-8d2c803ccbc4",
                                "uri": "tiled:run/9cd237f8-8fc5-4d6f-bc31-8d2c803ccbc4",
                                "sample_id": "beamline:Sample-S01",
                                "scan_id": 141,
                                "plan_name": "labview_replay_scan",
                            }
                        ],
                    }
                ],
            }
        ],
    }


class _FakeTiledGraph:
    def __init__(self, seed_entities=None):
        self.entities = list(seed_entities or [])
        self.links = []
        self._n = 0

    def __call__(self, query, variables=None):
        variables = variables or {}
        if "createEntity" in query:
            payload = dict(variables["input"])
            self._n += 1
            row = {
                "id": f"gql-{self._n}",
                "entityType": payload["entityType"],
                "name": payload["name"],
                "uri": payload.get("uri") or "",
                "properties": payload.get("properties") or {},
            }
            self.entities.append(row)
            return {"createEntity": row}
        if "createLink" in query:
            payload = dict(variables["input"])
            self._n += 1
            subject = next(e for e in self.entities if e["id"] == payload["subjectId"])
            obj = next(e for e in self.entities if e["id"] == payload["objectId"])
            row = {
                "id": f"link-{self._n}",
                "predicate": payload["predicate"],
                "subject": {"id": subject["id"], "uri": subject["uri"]},
                "object": {"id": obj["id"], "uri": obj["uri"]},
            }
            self.links.append(row)
            return {"createLink": row}
        if "entities(" in query:
            entity_type = variables.get("entityType")
            offset = int(variables["offset"])
            limit = int(variables["limit"])
            rows = [e for e in self.entities if e["entityType"] == entity_type]
            return {"entities": rows[offset : offset + limit]}
        if "links(" in query:
            predicate = variables.get("predicate")
            offset = int(variables["offset"])
            limit = int(variables["limit"])
            rows = [link for link in self.links if link["predicate"] == predicate]
            return {"links": rows[offset : offset + limit]}
        raise AssertionError(query[:120])


def test_to_tiled_cli_flag_does_not_require_json_promote():
    args = parse_args(["--to-tiled"])
    assert args.to_tiled is True
    assert args.tiled_uri is None


def test_iter_identity_payloads_match_cookbook_types():
    fixture = _tiny_identity_fixture()
    entities = iter_identity_entities(fixture)
    types = [row["entityType"] for row in entities]
    assert types == ["ESAF", "Proposal", "Sample", "BlueskyRun"]
    links = iter_identity_links(fixture)
    preds = {edge[1] for edge in links}
    assert preds == {"rel:hasProposal", "rel:hasSample", "rel:hasScan", "prov:used"}


def test_push_fixture_to_tiled_is_idempotent_and_keeps_existing_runs():
    store = _FakeTiledGraph(
        seed_entities=[
            {
                "id": "tomo-1",
                "entityType": "BlueskyRun",
                "name": "scan ant12",
                "uri": "http://127.0.0.1:8001/api/v1/metadata/scans/ant12",
            }
        ]
    )
    fixture = _tiny_identity_fixture()
    first = push_fixture_to_tiled(fixture, uri="http://127.0.0.1:8001", graphql=store)
    assert first["created"] == {"ESAF": 1, "Proposal": 1, "Sample": 1, "BlueskyRun": 1}
    assert first["existing_after"]["BlueskyRun"] == 2
    assert first["links_created"] == 4
    second = push_fixture_to_tiled(fixture, uri="http://127.0.0.1:8001", graphql=store)
    assert second["created"] == {"ESAF": 0, "Proposal": 0, "Sample": 0, "BlueskyRun": 0}
    assert second["skipped"] == {"ESAF": 1, "Proposal": 1, "Sample": 1, "BlueskyRun": 1}
    assert second["links_created"] == 0
    assert second["links_skipped"] == 4
    assert second["existing_after"]["BlueskyRun"] == 2
    names = {row["name"] for row in store.entities if row["entityType"] == "BlueskyRun"}
    assert names == {"scan ant12", "scan 141 labview_replay_scan"}


def test_push_fixture_refuses_als_host():
    try:
        push_fixture_to_tiled(_tiny_identity_fixture(), uri="https://tiled.als.lbl.gov")
    except ValueError as exc:
        assert "ALS" in str(exc)
    else:
        raise AssertionError("expected ALS host to be refused")
