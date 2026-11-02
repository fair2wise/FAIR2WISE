"""Job telemetry selection, port protocol conformance and the upload-limit ASGI middleware."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import upload_limits as ul
from app.modules.contracts import ports
from app.modules.contracts.jobs import JobState
from app.modules.jobs import telemetry


# ------------------------------------------------------------------ telemetry
class FakeStore:
    def __init__(self, jobs):
        self.jobs, self.calls = jobs, []

    def list_jobs(self, kind=None, limit=200):
        self.calls.append((kind, limit))
        return self.jobs


def job(job_id, state):
    return SimpleNamespace(job_id=job_id, state=state)


def test_summarize_counts_jobs_by_state_without_a_single_percentage():
    store = FakeStore([job("a", JobState.RUNNING), job("b", JobState.RUNNING), job("c", JobState.AWAITING_REVIEW),
                       job("d", JobState.FAILED)])
    out = telemetry.summarize(store, limit=7)
    assert out == {"jobs_by_state": {JobState.RUNNING.value: 2, JobState.AWAITING_REVIEW.value: 1,
                                     JobState.FAILED.value: 1}}
    assert store.calls == [(None, 7)] and telemetry.summarize(FakeStore([])) == {"jobs_by_state": {}}


def test_choose_job_prefers_running_then_most_recent_and_forwards_kind():
    store = FakeStore([job("new", JobState.AWAITING_REVIEW), job("run", JobState.RUNNING), job("old", JobState.FAILED)])
    assert telemetry.choose_job(store, kind="ingest") == "run" and store.calls == [("ingest", 50)]
    assert telemetry.choose_job(FakeStore([job("new", JobState.FAILED), job("old", JobState.FAILED)])) == "new"
    assert telemetry.choose_job(FakeStore([])) is None


# ------------------------------------------------------------------ ports
class GoodGraph:
    def capabilities(self): ...
    def get_entity(self, ctx, graph_id, entity_id): ...
    def find_entities(self, ctx, graph_id, query, *, limit): ...
    def neighborhood(self, ctx, graph_id, entity_id, *, hops, max_nodes): ...
    def apply_plan(self, ctx, plan, approval): ...


class GoodDocs:
    def put(self, ctx, data, *, media_type, metadata): ...
    def get(self, ctx, ref): ...
    def exists(self, ctx, ref): ...
    def list_refs(self, ctx, *, prefix=""): ...


def test_runtime_protocols_accept_conforming_objects_and_reject_partial_ones():
    assert isinstance(GoodGraph(), ports.GraphPort) and isinstance(GoodDocs(), ports.DocumentStore)

    class OnlyReads:
        def get_entity(self, ctx, graph_id, entity_id): ...

    assert not isinstance(OnlyReads(), ports.GraphPort)
    assert not isinstance(object(), ports.GraphPort) and not isinstance(GoodGraph(), ports.DocumentStore)


def test_explicit_subclasses_inherit_inert_protocol_defaults():
    """A class that subclasses a port and forgets a method gets ``None``, never a surprise side effect."""

    class Graph(ports.GraphPort):
        pass

    class Docs(ports.DocumentStore):
        pass

    g, d, ctx = Graph(), Docs(), object()
    assert g.capabilities() is None and g.get_entity(ctx, "g", "e") is None
    assert g.find_entities(ctx, "g", "q", limit=1) is None and g.neighborhood(ctx, "g", "e", hops=1, max_nodes=2) is None
    assert g.apply_plan(ctx, object(), object()) is None
    assert d.put(ctx, b"x", media_type="a/b", metadata={}) is None and d.get(ctx, "r") is None
    assert d.exists(ctx, "r") is None and d.list_refs(ctx) is None


def test_graph_capabilities_default_to_the_conservative_set():
    caps = ports.GraphCapabilities(backend="memory")
    assert caps.append_only_relations is True
    assert not (caps.bounded_traversal or caps.mutable_entity_properties or caps.batch_writes
                or caps.compare_and_swap or caps.owned_deletion)


# ------------------------------------------------------------------ upload limits
class Upload:
    def __init__(self, data, size=None):
        self._data, self.size, self.pos = data, size, 0

    async def read(self, n):
        chunk = self._data[self.pos:self.pos + n]
        self.pos += n
        return chunk


def run(coro):
    return asyncio.run(coro)


def test_read_upload_limited_checks_reported_size_and_streamed_bytes(monkeypatch):
    monkeypatch.setattr(ul, "_CHUNK_BYTES", 4)
    assert run(ul.read_upload_limited(Upload(b"abcdefghij", size=None), max_bytes=10)) == b"abcdefghij"
    with pytest.raises(HTTPException) as err:
        run(ul.read_upload_limited(Upload(b"x", size=11), max_bytes=10))
    assert err.value.status_code == 413
    with pytest.raises(HTTPException) as err:  # client lied about the size: the chunked read still enforces it
        run(ul.read_upload_limited(Upload(b"0123456789A", size=3), max_bytes=10))
    assert err.value.status_code == 413 and "10 byte" in err.value.detail


def test_require_pdf_allows_leading_whitespace_only():
    assert ul.require_pdf(b"\n  %PDF-1.7 data") == b"\n  %PDF-1.7 data"
    for bad in (b"<html>", b"", b"x" * 2000 + b"%PDF-"):
        with pytest.raises(HTTPException) as err:
            ul.require_pdf(bad)
        assert err.value.status_code == 415


def test_safe_child_refuses_traversal_and_hidden_names(tmp_path):
    assert ul.safe_child(tmp_path, "paper.pdf") == tmp_path.resolve() / "paper.pdf"
    for bad in ("", ".", "..", "a/b", "a\\b", "x\x00y", ".hidden", "../etc/passwd"):
        with pytest.raises(HTTPException) as err:
            ul.safe_child(tmp_path, bad)
        assert err.value.status_code == 422
    (tmp_path / "link").symlink_to(tmp_path.parent)
    with pytest.raises(HTTPException):
        ul.safe_child(tmp_path, "link")  # a symlink pointing outside the root is an escape


async def _drive(app, *, path="/upload", headers=(), chunks=(b"",), scope_type="http"):
    sent = []
    queue = list(chunks)

    async def receive():
        body = queue.pop(0) if queue else b""
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    async def send(message):
        sent.append(message)

    scope = {"type": scope_type, "path": path, "headers": list(headers), "method": "POST", "query_string": b""}
    await app(scope, receive, send)
    return sent


class Echo:
    """Wrapped app that reads the whole body, like a multipart parser would."""

    def __init__(self):
        self.read = 0

    async def __call__(self, scope, receive, send):
        while True:
            message = await receive()
            self.read += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _status(sent):
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def test_middleware_rejects_non_positive_limit():
    with pytest.raises(ValueError):
        ul.MaxBodySizeMiddleware(Echo(), max_bytes=0)


def test_middleware_refuses_declared_oversize_body_without_calling_the_app():
    inner = Echo()
    mw = ul.MaxBodySizeMiddleware(inner, max_bytes=10)
    sent = run(_drive(mw, headers=[(b"content-length", b"11")], chunks=(b"x" * 11,)))
    assert _status(sent) == 413 and inner.read == 0
    body = json.loads(next(m["body"] for m in sent if m["type"] == "http.response.body"))
    assert body["code"] == ul.TOO_LARGE_CODE and "10 byte" in body["message"]


def test_middleware_counts_streamed_bytes_when_content_length_is_missing_or_bogus():
    mw = ul.MaxBodySizeMiddleware(Echo(), max_bytes=10)
    assert _status(run(_drive(mw, chunks=(b"123456", b"78901")))) == 413
    assert _status(run(_drive(mw, headers=[(b"content-length", b"abc")], chunks=(b"123456", b"78901")))) == 413
    assert _status(run(_drive(mw, headers=[(b"content-length", b"4")], chunks=(b"1234",)))) == 200


def test_middleware_only_guards_http_requests_under_the_configured_prefixes():
    inner = Echo()
    mw = ul.MaxBodySizeMiddleware(inner, max_bytes=3, path_prefixes=["/upload"])
    assert _status(run(_drive(mw, path="/other", chunks=(b"way too long",)))) == 200
    assert _status(run(_drive(mw, path="/upload/x", chunks=(b"way too long",)))) == 413
    sent = run(_drive(inner, scope_type="lifespan"))  # sanity: non-http scope is not interpreted by Echo
    assert sent
    passthrough = []

    async def app(scope, receive, send):
        passthrough.append(scope["type"])

    run(_drive(ul.MaxBodySizeMiddleware(app, max_bytes=1), scope_type="websocket"))
    assert passthrough == ["websocket"]


def test_middleware_reraises_non_413_http_exceptions_from_the_app():
    async def app(scope, receive, send):
        raise HTTPException(status_code=418, detail="teapot")

    mw = ul.MaxBodySizeMiddleware(app, max_bytes=10)
    with pytest.raises(HTTPException) as err:
        run(_drive(mw))
    assert err.value.status_code == 418
