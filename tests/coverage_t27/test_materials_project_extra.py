"""Materials Project lookup: query extraction, search seam, record normalisation (no live MP calls)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.modules.f2w_agent import materials_project as mp


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("", []), (None, []),
        ("what is the band gap of MoS2?", ["MoS2"]),
        ("show MP-149 and mp-149 again", ["mp-149"]),
        ("compare TiO2 with SiC density", ["TiO2", "SiC"]),
        ("tell me about NaCl", ["NaCl"]),           # two elements: formula-like without a property cue
        ("What does ALS do with ESAF?", []),
        ("what kinds of materials are studied", []),
    ],
)
def test_extract_mp_queries(question, expected):
    got = mp.extract_mp_queries(question)
    assert got == expected


def test_should_query_matches_extraction():
    assert mp.should_query_materials_project("bulk modulus of TiO2") and not mp.should_query_materials_project("hello")


def test_element_counting_and_formula_screening():
    assert mp._element_count("CH3NH3PbI3") == 6  # symbols are counted per occurrence (H twice) and mp._element_count("lowercase") == 0
    assert mp._looks_like_formula("TiO2") and mp._looks_like_formula("NaCl") and not mp._looks_like_formula("Na")
    assert not mp._looks_like_formula("RSoXS") and not mp._looks_like_formula("abc")
    assert mp._parse_ok("TiO2") and not mp._parse_ok("Zz9Qq")


def test_cite_tokens_and_context_rendering():
    assert mp.cite_token({}) == "" and mp.cite_token({"material_id": "mp-1"}) == "[MP: mp-1]"
    rec = {"material_id": "mp-2", "formula_pretty": "TiO2", "band_gap": 3.1, "is_metal": False, "is_stable": True,
           "energy_above_hull": 0.0, "formation_energy_per_atom": -3.4, "density": 4.2, "spacegroup": "P42/mnm"}
    ctx = mp.format_mp_context([rec, {"material_id": "", "formula_pretty": "X"}])
    assert ctx.startswith("### Materials Project") and "- [MP: mp-2 TiO2]" in ctx
    for bit in ("band_gap_eV=3.1", "is_metal=False", "is_stable=True", "energy_above_hull_eV=0.0",
                "formation_energy_per_atom_eV=-3.4", "density_g_cm3=4.2", "spacegroup=P42/mnm",
                "url=https://next-gen.materialsproject.org/materials/mp-2"):
        assert bit in ctx
    assert mp.format_mp_context([]) == ""


def test_normalize_doc_accepts_dicts_pydantic_objects_and_attribute_bags():
    from_dict = mp._normalize_doc({"material_id": 5, "symmetry": {"symbol": "Fm-3m"}, "band_gap": 1.0})
    assert from_dict["material_id"] == "5" and from_dict["spacegroup"] == "Fm-3m" and from_dict["band_gap"] == 1.0
    crystal = mp._normalize_doc({"symmetry": {"crystal_system": "cubic"}})
    assert crystal["spacegroup"] == "cubic" and crystal["formula_pretty"] == ""

    class Model:
        def model_dump(self):
            return {"material_id": "mp-3", "symmetry": SimpleNamespace(symbol="P1"), "density": 2.0}

    dumped = mp._normalize_doc(Model())
    assert dumped["material_id"] == "mp-3" and dumped["spacegroup"] == "P1" and dumped["density"] == 2.0
    bag = mp._normalize_doc(SimpleNamespace(material_id="mp-4", formula_pretty="SiC", band_gap=2.3))
    assert bag["material_id"] == "mp-4" and bag["band_gap"] == 2.3 and bag["spacegroup"] == ""
    assert mp._normalize_doc(object())["material_id"] == ""


def test_search_summary_builds_mpid_or_formula_queries(monkeypatch):
    seen = {}

    class Summary:
        def search(self, **kw):
            seen.update(kw)
            return [{"material_id": "mp-1", "formula_pretty": "TiO2"}, {"material_id": "mp-2"}, {"material_id": "mp-3"}]

    class MPRester:
        def __init__(self, key):
            seen["key"] = key
            self.materials = SimpleNamespace(summary=Summary())

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(sys.modules, "mp_api.client", SimpleNamespace(MPRester=MPRester))
    monkeypatch.setitem(sys.modules, "mp_api", SimpleNamespace(client=sys.modules["mp_api.client"]))
    out = mp.search_mp_summary("TiO2", api_key="K", limit=2)
    assert [r["material_id"] for r in out] == ["mp-1", "mp-2"] and seen["formula"] == "TiO2" and seen["key"] == "K"
    assert seen["chunk_size"] == 2 and "material_ids" not in seen
    seen.clear()
    mp.search_mp_summary("MP-149", api_key="K", limit=0)
    assert seen["material_ids"] == ["mp-149"] and seen["chunk_size"] == 1


def test_lookup_dedupes_records_limits_and_reports_failure_modes(monkeypatch):
    calls = []

    def fake_search(query, *, api_key, limit):
        calls.append(query)
        if query == "SiC":
            raise RuntimeError("mp down")
        return [{"material_id": "mp-1", "formula_pretty": "TiO2"}, {"material_id": ""}, {"material_id": "mp-2"}]

    monkeypatch.setattr(mp, "search_mp_summary", fake_search)
    out = mp.lookup_materials_project("band gap of SiC and TiO2 and NaCl", api_key="K", limit=2)
    assert [r["material_id"] for r in out["records"]] == ["mp-1", "mp-2"] and out["skipped"] == "search_error"
    assert "[MP: mp-1 TiO2]" in out["context"] and out["triggered"] is True
    only_fail = mp.lookup_materials_project("band gap of SiC", api_key="K")
    assert only_fail["records"] == [] and only_fail["skipped"] == "search_error"
    monkeypatch.setattr(mp, "search_mp_summary", lambda *a, **k: [])
    assert mp.lookup_materials_project("band gap of TiO2", api_key="K")["skipped"] == "no_hits"
    assert mp.lookup_materials_project("nothing relevant here") == {"triggered": False, "queries": [], "records": [],
                                                                    "context": "", "skipped": None}


def test_lookup_without_key_never_calls_the_service_and_context_wrapper(monkeypatch):
    monkeypatch.delenv("MP_API_KEY", raising=False)
    monkeypatch.setattr(mp, "search_mp_summary", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert mp.lookup_materials_project("band gap of TiO2")["skipped"] == "no_api_key"
    assert mp.mp_context_for_question("band gap of TiO2") == ""
    monkeypatch.setenv("MP_API_KEY", " envkey ")
    monkeypatch.setattr(mp, "search_mp_summary", lambda q, *, api_key, limit: [{"material_id": "mp-9", "api": api_key}])
    assert "[MP: mp-9]" in mp.mp_context_for_question("band gap of TiO2")
