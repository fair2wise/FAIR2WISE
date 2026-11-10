"""``harvest_rsoxs``, ``download_pdfs`` and the f2w API reach OpenAlex, arXiv and Unpaywall only through the hardened helpers.

Extends ``test_download_agent_egress.py`` (T17B/H8 legacy_ports_plan P1 follow-on): a direct client (``pyalex``, the
``arxiv`` package, ``feedparser``) reappearing in any of these modules, including as a lazy import inside a function,
fails here; so does a helper call without its getter argument (the getter is what the tests patch and what carries
the egress policy), and a new ``requests`` call site in ``download_pdfs`` (the known oracle/legacy set is pinned).
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.modules.commands import harvest_rsoxs
from app.modules.f2w_agent import api
from app.modules.literature import download_pdfs, legacy_download

MODULES = {"harvest_rsoxs": harvest_rsoxs, "download_pdfs": download_pdfs, "api": api}
CLIENTS = {"pyalex", "arxiv", "feedparser", "lxml", "defusedxml"}
GETTER = {"openalex_get": "get_json", "openalex_pages": "get_json", "openalex_work": "get_json",
          "openalex_search": "get_json", "unpaywall_oa_pdfs": "get_json", "arxiv_results": "get_bytes",
          "arxiv_search": "get_bytes"}


def _tree(module: object) -> ast.AST:
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))  # type: ignore[attr-defined]


def _imported(tree: ast.AST) -> set[str]:
    roots = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    roots |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and not n.level}
    return roots


def test_no_direct_metadata_client_in_any_harvest_module():
    for name, module in MODULES.items():
        tree = _tree(module)
        assert not _imported(tree) & CLIENTS, name  # ast.walk covers imports inside functions
        assert not any(isinstance(n, ast.Name) and n.id in {"pyalex", "pyalex_config", "arxiv"} for n in ast.walk(tree)), name
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                assert getattr(fn, "id", "") not in {"__import__", "import_module"}, name
                assert getattr(fn, "attr", "") != "import_module", name


def test_helper_calls_pass_their_getter_explicitly():
    seen: set[str] = set()
    for name, module in (("harvest_rsoxs", harvest_rsoxs), ("download_pdfs", download_pdfs), ("api", api)):
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in GETTER:
                helper = node.func.id  # type: ignore[attr-defined]
                seen.add(helper)
                assert GETTER[helper] in {k.arg for k in node.keywords}, f"{name}: {helper} without {GETTER[helper]}"
    assert seen >= {"openalex_get", "openalex_pages", "openalex_work", "unpaywall_oa_pdfs", "arxiv_results", "openalex_search"}


OPENALEX_HELPERS = {"openalex_get", "openalex_pages", "openalex_work", "openalex_search"}


def _is_key_call(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "id", "") == "openalex_api_key" and not node.args


def test_openalex_calls_pass_the_exported_key_through_the_helper_only():
    # Owner decision 2026-11-09: the key comes from the process environment through legacy_download.openalex_api_key()
    # at request time, as the api_key argument (the helper sends it as the Authorization header; never a URL parameter).
    seen: set[str] = set()
    for name, module in MODULES.items():
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in OPENALEX_HELPERS:
                seen.add(node.func.id)  # type: ignore[attr-defined]
                keyed = [k.value for k in node.keywords if k.arg == "api_key"]
                assert keyed and _is_key_call(keyed[0]), f"{name}: {node.func.id} without api_key=openalex_api_key()"  # type: ignore[attr-defined]
        assert "OPENALEX_API_KEY" not in Path(module.__file__).read_text(encoding="utf-8"), name  # type: ignore[attr-defined]
    assert seen == OPENALEX_HELPERS
    assert harvest_rsoxs.openalex_api_key is api.openalex_api_key is legacy_download.openalex_api_key


def test_the_getters_are_the_hardened_ones():
    assert harvest_rsoxs.hardened_get_json is legacy_download.hardened_get_json
    assert harvest_rsoxs.hardened_get_bytes is legacy_download.hardened_get_bytes
    assert api.hardened_get_json is legacy_download.hardened_get_json
    assert harvest_rsoxs.unpaywall_oa_pdfs is legacy_download.unpaywall_oa_pdfs


def test_harvest_no_longer_uses_the_requests_unpaywall_fetch():
    calls = {n.func.attr for n in ast.walk(_tree(harvest_rsoxs)) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id == "dl"}
    assert "fetch_unpaywall_oa_pdfs" not in calls
    assert "requests" not in _imported(_tree(harvest_rsoxs)) and "requests" not in _imported(_tree(api))


def test_requests_call_sites_in_download_pdfs_are_the_known_legacy_set():
    # fetch_unpaywall_oa_pdfs and download_pdf are the parity oracles; _oa_get/_oa_post carry the OSTI/CORE/Europe PMC/
    # Crossref/Semantic Scholar lookups that are outside the pyalex/arxiv/Unpaywall port (listed in the plan).
    users = {f.name for f in _tree(download_pdfs).body if isinstance(f, ast.FunctionDef)
             and any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "requests"
                     for n in ast.walk(f))}
    assert users == {"fetch_unpaywall_oa_pdfs", "_oa_get", "_oa_post", "download_pdf"}
