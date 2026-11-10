"""``OPENALEX_API_KEY`` (owner decision 2026-11-09): header-only, process environment only, never in a URL, log or repr.

Every OpenAlex caller on the hardened helpers is driven with the key exported and without it, against a recording
``httpx.MockTransport``. Tripwires: the key never appears in any request URL, in any log record (DEBUG and up), in the
``repr`` of the agent or query objects, or in a ``.env`` that was not exported. No network.
"""

from __future__ import annotations

from functools import partial
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.modules import dotenv_entry
from app.modules.commands import harvest_rsoxs as hv
from app.modules.f2w_agent import api as api_mod
from app.modules.f2w_agent import download_agent as da
from app.modules.literature import download_pdfs as dp
from app.modules.literature import legacy_download as ld
from app.modules.literature.legacy_download import OPENALEX_API_KEY_ENV, hardened_get_json, openalex_api_key
from app.modules.literature.safe_fetch import NetworkPolicy

PUBLIC = NetworkPolicy(resolver=lambda host: ["93.184.216.34"])
KEY = "oa-secret-key-XYZ987"
WORKS = b'{"meta": {"next_cursor": null}, "results": [{"id": "https://openalex.org/W1", "title": "T", "doi": "https://doi.org/10.1/x"}]}'
WORK = b'{"id": "https://openalex.org/W1", "title": "T", "doi": "https://doi.org/10.1/x", "open_access": {"oa_url": null}}'


class Recorder:
    def __init__(self, body: bytes = WORKS):
        self.body, self.requests = body, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, content=self.body)

    def getter(self) -> Any:
        return partial(hardened_get_json, client=httpx.Client(transport=httpx.MockTransport(self.handler)), policy=PUBLIC)


def test_openalex_api_key_reads_the_process_environment_at_call_time(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(OPENALEX_API_KEY_ENV, raising=False)
    assert openalex_api_key() is None
    monkeypatch.setenv(OPENALEX_API_KEY_ENV, "   ")
    assert openalex_api_key() is None
    monkeypatch.setenv(OPENALEX_API_KEY_ENV, f" {KEY} ")
    assert openalex_api_key() == KEY
    assert openalex_api_key({}) is None and openalex_api_key({OPENALEX_API_KEY_ENV: "k"}) == "k"


def _drive(name: str, rec: Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> object:
    """Run one caller path over ``rec``; return the object whose ``repr`` must not leak the key."""
    if name == "agent_search":
        monkeypatch.setattr(da, "hardened_get_json", rec.getter())
        agent = da.DownloadAgent(backend="ollama", model="test", download_delay_seconds=0, mailto="curator@lbl.gov")
        assert agent._search_one("graphene", 5)
        return agent
    if name == "agent_refresh":
        rec.body = WORK
        monkeypatch.setattr(da, "hardened_get_json", rec.getter())
        agent = da.DownloadAgent(backend="ollama", model="test", download_delay_seconds=0, mailto=None)
        agent._refresh_candidate_urls({"id": "https://openalex.org/W1", "title": "T"})
        return agent
    if name == "api_publications":
        monkeypatch.setattr(api_mod, "hardened_get_json", rec.getter())
        assert api_mod._search_openalex_publications("graphene", 3)
        return api_mod
    if name == "harvest_get":
        monkeypatch.setattr(hv, "hardened_get_json", rec.getter())
        query = hv.Works("curator@lbl.gov").search("graphene")
        assert query.get(per_page=5)
        return query
    if name == "harvest_paginate":
        monkeypatch.setattr(hv, "hardened_get_json", rec.getter())
        query = hv.Authors().search("Doe")
        assert list(query.paginate(per_page=5))
        return query
    if name == "harvest_item":
        rec.body = WORK
        monkeypatch.setattr(hv, "hardened_get_json", rec.getter())
        query = hv.Works()
        assert query["W1"]
        return query
    if name == "harvest_doi_urls":
        rec.body = WORK
        monkeypatch.setattr(hv, "hardened_get_json", rec.getter())
        hv._openalex_oa_urls_for_doi("10.1/x", "curator@lbl.gov")
        return hv
    if name == "download_pdfs_workflow":
        monkeypatch.setattr(ld, "hardened_get_json", rec.getter())
        monkeypatch.setattr(dp, "download_pdf", lambda url, dest: True)
        monkeypatch.setattr(dp.time, "sleep", lambda s: None)
        dp.run_openalex_workflow("graphene", str(tmp_path), 1)
        return dp
    raise AssertionError(name)


PATHS = ["agent_search", "agent_refresh", "api_publications", "harvest_get", "harvest_paginate", "harvest_item",
         "harvest_doi_urls", "download_pdfs_workflow"]


@pytest.mark.parametrize("name", PATHS)
def test_exported_key_travels_as_the_bearer_header_only(name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                       caplog: pytest.LogCaptureFixture):
    monkeypatch.setenv(OPENALEX_API_KEY_ENV, KEY)
    monkeypatch.setenv("OPENALEX_EMAIL", "curator@lbl.gov")
    rec = Recorder()
    with caplog.at_level(logging.DEBUG):
        holder = _drive(name, rec, monkeypatch, tmp_path)
    assert rec.requests, name
    for request in rec.requests:
        assert KEY not in str(request.url) and "api_key" not in str(request.url)  # never a query parameter
        if request.url.host == "api.openalex.org":
            assert request.headers["authorization"] == f"Bearer {KEY}"
        else:  # Unpaywall (refresh path): the key is OpenAlex's, never sent anywhere else
            assert "authorization" not in request.headers
    assert any(r.url.host == "api.openalex.org" for r in rec.requests)
    assert KEY not in caplog.text
    assert KEY not in repr(holder) and KEY not in str(vars(holder) if hasattr(holder, "__dict__") else holder)


@pytest.mark.parametrize("name", PATHS)
def test_without_the_export_no_authorization_header_is_sent(name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv(OPENALEX_API_KEY_ENV, raising=False)
    monkeypatch.setenv("OPENALEX_EMAIL", "curator@lbl.gov")
    rec = Recorder()
    _drive(name, rec, monkeypatch, tmp_path)
    assert rec.requests and all("authorization" not in r.headers for r in rec.requests)


def test_key_is_read_per_request_so_an_export_after_construction_is_honoured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(OPENALEX_API_KEY_ENV, raising=False)
    rec = Recorder()
    monkeypatch.setattr(hv, "hardened_get_json", rec.getter())
    query = hv.Works().search("x")
    query.get(per_page=2)
    monkeypatch.setenv(OPENALEX_API_KEY_ENV, KEY)
    query.get(per_page=2)
    assert "authorization" not in rec.requests[0].headers and rec.requests[1].headers["authorization"] == f"Bearer {KEY}"
    assert str(rec.requests[0].url) == str(rec.requests[1].url)  # the same request, plus the header


def test_a_dotenv_file_never_supplies_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_file = tmp_path / ".env"
    env_file.write_text(f"{OPENALEX_API_KEY_ENV}=from-the-file\nOPENALEX_EMAIL=file@lbl.gov\n")
    monkeypatch.setenv(dotenv_entry.DOTENV_VAR, str(env_file))
    monkeypatch.delenv(OPENALEX_API_KEY_ENV, raising=False)
    monkeypatch.delenv("OPENALEX_EMAIL", raising=False)
    assert dotenv_entry.load_entrypoint_dotenv(tmp_path) == env_file
    assert OPENALEX_API_KEY_ENV not in dotenv_entry.os.environ and openalex_api_key() is None
    assert dotenv_entry.os.environ["OPENALEX_EMAIL"] == "file@lbl.gov"  # other values load as before
    monkeypatch.setenv(OPENALEX_API_KEY_ENV, KEY)  # exported: kept, the file's value does not override it
    assert dotenv_entry.load_entrypoint_dotenv(tmp_path) == env_file
    assert openalex_api_key() == KEY
    assert OPENALEX_API_KEY_ENV in dotenv_entry.PROCESS_ONLY_VARS


def test_no_caller_reads_the_environment_name_itself():
    # the helper is the single reader: no module spells the variable name or reads it from os.environ directly
    for module in (da, api_mod, hv, dp):
        text = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
        assert "OPENALEX_API_KEY" not in text, module.__name__
