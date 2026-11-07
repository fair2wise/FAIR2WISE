"""The legacy ``DownloadAgent`` fetches PDF bytes only through the hardened downloader (T17B/H8, step 3 audit).

Owner decision 2026-11-03/04: the retained agent and the harvest scripts sit on
``literature.legacy_download.hardened_download_pdf`` (T29 did the agent's PDF path; this pins it). The
transport is covered by ``tests/literature/test_safe_fetch.py`` and ``tests/test_download_parity.py``.
The three metadata hops (OpenAlex, arXiv, Unpaywall) moved onto ``hardened_get_json``/``hardened_get_bytes``
(legacy_ports_plan.md P1), so the set of direct hops is now empty and pinned that way: a direct client
(``pyalex``, ``arxiv``, ``requests``, ``download_pdfs.fetch_*``) reappearing in the agent fails this file.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.modules.f2w_agent import download_agent as da
from app.modules.literature import download_pdfs as dl
from app.modules.literature import legacy_download
from app.modules.literature.legacy_download import hardened_download_pdf, hardened_get_bytes, hardened_get_json

SRC = Path(da.__file__)
TREE = ast.parse(SRC.read_text(encoding="utf-8"))


def _imported_roots(tree: ast.AST) -> set[str]:
    roots = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    roots |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and not n.level}
    return roots


def test_no_private_http_code_in_the_agent():
    assert not _imported_roots(TREE) & {"requests", "httpx", "urllib", "urllib3", "http", "socket", "aiohttp"}


def test_pdf_bytes_flow_only_through_the_hardened_downloader():
    assert da.hardened_download_pdf is hardened_download_pdf
    calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
             for n in ast.walk(TREE) if isinstance(n, ast.Call)}
    assert "hardened_download_pdf" in calls
    assert "download_pdf" not in calls  # the requests-based parity oracle in download_pdfs is never called


def test_no_direct_metadata_hop_remains():
    # pyalex, the arxiv package and download_pdfs.fetch_unpaywall_oa_pdfs are gone from the agent; every
    # metadata lookup is one of the hardened helpers, called with the hardened getter.
    assert not _imported_roots(TREE) & {"pyalex", "arxiv", "requests", "feedparser", "lxml", "xml", "defusedxml"}
    imported = {a.name for n in ast.walk(TREE) if isinstance(n, ast.ImportFrom) for a in n.names}
    imported |= {a.name for n in ast.walk(TREE) if isinstance(n, ast.Import) for a in n.names}
    assert not {n for n in imported if n.startswith("fetch_")}
    assert not any(isinstance(n, ast.Name) and n.id in {"pyalex", "arxiv"} for n in ast.walk(TREE))
    for node in ast.walk(TREE):  # no dynamic import of a client either (the old code imported lazily inside methods)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"__import__", "import_module"}:
            raise AssertionError("dynamic import in the download agent")
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "import_module":
            raise AssertionError("dynamic import in the download agent")
    dl_calls = {n.func.attr for n in ast.walk(TREE) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "_dl"}
    assert dl_calls == {"is_reliable_pdf_url", "select_openalex_oa_pdfs", "rewrite_oa_pdf_url"}
    assert not {c for c in dl_calls if c.startswith("fetch_")}


def test_metadata_goes_through_the_hardened_helpers():
    assert da.hardened_get_json is hardened_get_json and da.hardened_get_bytes is hardened_get_bytes
    assert (da.openalex_search, da.openalex_work, da.arxiv_search, da.unpaywall_oa_pdfs) == (
        legacy_download.openalex_search, legacy_download.openalex_work, legacy_download.arxiv_search,
        legacy_download.unpaywall_oa_pdfs)
    calls = {n.func.id: n for n in ast.walk(TREE) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    for helper, kwarg in (("openalex_search", "get_json"), ("openalex_work", "get_json"),
                          ("unpaywall_oa_pdfs", "get_json"), ("arxiv_search", "get_bytes")):
        sites = [n for n in ast.walk(TREE) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == helper]
        assert sites, helper
        for site in sites:  # the getter is always passed explicitly (and is patchable at module level)
            assert {k.arg for k in site.keywords} >= {kwarg}, helper
    assert calls  # sanity


def test_the_helper_module_itself_has_no_direct_client():
    tree = ast.parse(Path(legacy_download.__file__).read_text(encoding="utf-8"))
    assert not _imported_roots(tree) & {"requests", "urllib3", "pyalex", "arxiv", "socket", "aiohttp"}
    assert not any(isinstance(n, ast.Import) and any(a.name.startswith("urllib.request") for a in n.names)
                   for n in ast.walk(tree))
    assert dl.fetch_unpaywall_oa_pdfs is not legacy_download.unpaywall_oa_pdfs  # the requests oracle stays apart
