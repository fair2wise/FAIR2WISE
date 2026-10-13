"""Serve the real legacy UI FastAPI app over HTTP with fake inference (T18B part 2).

The app is ``app.modules.f2w_agent.api.create_app`` built exactly as the T18A
contract harness builds it (``_legacy_ui_harness.install`` + ``make_ui_app``):
real routes, access guard, disclosure projection, service, orchestrator,
session memory and traces, with deterministic fakes only for models, paper
providers, Splash and Tiled. Profile ``:local`` (no startup profile is set).
``F2W_RUNTIME`` is passed through unchanged, so the same launcher serves the
default runtime and ``F2W_RUNTIME=langgraph``.

What this launcher adds on top of the harness, all outside ``app/``:

* a second synthetic JSON graph (``matkg_contract_v2.json``) so the browser can
  select two graphs, and three question markers in the retrieval fake:
  ``multi-graph`` (answer cites one node of each graph), ``[slow]`` (retrieval
  sleeps, for browser cancellation) and the harness's own ``insufficient`` /
  ``explode`` / ``crash the verdict`` triggers;
* ``FaultMiddleware``: transport-level fault injection keyed on a marker in the
  JSON request body (``[fallback-503]``: the stream route answers 503 before any
  event, so the client must fall back to the real non-stream route;
  ``[fragment]``: the real SSE body is re-sent in 1-7 byte pieces). The real
  route handler still produces every byte the browser parses, except the 503;
* ``CaptureMiddleware``: appends every request/response (raw SSE bytes
  included, sanitized) to ``<state>/capture.jsonl`` for the baseline diff;
* ``<state>/observations.json``: fake call counts, retrieval kwargs and the
  sha256 of every graph file, rewritten after each request, so specs can
  assert that nothing was published.

Usage (from a checkout with no ``.env``)::

    env -i PATH=<venv>/bin:/usr/bin:/bin HOME=<tmp> TMPDIR=<tmp> [F2W_RUNTIME=langgraph] \\
        <venv>/bin/python tests/contracts/legacy_ui/runtime/launch_live_backend.py \\
        --state-dir <tmp>/state --ready-file <tmp>/ready.json

The server binds 127.0.0.1 on an ephemeral port (``--port 0``) and writes
``{"port": N, "pid": P, "state_dir": ...}`` to ``--ready-file`` once it accepts
connections. Stop it with SIGTERM/SIGINT.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
LEGACY_UI_DIR = HERE.parent
REPO_ROOT = HERE.parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(LEGACY_UI_DIR))

import pytest  # noqa: E402  (MonkeyPatch only)

import _legacy_ui_harness as H  # noqa: E402

SECOND_GRAPH_FILENAME = "matkg_contract_v2.json"
SECOND_GRAPH: Dict[str, Any] = {
    "things": [
        {
            "id": "matkg:Beta",
            "name": "Beta film",
            "category": "Material",
            "description": "Synthetic node of the second contract graph (beta film).",
            "publications": [
                {
                    "source_paper": "beta_paper.pdf",
                    "paper_title": "Synthetic second-graph paper on beta films",
                    "doi": "10.0000/contract.beta",
                    "publication_year": 2025,
                    "authors": ["Second Author"],
                }
            ],
            "source_papers": ["beta_paper.pdf"],
        },
    ],
    "associations": [],
}

SLOW_SECONDS = 30.0
FRAGMENT_SIZES = (1, 2, 3, 7)


class Observations:
    """Fake-call bookkeeping written to ``observations.json`` for the specs."""

    def __init__(self, state_dir: Path, ctx: H.Ctx) -> None:
        self.path = state_dir / "observations.json"
        self.ctx = ctx
        self.retrieval_calls: List[Dict[str, Any]] = []
        self.faults: List[Dict[str, Any]] = []
        self.requests: List[Dict[str, Any]] = []

    def graph_hashes(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for path in sorted(self.ctx.workspace.rglob("*.json")):
            rel = path.relative_to(self.ctx.workspace).as_posix()
            if not (rel.startswith(("storage/kg/", "run/uploads/")) or rel == "run/kg.json"):
                continue
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out

    def flush(self) -> None:
        data = {
            "fake_calls": dict(sorted(self.ctx.calls.items())),
            "retrieval_calls": self.retrieval_calls,
            "faults": self.faults,
            "requests": self.requests,
            "graph_sha256": self.graph_hashes(),
            "splash_entities": len(self.ctx.splash_entities),
            "splash_links": len(self.ctx.splash_links),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def wrap_retrieval(mp: pytest.MonkeyPatch, obs: Observations) -> None:
    """Extend the harness retrieval fake with multi-graph and slow markers."""
    from app.modules.f2w_agent import api as api_mod

    base = api_mod.RetrievalAgent

    class LiveRetrieval(base):  # type: ignore[misc, valid-type]
        async def query(self, question, *args, **kwargs):
            q = str(question).lower()
            obs.retrieval_calls.append({
                "question": str(question),
                "source_rag": kwargs.get("source_rag"),
                "use_live_tiled": kwargs.get("use_live_tiled"),
                "graph_file": str(self.graph_file) if self.graph_file else None,
            })
            obs.flush()
            if "[slow]" in q:
                await asyncio.sleep(SLOW_SECONDS)
            if "multi-graph" in q:
                obs.ctx.bump("retrieval.query")
                return {
                    "status": "success",
                    "sufficient": True,
                    "answer": ("Term A [KG: matkg:TermA] comes from the first graph and "
                               "Beta film [KG: matkg:Beta] from the second graph."),
                    "selected": ["matkg:TermA", "matkg:Beta"],
                    "direct_evidence_count": 2,
                    "no_evidence": False,
                    "graph_source_requested": "json",
                    "graph_source_used": "json",
                }
            return await super().query(question, *args, **kwargs)

    mp.setattr(api_mod, "RetrievalAgent", LiveRetrieval)


# --------------------------------------------------------------------------- ASGI layers


async def _read_body(receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return b"".join(chunks)
        chunks.append(message.get("body", b""))
        if not message.get("more_body"):
            return b"".join(chunks)


def _replay(body: bytes, receive):
    sent = False

    async def _receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return _receive


def _marker_text(body: bytes) -> str:
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("message") or "").lower()


class FaultMiddleware:
    """Marker-driven transport faults around the real routes."""

    STREAM_PATHS = ("/chat/stream", "/chat/action/stream")

    def __init__(self, app, obs: Observations) -> None:
        self.app = app
        self.obs = obs

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in self.STREAM_PATHS:
            await self.app(scope, receive, send)
            return
        body = await _read_body(receive)
        marker = _marker_text(body)
        if "[fallback-503]" in marker:
            self.obs.faults.append({"fault": "fallback-503", "path": scope["path"]})
            payload = b"synthetic pre-first-event stream failure"
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                    (b"content-length", str(len(payload)).encode()),
                                    (b"access-control-allow-origin", _origin(scope)),
                                    (b"access-control-allow-credentials", b"true"),
                                    (b"vary", b"Origin")]})
            await send({"type": "http.response.body", "body": payload})
            return
        if "[fragment]" in marker:
            self.obs.faults.append({"fault": "fragment", "path": scope["path"]})
            await self.app(scope, _replay(body, receive), _fragmenting(send))
            return
        await self.app(scope, _replay(body, receive), send)


def _origin(scope) -> bytes:
    for key, value in scope.get("headers") or []:
        if key == b"origin":
            return value
    return b"*"


def _fragmenting(send):
    index = 0

    async def _send(message):
        nonlocal index
        if message["type"] != "http.response.body":
            await send(message)
            return
        data = message.get("body", b"")
        more = message.get("more_body", False)
        pos = 0
        while pos < len(data):
            size = FRAGMENT_SIZES[index % len(FRAGMENT_SIZES)]
            index += 1
            piece = data[pos:pos + size]
            pos += size
            await send({"type": "http.response.body", "body": piece, "more_body": True})
            await asyncio.sleep(0.002)
        if not more:
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    return _send


class CaptureMiddleware:
    """Record every exchange (sanitized) as one JSON line."""

    def __init__(self, app, obs: Observations, capture_path: Path) -> None:
        self.app = app
        self.obs = obs
        self.capture_path = capture_path
        self.seq = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = await _read_body(receive)
        self.seq += 1
        record: Dict[str, Any] = {
            "seq": self.seq,
            "method": scope["method"],
            "path": scope["path"],
            "query": scope.get("query_string", b"").decode("latin-1"),
            "origin": _origin(scope).decode("latin-1") if _origin(scope) != b"*" else None,
        }
        try:
            record["request_json"] = json.loads(body) if body else None
        except ValueError:
            record["request_text"] = body.decode("utf-8", "replace")
        chunks: List[bytes] = []
        state: Dict[str, Any] = {"status": None, "headers": {}, "disconnected": False, "body_sends": 0,
                                 "completed": False}

        async def _send(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["headers"] = {k.decode("latin-1"): v.decode("latin-1") for k, v in message.get("headers", [])}
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                state["body_sends"] += 1
                if not message.get("more_body", False):
                    state["completed"] = True
            try:
                await send(message)
            except Exception:
                state["disconnected"] = True
                raise

        started = time.monotonic()
        try:
            await self.app(scope, _replay(body, receive), _send)
        except asyncio.CancelledError:
            state["disconnected"] = True
            raise
        finally:
            raw = b"".join(chunks)
            if state["status"] is not None and not state["completed"]:
                # The app returned (or was cancelled) without finishing the body:
                # Starlette ends a StreamingResponse this way on client disconnect.
                state["disconnected"] = True
            ctype = state["headers"].get("content-type", "")
            record.update({
                "status": state["status"],
                "content_type": ctype,
                "cors": {k: v for k, v in state["headers"].items() if k.startswith("access-control-")},
                "body_sends": state["body_sends"],
                "disconnected": state["disconnected"],
                "elapsed_s": round(time.monotonic() - started, 3),
                "wall_end": round(time.time(), 3),
            })
            text = H.sanitize_text(raw.decode("utf-8", "replace"), self.obs.ctx)
            if ctype.startswith("text/event-stream"):
                record["sse_raw"] = text
            else:
                try:
                    record["response_json"] = json.loads(text) if text else None
                except ValueError:
                    record["response_text"] = text
            if record.get("request_json") is not None:
                record["request_json"] = H.sanitize_obj(record["request_json"], self.obs.ctx)
            with self.capture_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.obs.requests.append({"seq": self.seq, "method": scope["method"], "path": scope["path"],
                                      "status": state["status"], "disconnected": state["disconnected"]})
            self.obs.flush()


# --------------------------------------------------------------------------- main


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--kg-mode", choices=("splash", "json"), default="splash")
    args = parser.parse_args(argv)

    import uvicorn

    state_dir: Path = args.state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    capture = state_dir / "capture.jsonl"
    capture.write_text("", encoding="utf-8")

    # Bind before the harness blocks outbound sockets; the server only accepts.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]

    with pytest.MonkeyPatch.context() as mp:
        ctx = H.install(mp, state_dir / "ws", REPO_ROOT)
        (ctx.workspace / "storage" / "kg" / SECOND_GRAPH_FILENAME).write_text(
            json.dumps(SECOND_GRAPH, indent=2), encoding="utf-8")
        obs = Observations(state_dir, ctx)
        wrap_retrieval(mp, obs)
        app = H.make_ui_app(ctx, kg_mode=args.kg_mode)
        obs.flush()
        asgi = CaptureMiddleware(FaultMiddleware(app, obs), obs, capture)

        config = uvicorn.Config(asgi, log_level="warning", lifespan="off", access_log=False)
        server = uvicorn.Server(config)

        async def _serve():
            task = asyncio.create_task(server.serve(sockets=[sock]))
            while not server.started:
                if task.done():
                    return await task
                await asyncio.sleep(0.05)
            args.ready_file.write_text(json.dumps({
                "port": port, "pid": os.getpid(), "state_dir": str(state_dir),
                "workspace": str(ctx.workspace), "runtime": os.environ.get("F2W_RUNTIME") or "legacy",
                "kg_mode": args.kg_mode,
            }), encoding="utf-8")
            await task

        asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
