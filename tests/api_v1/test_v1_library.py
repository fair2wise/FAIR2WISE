"""T35 (A8): ``/v1/library`` on ``:local`` over a mounted papers folder; absent (404) on ALS."""
from __future__ import annotations

import io

import pytest

from _v1_support import build_als, build_local

from app.api.policies import API_ROUTE_POLICIES
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService
from app.modules.literature.openalex import QuotaBreaker
from app.modules.security.disclosure import SOURCE_SERVING_PATH_RE
from tests.library.test_library_service import DOI, TEXT, TITLE, FakeProbe, Registry, pdf
from tests.literature.lit_fakes import openalex_work
from tests.security.security_fakes import local_settings


def _service(tmp_path, **kw):
    lib = tmp_path / "papers"
    lib.mkdir(exist_ok=True)
    kw.setdefault("probe", FakeProbe({"a.pdf": ProbeResult(status="ok", title="Local A", page_text=TEXT)}))
    kw.setdefault("fetch_factory", lambda: Registry({DOI: openalex_work(DOI, TITLE)}))
    return LibraryService(lib, tmp_path / "state" / "catalog.sqlite", settings=local_settings(),
                          breaker=QuotaBreaker(), **kw), lib


@pytest.fixture
def lib(monkeypatch, tmp_path):
    service, folder = _service(tmp_path)
    return build_local(monkeypatch, tmp_path, library=service), service, folder


def _upload(c, name="a.pdf", data=None, ctype="application/pdf"):
    return c.post("/v1/library/papers", files={"file": (name, io.BytesIO(pdf("a") if data is None else data), ctype)})


def test_status_papers_upload_verify_roundtrip(lib, tmp_path):
    app, service, folder = lib
    c = app.client()
    assert c.get("/v1/library").json() == {"mounted": True, "paper_count": 0, "indexed_count": 0, "quota_open": False}
    assert c.get("/v1/library/papers").json()["papers"] == []
    up = _upload(c)
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["duplicate"] is False
    paper = body["paper"]
    assert set(paper) >= {"id", "relpath", "file_name", "sha256", "size_bytes", "title", "doi", "verify_status",
                          "verified_at", "index_status"}
    assert paper["file_name"] == "a.pdf" and paper["title"] == "Local A" and paper["doi"] == DOI
    assert paper["verify_status"] == "unverified" and paper["index_status"] == "not_indexed"
    assert (folder / "a.pdf").is_file()
    dup = _upload(c, "renamed.pdf")
    assert dup.json()["duplicate"] is True and dup.json()["paper"]["id"] == paper["id"]
    listed = c.get("/v1/library/papers").json()
    assert listed["total"] == 1 and listed["papers"][0]["id"] == paper["id"]
    assert c.get("/v1/library").json()["paper_count"] == 1
    verified = c.post(f"/v1/library/papers/{paper['id']}/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["paper"]["verify_status"] == "verified" and verified.json()["paper"]["title"] == TITLE
    assert str(tmp_path) not in up.text + verified.text + c.get("/v1/library/papers").text


def test_upload_error_statuses(lib):
    app, service, folder = lib
    c = app.client()
    assert _upload(c, "x.pdf", b"MZ not a pdf").status_code == 415
    assert _upload(c, ".hidden.pdf").status_code == 422
    assert c.post("/v1/library/papers").status_code == 415  # multipart is required
    assert c.post("/v1/library/papers", files={"x": (None, "y")}).status_code == 422  # field `file` is required
    assert c.post("/v1/library/papers", files={"upload": ("a.pdf", pdf())}).status_code == 422  # wrong field name
    service.max_upload_bytes = 100
    assert _upload(c, "big.pdf", b"%PDF-1.4\n" + b"x" * 500).status_code == 413
    assert list(folder.iterdir()) == []


def test_traversal_names_land_inside_the_folder(lib, tmp_path):
    app, service, folder = lib
    c = app.client()
    assert _upload(c, "../../escape.pdf").json()["paper"]["relpath"] == "escape.pdf"
    assert _upload(c, "..%2f..%2fx.pdf", pdf("2")).status_code == 422  # a dot-leading name, whatever follows
    assert not (tmp_path / "escape.pdf").exists() and (folder / "escape.pdf").is_file()


@pytest.mark.parametrize("pid", ["x", "..", "0" * 15, "G" * 16, "0" * 17, "..%2f..%2fetc"])
def test_verify_with_a_bad_or_unknown_id_is_404(lib, pid):
    app, _, _ = lib
    resp = app.client().post(f"/v1/library/papers/{pid}/verify")
    assert resp.status_code == 404, resp.text
    assert app.client().post("/v1/library/papers/" + "0" * 16 + "/verify").status_code == 404


def test_not_mounted_probe_and_refusals(monkeypatch, tmp_path):
    service = LibraryService(None, tmp_path / "state" / "catalog.sqlite", settings=local_settings(),
                             breaker=QuotaBreaker())
    app = build_local(monkeypatch, tmp_path, library=service)
    c = app.client()
    assert c.get("/v1/library").json() == {"mounted": False, "paper_count": 0, "indexed_count": 0, "quota_open": False}
    assert c.get("/v1/library/papers").json()["papers"] == []
    assert _upload(c).status_code == 501


def test_default_service_without_the_mount_setting_says_not_mounted(monkeypatch, tmp_path):
    monkeypatch.delenv("F2W_LIBRARY_DIR", raising=False)
    app = build_local(monkeypatch, tmp_path)
    assert app.client().get("/v1/library").json()["mounted"] is False


def test_the_mount_comes_from_the_startup_setting(monkeypatch, tmp_path):
    (tmp_path / "mnt").mkdir()
    (tmp_path / "mnt" / "p.pdf").write_bytes(pdf("p"))
    app = build_local(monkeypatch, tmp_path, after_install=lambda mp: mp.setenv("F2W_LIBRARY_DIR", str(tmp_path / "mnt")))
    assert app.client().get("/v1/library").json()["paper_count"] == 1


def test_no_route_serves_pdf_bytes_or_matches_the_source_serving_pattern(lib):
    app, service, folder = lib
    c = app.client()
    paper = _upload(c).json()["paper"]
    for path in (f"/v1/library/papers/{paper['id']}", f"/v1/library/papers/{paper['id']}/file",
                 f"/v1/library/papers/{paper['id']}/pdf", "/v1/library/file", "/v1/library/a.pdf"):
        assert c.get(path).status_code in (404, 405), path
    mine = [p for p in API_ROUTE_POLICIES if p.route_id in ("V56", "V57", "V58", "V59")]
    assert [p.route_id for p in mine] == ["V56", "V57", "V58", "V59"]
    pattern = SOURCE_SERVING_PATH_RE
    assert not any(pattern.search(p.path.replace("{paper_id}", "x")) for p in mine)
    for route in (c.app.routes):
        if getattr(route, "path", "").startswith("/v1/library"):
            assert not pattern.search(route.path)
    for r in (c.get("/v1/library"), c.get("/v1/library/papers")):
        assert b"%PDF" not in r.content and r.headers["content-type"].startswith("application/json")


def test_library_job_kind_runs_verify_through_the_worker_path(lib):
    app, service, folder = lib
    c = app.client()
    _upload(c)
    submitted = c.post("/v1/jobs", json={"kind": "library_verify", "inputs": {"corpus_scope": ["library"], "params": {}}})
    assert submitted.status_code in (200, 201, 202), submitted.text
    app.work()
    assert c.get("/v1/library/papers").json()["papers"][0]["verify_status"] == "verified"


def test_routes_are_absent_on_als(monkeypatch, tmp_path):
    als = build_als(monkeypatch, tmp_path)
    for who in ("anon", "user", "admin"):
        c = als.client(who)
        for method, path in (("GET", "/v1/library"), ("GET", "/v1/library/papers"), ("POST", "/v1/library/papers"),
                             ("POST", "/v1/library/papers/" + "0" * 16 + "/verify")):
            resp = c.request(method, path)
            assert resp.status_code == 404 and resp.json() == {"detail": "Not Found"}, (who, method, path, resp.text)
    assert not hasattr(als.app.state, "f2w_library")


def test_job_submission_shape_used_by_the_ui_client(lib):
    """``libraryApi.ts`` posts ``{kind, inputs: {}, nonce}``; the route forbids a top-level ``nonce``."""
    app, _, _ = lib
    c = app.client()
    assert c.post("/v1/jobs", json={"kind": "library_verify", "inputs": {}, "nonce": "n1"}).status_code == 422
    plain = c.post("/v1/jobs", json={"kind": "library_verify", "inputs": {}})
    assert plain.status_code == 200, plain.text
    nonced = c.post("/v1/jobs", json={"kind": "library_verify", "inputs": {"params": {"nonce": "n1"}}})
    assert nonced.status_code == 200 and nonced.json()["job_id"] != plain.json()["job_id"]
