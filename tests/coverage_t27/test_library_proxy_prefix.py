"""``F2W_LIBRARY_PROXY_PREFIX``: validated at API startup; ``open_url`` on every candidate; no request is ever made."""
from __future__ import annotations

from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api_v1"))

from _v1_support import build_local  # noqa: E402

from app.modules.jobs.keys import OperationInputs
from app.modules.library.harvest import Candidate
from app.modules.library.service import LibraryService
from app.modules.literature.openalex import QuotaBreaker
from app.modules.security.authorization import StartupConfigError
from tests.library.test_library_service import FakeProbe
from tests.security.security_fakes import local_settings

RUN = OperationInputs(corpus_scope=("rsoxs",), params={"topic": "rsoxs"}).job_identity("library_harvest")
PREFIX = "https://ezproxy.example.edu/login?url="


def _build(monkeypatch, tmp_path):
    lib = tmp_path / "papers"
    lib.mkdir()
    service = LibraryService(lib, tmp_path / "state" / "catalog.sqlite", settings=local_settings(), probe=FakeProbe(),
                             breaker=QuotaBreaker())
    return build_local(monkeypatch, tmp_path, library=service)


def _candidates(app):
    harvester = app.app.state.f2w_harvest
    harvester.store.create_run(RUN, topic="rsoxs", category="rsoxs", schema=None, sources=["arxiv"], max_downloads=3)
    harvester.store.save_candidate(RUN, Candidate(title="With a DOI", source="openalex", year=2020, doi="10.1/p",
                                                  landing_url="https://example.org/landing"), status="paywalled")
    harvester.store.save_candidate(RUN, Candidate(title="Without", source="arxiv", year=2020, arxiv_id="2101.00001",
                                                  landing_url="https://arxiv.org/abs/2101.00001"), status="blocked")
    harvester.store.save_candidate(RUN, Candidate(title="Nothing to open", source="arxiv", year=2020), status="failed")
    return {c["title"]: c for c in app.client().get(f"/v1/library/harvest/runs/{RUN}/candidates").json()["candidates"]}


def test_without_a_prefix_open_url_is_the_plain_target(monkeypatch, tmp_path):
    monkeypatch.delenv("F2W_LIBRARY_PROXY_PREFIX", raising=False)
    got = _candidates(_build(monkeypatch, tmp_path))
    assert got["With a DOI"]["open_url"] == "https://doi.org/10.1/p"
    assert got["Without"]["open_url"] == "https://arxiv.org/abs/2101.00001"
    assert got["Nothing to open"]["open_url"] is None


def test_with_a_prefix_open_url_is_prefix_plus_target_and_nothing_is_requested(monkeypatch, tmp_path):
    monkeypatch.setenv("F2W_LIBRARY_PROXY_PREFIX", PREFIX)
    app = _build(monkeypatch, tmp_path)

    def refuse(*a, **k):
        raise AssertionError("the server must not request a publisher or the proxy")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    got = _candidates(app)
    assert got["With a DOI"]["open_url"] == PREFIX + "https://doi.org/10.1/p"
    assert got["Without"]["open_url"] == PREFIX + "https://arxiv.org/abs/2101.00001"
    assert got["Nothing to open"]["open_url"] is None
    assert app.app.state.f2w_harvest.proxy_prefix == PREFIX


@pytest.mark.parametrize("bad", ["http://ezproxy.example.edu/login?url=", "https://u:secret-pw@ezproxy.example.edu/login?url=",
                                 "https://ezproxy.example.edu/login?url", "https://ezproxy.example.edu/a b=", "https://x/" + "y" * 300])
def test_an_invalid_prefix_stops_the_api_naming_the_variable_but_never_the_value(monkeypatch, tmp_path, bad):
    monkeypatch.setenv("F2W_LIBRARY_PROXY_PREFIX", bad)
    with pytest.raises(StartupConfigError) as err:
        _build(monkeypatch, tmp_path)
    text = str(err.value)
    assert text.startswith("config_rejected:") and "F2W_LIBRARY_PROXY_PREFIX" in text
    assert "secret-pw" not in text and "ezproxy" not in text
