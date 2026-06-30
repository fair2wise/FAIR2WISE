from pathlib import Path

from app.modules.f2w_agent import kg_update


def _splash_repo(tmp_path):
    repo = tmp_path / "splash_links"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "import_kg.py").write_text("print('import')\n")
    return repo


def test_splash_reimport_refuses_wipe_without_explicit_allow(tmp_path):
    repo = _splash_repo(tmp_path)
    db = repo / "links.sqlite"
    db.write_text("old db")
    kg = tmp_path / "kg.json"
    kg.write_text('{"things": [], "associations": []}')

    result = kg_update.splash_reimport(str(kg), splash_repo=str(repo), wipe=True, allow_wipe=False)

    assert result["status"] == "error"
    assert "--allow-splash-wipe" in result["message"]
    assert result["db_path"] == str(db)
    assert db.read_text() == "old db"


def test_splash_reimport_validates_import_script(tmp_path):
    repo = tmp_path / "splash_links"
    repo.mkdir()
    kg = tmp_path / "kg.json"
    kg.write_text('{"things": [], "associations": []}')

    result = kg_update.splash_reimport(str(kg), splash_repo=str(repo), wipe=False)

    assert result["status"] == "error"
    assert "import script not found" in result["message"]


def test_splash_reimport_reports_db_path_when_pixi_missing(tmp_path, monkeypatch):
    repo = _splash_repo(tmp_path)
    db = repo / "links.sqlite"
    db.write_text("old db")
    kg = tmp_path / "kg.json"
    kg.write_text('{"things": [], "associations": []}')
    monkeypatch.setattr(kg_update.shutil, "which", lambda name: None)

    result = kg_update.splash_reimport(str(kg), splash_repo=str(repo), wipe=True, allow_wipe=True)

    assert result["status"] == "error"
    assert result["message"] == "pixi not found on PATH"
    assert result["db_path"] == str(db)
    assert db.exists()


def test_splash_reimport_wipes_through_api_not_sqlite_unlink(tmp_path, monkeypatch):
    repo = _splash_repo(tmp_path)
    db = repo / "links.sqlite"
    db.write_text("old db")
    kg = tmp_path / "kg.json"
    kg.write_text('{"things": [], "associations": []}')
    calls = []

    class Proc:
        returncode = 0
        stdout = "imported"
        stderr = ""

    monkeypatch.setattr(kg_update.shutil, "which", lambda name: "/usr/bin/pixi")
    monkeypatch.setattr(kg_update, "_wipe_splash_via_graphql", lambda uri: calls.append(uri) or 7)
    monkeypatch.setattr(kg_update.subprocess, "run", lambda *args, **kwargs: Proc())

    result = kg_update.splash_reimport(
        str(kg),
        splash_repo=str(repo),
        splash_uri="splash://localhost:8081",
        wipe=True,
        allow_wipe=True,
    )

    assert result["status"] == "success"
    assert result["deleted_entities"] == 7
    assert calls == ["splash://localhost:8081"]
    assert db.read_text() == "old db"


def test_splash_reimport_reports_missing_live_sqlite_handle(tmp_path, monkeypatch):
    repo = _splash_repo(tmp_path)
    db = repo / "links.sqlite"
    kg = tmp_path / "kg.json"
    kg.write_text('{"things": [], "associations": []}')

    monkeypatch.setattr(kg_update.shutil, "which", lambda name: "/usr/bin/pixi")
    monkeypatch.setattr(
        kg_update,
        "_wipe_splash_via_graphql",
        lambda uri: (_ for _ in ()).throw(AssertionError("wipe should not run")),
    )

    result = kg_update.splash_reimport(
        str(kg),
        splash_repo=str(repo),
        splash_uri="splash://localhost:8081",
        wipe=True,
        allow_wipe=True,
    )

    assert result["status"] == "error"
    assert "DB file is missing" in result["message"]
    assert result["db_path"] == str(db)
