"""Harvest fetches, Tiled requests and library operations, counted at their choke points."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import requests

from app.modules import metrics
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.graph.backends.tiled_client import TiledGraphClient
from app.modules.graph.backends.tiled_mutations import TiledMutationClient
from app.modules.metrics.ops import fetch_outcome, operation_outcome
from tests.library.test_library_harvest import PAPERS, SERVED, Net, World, boom, fixed
from tests.library.test_library_service import make, pdf

from .conftest import points


class FakeResp:
    def __init__(self, status: int, body: object) -> None:
        self.status_code, self._body = status, body

    def json(self) -> object:
        return self._body


class FakeSession:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)

    def post(self, url: str, **kw: Any) -> Any:  # noqa: ANN401
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def counts(reader: Any, name: str, *keys: str) -> dict[tuple[str, ...], int]:  # noqa: ANN401
    return {tuple(p.attributes[k] for k in keys): p.value for p in points(reader, name)}


# -- harvest -------------------------------------------------------------------------------------------------


def test_harvest_counts_searches_and_downloads_by_source_and_outcome(tmp_path: Path, reader: Any) -> None:  # noqa: ANN401
    net = Net({**SERVED, "https://oa.example/b.pdf": "refused:not_pdf", "https://oa.example/c.pdf": "blocked:private"})
    world = World(tmp_path, {"arxiv": fixed(*PAPERS), "openalex": boom}, net)
    world.run()
    got = counts(reader, "f2w.harvest.fetches", "source", "outcome")
    assert got[("arxiv", "ok")] == 1 and got[("openalex", "failed")] == 1
    assert got[("pdf", "ok")] == 1 and got[("pdf", "refused")] == 1 and got[("pdf", "blocked")] == 1
    blob = repr(points(reader, "f2w.harvest.fetches"))
    for needle in ("oa.example", "10.1/", "Resonant"):
        assert needle not in blob


def test_fetch_outcome_mapping() -> None:
    assert [fetch_outcome(s) for s in ("ok", "blocked:x", "refused:too_large", "failed:http_404", "weird")] == [
        "ok", "blocked", "refused", "failed", "failed"]
    metrics.record_harvest_fetch(source="https://x/y?z=1", outcome="10.1000/abc")  # off: a no-op, never raises


def test_unknown_source_and_outcome_become_other(reader: Any) -> None:  # noqa: ANN401
    metrics.record_harvest_fetch(source="https://evil.example/?q=1", outcome="10.1000/abc")
    assert counts(reader, "f2w.harvest.fetches", "source", "outcome") == {("other", "other"): 1}


# -- tiled ---------------------------------------------------------------------------------------------------


def tiled(session: FakeSession, cls: type = TiledGraphClient) -> Any:  # noqa: ANN401
    return cls("http://127.0.0.1:9", profile="test", session=session)


def test_tiled_reads_and_writes_are_counted_with_outcomes(reader: Any) -> None:  # noqa: ANN401
    ok = FakeResp(200, {"data": {"entity": None}})
    client = tiled(FakeSession(ok, FakeResp(503, {}), requests.ConnectionError("down"), FakeResp(403, {})))
    client.query("query { entity(id: \"x\") { id } }")
    for _ in range(3):
        with pytest.raises(ContractError):
            client.query("query { entity(id: \"x\") { id } }")
    writer = tiled(FakeSession(ok, FakeResp(200, {"errors": [{"message": "boom"}]})), TiledMutationClient)
    writer.mutate("mutation { x }", {})
    with pytest.raises(ContractError):
        writer.mutate("mutation { x }", {})
    assert counts(reader, "f2w.tiled.operations", "operation", "outcome") == {
        ("read", "ok"): 1, ("read", "unavailable"): 2, ("read", "denied"): 1,
        ("write", "ok"): 1, ("write", "error"): 1}


def test_tiled_decorator_keeps_behaviour_when_off() -> None:
    assert not metrics.is_enabled()
    assert tiled(FakeSession(FakeResp(200, {"data": {"a": 1}}))).query("query { a }") == {"a": 1}


# -- library -------------------------------------------------------------------------------------------------


def test_library_operations_are_counted(tmp_path: Path, reader: Any) -> None:  # noqa: ANN401
    service, _ = make(tmp_path)
    service.create_category("rsoxs")
    service.upload("a.pdf", pdf("alpha"), "rsoxs")
    with pytest.raises(Exception):  # noqa: B017, PT011 - an invalid upload (not a PDF) is refused
        service.upload("b.pdf", b"not a pdf", "rsoxs")
    got = counts(reader, "f2w.library.operations", "operation", "outcome")
    assert got[("create_category", "ok")] == 1 and got[("upload", "ok")] == 1
    assert sum(v for (op, out), v in got.items() if op == "upload" and out != "ok") == 1
    assert all(op in {"refresh", "upload", "create_category", "verify_paper", "verify_all"} for op, _ in got)
    assert "a.pdf" not in repr(points(reader, "f2w.library.operations"))


@pytest.mark.parametrize("error,expected", [
    (None, "ok"), (ContractError(ErrorCode.DENIED, "x"), "denied"), (ContractError(ErrorCode.UNAVAILABLE, "x"), "unavailable"),
    (ContractError(ErrorCode.INVALID_INPUT, "x"), "invalid"), (ContractError(ErrorCode.INTERNAL, "x"), "error"),
    (RuntimeError("x"), "error"),
])
def test_operation_outcome(error: BaseException | None, expected: str) -> None:
    assert operation_outcome(error) == expected
