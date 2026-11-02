"""T29 B29: the legacy DownloadAgent's transport is the hardened T05 fetcher, with parity on the download fixtures.

Each scenario (the fixtures of ``tests/test_download_pdfs.py`` plus failure modes) is run through the
legacy ``download_pdfs.download_pdf`` (``requests`` faked) and through ``hardened_download_pdf``
(``httpx.MockTransport`` and a fixed public resolver): same boolean, same bytes at ``dest``, no leftovers.
No network and no real sleeping.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.modules.f2w_agent import download_agent as mod
from app.modules.f2w_agent.download_agent import DownloadAgent
from app.modules.literature import download_pdfs
from app.modules.literature.legacy_download import hardened_download_pdf
from app.modules.literature.safe_fetch import NetworkPolicy, SafeFetcher

PDF = b"%PDF-1.4\nbody"
URL = "https://arxiv.org/pdf/2401.00001"


class LegacyResponse:
    """The ``requests`` response shape of tests/test_download_pdfs.py."""

    def __init__(self, chunks: list[bytes], *, content_type: str = "application/pdf", status: int = 200) -> None:
        self._chunks, self.status = chunks, status
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise download_pdfs.requests.HTTPError(str(self.status))

    def iter_content(self, size: int):
        yield from self._chunks


#: name -> (chunks, content_type, status, expected download result)
SCENARIOS: dict[str, tuple[list[bytes], str, int, bool]] = {
    "valid_pdf_split_magic": ([b"%P", b"DF-1.4\nbody"], "application/pdf", 200, True),
    "valid_pdf_wrong_content_type": ([PDF], "application/octet-stream", 200, True),
    "html_landing_page": ([b"<html><title>Article landing page</title></html>"], "text/html", 200, False),
    "empty_body": ([], "application/pdf", 200, False),
    "truncated_magic": ([b"%PD"], "application/pdf", 200, False),
    "http_404": ([b"not found"], "text/html", 404, False),
    "http_500": ([b"oops"], "text/html", 500, False),
}


def run_legacy(tmp: Path, monkeypatch: pytest.MonkeyPatch, scenario: str) -> tuple[bool, bytes | None, list[str]]:
    chunks, ctype, status, _ = SCENARIOS[scenario]
    monkeypatch.setattr(download_pdfs.requests, "get",
                        lambda *a, **k: LegacyResponse(chunks, content_type=ctype, status=status))
    monkeypatch.setattr(download_pdfs.time, "sleep", lambda s: None)
    dest = tmp / "paper.pdf"
    ok = download_pdfs.download_pdf(URL, str(dest))
    return ok, dest.read_bytes() if dest.exists() else None, sorted(p.name for p in tmp.iterdir())


def fetcher_for(scenario: str, calls: list[str] | None = None) -> SafeFetcher:
    chunks, ctype, status, _ = SCENARIOS[scenario]

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(status, content=b"".join(chunks), headers={"Content-Type": ctype})

    policy = NetworkPolicy(resolver=lambda host: ["93.184.216.34"])
    return SafeFetcher(httpx.Client(transport=httpx.MockTransport(handler)), policy)


def run_hardened(tmp: Path, scenario: str) -> tuple[bool, bytes | None, list[str]]:
    dest = tmp / "paper.pdf"
    ok = hardened_download_pdf(URL, str(dest), fetcher=fetcher_for(scenario), sleep=lambda s: None)
    return ok, dest.read_bytes() if dest.exists() else None, sorted(p.name for p in tmp.iterdir())


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_download_parity_with_the_legacy_downloader(scenario: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    legacy_dir, hard_dir = tmp_path / "legacy", tmp_path / "hardened"
    legacy_dir.mkdir()
    hard_dir.mkdir()
    legacy = run_legacy(legacy_dir, monkeypatch, scenario)
    hardened = run_hardened(hard_dir, scenario)
    assert hardened == legacy
    assert hardened[0] is SCENARIOS[scenario][3]
    assert not any(name.endswith(".part") for name in hardened[2])


def test_network_error_returns_false_without_a_file(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    fetcher = SafeFetcher(httpx.Client(transport=httpx.MockTransport(handler)),
                          NetworkPolicy(resolver=lambda host: ["93.184.216.34"]))
    pauses: list[float] = []
    assert hardened_download_pdf(URL, str(tmp_path / "p.pdf"), fetcher=fetcher, sleep=pauses.append) is False
    assert not (tmp_path / "p.pdf").exists() and pauses == [5.0, 5.0]   # retried like the legacy loop


def test_http_failure_is_retried_but_a_non_pdf_answer_is_not(tmp_path: Path):
    calls: list[str] = []
    assert not hardened_download_pdf(URL, str(tmp_path / "a.pdf"), fetcher=fetcher_for("http_500", calls),
                                     sleep=lambda s: None)
    assert len(calls) == 3
    calls.clear()
    assert not hardened_download_pdf(URL, str(tmp_path / "b.pdf"), fetcher=fetcher_for("html_landing_page", calls),
                                     sleep=lambda s: None)
    assert len(calls) == 1


def test_hardening_blocks_what_the_legacy_downloader_would_fetch(tmp_path: Path):
    calls: list[str] = []
    private = SafeFetcher(httpx.Client(transport=httpx.MockTransport(lambda r: calls.append("x") or httpx.Response(200))),
                          NetworkPolicy(resolver=lambda host: ["10.0.0.5"]))
    assert not hardened_download_pdf("https://arxiv.org/pdf/1", str(tmp_path / "p.pdf"), fetcher=private,
                                     sleep=lambda s: None)
    assert calls == [] and not (tmp_path / "p.pdf").exists()

    def redirect(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8011/internal.pdf"})

    redirecting = SafeFetcher(httpx.Client(transport=httpx.MockTransport(redirect)),
                              NetworkPolicy(resolver=lambda host: ["93.184.216.34"]))
    assert not hardened_download_pdf(URL, str(tmp_path / "q.pdf"), fetcher=redirecting, sleep=lambda s: None)
    assert calls == [URL]                                    # the private hop was never requested


def agent_run(tmp: Path, downloader: Any) -> tuple[dict[str, Any], list[str]]:
    agent = DownloadAgent(backend="ollama", model="test", download_delay_seconds=0, validate_downloads=False)
    candidates = [
        {"id": "W1", "doi": "https://doi.org/10.1234/ok", "title": "Good", "pdf_urls": [URL], "score": 0.9},
        {"id": "W2", "doi": "https://doi.org/10.1234/bad", "title": "Landing", "pdf_urls": ["https://arxiv.org/pdf/2401.00002"], "score": 0.5},
    ]
    result = agent._download_candidates("q", [], str(tmp / "pdfs"), candidates, max_papers=2)
    manifest = [json.loads(line)["status"] for line in Path(result["manifest"]).read_text().splitlines()]
    return result, manifest


def test_download_agent_results_are_unchanged_by_the_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def legacy(url: str, dest: str) -> bool:
        scenario = "valid_pdf_split_magic" if url == URL else "html_landing_page"
        chunks, ctype, status, _ = SCENARIOS[scenario]
        monkeypatch.setattr(download_pdfs.requests, "get", lambda *a, **k: LegacyResponse(chunks, content_type=ctype))
        monkeypatch.setattr(download_pdfs.time, "sleep", lambda s: None)
        return download_pdfs.download_pdf(url, dest)

    def hardened(url: str, dest: str) -> bool:
        scenario = "valid_pdf_split_magic" if url == URL else "html_landing_page"
        return hardened_download_pdf(url, dest, fetcher=fetcher_for(scenario), sleep=lambda s: None)

    monkeypatch.setattr(mod, "hardened_download_pdf", legacy)
    (tmp_path / "a").mkdir()
    legacy_result, legacy_manifest = agent_run(tmp_path / "a", legacy)
    monkeypatch.setattr(mod, "hardened_download_pdf", hardened)
    (tmp_path / "b").mkdir()
    hard_result, hard_manifest = agent_run(tmp_path / "b", hardened)

    def strip(result: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in result.items() if k not in {"downloaded", "target_dir", "manifest"}}

    assert strip(hard_result) == strip(legacy_result)
    assert hard_manifest == legacy_manifest == ["downloaded", "failed"]
    assert [Path(p).name for p in hard_result["downloaded"]] == [Path(p).name for p in legacy_result["downloaded"]]
    assert all(Path(p).read_bytes() == b"%PDF-1.4\nbody" for p in hard_result["downloaded"])


def test_the_agent_uses_the_hardened_downloader_by_default():
    assert mod.hardened_download_pdf is hardened_download_pdf
    assert "download_pdfs.requests" not in Path(mod.__file__).read_text()
