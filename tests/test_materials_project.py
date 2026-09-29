from app.modules.f2w_agent.materials_project import (
    extract_mp_queries,
    format_mp_context,
    lookup_materials_project,
    should_query_materials_project,
)


def test_extracts_formula_and_mp_id():
    assert extract_mp_queries("What is the band gap of TiO2?") == ["TiO2"]
    assert extract_mp_queries("Look up mp-149 on Materials Project") == ["mp-149"]
    assert "MoS2" in extract_mp_queries("crystal structure of MoS2")


def test_skips_general_rsoxs_materials_question():
    assert extract_mp_queries("what kinds of materials can I study with RSoXS") == []
    assert should_query_materials_project("tell me about the beamline energy range") is False


def test_skips_stop_tokens():
    assert extract_mp_queries("What is RSoXS?") == []
    assert extract_mp_queries("In the ALS ESAF catalog") == []


def test_lookup_skips_without_key(monkeypatch):
    monkeypatch.delenv("MP_API_KEY", raising=False)
    pack = lookup_materials_project("band gap of TiO2")
    assert pack["triggered"] is True
    assert pack["skipped"] == "no_api_key"
    assert pack["context"] == ""


def test_lookup_formats_context(monkeypatch):
    monkeypatch.setenv("MP_API_KEY", "test-key-not-real")

    def fake_search(query, *, api_key, limit=3):
        assert query == "TiO2"
        assert api_key == "test-key-not-real"
        return [{
            "material_id": "mp-2657",
            "formula_pretty": "TiO2",
            "band_gap": 2.1,
            "is_stable": True,
            "is_metal": False,
            "density": 4.2,
            "spacegroup": "P4_2/mnm",
        }]

    monkeypatch.setattr(
        "app.modules.f2w_agent.materials_project.search_mp_summary",
        fake_search,
    )
    pack = lookup_materials_project("band gap of TiO2")
    assert pack["skipped"] is None
    assert "[MP: mp-2657 TiO2]" in pack["context"]
    assert "band_gap_eV=2.1" in pack["context"]
    assert format_mp_context(pack["records"]).startswith("### Materials Project")
