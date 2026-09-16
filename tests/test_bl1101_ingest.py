"""Tests for BL 11.0.1.2 ops schema and replayable ingest."""

import hashlib
import json
import shutil
from pathlib import Path

from app.modules import json2kg
from app.modules.bl1101_ingest import (
    ALS_PAGE_URL,
    apply_motor_stage_map_to_graph,
    ingest_fixture,
    load_motor_stage_map,
    next_snapshot_version,
    parse_als_contact_people,
    parse_als_page,
    parse_blueprint_html,
    parse_blueprint_people,
    records_to_graph,
    resolve_beam_path_order,
    run_motor_link_promote,
)
from app.modules.term_extractor.schema import SchemaHelper

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_HTML = """
<html>
<header class="titleblock">
  <div><div class="k">Drawing</div><div class="v">BL 11.0.1.2 RSOXS · Reflectometer endstation</div></div>
  <div><div class="k">Date</div><div class="v">2026-09-09</div></div>
  <div class="sources"><span><b>Beam</b> ~200–1500 eV</span></div>
</header>
<div class="stages">
  <div class="stage">
    <div class="stage-name">Sample stage<span class="stage-id">§B.2</span></div>
    <div class="stage-what">Four-axis manipulator.</div>
    <div class="stage-does">X brings the bar into the beam.</div>
    <div class="stage-range">
      <a href="#dev-sample_x">Sample X</a>
    </div>
  </div>
</div>
<table id="corr">
  <tr id="dev-sample_x">
    <td class="drawn">B</td>
    <td class="name"><span class="hot" data-tip="&lt;span class=&quot;tip-k&quot;&gt;motor&lt;/span&gt;&lt;span class=&quot;tip-m&quot;&gt;Brings the sample bar into the beam.&lt;/span&gt;">Sample X</span></td>
    <td class="cls">SimMotorRecord</td>
    <td class="pvcell"><span class="pv">SIM11012:sample_x</span></td>
    <td class="pvcell"><span class="hd">d.motors[&quot;sample_x&quot;]</span></td>
    <td class="cls">EpicsSimMotor</td>
    <td class="src">motors/sample_x.md</td>
  </tr>
  <tr id="dev-sim_det">
    <td class="drawn">C</td>
    <td class="name"><span class="hot" data-tip="&lt;span class=&quot;tip-k&quot;&gt;detector&lt;/span&gt;&lt;span class=&quot;tip-m&quot;&gt;AXIS-SXR-40 area detector.&lt;/span&gt;">sim_det (AXIS-SXR-40)</span></td>
    <td class="cls">SimDetectorRecord</td>
    <td class="pvcell"><span class="pv">SIM11012:sim_det</span></td>
    <td class="pvcell"><span class="hd">d.detectors[&quot;sim_det&quot;]</span></td>
    <td class="cls">EpicsSimAxisSXR40</td>
    <td class="src">detectors/sim_det.md</td>
  </tr>
</table>
</html>
"""

FIXTURE_PY = '''
from ophyd import Device, Component as Cpt, EpicsMotor

class SampleStage(Device):
    """Four-axis sample manipulator."""

    x = Cpt(EpicsMotor, "sample_x")
    y = Cpt(EpicsMotor, "sample_y")


def scan(detectors, motor, start=0.0, stop=1.0, num=11, *, md=None):
    yield from _scan(detectors, motor, start, stop, num, md=md)
'''


def test_bl1101_schema_loads():
    helper = SchemaHelper(str(ROOT / "storage/schema/bl1101_schema.yaml"))
    for cls in (
        "Beamline",
        "Detector",
        "Motor",
        "ProcessVariable",
        "OphydDevice",
        "BlueskyPlan",
        "Person",
        "Role",
        "BeamlineScientist",
    ):
        assert cls in helper.classes
    for slot in (
        "hasDetector",
        "hasMotor",
        "hasPV",
        "runsSoftware",
        "supportsGeometry",
        "implementsPlan",
        "configured_by",
        "feeds",
        "upstream_of",
        "beam_path_next",
        "connected_to",
        "hasRole",
        "supports",
    ):
        assert slot in helper.slots


def test_next_snapshot_version_appends(tmp_path: Path):
    assert next_snapshot_version(tmp_path) == 1
    (tmp_path / "matkg_bl1101_v1.json").write_text("{}")
    assert next_snapshot_version(tmp_path) == 2
    (tmp_path / "matkg_bl1101_v2.json").write_text("{}")
    (tmp_path / "matkg_bl1101_v2.bak.json").write_text("{}")
    assert next_snapshot_version(tmp_path) == 3


def test_parse_blueprint_html_devices_and_stages():
    parsed = parse_blueprint_html(FIXTURE_HTML)
    assert "sample_x" in parsed["devices"]
    sample = parsed["devices"]["sample_x"]
    assert sample.pv == "SIM11012:sample_x"
    assert sample.ophyd_name == "sample_x"
    assert sample.category == "Motor"
    assert parsed["devices"]["sim_det"].category == "Detector"
    assert any(stage.name == "Sample stage" for stage in parsed["stages"])


def test_fixture_ingest_produces_beamline_nodes_and_edges(tmp_path: Path):
    py_path = tmp_path / "devices.py"
    py_path.write_text(FIXTURE_PY)
    records = ingest_fixture(FIXTURE_HTML, [py_path])
    graph = records_to_graph(records, {"schema_version": "bl1101_v1", "id_prefix": "beamline"})

    nodes = {node["id"]: node for node in graph["things"]}
    assert any(node_id.startswith("beamline:") for node_id in nodes)
    assert "beamline:BL-11-0-1-2" in nodes
    assert nodes["beamline:BL-11-0-1-2"]["category"] == "Beamline"
    assert any(node["category"] == "Motor" for node in nodes.values())
    assert any(node["category"] == "Detector" for node in nodes.values())
    assert any(node["category"] == "ProcessVariable" for node in nodes.values())
    assert any(node["category"] == "CodeSnippet" for node in nodes.values())
    assert any(node["category"] == "OphydDevice" for node in nodes.values())
    predicates = {edge["predicate"] for edge in graph["associations"]}
    assert "rel:hasMotor" in predicates
    assert "rel:hasDetector" in predicates
    assert "rel:hasPV" in predicates
    assert "rel:part_of" in predicates
    assert all(edge["subject"].startswith("beamline:") for edge in graph["associations"])
    assert all(not edge["subject"].startswith("matkg:") for edge in graph["associations"])


def test_json2kg_upgrades_unknown_stubs_when_term_arrives_later():
    graph = json2kg.build_graph(
        [
            {
                "id": "beamline:BL-11-0-1-2",
                "term": "ALS Beamline 11.0.1.2",
                "category": "Beamline",
                "relations": [{"relation": "hasMotor", "related_id": "beamline:Motor-sample-x"}],
            },
            {
                "id": "beamline:Motor-sample-x",
                "term": "Sample X",
                "category": "Motor",
                "definition": "Sample translation.",
            },
        ],
        default_id_prefix="beamline",
    )
    node = {n["id"]: n for n in graph["things"]}["beamline:Motor-sample-x"]
    assert node["category"] == "Motor"
    assert node["description"] == "Sample translation."


def test_json2kg_preserves_beamline_curie_and_related_id():
    graph = json2kg.build_graph(
        [
            {
                "id": "beamline:BL-11-0-1-2",
                "term": "ALS Beamline 11.0.1.2",
                "category": "Beamline",
                "id_prefix": "beamline",
                "relations": [
                    {
                        "relation": "hasDetector",
                        "related_term": "AXIS-SXR-40",
                        "related_id": "beamline:Detector-AXIS-SXR-40",
                    }
                ],
            }
        ],
        default_id_prefix="beamline",
    )
    ids = {node["id"] for node in graph["things"]}
    assert "beamline:BL-11-0-1-2" in ids
    assert "beamline:Detector-AXIS-SXR-40" in ids
    assert graph["associations"][0]["predicate"] == "rel:hasDetector"


def test_json2kg_curated_short_snippet_survives():
    graph = json2kg.build_graph(
        [
            {
                "id": "beamline:ophyd-SampleStage",
                "term": "SampleStage",
                "category": "OphydDevice",
                "source_papers": ["fixture:devices.py:SampleStage"],
            }
        ],
        code_snippets=[
            {
                "id": "beamline:snippet-SampleStage",
                "curated": True,
                "function_name": "SampleStage",
                "code_snippet": "class SampleStage(Device):\n    x = Cpt(EpicsMotor, 'sample_x')\n",
                "source_paper": "fixture:devices.py:SampleStage",
                "source_file_path": "devices.py",
                "source_start_line": 4,
                "source_end_line": 8,
            }
        ],
        default_id_prefix="beamline",
    )
    snippets = [node for node in graph["things"] if node["category"] == "CodeSnippet"]
    assert len(snippets) == 1
    assert snippets[0]["id"].startswith("beamline:")
    assert snippets[0]["source_start_line"] == 4


# A′ DOM lists exit slits (§A.5) before M103 (§A.6). Optical order is the reverse.
TOPOLOGY_HTML = """
<html>
<section id="sheet-a">
  <figure>
    <svg viewBox="0 0 1000 800" xmlns="http://www.w3.org/2000/svg">
      <text x="110" y="192" class="big">Storage ring</text>
      <text x="290" y="192" class="big">EPU</text>
      <text x="470" y="192" class="big">M101</text>
      <text x="650" y="192" class="big">Mono 101</text>
      <text x="830" y="192" class="big">M103</text>
      <text x="110" y="512" class="big">Exit slits</text>
      <text x="290" y="512" class="big">PZT shutter</text>
    </svg>
    <figcaption>
      Element order: mono → M103 → exit slits → PZT shutter → chamber.
    </figcaption>
  </figure>
</section>
<div class="stages">
  <div class="stage">
    <div class="stage-name">Exit slits<span class="stage-id">§A.5</span></div>
    <div class="stage-what">Downstream of M103.</div>
  </div>
  <div class="stage">
    <div class="stage-name">M103<span class="stage-id">§A.6</span></div>
    <div class="stage-what">KB pair between the mono and the exit slits.</div>
  </div>
  <div class="stage">
    <div class="stage-name">Sample stage<span class="stage-id">§B.2</span></div>
    <div class="stage-what">Four-axis manipulator.</div>
  </div>
  <div class="stage">
    <div class="stage-name">Detector<span class="stage-id">§B.3</span></div>
    <div class="stage-what">AXIS-SXR-40.</div>
  </div>
</div>
</html>
"""


def test_a_prime_dom_order_is_not_the_beam_path():
    parsed = parse_blueprint_html(TOPOLOGY_HTML)
    stage_names = [stage.name for stage in parsed["stages"]]
    assert stage_names.index("Exit slits") < stage_names.index("M103")
    path = [name.casefold() for name in parsed["beam_path"]]
    assert path.index("m103") < path.index("exit slits")
    assert "storage ring" in path
    assert "sample stage" in path
    assert "detector" in path


def test_resolve_beam_path_ignores_a_prime_dom():
    path = resolve_beam_path_order(
        svg_names=[],
        caption_names=[],
        stage_names=["Exit slits", "M103", "Sample stage"],
    )
    assert path == []


def test_ingest_fixture_m103_is_upstream_of_exit_slits(tmp_path: Path):
    records = ingest_fixture(TOPOLOGY_HTML, [])
    graph = records_to_graph(records, {"schema_version": "bl1101_v2", "id_prefix": "beamline"})
    nodes = {node["id"]: node for node in graph["things"]}
    m103_id = next(nid for nid, node in nodes.items() if node.get("name") == "M103")
    exit_id = next(nid for nid, node in nodes.items() if node.get("name") == "Exit slits")
    assert int(nodes[m103_id].get("beam_path_index") or 0) < int(nodes[exit_id].get("beam_path_index") or 99)
    upstream = {
        (edge["subject"], edge["object"])
        for edge in graph["associations"]
        if edge["predicate"] in {
            "rel:upstream_of",
            "rel:beam_path_next",
            "rel:feeds",
            "rel:connected_to",
        }
    }
    assert (m103_id, exit_id) in upstream
    assert (exit_id, m103_id) not in upstream


def test_real_blueprint_svg_puts_m103_before_exit_slits():
    path = ROOT / "papers/rsoxs/beamline_blueprint/blueprint_bl11012_version09092026.html"
    if not path.exists():
        return
    parsed = parse_blueprint_html(path.read_text(encoding="utf-8"))
    names = [name.casefold() for name in parsed["beam_path"]]
    assert names.index("m103") < names.index("exit slits")
    assert names[0] == "storage ring"
    assert names[-1] == "detector"
    assert "epu" in names
    people = [person["name"] for person in parsed["people"]]
    assert "Thomas Ferron" in people


def test_parse_als_contacts_does_not_invent_names():
    people = parse_als_contact_people(
        {"Primary contact(s)": "Cheng Wang cwang2@lbl.gov | Thomas Ferron TJFerron@lbl.gov"},
        source=ALS_PAGE_URL,
    )
    names = [person["name"] for person in people]
    assert names == ["Cheng Wang", "Thomas Ferron"]
    assert people[0]["email"] == "cwang2@lbl.gov"
    assert people[1]["source"] == ALS_PAGE_URL
    assert parse_als_contact_people({}, "") == []


def test_parse_blueprint_people_reads_reviewed_with():
    people = parse_blueprint_people(
        {"header": {"sources": "D 2026-09-09 reviewed with Thomas Ferron · E interactive"}},
    )
    assert [person["name"] for person in people] == ["Thomas Ferron"]
    noise = parse_blueprint_people(
        {},
        "A″ operating rules: what the scientist does every time, review with Thomas, 2026-09-09.",
    )
    assert noise == []


def test_ingest_kg_contains_beamline_scientists():
    als_html = """
    <html><h1>Beamline 11.0.1.2</h1>
    <table>
      <tr><th>Minimum energy (eV)</th><td>165</td></tr>
      <tr><th>Primary contact(s)</th><td>Cheng Wang; Thomas Ferron</td></tr>
    </table>
    </html>
    """
    parsed = parse_als_page(als_html)
    assert {p["name"] for p in parsed["people"]} == {"Cheng Wang", "Thomas Ferron"}
    records = ingest_fixture(als_html + TOPOLOGY_HTML, [])
    graph = records_to_graph(records, {"schema_version": "bl1101_v2", "id_prefix": "beamline"})
    people = [node for node in graph["things"] if node.get("category") == "Person"]
    names = {node["name"] for node in people}
    assert "Cheng Wang" in names
    assert "Thomas Ferron" in names
    assert all(node["id"].startswith("beamline:") for node in people)
    role_id = next(
        node["id"] for node in graph["things"] if node.get("category") == "BeamlineScientist"
    )
    person_ids = {node["id"] for node in people}
    rels = {(e["subject"], e["predicate"], e["object"]) for e in graph["associations"]}
    for pid in person_ids:
        assert (pid, "rel:hasRole", role_id) in rels
        assert (pid, "rel:supports", "beamline:BL-11-0-1-2") in rels


V3_PATH = ROOT / "storage/kg/matkg_bl1101_v3.json"
V4_PATH = ROOT / "storage/kg/matkg_bl1101_v4.json"
V3_FILE_SHA256 = "91792ff3dce2deba1723240866872045c0609c6c1b572fe368719ca13ca2201c"

MOTOR_LINK_HTML = """
<html>
<div class="stages">
  <div class="stage">
    <div class="stage-name">Upstream slits (JJ)<span class="stage-id">§A.13</span></div>
    <div class="stage-what">First JJ set after the gold mesh.</div>
  </div>
  <div class="stage">
    <div class="stage-name">Sample stage<span class="stage-id">§B.2</span></div>
    <div class="stage-what">Four-axis manipulator.</div>
    <div class="stage-range"><a href="#dev-sample_z">Sample Z</a></div>
  </div>
  <div class="stage">
    <div class="stage-name">Beam stop<span class="stage-id">§B.4</span></div>
    <div class="stage-what">Hides the direct beam.</div>
    <div class="stage-range"><a href="#dev-beam_stop_x">Beam Stop X</a></div>
  </div>
  <div class="stage">
    <div class="stage-name">Exit slits<span class="stage-id">§A.5</span></div>
    <div class="stage-what">Downstream of M103.</div>
  </div>
</div>
<table id="corr">
  <tr id="dev-upstream_jj_vert_aperture">
    <td class="drawn">A</td>
    <td class="name"><span class="hot" data-tip="&lt;span class=&quot;tip-k&quot;&gt;motor&lt;/span&gt;&lt;span class=&quot;tip-m&quot;&gt;Vertical opening.&lt;/span&gt;">Upstream JJ Vert Aperture</span></td>
    <td class="cls">SimMotorRecord</td>
    <td class="pvcell"><span class="pv">SIM11012:upstream_jj_vert_aperture</span></td>
    <td class="pvcell"><span class="hd">d.motors[&quot;upstream_jj_vert_aperture&quot;]</span></td>
    <td class="cls">EpicsSimMotor</td>
    <td class="src">motors/upstream_jj.md</td>
  </tr>
  <tr id="dev-exit_slit_top">
    <td class="drawn">A</td>
    <td class="name"><span class="hot" data-tip="&lt;span class=&quot;tip-k&quot;&gt;motor&lt;/span&gt;&lt;span class=&quot;tip-m&quot;&gt;Top jaw.&lt;/span&gt;">Exit Slit Top</span></td>
    <td class="cls">SimMotorRecord</td>
    <td class="pvcell"><span class="pv">SIM11012:exit_slit_top</span></td>
    <td class="pvcell"><span class="hd">d.motors[&quot;exit_slit_top&quot;]</span></td>
    <td class="cls">EpicsSimMotor</td>
    <td class="src">motors/exit_slit_top.md</td>
  </tr>
  <tr id="dev-samplerot0">
    <td class="drawn">B</td>
    <td class="name"><span class="hot" data-tip="&lt;span class=&quot;tip-k&quot;&gt;motor&lt;/span&gt;&lt;span class=&quot;tip-m&quot;&gt;Sample rotation axis 0.&lt;/span&gt;">SampleRot0</span></td>
    <td class="cls">SimMotorRecord</td>
    <td class="pvcell"><span class="pv">SIM11012:samplerot0</span></td>
    <td class="pvcell"><span class="hd">d.motors[&quot;samplerot0&quot;]</span></td>
    <td class="cls">EpicsSimMotor</td>
    <td class="src">motors/samplerot0.md</td>
  </tr>
  <tr id="dev-sample_z">
    <td class="drawn">B</td>
    <td class="name"><span class="hot" data-tip="&lt;span class=&quot;tip-k&quot;&gt;motor&lt;/span&gt;&lt;span class=&quot;tip-m&quot;&gt;Sample Z.&lt;/span&gt;">Sample Z</span></td>
    <td class="cls">SimMotorRecord</td>
    <td class="pvcell"><span class="pv">SIM11012:sample_z</span></td>
    <td class="pvcell"><span class="hd">d.motors[&quot;sample_z&quot;]</span></td>
    <td class="cls">EpicsSimMotor</td>
    <td class="src">motors/sample_z.md</td>
  </tr>
  <tr id="dev-sample_z_galil_c">
    <td class="drawn">B</td>
    <td class="name"><span class="hot" data-tip="&lt;span class=&quot;tip-k&quot;&gt;motor&lt;/span&gt;&lt;span class=&quot;tip-m&quot;&gt;GALIL alias.&lt;/span&gt;">Sample Z GALIL C</span></td>
    <td class="cls">SimMotorRecord</td>
    <td class="pvcell"><span class="pv">SIM11012:sample_z_galil_c</span></td>
    <td class="pvcell"><span class="hd">d.motors[&quot;sample_z_galil_c&quot;]</span></td>
    <td class="cls">EpicsSimMotor</td>
    <td class="src">motors/sample_z_galil_c.md</td>
  </tr>
  <tr id="dev-beam_stop_x">
    <td class="drawn">B</td>
    <td class="name"><span class="hot" data-tip="&lt;span class=&quot;tip-k&quot;&gt;motor&lt;/span&gt;&lt;span class=&quot;tip-m&quot;&gt;Beam stop X.&lt;/span&gt;">Beam Stop X</span></td>
    <td class="cls">SimMotorRecord</td>
    <td class="pvcell"><span class="pv">SIM11012:beam_stop_x</span></td>
    <td class="pvcell"><span class="hd">d.motors[&quot;beam_stop_x&quot;]</span></td>
    <td class="cls">EpicsSimMotor</td>
    <td class="src">motors/beam_stop_x.md</td>
  </tr>
  <tr id="dev-fake_motor">
    <td class="drawn">—</td>
    <td class="name"><span class="hot" data-tip="&lt;span class=&quot;tip-k&quot;&gt;motor&lt;/span&gt;&lt;span class=&quot;tip-m&quot;&gt;Placeholder.&lt;/span&gt;">Fake Motor</span></td>
    <td class="cls">SimMotorRecord</td>
    <td class="pvcell"><span class="pv">SIM11012:fake_motor</span></td>
    <td class="pvcell"><span class="hd">d.motors[&quot;fake_motor&quot;]</span></td>
    <td class="cls">EpicsSimMotor</td>
    <td class="src">motors/fake_motor.md</td>
  </tr>
</table>
</html>
"""


def _stage_id(nodes, name: str) -> str:
    return next(nid for nid, node in nodes.items() if node.get("name") == name)


def _ophyd_id(nodes, ophyd: str) -> str:
    return next(nid for nid, node in nodes.items() if node.get("ophyd_name") == ophyd)


def _motor_stage_links(graph):
    nodes = {node["id"]: node for node in graph["things"]}
    stage_ids = {nid for nid, node in nodes.items() if node.get("category") == "BeamlineStage"}
    part_of = {}
    for edge in graph["associations"]:
        if edge["predicate"] != "rel:part_of":
            continue
        part_of.setdefault(edge["subject"], []).append(edge["object"])
    motors = [node for node in graph["things"] if node.get("category") == "Motor"]
    linked = []
    hanging = []
    for motor in motors:
        stages = [obj for obj in part_of.get(motor["id"], []) if obj in stage_ids]
        (linked if stages else hanging).append(motor)
    return nodes, linked, hanging, part_of, stage_ids


def test_motor_stage_map_targets_existing_v3_stages():
    mapping = load_motor_stage_map()
    assert mapping["motors"]
    assert mapping["aliases"]["sample_z_galil_c"] == "sample_z"
    if not V3_PATH.exists():
        return
    graph = json.loads(V3_PATH.read_text(encoding="utf-8"))
    stage_names = {
        node["name"]
        for node in graph["things"]
        if node.get("category") == "BeamlineStage"
    }
    for ophyd, stage in mapping["motors"].items():
        assert stage in stage_names, f"{ophyd} → missing stage {stage!r}"
    ophyds = {
        node.get("ophyd_name")
        for node in graph["things"]
        if node.get("category") == "Motor"
    }
    for ophyd in mapping["motors"]:
        assert ophyd in ophyds
    for alias, canonical in mapping["aliases"].items():
        assert alias in ophyds
        assert canonical in ophyds


def test_ingest_applies_map_to_hanging_jj_sample_galil_and_jaws():
    records = ingest_fixture(MOTOR_LINK_HTML, [])
    graph = records_to_graph(records, {"schema_version": "bl1101_v2", "id_prefix": "beamline"})
    nodes = {node["id"]: node for node in graph["things"]}
    rels = {(e["subject"], e["predicate"], e["object"]) for e in graph["associations"]}
    jj = _ophyd_id(nodes, "upstream_jj_vert_aperture")
    jaws = _ophyd_id(nodes, "exit_slit_top")
    rot = _ophyd_id(nodes, "samplerot0")
    galil = _ophyd_id(nodes, "sample_z_galil_c")
    canon = _ophyd_id(nodes, "sample_z")
    fake = _ophyd_id(nodes, "fake_motor")
    assert (jj, "rel:part_of", _stage_id(nodes, "Upstream slits (JJ)")) in rels
    assert (jaws, "rel:part_of", _stage_id(nodes, "Exit slits")) in rels
    assert (rot, "rel:part_of", _stage_id(nodes, "Sample stage")) in rels
    assert (galil, "rel:part_of", _stage_id(nodes, "Sample stage")) in rels
    assert (galil, "rel:related_to", canon) in rels
    assert not any(
        e["subject"] == fake and e["predicate"] == "rel:part_of" and nodes.get(e["object"], {}).get("category") == "BeamlineStage"
        for e in graph["associations"]
    )


def test_apply_motor_stage_map_is_idempotent_and_keeps_beam_path():
    mapping = {
        "motors": {"upstream_jj_vert_aperture": "Upstream slits (JJ)"},
        "aliases": {"sample_z_galil_c": "sample_z"},
        "leave_on_beamline": {},
    }
    graph = {
        "things": [
            {"id": "beamline:Motor-jj", "name": "JJ", "category": "Motor", "ophyd_name": "upstream_jj_vert_aperture"},
            {"id": "beamline:Motor-z", "name": "Z", "category": "Motor", "ophyd_name": "sample_z"},
            {"id": "beamline:Motor-z-galil", "name": "Z GALIL", "category": "Motor", "ophyd_name": "sample_z_galil_c"},
            {"id": "beamline:stage-Upstream-slits-JJ", "name": "Upstream slits (JJ)", "category": "BeamlineStage"},
            {"id": "beamline:stage-Sample-stage", "name": "Sample stage", "category": "BeamlineStage"},
            {"id": "beamline:stage-M103", "name": "M103", "category": "BeamlineStage"},
            {"id": "beamline:stage-Exit-slits", "name": "Exit slits", "category": "BeamlineStage"},
        ],
        "associations": [
            {
                "subject": "beamline:stage-M103",
                "predicate": "rel:beam_path_next",
                "object": "beamline:stage-Exit-slits",
                "has_evidence": "v3",
            },
            {
                "subject": "beamline:Motor-z",
                "predicate": "rel:part_of",
                "object": "beamline:stage-Sample-stage",
                "has_evidence": "v3",
            },
        ],
    }
    once, stats = apply_motor_stage_map_to_graph(graph, mapping)
    twice, stats2 = apply_motor_stage_map_to_graph(once, mapping)
    assert stats["linked"] >= 2
    assert stats2["linked"] == 0
    bpn = [
        (e["subject"], e["object"])
        for e in twice["associations"]
        if e["predicate"] == "rel:beam_path_next"
    ]
    assert bpn == [("beamline:stage-M103", "beamline:stage-Exit-slits")]


def test_promote_writes_v4_without_touching_source(tmp_path: Path):
    if not V3_PATH.exists():
        return
    src = tmp_path / "matkg_bl1101_v3.json"
    shutil.copyfile(V3_PATH, src)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    result = run_motor_link_promote(source_kg=src, version=4, kg_dir=tmp_path, terms_dir=tmp_path)
    assert result["kg_path"].name == "matkg_bl1101_v4.json"
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    graph = json.loads(result["kg_path"].read_text(encoding="utf-8"))
    nodes, linked, hanging, part_of, stage_ids = _motor_stage_links(graph)
    assert len([n for n in graph["things"] if n.get("category") == "Motor"]) == 90
    assert len(linked) >= 80
    assert len(hanging) <= 10
    v3 = json.loads(V3_PATH.read_text(encoding="utf-8"))
    v3_bpn = {
        (e["subject"], e["object"])
        for e in v3["associations"]
        if e["predicate"] == "rel:beam_path_next"
    }
    v4_bpn = {
        (e["subject"], e["object"])
        for e in graph["associations"]
        if e["predicate"] == "rel:beam_path_next"
    }
    assert v4_bpn == v3_bpn
    assert len(v4_bpn) == 15
    sample_stage = _stage_id(nodes, "Sample stage")
    exit_stage = _stage_id(nodes, "Exit slits")
    up_jj = _stage_id(nodes, "Upstream slits (JJ)")
    mid_jj = _stage_id(nodes, "Middle slits (JJ)")
    scatter = _stage_id(nodes, "Scatter slits (JJ)")
    beam_stop = _stage_id(nodes, "Beam stop")
    detector = _stage_id(nodes, "Detector")
    by_ophyd = {n.get("ophyd_name"): n for n in graph["things"] if n.get("category") == "Motor"}
    for ophyd, stage in (
        ("upstream_jj_vert_aperture", up_jj),
        ("middle_jj_horz_trans", mid_jj),
        ("in_chamber_jj_vert_aperture", scatter),
        ("exit_slit_top", exit_stage),
        ("exit_slit_left", exit_stage),
        ("samplerot0", sample_stage),
        ("sample_azimuthal_rotation", sample_stage),
        ("sample_z_galil_c", sample_stage),
        ("beam_stop_x_galil_f", beam_stop),
        ("ccd_theta_galil_e", detector),
    ):
        assert stage in part_of[by_ophyd[ophyd]["id"]], ophyd
    fake_id = by_ophyd["fake_motor"]["id"]
    assert not any(obj in stage_ids for obj in part_of.get(fake_id, []))


def test_v3_snapshot_file_unchanged():
    if not V3_PATH.exists():
        return
    digest = hashlib.sha256(V3_PATH.read_bytes()).hexdigest()
    assert digest == V3_FILE_SHA256


def test_v4_snapshot_preserves_v3_beam_path_and_links_motors():
    if not (V3_PATH.exists() and V4_PATH.exists()):
        return
    v3 = json.loads(V3_PATH.read_text(encoding="utf-8"))
    v4 = json.loads(V4_PATH.read_text(encoding="utf-8"))
    assert v3["metadata"]["graph_snapshot"]["version"] == 3
    assert v4["metadata"]["graph_snapshot"]["version"] == 4
    assert len(v3["things"]) == len(v4["things"])
    motors_v3 = [n for n in v3["things"] if n.get("category") == "Motor"]
    motors_v4 = [n for n in v4["things"] if n.get("category") == "Motor"]
    assert len(motors_v3) == len(motors_v4) == 90
    v3_bpn = {
        (e["subject"], e["object"])
        for e in v3["associations"]
        if e["predicate"] == "rel:beam_path_next"
    }
    v4_bpn = {
        (e["subject"], e["object"])
        for e in v4["associations"]
        if e["predicate"] == "rel:beam_path_next"
    }
    assert v4_bpn == v3_bpn
    _, linked, hanging, part_of, _ = _motor_stage_links(v4)
    assert len(linked) >= 80
    assert len(hanging) <= 10
    nodes = {n["id"]: n for n in v4["things"]}
    by_ophyd = {n.get("ophyd_name"): n for n in motors_v4}
    sample = _stage_id(nodes, "Sample stage")
    assert sample in part_of[by_ophyd["samplerot2"]["id"]]
    assert sample in part_of[by_ophyd["sample_z_galil_c"]["id"]]
    related = {
        (e["subject"], e["object"])
        for e in v4["associations"]
        if e["predicate"] == "rel:related_to"
    }
    assert (by_ophyd["sample_z_galil_c"]["id"], by_ophyd["sample_z"]["id"]) in related
