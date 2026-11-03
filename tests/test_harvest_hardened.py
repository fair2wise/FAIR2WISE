"""The harvest scripts download and look up metadata only through the hardened transport (no network).

Owner decision 2026-11-03 (T17B): ``scripts/harvest_*.py`` move onto the hardened downloader. The
transport behaviour itself is covered by ``tests/literature/test_safe_fetch.py``,
``tests/test_download_parity.py`` and ``tests/literature/test_legacy_download_json.py``; these tests pin
that the scripts reach it.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import harvest_rsoxs as hr  # noqa: E402
import harvest_tomo as ht  # noqa: E402
import harvest_xray as hx  # noqa: E402

from app.modules.literature import legacy_download  # noqa: E402
from app.modules.literature.safe_fetch import NetworkPolicy, SafeFetcher  # noqa: E402

SCRIPTS = [ROOT / "scripts" / f"harvest_{n}.py" for n in ("rsoxs", "xray", "tomo")]
PDF = b"%PDF-1.4\nbody"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_no_private_http_code_remains(script: Path):
    text = script.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imported & {"requests", "httpx", "urllib3", "http", "socket"}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not attrs & {"urlopen", "urlretrieve", "Request"}, "urllib.request is not used by the scripts"
    # ``dl.download_pdf`` is the legacy parity oracle; the scripts must not call it
    assert "dl.download_pdf(" not in text


def test_scripts_resolve_to_the_hardened_downloader():
    assert hr.download_pdf is legacy_download.hardened_download_pdf
    assert hr.hardened_get_json is legacy_download.hardened_get_json
    assert hx.hr is hr and ht.hr is hr


def _install(monkeypatch: pytest.MonkeyPatch, handler, addresses=("93.184.216.34",)) -> None:
    fetcher = SafeFetcher(httpx.Client(transport=httpx.MockTransport(handler)),
                          NetworkPolicy(resolver=lambda host: list(addresses)))
    monkeypatch.setattr(legacy_download, "default_fetcher", lambda policy=None: fetcher)
    monkeypatch.setattr(legacy_download.time, "sleep", lambda s: None)


def test_download_saves_a_valid_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, lambda r: httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"}))
    dest = tmp_path / "a.pdf"
    assert hr.download_pdf("https://arxiv.org/pdf/2401.00001", str(dest)) is True
    assert dest.read_bytes() == PDF and sorted(p.name for p in tmp_path.iterdir()) == ["a.pdf"]


@pytest.mark.parametrize("url, addresses", [
    ("https://arxiv.org/pdf/1", ("10.1.2.3",)),
    ("https://169.254.169.254/latest/meta-data", ("169.254.169.254",)),
    ("http://127.0.0.1:8011/internal.pdf", ("127.0.0.1",)),
    ("file:///etc/passwd", ("93.184.216.34",)),
    ("https://user:pw@arxiv.org/pdf/1", ("93.184.216.34",)),
])
def test_internal_and_unsafe_urls_are_refused_without_a_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                url: str, addresses: tuple[str, ...]):
    calls: list[str] = []
    _install(monkeypatch, lambda r: calls.append(str(r.url)) or httpx.Response(200, content=PDF), addresses)
    dest = tmp_path / "b.pdf"
    assert hr.download_pdf(url, str(dest)) is False
    assert calls == [] and not dest.exists()


def test_redirect_to_internal_host_is_not_followed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/x.pdf"})

    _install(monkeypatch, handler)
    assert hr.download_pdf("https://arxiv.org/pdf/2", str(tmp_path / "c.pdf")) is False
    assert calls == ["https://arxiv.org/pdf/2"]


def test_html_landing_page_is_rejected_without_leftovers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, lambda r: httpx.Response(200, content=b"<html>paywall</html>"))
    assert hr.download_pdf("https://arxiv.org/pdf/3", str(tmp_path / "d.pdf")) is False
    assert list(tmp_path.iterdir()) == []


def test_metadata_lookups_go_through_the_policy(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []

    def fake(url: str, *, headers=None, **kw):
        seen.append(url)
        raise legacy_download.JSONFetchError("blocked URL (private_address)")

    monkeypatch.setattr(hr, "hardened_get_json", fake)
    assert hr._crossref_by_doi_or_title("10.1021/acs.macromol.8b02198") is None   # logged, not raised
    assert seen and seen[0].startswith("https://api.crossref.org/works/")
    assert hr._openalex_oa_urls_for_doi("10.1021/x", None) == ([], None, [])
