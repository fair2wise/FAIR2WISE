"""T32 (owner decision B4): ``/v1/folders`` on ``:local`` over roots fixed in configuration.

Roots come from ``F2W_FOLDER_ROOTS`` (or a ``FolderRoots`` given to the factory), never from a
request. Every traversal attempt (``..``, absolute paths, encoded separators, backslashes,
symlinks escaping or even staying inside the root) is a 4xx that names no path; the ALS
counterpart (routes absent, 404) is in ``test_v1_als.py``.
"""
from __future__ import annotations

import os
from urllib.parse import quote

import pytest

from _v1_support import build_local

from app.api.folders import FOLDER_ROOTS_ENV, NOT_FOUND_MESSAGE, FolderRoots, check_relative
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.security.authorization import StartupConfigError

PDF = b"%PDF-1.7\n%fixture\n"


def _tree(tmp_path):
    root, outside = tmp_path / "papers", tmp_path / "outside"
    (root / "rsoxs" / "2024").mkdir(parents=True)
    (root / "rsoxs" / "a.pdf").write_bytes(PDF)
    (root / "rsoxs" / "2024" / "b.pdf").write_bytes(PDF * 2)
    (root / "rsoxs" / "notes.txt").write_text("not a document")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "h.pdf").write_bytes(PDF)
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(PDF)
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "escape.pdf").symlink_to(outside / "secret.pdf")
    (root / "inner").symlink_to(root / "rsoxs", target_is_directory=True)  # inside, still never followed
    return root, outside


@pytest.fixture
def local(monkeypatch, tmp_path):
    root, _outside = _tree(tmp_path)
    (tmp_path / "second").mkdir()
    roots = FolderRoots.from_paths({"papers": root, "second": tmp_path / "second"})
    return build_local(monkeypatch, tmp_path, folder_roots=roots)


def test_roots_and_listing_show_labels_and_relative_paths_only(local, tmp_path):
    c = local.client()
    assert c.get("/v1/folders/roots").json() == {"roots": [{"root": "papers", "exists": True},
                                                            {"root": "second", "exists": True}]}
    top = c.get("/v1/folders/list", params={"root": "papers"})
    assert top.status_code == 200, top.text
    body = top.json()
    assert body["root"] == "papers" and body["relative_path"] == "" and body["truncated"] is False
    by_name = {e["name"]: e for e in body["entries"]}
    assert set(by_name) == {"rsoxs", "escape", "escape.pdf", "inner"}  # .hidden skipped
    assert by_name["rsoxs"]["kind"] == "directory" and by_name["rsoxs"]["relative_path"] == "rsoxs"
    assert by_name["escape"]["kind"] == "link" and by_name["escape.pdf"]["kind"] == "link"
    assert by_name["inner"]["kind"] == "link" and by_name["escape"]["size"] is None
    sub_resp = c.get("/v1/folders/list", params={"root": "papers", "path": "rsoxs"})
    sub = sub_resp.json()
    names = [e["name"] for e in sub["entries"]]
    assert names == ["2024", "a.pdf", "notes.txt"]  # directories first, then names
    a = next(e for e in sub["entries"] if e["name"] == "a.pdf")
    assert a["document"] is True and a["size"] == len(PDF) and a["relative_path"] == "rsoxs/a.pdf"
    assert next(e for e in sub["entries"] if e["name"] == "notes.txt")["document"] is False
    assert str(tmp_path) not in top.text + sub_resp.text and "/private/" not in top.text + sub_resp.text
    assert c.get("/v1/folders/list", params={"root": "second"}).json()["entries"] == []
    assert local.port.calls == 0


def test_index_inventories_documents_recursively_without_following_links(local, tmp_path):
    c = local.client()
    out = c.get("/v1/folders/index", params={"root": "papers"}).json()
    assert [d["relative_path"] for d in out["documents"]] == ["rsoxs/a.pdf", "rsoxs/2024/b.pdf"]
    assert out["count"] == 2 and out["truncated"] is False and out["folders_scanned"] == 3  # root, rsoxs, 2024
    assert "secret" not in c.get("/v1/folders/index", params={"root": "papers"}).text
    bounded = c.get("/v1/folders/index", params={"root": "papers", "limit": 1}).json()
    assert len(bounded["documents"]) == 1 and bounded["count"] == 2 and bounded["truncated"] is True
    sub = c.get("/v1/folders/index", params={"root": "papers", "path": "rsoxs/2024"}).json()
    assert [d["relative_path"] for d in sub["documents"]] == ["rsoxs/2024/b.pdf"] and sub["relative_path"] == "rsoxs/2024"
    assert str(tmp_path) not in sub.__repr__()


@pytest.mark.parametrize("path", [
    "..", "../outside", "rsoxs/../..", "rsoxs/../../outside/secret.pdf",
    "/etc", "/" + "outside", "~/x", "C:\\\\x", "rsoxs\\\\..\\\\..",
    "%2e%2e", "%2e%2e%2f%2e%2e", "rsoxs%2f..%2f..", "rsoxs%5c..", ".", "rsoxs//2024", "a\x00b", ".hidden",
    "rsoxs/.git",
])
def test_traversal_attempts_are_refused_without_a_path(local, path, tmp_path):
    c = local.client()
    for route in ("/v1/folders/list", "/v1/folders/index"):
        resp = c.get(f"{route}?root=papers&path={quote(path, safe='%')}")
        assert resp.status_code in (404, 422), (route, path, resp.status_code, resp.text)
        assert resp.json()["code"] in ("invalid_input", "not_found"), resp.text
        text = resp.text
        assert str(tmp_path) not in text and "outside" not in text and "secret" not in text and "etc" not in text


@pytest.mark.parametrize("path", ["escape", "escape/secret.pdf", "escape.pdf", "inner", "inner/2024",
                                  "missing", "rsoxs/a.pdf", "rsoxs/notes.txt"])
def test_links_files_and_missing_folders_are_the_same_404(local, path, tmp_path):
    """A link (even one staying inside the root), a file and a missing folder are indistinguishable."""
    c = local.client()
    for route in ("/v1/folders/list", "/v1/folders/index"):
        resp = c.get(route, params={"root": "papers", "path": path})
        assert resp.status_code == 404, (route, path, resp.text)
        assert resp.json() == {"code": "not_found", "message": NOT_FOUND_MESSAGE, "retryable": False}


def test_unknown_root_is_404_and_roots_never_come_from_the_request(local, tmp_path):
    c = local.client()
    for root in ("outside", "/outside", "../outside", "/", "..", "papers/..", "PAPERS", "papers=/outside"):
        resp = c.get("/v1/folders/list", params={"root": root})
        assert resp.status_code == 404 and resp.json()["message"] == NOT_FOUND_MESSAGE, (root, resp.text)
        assert str(tmp_path) not in resp.text
    assert c.get("/v1/folders/list", params={"root": "x" * 65}).status_code == 422  # label bound
    assert c.get("/v1/folders/list").status_code == 422  # root is required


def test_check_relative_rules():
    assert check_relative("") == check_relative(None) and not check_relative("").parts
    assert check_relative("a/b/").parts == ("a", "b")
    for bad in ("/a", "~", "a/../b", "./a", "a//b", "a\\b", "C:x", "a\x7fb", "x" * 300, ".hidden/a", "a/.b"):
        with pytest.raises(ContractError) as err:
            check_relative(bad)
        assert err.value.code is ErrorCode.INVALID_INPUT, bad


def test_folder_roots_from_startup_and_validation(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    roots = FolderRoots.from_startup({FOLDER_ROOTS_ENV: os.pathsep.join([f"intake={a}", str(b)])})
    assert set(roots.roots) == {"intake", "b"} and roots.roots["intake"] == a.resolve()
    assert roots.describe() == [{"root": "b", "exists": False}, {"root": "intake", "exists": True}]
    assert not FolderRoots.from_startup({}).configured and FolderRoots.from_startup(None).describe() == []
    for raw in ("relative/path", f"a={a}{os.pathsep}a={b}", f"bad label={a}", f"../x={a}"):
        with pytest.raises(StartupConfigError):
            FolderRoots.from_startup({FOLDER_ROOTS_ENV: raw})
    with pytest.raises(ContractError) as err:
        roots.list_folder("intake", "..")
    assert err.value.code is ErrorCode.INVALID_INPUT
    with pytest.raises(ContractError) as err:
        roots.list_folder("b")  # configured but absent on disk
    assert err.value.code is ErrorCode.NOT_FOUND and str(b) not in str(err.value)


def test_default_roots_come_from_the_startup_environment(monkeypatch, tmp_path):
    """Without a ``FolderRoots`` argument the factory reads ``F2W_FOLDER_ROOTS``; unset means no roots."""
    root, _outside = _tree(tmp_path)
    app = build_local(monkeypatch, tmp_path, after_install=lambda mp: mp.setenv(FOLDER_ROOTS_ENV, f"papers={root}"))
    assert app.client().get("/v1/folders/roots").json() == {"roots": [{"root": "papers", "exists": True}]}
    bare = build_local(monkeypatch, tmp_path / "bare",
                       after_install=lambda mp: mp.delenv(FOLDER_ROOTS_ENV, raising=False))
    assert bare.client().get("/v1/folders/roots").json() == {"roots": []}
    assert bare.client().get("/v1/folders/list", params={"root": "papers"}).status_code == 404


def test_sdk_and_cli_context_use_the_same_folder_policy(local):
    """The shared operations apply the policy for direct callers too (ingest.acquire; same refusals)."""
    ctx = local.ops.cli_context()
    assert [r["root"] for r in local.ops.folder_roots(ctx)["roots"]] == ["papers", "second"]
    assert local.ops.folder_index(ctx, "papers", "rsoxs")["count"] == 2
    with pytest.raises(ContractError) as err:
        local.ops.list_folder(ctx, "papers", "escape")
    assert err.value.code is ErrorCode.NOT_FOUND
    worker = ctx.model_copy(update={"capabilities": frozenset({"chat.ask"})})
    with pytest.raises(ContractError) as err:
        local.ops.folder_roots(worker)
    assert err.value.code is ErrorCode.DENIED
