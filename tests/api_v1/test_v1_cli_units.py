"""T14: the operations CLI, the default (unwired) operations and the guard units."""
from __future__ import annotations

import json

import pytest

from app.api import cli
from app.api.operations import PublicationGate, build_operations
from app.api.origin_gate import browser_origin_refusal, origin_allowed
from app.api.upload_limits import require_pdf, safe_child
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.security.authorization import DeploymentSettings

LOCAL = DeploymentSettings(profile="local", writer_identity="f2w-writer", tiled_target="http://127.0.0.1:59874")


def test_default_operations_register_no_kind_and_no_publication(tmp_path):
    ops = build_operations(LOCAL, tmp_path / "ops")
    ctx = ops.cli_context()
    assert ops.jobs._kinds == {} and ops.publication is None
    with pytest.raises(ContractError) as err:
        ops.submit_job(ctx, "ingest", {"corpus_scope": ["rsoxs"]})
    assert err.value.code is ErrorCode.INVALID_INPUT
    with pytest.raises(ContractError) as err:
        ops.submit_publication(ctx, "rsoxs/v1", "http://127.0.0.1:59874/api#f2w")
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert ops.audit_status(ctx)["degraded"] is False
    names = {p.name for p in (tmp_path / "ops").iterdir() if p.suffix == ".sqlite3"}
    assert names == {"audit.sqlite3", "curation.sqlite3", "jobs.sqlite3"}


def test_cli_prints_json_from_the_shared_operations(tmp_path, capsys):
    root = str(tmp_path / "ops")
    assert cli.main(["--root", root, "jobs", "list"], environ={}) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert cli.main(["--root", root, "audit", "status"], environ={}) == 0
    assert json.loads(capsys.readouterr().out)["degraded"] is False
    assert cli.main(["--root", root, "ops", "status"], environ={}) == 0
    assert json.loads(capsys.readouterr().out)["kinds"] == []
    assert cli.main(["--root", root, "jobs", "get", "job-missing"], environ={}) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "not_found"


def test_cli_refuses_an_als_profile(tmp_path, capsys):
    env = {"F2W_DEPLOYMENT_PROFILE": "als-dev"}
    assert cli.main(["--root", str(tmp_path / "ops"), "jobs", "list"], environ=env) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "denied"


def test_publication_gate_matches_base_and_namespace():
    gate = PublicationGate(frozenset({"http://127.0.0.1:59874/api#f2w"}))
    assert gate.allows("http://127.0.0.1:59874/api#f2w")
    assert not gate.allows("http://127.0.0.1:59874/api#other")
    assert not gate.allows("http://127.0.0.1:59875/api#f2w")
    open_base = PublicationGate(frozenset({"http://127.0.0.1:59874"}))
    assert open_base.allows("http://127.0.0.1:59874/api#any") and not open_base.allows("not a url")
    assert not PublicationGate().allows("http://127.0.0.1:59874/api#f2w")
    with pytest.raises(ContractError) as err:
        gate.check("https://tiled.als.lbl.gov/api#f2w", LOCAL)
    assert err.value.code is ErrorCode.DENIED


def test_origin_rules():
    host = {"host": "f2w.example.org"}
    assert origin_allowed("https://f2w.example.org", host, scheme="https")
    assert not origin_allowed("https://f2w.example.org", host, scheme="http")  # scheme and port differ
    assert not origin_allowed("https://f2w.example.org:8443", host, scheme="https")
    assert not origin_allowed("https://evil.example", host)
    assert origin_allowed("https://ui.example", host, allowed_origins=["https://ui.example"])
    assert origin_allowed("http://localhost:5173", host, allowed_origin_regex=r"http://localhost:\d+")
    assert not origin_allowed("http://localhost:5173.evil.example", host,
                              allowed_origin_regex=r"http://localhost:\d+")
    assert browser_origin_refusal("GET", {"origin": "https://evil.example"}) is None
    assert browser_origin_refusal("POST", {}) is None
    assert browser_origin_refusal("DELETE", {"sec-fetch-site": "cross-site"})


def test_upload_helpers(tmp_path):
    from fastapi import HTTPException

    assert require_pdf(b"%PDF-1.7\n...") == b"%PDF-1.7\n..."
    with pytest.raises(HTTPException) as err:
        require_pdf(b"<html>")
    assert err.value.status_code == 415
    assert safe_child(tmp_path, "paper.pdf") == (tmp_path / "paper.pdf").resolve()
    for bad in ("../x.pdf", "a/b.pdf", ".hidden", "", "..", "a\\b"):
        with pytest.raises(HTTPException):
            safe_child(tmp_path, bad)
