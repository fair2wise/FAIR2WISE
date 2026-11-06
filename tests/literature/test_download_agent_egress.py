"""The legacy ``DownloadAgent`` fetches PDF bytes only through the hardened downloader (T17B/H8, step 3 audit).

Owner decision 2026-11-03/04: the retained agent and the harvest scripts sit on
``literature.legacy_download.hardened_download_pdf`` (T29 did the agent's PDF path; this pins it). The
transport is covered by ``tests/literature/test_safe_fetch.py`` and ``tests/test_download_parity.py``.
The remaining metadata hops that do not yet go through the hardened JSON fetcher are pinned by name, so a
new one is noticed and the planned follow-up (``docs/migration/reviews/H8/legacy_ports_plan.md``, section
"download_agent") stays exact.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.modules.f2w_agent import download_agent as da
from app.modules.literature import download_pdfs as dl
from app.modules.literature.legacy_download import hardened_download_pdf

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


def test_the_remaining_metadata_hops_are_exactly_the_documented_ones():
    # Third-party API clients the agent still uses for metadata (not PDF bytes): OpenAlex via pyalex, arXiv
    # via the arxiv package, Unpaywall via download_pdfs.fetch_unpaywall_oa_pdfs (requests). Moving them onto
    # hardened_get_json is the proposed follow-up; adding a hop here without updating the plan fails this test.
    third_party = _imported_roots(TREE) & {"pyalex", "arxiv"}
    assert third_party == {"pyalex", "arxiv"}
    dl_calls = {n.func.attr for n in ast.walk(TREE) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "_dl"}
    assert dl_calls == {"is_reliable_pdf_url", "select_openalex_oa_pdfs", "rewrite_oa_pdf_url", "fetch_unpaywall_oa_pdfs"}
    fetching = {name for name in dl_calls if name.startswith("fetch_")}
    assert fetching == {"fetch_unpaywall_oa_pdfs"} and callable(getattr(dl, "fetch_unpaywall_oa_pdfs"))
