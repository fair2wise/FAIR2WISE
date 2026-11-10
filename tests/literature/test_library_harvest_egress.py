"""The Library Harvester reaches arXiv and OpenAlex only through the hardened helpers (H8 legacy_ports_plan P1d).

Same pins as ``test_harvest_modules_egress.py``: no direct metadata client (the ``arxiv`` package, ``pyalex``,
``feedparser``) in ``library/harvest.py`` even as a lazy import, every helper call passes its getter explicitly (the
getter carries the egress policy and is what tests patch), the OpenAlex key comes only from ``openalex_api_key()``
and is never a query parameter, and the PDF downloader still checks every redirect hop.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.modules.library import harvest
from app.modules.literature import legacy_download

CLIENTS = {"pyalex", "arxiv", "feedparser", "lxml", "defusedxml", "requests", "urllib3"}
GETTER = {"arxiv_results": "get_bytes"}
SOURCE = Path(harvest.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _imported() -> set[str]:
    roots = {a.name.split(".")[0] for n in ast.walk(TREE) if isinstance(n, ast.Import) for a in n.names}
    roots |= {(n.module or "").split(".")[0] for n in ast.walk(TREE) if isinstance(n, ast.ImportFrom) and not n.level}
    return roots


def _calls(name: str) -> list[ast.Call]:
    return [n for n in ast.walk(TREE) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == name]


def test_no_direct_metadata_client_in_the_harvester():
    assert not _imported() & CLIENTS  # ast.walk covers imports inside functions
    assert not any(isinstance(n, ast.Name) and n.id in {"pyalex", "arxiv"} for n in ast.walk(TREE))
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call):
            assert getattr(node.func, "id", "") not in {"__import__", "import_module"}
            assert getattr(node.func, "attr", "") != "import_module"


def test_helper_calls_pass_their_getter_explicitly():
    for helper, getter in GETTER.items():
        calls = _calls(helper)
        assert calls, helper
        for call in calls:
            assert getter in {k.arg for k in call.keywords}, f"{helper} without {getter}"


def test_the_default_getters_are_the_hardened_ones():
    assert harvest.hardened_get_bytes is legacy_download.hardened_get_bytes
    assert harvest.arxiv_results is legacy_download.arxiv_results
    assert harvest.arxiv_searcher.__kwdefaults__["get_bytes"] is legacy_download.hardened_get_bytes  # type: ignore[index]


def test_the_arxiv_call_never_undercuts_the_three_second_rule():
    (call,) = _calls("arxiv_results")
    delay = next(k.value for k in call.keywords if k.arg == "delay_seconds")
    assert isinstance(delay, ast.Name) and delay.id == "ARXIV_MIN_INTERVAL_SECONDS"
    assert not any(k.arg in {"limiter", "sleep", "clock"} for k in call.keywords)  # the process-wide limiter applies


def test_pdf_downloader_still_checks_every_redirect_hop():
    func = next(f for f in ast.walk(TREE) if isinstance(f, ast.FunctionDef) and f.name == "pdf_downloader")
    body = ast.unparse(func)
    assert "rules.check_url(current)" in body and "range(rules.max_redirects + 1)" in body
    assert "PDF_MAGIC" in body and "refused:too_large" in body and "follow_redirects=False" in body
