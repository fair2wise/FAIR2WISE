"""T14: the operations CLI, the default (unwired) operations and the guard units."""
from __future__ import annotations

import json
from pathlib import Path

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


def test_cli_t31_commands_cost_scales_vocab_and_bulk_review(tmp_path, capsys):
    """T31 (B5): the CLI wrappers call the same operations as /v1 with the local-operator context."""
    root = str(tmp_path / "ops")

    def run(*argv):
        code = cli.main(["--root", root, *argv], environ={})
        out = capsys.readouterr()
        return code, json.loads(out.out or out.err)

    code, shown = run("cost", "show")
    assert code == 0 and shown["configured"] and shown["enabled"] is False and shown["budget_cap_usd"] is None
    code, saved = run("cost", "set", "--enable", "--cap-usd", "3")
    assert code == 0 and saved["enabled"] is True and saved["budget_cap_usd"] == 3.0
    code, cleared = run("cost", "set", "--disable", "--no-cap")
    assert code == 0 and cleared["enabled"] is False and cleared["budget_cap_usd"] is None
    code, bad = run("cost", "set", "--enable", "--cap-usd", "-2")
    assert code == 2 and bad["code"] == "invalid_input"

    code, record = run("scales", "record", "--corpus", "rsoxs", "--work", "10.1/x", "--figure", "fig1",
                       "--panel", "0", "--nm-per-px", "2.0", "--bar-px", "50", "--bar-nm", "100")
    assert code == 0 and record["recorded_by"] == "local-operator" and record["nm_per_px"] == 2.0
    code, listed = run("scales", "list", "--work", "10.1/x")
    assert code == 0 and [r["id"] for r in listed] == [record["id"]]
    code, one = run("scales", "show", record["id"])
    assert code == 0 and one["bar_length_nm"] == 100.0
    code, missing = run("scales", "show", "fsc-missing")
    assert code == 2 and missing["code"] == "not_found"

    code, quality = run("vocab", "quality", "--limit", "5")
    assert code == 0 and quality == {"snapshots": [], "count": 0, "latest": None}

    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps([{"case_id": "case-x", "bound_revision": "r", "accept": False}]))
    code, bulk = run("cases", "bulk-review", "--decisions", f"@{decisions}")
    assert code == 0 and bulk["failed"] == 1 and bulk["results"][0]["code"] == "not_found"
    code, inline = run("cases", "bulk-review", "--decisions", '[{"case_id": "c", "bound_revision": "r", "accept": true}]')
    assert code == 0 and inline["results"][0]["status"] == "error"
    code, bad = run("cases", "bulk-review", "--decisions", "{not json")
    assert code == 2 and bad["code"] == "invalid_input"
    code, bad = run("cases", "bulk-review", "--decisions", "@" + str(tmp_path / "nope.json"))
    assert code == 2 and bad["code"] == "invalid_input"


def test_cli_folders_read_the_roots_from_the_startup_environment(tmp_path, capsys):
    """T32 (B4): ``folders`` browses only the roots named by F2W_FOLDER_ROOTS; the request names a label."""
    papers = tmp_path / "papers"
    (papers / "sub").mkdir(parents=True)
    (papers / "sub" / "a.pdf").write_bytes(b"%PDF-1.7\n")
    root = str(tmp_path / "ops")
    env = {"F2W_FOLDER_ROOTS": f"papers={papers}"}

    def run(*argv, environ=env):
        code = cli.main(["--root", root, *argv], environ=environ)
        out = capsys.readouterr()
        return code, json.loads(out.out or out.err), out.out + out.err

    code, roots, _ = run("folders", "roots")
    assert code == 0 and roots == {"roots": [{"root": "papers", "exists": True}]}
    code, listed, text = run("folders", "list", "--root", "papers", "--path", "sub")
    assert code == 0 and [e["name"] for e in listed["entries"]] == ["a.pdf"] and str(papers) not in text
    code, index, _ = run("folders", "index", "--root", "papers")
    assert code == 0 and [d["relative_path"] for d in index["documents"]] == ["sub/a.pdf"]
    code, refused, text = run("folders", "list", "--root", "papers", "--path", "../outside")
    assert code == 2 and refused["code"] == "invalid_input" and str(tmp_path) not in text
    code, unknown, _ = run("folders", "list", "--root", "other")
    assert code == 2 and unknown["code"] == "not_found"
    code, none, _ = run("folders", "roots", environ={})
    assert code == 0 and none == {"roots": []}
    assert not Path("other").exists()  # the folder label never becomes an operations root (dest collision)


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
