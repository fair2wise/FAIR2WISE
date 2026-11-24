"""T38: KG bundles, schema lock, estimate, ranking and the legacy graph-list hooks (fakes only)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.library import bundles as B
from app.modules.library.estimate import ModelPrice, PaperFacts, estimate_extraction, verify_confirmation
from app.modules.library.ranking import KeywordRanker, rank_papers
from app.modules.library.schemas import list_schemas, manifest_block, resolve_schema
from app.modules.graph.snapshot import build_graph_isolated

SCHEMA = {"id": "matkg_schema@0.3.1", "version": "0.3.1", "sha256": "abc", "path": "storage/schema/v0.3.1/matkg_schema.yaml"}


def _kg() -> dict:
    terms = [{"term": "P3HT", "definition": "A polymer.", "category": "Polymer", "source_papers": ["a.pdf"], "pages": [1]},
             {"term": "GIWAXS", "definition": "Scattering.", "category": "ExperimentalTechnique",
              "source_papers": ["a.pdf", "b.pdf"], "pages": [2]}]
    return build_graph_isolated(terms, [])


@pytest.fixture
def store(tmp_path):
    return B.BundleStore(tmp_path / "kgs", clock=lambda: "2026-11-02T00:00:00Z")


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a b", "-x", "x" * 65, "a.b", "../x", "é", None, 3])
def test_names_are_validated(bad):
    with pytest.raises(ContractError) as err:
        B.validate_name(bad)
    assert err.value.code == ErrorCode.INVALID_INPUT


def test_create_collision_and_files(store):
    b = store.create("alpha", schema=SCHEMA, topic="rsoxs", papers=[B.PaperRef("a.pdf", "h1", 3)])
    assert sorted(p.name for p in b.directory.iterdir()) == sorted(B.BUNDLE_FILES)
    assert store.get("alpha").summary() == {
        "name": "alpha", "topic": "rsoxs", "schema": "matkg_schema@0.3.1", "model": None, "backend": None,
        "paper_count": 1, "parent": None, "reconstructed": False, "updated_at": "2026-11-02T00:00:00Z"}
    with pytest.raises(ContractError) as err:
        store.create("alpha", schema=SCHEMA)
    assert err.value.code == ErrorCode.CONFLICT
    assert [x.name for x in store.list()] == ["alpha"]


def test_failed_create_leaves_no_directory(store, monkeypatch):
    monkeypatch.setattr(B, "_write_json", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        store.create("boom", schema=SCHEMA)
    assert not (store.root / "boom").exists()


def test_symlinked_bundle_dir_is_refused(store, tmp_path):
    store.root.mkdir(parents=True)
    (tmp_path / "out").mkdir()
    (store.root / "evil").symlink_to(tmp_path / "out", target_is_directory=True)
    with pytest.raises(ContractError):
        store.get("evil")
    assert store.list() == []


def test_schema_cannot_change_on_continuation(store):
    store.create("p", schema=SCHEMA, terms={"terms": [], "code_snippets": []})
    with pytest.raises(ContractError) as err:
        store.fork("p", "child", schema_id="rsoxs_schema@0.3.1")
    assert err.value.code == ErrorCode.INVALID_INPUT and "locked" in str(err.value)
    child = store.fork("p", "child", schema_id="matkg_schema@0.3.1")
    assert child.manifest["parent"] == "p" and child.schema == "matkg_schema@0.3.1"
    inherited = store.fork("p", "child2")
    assert inherited.schema == "matkg_schema@0.3.1"
    with pytest.raises(ContractError):
        store.record_extraction("p", schema_id="rsoxs_schema@0.3.1", model="m", backend="cborg", papers=[])
    with pytest.raises(ContractError) as err:
        store.fork("p", "p")
    assert err.value.code == ErrorCode.CONFLICT


def test_continue_from_kg_builds_a_reconstructed_child(store, tmp_path):
    src = tmp_path / "matkg_old.json"
    kg = _kg()
    src.write_text(json.dumps(kg))
    bundle, warnings = B.continue_from_kg(store, src, "cont", schema=SCHEMA, topic="t")
    assert bundle.manifest["reconstructed"] is True and bundle.manifest["parent"] is None
    assert sorted(p["name"] for p in bundle.manifest["papers"]) == ["a.pdf", "b.pdf"]
    terms = json.loads(bundle.file(B.TERMS_FILE).read_text())
    assert {t["term"] for t in terms["terms"]} >= {"P3HT", "GIWAXS"}
    assert json.loads(bundle.file(B.KG_FILE).read_text()) == kg
    assert json.loads(bundle.file(B.PROCESSED_FILE).read_text()) == ["a.pdf", "b.pdf"]
    manifest = json.loads(bundle.file(B.EXTRACTION_MANIFEST_FILE).read_text())
    assert {e["extraction_state"] for e in manifest["papers"].values()} == {"reconstructed"}
    with pytest.raises(ContractError) as err:
        B.continue_from_kg(store, src, "cont", schema=SCHEMA)
    assert err.value.code == ErrorCode.CONFLICT
    bad = tmp_path / "bad.json"
    bad.write_text('{"x": 1}')
    with pytest.raises(ContractError) as err:
        B.continue_from_kg(store, bad, "bad", schema=SCHEMA)
    assert err.value.code == ErrorCode.INVALID_INPUT and not store.exists("bad")


def test_export_writes_schema_metadata_and_refuses_foreign_files(store, tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    bundle = store.create("alpha", schema=SCHEMA, topic="x", kg=_kg())
    name = B.export_bundle(bundle, release, {"label": "General"})
    assert name == "matkg_alpha.json"
    out = json.loads((release / name).read_text())
    assert out["metadata"]["schema"] == "matkg_schema@0.3.1" and out["metadata"]["f2w_bundle"] == "alpha"
    assert out["things"] == _kg()["things"]
    raw = (release / name).read_text(encoding="utf-8")
    assert raw == json.dumps(json.loads(raw), indent=2, ensure_ascii=False)  # the serialization release adoption asserts
    assert "metadata" not in json.loads(bundle.file(B.KG_FILE).read_text())  # the bundle copy is untouched
    assert B.export_bundle(bundle, release) == name  # re-export of its own file is allowed
    (release / "matkg_beta.json").write_text("{}")
    with pytest.raises(ContractError) as err:
        B.export_bundle(store.create("beta", schema=SCHEMA), release)
    assert err.value.code == ErrorCode.CONFLICT
    (tmp_path / "elsewhere.json").write_text("{}")
    (release / "matkg_gamma.json").symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(ContractError):
        B.export_bundle(store.create("gamma", schema=SCHEMA), release)
    assert (tmp_path / "elsewhere.json").read_text() == "{}"
    with pytest.raises(ContractError) as err:
        B.export_bundle(bundle, tmp_path / "missing")
    assert err.value.code == ErrorCode.UNAVAILABLE


def test_legacy_hooks_list_resolve_and_lock_schema(store, tmp_path):
    assert B.registered_graph_paths(tmp_path) == []
    store.create("alpha", schema=SCHEMA, kg=_kg())
    store.create("empty", schema=SCHEMA)  # kg.json present but a placeholder: still listed
    paths = B.registered_graph_paths(tmp_path)
    assert "kgs/alpha/matkg_alpha.json" in paths
    real = B.resolve_registered_graph("kgs/alpha/matkg_alpha.json", tmp_path)
    assert real == (tmp_path / "kgs" / "alpha" / "kg.json").resolve()
    for bad in ("kgs/alpha/matkg_beta.json", "kgs/../x/matkg_x.json", "kgs/alpha/kg.json", "storage/kg/matkg_a.json",
                "/etc/passwd", "kgs/nope/matkg_nope.json"):
        assert B.resolve_registered_graph(bad, tmp_path) is None
    assert B.schema_path_for_registered_graph("kgs/alpha/matkg_alpha.json", tmp_path) == SCHEMA["path"]
    assert B.schema_path_for_registered_graph("storage/kg/matkg_rsoxs_v1.json", tmp_path) is None


def test_legacy_list_function_is_unchanged_when_no_bundles(monkeypatch, tmp_path):
    from app.modules.f2w_agent.api import list_storage_kg_json_files

    monkeypatch.delenv(B.WORKDIR_ENV, raising=False)
    base = list_storage_kg_json_files()
    monkeypatch.setenv(B.WORKDIR_ENV, str(tmp_path))
    assert list_storage_kg_json_files() == base
    B.BundleStore(tmp_path / "kgs").create("alpha", schema=SCHEMA, kg=_kg())
    assert list_storage_kg_json_files() == base + ["kgs/alpha/matkg_alpha.json"]
    assert list_storage_kg_json_files(Path.cwd()) == base  # an explicit root never sees bundles


def test_schemas_come_from_the_registry_v03x():
    choices = list_schemas()
    names = {c["name"] for c in choices}
    assert names and all("@0.3." in n for n in names)
    assert "bl1101_schema@0.3.1" not in names  # graph-view only
    pick = resolve_schema(sorted(names)[0])
    assert set(manifest_block(pick)) == {"id", "version", "sha256", "path"}
    for bad in ("matkg_schema", "nope", "rsoxs_schema_extract"):
        with pytest.raises(ContractError):
            resolve_schema(bad)


def test_estimate_is_deterministic_and_token_binds_the_selection():
    papers = [PaperFacts("p2", pages=10, sha256="h2"), PaperFacts("p1", pages=5, sha256="h1"), PaperFacts("p3")]
    a = estimate_extraction(papers, "m", "s")
    b = estimate_extraction(list(reversed(papers)) + [papers[0]], "m", "s")
    assert a == b and a["pages"] == 15 and a["unknown_pages"] == ["p3"] and a["estimated_cost_usd"] is None
    priced = estimate_extraction(papers, "m", "s", {"m": ModelPrice(1.0, 2.0)})
    assert priced["estimated_cost_usd"] == round(15 * 1200 / 1e6 + 15 * 400 / 1e6 * 2, 4)
    assert priced["confirmation"] != a["confirmation"]
    verify_confirmation(a["confirmation"], papers, "m", "s")
    for args in ((papers[:2], "m", "s"), (papers, "other", "s"), (papers, "m", "t")):
        with pytest.raises(ContractError) as err:
            verify_confirmation(a["confirmation"], *args)
        assert err.value.code == ErrorCode.STALE_APPROVAL
    with pytest.raises(ContractError):
        estimate_extraction([], "m", "s")
    with pytest.raises(ContractError):
        estimate_extraction(papers, " ", "s")


def test_keyword_ranking_is_deterministic():
    papers = [{"name": "z.pdf", "title": "Polymer films", "doi": "10.1/z"},
              {"name": "a.pdf", "title": "RSoXS of polymer blends", "doi": ""},
              {"name": "rsoxs_notes.pdf", "title": "", "doi": ""},
              {"name": "b.pdf", "title": "Unrelated", "doi": ""}]
    ranked = rank_papers(papers, "RSoXS polymer")
    assert [p["name"] for p in ranked] == ["a.pdf", "z.pdf", "rsoxs_notes.pdf", "b.pdf"]
    assert [p["name"] for p in rank_papers(papers, "")] == ["a.pdf", "b.pdf", "rsoxs_notes.pdf", "z.pdf"]
    assert KeywordRanker().rank(papers, "zzz")[0][0] == 0


def test_default_release_dir_prefers_env_then_workdir_then_storage(tmp_path, monkeypatch):
    monkeypatch.delenv(B.RELEASE_DIR_ENV, raising=False)
    monkeypatch.delenv(B.WORKDIR_ENV, raising=False)
    assert B.default_release_dir().parts[-2:] == ("storage", "kg")
    monkeypatch.setenv(B.WORKDIR_ENV, str(tmp_path / "state"))
    assert B.default_release_dir() == tmp_path / "state" / "kg_releases"   # the writable volume, not /app/storage/kg
    monkeypatch.setenv(B.RELEASE_DIR_ENV, str(tmp_path / "rel"))
    assert B.default_release_dir() == tmp_path / "rel"


def test_ensure_release_dir_creates_and_proves_it_is_writable(tmp_path):
    target = tmp_path / "state" / "kg_releases"
    B.ensure_release_dir(target)
    assert target.is_dir() and list(target.iterdir()) == []   # the probe file is removed


def test_ensure_release_dir_on_a_read_only_directory_names_the_fix(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        if _can_write(ro):
            pytest.skip("running as a user that ignores directory permissions")
        with pytest.raises(ContractError) as err:
            B.ensure_release_dir(ro)
        assert err.value.code == ErrorCode.UNAVAILABLE and err.value.retryable is False
        assert B.RELEASE_DIR_ENV in str(err.value) and str(ro) in str(err.value)
    finally:
        ro.chmod(0o700)


def test_export_to_a_read_only_directory_is_a_structured_error_not_an_oserror(store, tmp_path, monkeypatch):
    release = tmp_path / "release"
    release.mkdir()
    bundle = store.create("alpha", schema=SCHEMA, topic="x", kg=_kg())

    def refuse(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(B.tempfile, "mkstemp", refuse)
    with pytest.raises(ContractError) as err:
        B.export_bundle(bundle, release)
    assert err.value.code == ErrorCode.UNAVAILABLE and B.RELEASE_DIR_ENV in str(err.value)
    assert "Read-only" in str(err.value)


def _can_write(directory: Path) -> bool:
    probe = directory / ".probe"
    try:
        probe.write_text("x")
    except OSError:
        return False
    probe.unlink()
    return True


# --- pilot 2026-11-24: an extracted KG is usable in chat, the viewer and Operations under its own name ----------------


def _bundle_with_cloud_tomography(tmp_path, monkeypatch):
    workdir = tmp_path / "state"
    monkeypatch.setenv(B.WORKDIR_ENV, str(workdir))
    terms = [{"term": "cloud tomography", "definition": "Imaging of clouds.", "category": "Method",
              "source_papers": ["a.pdf"], "pages": [1]}]
    store = B.BundleStore(workdir / "kgs", clock=lambda: "2026-11-24T00:00:00Z")
    bundle = store.create("tomo_test", schema=SCHEMA, topic="tomo", kg=build_graph_isolated(terms, []))
    return workdir, bundle


def test_a_bundles_kg_file_is_the_graph_named_by_its_bundle(tmp_path, monkeypatch):
    from app.modules.f2w_agent.multi_kg import graph_id_for_path
    from app.modules.graph.snapshots import graph_id_for_snapshot

    _, bundle = _bundle_with_cloud_tomography(tmp_path, monkeypatch)
    real = bundle.directory / B.KG_FILE
    assert graph_id_for_path(str(real)) == "tomo_test" == graph_id_for_snapshot(real)   # was "kg"
    assert graph_id_for_path("kgs/tomo_test/matkg_tomo_test.json") == "tomo_test"
    assert graph_id_for_path("runs/session/kg.json") == "kg"    # the session graph keeps its name


def test_chat_node_lookup_finds_the_extracted_entity_by_its_graph(tmp_path, monkeypatch):
    from app.modules.f2w_agent.api import resolve_graph_node

    _, bundle = _bundle_with_cloud_tomography(tmp_path, monkeypatch)
    real = bundle.directory / B.KG_FILE
    node = resolve_graph_node("matkg:cloudtomography", fallback_path=tmp_path / "missing.json",
                              graph_paths_by_id={"tomo_test": real})
    assert node is not None and node.graph_id == "tomo_test" and node.label == "cloud tomography"
    assert node.graph_label != "kg"
