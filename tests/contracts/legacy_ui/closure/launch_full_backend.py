"""Serve the migrated F2W API with a real disposable Tiled for the T18C browser suite.

What runs (everything else is real application code):

* **API:** ``app.api.main.create_api_app`` (T14 factory: the 19 legacy UI routes, the
  access guard on ``:local``, the disclosure projection, ``/v1`` jobs/review/publication),
  built from the T18A harness configuration. Fakes only for models, paper providers and
  the retrieval agent's answer text (``_legacy_ui_harness`` + the T18B launcher markers).
* **Tiled:** a disposable ``tiled[server]==0.2.18`` started by
  ``tests/fixtures/tiled_server.start_tiled_instance`` (``F2W_TILED_PYTHON``; ephemeral
  127.0.0.1 port, owned root, writer key) and seeded with beamline-identity entities. The
  legacy live-Tiled lookup (``app.modules.tiled_graph``) is **not** faked: settings probes,
  ``[tiled]`` chat turns and ``GET /graph/neighborhood`` page that server over GraphQL.
* **Editable ("splash") mode:** two switchable conditions, chosen per request from
  ``<state>/splash_mode`` (written by the specs):

  - ``absent`` (default): no Splash service exists. The real ``kg_update.splash_*``
    entity/link functions run against ``KG_RAG_SPLASH_URI`` = a reserved 127.0.0.1 port
    that is bound but never listens, so every call is refused exactly as on a host
    without Splash. This is the T18C target condition (no Splash process).
  - ``fake``: the T18A in-memory Splash store (baseline semantics, for comparison only).

  ``load_splash_graph`` / ``rebuild_kg`` / ``splash_reimport`` stay the T18A fakes in
  both conditions (the real reimport wipes a Splash DB; STOP-4 in the legacy ADR).

Network: after startup only the Tiled port and the reserved Splash port on 127.0.0.1 are
connectable; the developer's servers (8011, 8765, 8002, 8004, 5057) and every other host
are refused. No ``.env`` is read (the harness refuses to start next to one).

Usage (from a checkout with no ``.env``)::

    env -i PATH=<venv>/bin:/usr/bin:/bin HOME=<tmp> TMPDIR=<tmp> F2W_TILED_PYTHON=<tiled venv python> \\
        <venv>/bin/python tests/contracts/legacy_ui/closure/launch_full_backend.py \\
        --state-dir <tmp>/state --ready-file <tmp>/ready.json

Stop it with SIGTERM/SIGINT; the Tiled server and its root are destroyed on exit.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
LEGACY_UI_DIR = HERE.parent
REPO_ROOT = HERE.parents[3]
for extra in (REPO_ROOT, LEGACY_UI_DIR, LEGACY_UI_DIR / "runtime", REPO_ROOT / "tests" / "fixtures"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import pytest  # noqa: E402  (MonkeyPatch only)

import _legacy_ui_harness as H  # noqa: E402
import launch_live_backend as T18B  # noqa: E402

#: Ports of the developer's own servers; never connectable from this process.
FORBIDDEN_PORTS = frozenset({8011, 8765, 8002, 8004, 5057})

#: Beamline-identity records seeded into the disposable Tiled (legacy lookup shape).
ESAF_URI = "beamline:ESAF-2026-00043"
SAMPLE_URIS = ("beamline:Sample-P3HT-blend-7", "beamline:Sample-PCBM-film-2")
SEED_BEAMLINE = [
    ("ESAF", "ESAF 2026-00043", ESAF_URI,
     {"esaf_number": "2026-00043", "scientist": "Synthetic Scientist", "title": "Synthetic RSoXS beamtime"}),
    ("Sample", "P3HT blend 7", SAMPLE_URIS[0], {"sample_code": "P3HT-blend-7", "esaf_number": "2026-00043"}),
    ("Sample", "PCBM film 2", SAMPLE_URIS[1], {"sample_code": "PCBM-film-2", "esaf_number": "2026-00043"}),
]

# Synthetic source chunk for the disclosure markers ([quote] / [dump]); same construction as
# tests/api_wiring/test_wiring_quotes.py (H2 O-D2 cases).
PDF = "synthetic_contrast.pdf"
CITE = f"[PDF: {PDF} p.3]"
CHUNK = ("Resonant soft X-ray scattering enhances the contrast between chemically similar polymer domains "
         "when the photon energy is tuned to an absorption edge of one component. The measured scattering "
         "intensity then reflects differences in the complex refractive index rather than electron density "
         "alone, which allows domain spacing to be resolved in blends that are invisible to hard X-rays. "
         "Sample thickness and beam damage limit the usable exposure time for each energy.")
QUOTE = "the measured scattering intensity then reflects differences in the complex refractive index"
MERGED = ("### Knowledge-graph candidates\nStructured KG hits (not PDF text). Cite as [KG:graph_id: name].\n"
          "- [KG: matkg:TermA] (Technique) score=1.000\n\n"
          "### Literature source evidence\nCite passages as [PDF: filename p.N]. These are journal/preprint "
          f"chunks, not beamline docs.\n{CITE}\n{CHUNK}\n")
DISCLOSURE_ANSWERS = {
    "[quote]": f'The paper states that "{QUOTE[0].upper() + QUOTE[1:]}" {CITE} [KG: matkg:TermA].',
    "[dump]": f"Here is the page: {CHUNK} {CITE} [KG: matkg:TermA]",
}


def reserve_dead_port() -> socket.socket:
    """Bind a 127.0.0.1 port and never listen: connections to it are refused."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


def seed_beamline(inst) -> Dict[str, str]:
    """Create the ESAF/Sample identity records and their ``rel:hasSample`` links."""
    create_entity = "mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }"
    create_link = "mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }"
    ids: Dict[str, str] = {}
    for kind, name, uri, props in SEED_BEAMLINE:
        ids[uri] = inst.graphql(create_entity, {"i": {"entityType": kind, "name": name, "uri": uri,
                                                      "properties": props}})["createEntity"]["id"]
    for sample in SAMPLE_URIS:
        inst.graphql(create_link, {"i": {"subjectId": ids[ESAF_URI], "predicate": "rel:hasSample",
                                         "objectId": ids[sample], "properties": {"seed": "t18c"}}})
    return ids


# Captured at import, before the harness blocks every socket.
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection


class NetworkRefused(OSError):
    """An outbound connection outside the allowlist."""


def _install_guard(mp: pytest.MonkeyPatch, ports: set) -> None:
    def check(address: Any) -> None:
        host, port = (address[0], address[1]) if isinstance(address, tuple) else (address, None)
        if host not in ("127.0.0.1", "localhost") or port in FORBIDDEN_PORTS or port not in ports:
            raise NetworkRefused(f"T18C launcher: outbound connection to {address!r} is not allowed")

    def connect(self, address, *a):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            check(address)
        return _REAL_CONNECT(self, address, *a)

    def connect_ex(self, address, *a):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            check(address)
        return _REAL_CONNECT_EX(self, address, *a)

    def create_connection(address, *a, **k):
        check(address)
        return _REAL_CREATE_CONNECTION(address, *a, **k)

    mp.setattr(socket.socket, "connect", connect)
    mp.setattr(socket.socket, "connect_ex", connect_ex)
    mp.setattr(socket, "create_connection", create_connection)


SPLASH_SWITCHED = ("splash_find_entity_by_matkg_id", "splash_update_entity", "splash_create_entity",
                   "splash_create_link", "splash_find_links", "splash_delete_link")


class SplashSwitch:
    """Route the six Splash entity/link calls to the real client (``absent``) or the fake."""

    def __init__(self, state_dir: Path, real: Dict[str, Any], fake: Dict[str, Any], ctx: H.Ctx) -> None:
        self.file = state_dir / "splash_mode"
        self.real, self.fake, self.ctx = real, fake, ctx
        self.file.write_text("absent", encoding="utf-8")

    def mode(self) -> str:
        try:
            value = self.file.read_text(encoding="utf-8").strip()
        except OSError:
            value = "absent"
        return value if value in ("absent", "fake") else "absent"

    def install(self, mp: pytest.MonkeyPatch, kg_update) -> None:
        for name in SPLASH_SWITCHED:
            def call(*args, __name=name, **kwargs):
                if self.mode() == "fake":
                    return self.fake[__name](*args, **kwargs)
                self.ctx.bump(f"splash_absent.{__name}")
                return self.real[__name](*args, **kwargs)
            mp.setattr(kg_update, name, call)


def wrap_retrieval_full(mp: pytest.MonkeyPatch, obs: "FullObservations") -> None:
    """Add the live-Tiled marker and the disclosure markers on top of the T18B fake."""
    from app.modules import tiled_graph
    from app.modules.f2w_agent import api as api_mod

    base = api_mod.RetrievalAgent

    class FullRetrieval(base):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Like RetrievalAgent.__init__/set_live_tiled (retrieval_agent.py:413,492-497).
            cfg = tiled_graph.load_tiled_config(uri=kwargs.get("tiled_uri"), enabled=kwargs.get("live_tiled"))
            self._tiled_uri = cfg.uri
            self._last_tiled_meta = cfg.snapshot()

        def set_live_tiled(self, enabled, uri=None):
            super().set_live_tiled(enabled, uri=uri)
            cfg = tiled_graph.load_tiled_config(uri=uri if uri is not None else self._tiled_uri, enabled=enabled)
            self._live_tiled = cfg.enabled
            self._tiled_uri = cfg.uri
            self._last_tiled_meta = cfg.snapshot()

        async def query(self, question, *args, **kwargs):
            q = str(question).lower()
            for marker, answer in DISCLOSURE_ANSWERS.items():
                if marker in q:
                    verdict = await super().query("What is Term A resonant scattering?", *args, **kwargs)
                    self._trace_merged_context = MERGED
                    return dict(verdict, answer=answer)
            if "[tiled]" in q:
                return await self._tiled_turn(question, kwargs)
            return await super().query(question, *args, **kwargs)

        async def _tiled_turn(self, question, kwargs):
            obs.ctx.bump("retrieval.query")
            live = kwargs.get("use_live_tiled")
            live = self._live_tiled if live is None else bool(live)
            if not live:
                return {"status": "success", "sufficient": False, "missing_topics": ["experiment identity"],
                        "selected": [], "direct_evidence_count": 0, "no_evidence": True,
                        "graph_source_requested": "splash", "graph_source_used": "splash"}
            pack = await asyncio.get_event_loop().run_in_executor(
                None, lambda: tiled_graph.lookup_experiment_identity(question, uri=self._tiled_uri, enabled=True,
                                                                     include_catalog=False))
            self._last_tiled_meta = dict(pack.meta(), tiled_uri=self._tiled_uri)
            obs.tiled_lookups.append({"status": pack.status, "hits": [h.id for h in pack.hits][:10]})
            if pack.graph is not None and pack.graph.nodes:
                if not isinstance(getattr(self, "_graphs", None), dict):
                    self._graphs = {}
                self._graphs[tiled_graph.TILED_GRAPH_ID] = pack.graph
            hits = [h for h in pack.hits if not str(h.id).startswith("tiled:count:")][:3]
            if not hits:
                return {"status": "success", "sufficient": False, "missing_topics": ["experiment identity"],
                        "selected": [], "direct_evidence_count": 0, "no_evidence": True,
                        "graph_source_requested": "splash", "graph_source_used": "splash"}
            cites = " ".join(f"[KG: {h.id}]" for h in hits)
            return {
                "status": "success", "sufficient": True,
                "answer": f"Live Tiled lists these experiment records: {cites}.",
                "selected": [h.id for h in hits],
                "selected_hits": [{"id": h.id, "graph_id": h.graph_id, "score": h.score} for h in hits],
                "direct_evidence_count": len(hits), "no_evidence": False,
                "graph_source_requested": "splash", "graph_source_used": "splash",
            }

    mp.setattr(api_mod, "RetrievalAgent", FullRetrieval)


class FullObservations(T18B.Observations):
    """T18B observations plus Tiled, Splash-mode and secret-leak bookkeeping."""

    def __init__(self, state_dir: Path, ctx: H.Ctx, inst, splash: Optional[SplashSwitch] = None) -> None:
        super().__init__(state_dir, ctx)
        self.inst = inst
        self.splash = splash
        self.tiled_lookups: List[Dict[str, Any]] = []
        self.secret_hits: List[Dict[str, Any]] = []
        self.secret_sha256 = hashlib.sha256((inst.writer_key or "").encode()).hexdigest()

    def tiled_counts(self) -> Dict[str, Any]:
        try:
            ents = self.inst.graphql("{ entities(limit: 10000) { id uri properties } }")["entities"]
            links = self.inst.graphql("{ links(limit: 10000) { id } }")["links"]
        except Exception as exc:  # report, never hide
            return {"error": type(exc).__name__}
        return {"entities": len(ents), "links": len(links),
                "fingerprint": hashlib.sha256(json.dumps(sorted(ents, key=lambda e: e["id"]),
                                                         sort_keys=True).encode()).hexdigest()}

    def flush(self) -> None:
        # One atomic write of the whole record (the specs read it concurrently).
        data = {
            "fake_calls": dict(sorted(self.ctx.calls.items())),
            "retrieval_calls": self.retrieval_calls,
            "faults": self.faults,
            "requests": self.requests,
            "graph_sha256": self.graph_hashes(),
            "splash_entities": len(self.ctx.splash_entities),
            "splash_links": len(self.ctx.splash_links),
            "tiled": self.tiled_counts(), "tiled_lookups": self.tiled_lookups,
            "splash_mode": self.splash.mode() if self.splash else None,
            "secret_hits": self.secret_hits,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class SecretScan:
    """Record any response body or header that carries the Tiled writer key."""

    def __init__(self, app, obs: FullObservations, secret: str) -> None:
        self.app, self.obs, self.secret = app, obs, secret.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.secret:
            await self.app(scope, receive, send)
            return

        async def _send(message):
            blob = b""
            if message["type"] == "http.response.start":
                blob = b"".join(k + b":" + v for k, v in message.get("headers", []))
            elif message["type"] == "http.response.body":
                blob = message.get("body", b"")
            if self.secret in blob:
                self.obs.secret_hits.append({"path": scope["path"], "part": message["type"]})
            await send(message)

        await self.app(scope, receive, _send)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    import uvicorn
    from tiled_server import start_tiled_instance, tiled_python

    if tiled_python() is None:
        print("F2W_TILED_PYTHON must name the isolated tiled[server]==0.2.18 interpreter", file=sys.stderr)
        return 2

    state_dir: Path = args.state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    capture = state_dir / "capture.jsonl"
    capture.write_text("", encoding="utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    api_port = sock.getsockname()[1]
    dead = reserve_dead_port()
    dead_port = dead.getsockname()[1]

    inst = start_tiled_instance(state_dir / "tiled-base", seed=False)
    try:
        seed_ids = seed_beamline(inst)
        tiled_port = int(inst.url.rsplit(":", 1)[1])
        with pytest.MonkeyPatch.context() as mp:
            from app.modules.f2w_agent import kg_update

            real = {name: getattr(kg_update, name) for name in SPLASH_SWITCHED}
            ctx = H.install(mp, state_dir / "ws", REPO_ROOT)
            fake = {name: getattr(kg_update, name) for name in SPLASH_SWITCHED}
            _install_guard(mp, {tiled_port, dead_port})
            # Live Tiled points at the disposable server only; the key stays server side.
            mp.setenv("TILED_URI", inst.url)
            mp.setenv("TILED_API_KEY", inst.writer_key or "")
            mp.setenv("KG_RAG_SPLASH_URI", f"http://127.0.0.1:{dead_port}")
            (ctx.workspace / "storage" / "kg" / T18B.SECOND_GRAPH_FILENAME).write_text(
                json.dumps(T18B.SECOND_GRAPH, indent=2), encoding="utf-8")
            splash = SplashSwitch(state_dir, real, fake, ctx)
            splash.install(mp, kg_update)
            obs = FullObservations(state_dir, ctx, inst, splash)
            T18B.wrap_retrieval(mp, obs)
            wrap_retrieval_full(mp, obs)

            from app.api.main import create_api_app
            from app.modules.f2w_agent.coordinator import CoordinatorConfig

            cfg = CoordinatorConfig(
                backend="cborg", model="lbl/cborg-chat", graph=str(Path("storage/kg") / H.GRAPH_FILENAME),
                kg_mode="splash", workdir=ctx.workspace / "run", max_rounds=3, workflow_mode="agentic",
                extraction_mode="targeted", targeted_max_pages=6, download_delay_seconds=0.0,
                validate_downloads=False, workers=1)
            app = create_api_app(cfg)
            obs.flush()
            asgi = T18B.CaptureMiddleware(T18B.FaultMiddleware(SecretScan(app, obs, inst.writer_key or ""), obs),
                                          obs, capture)
            server = uvicorn.Server(uvicorn.Config(asgi, log_level="warning", lifespan="off", access_log=False))

            def _stop(*_a):
                server.should_exit = True
            signal.signal(signal.SIGTERM, _stop)

            async def _serve():
                task = asyncio.create_task(server.serve(sockets=[sock]))
                while not server.started:
                    if task.done():
                        return await task
                    await asyncio.sleep(0.05)
                args.ready_file.write_text(json.dumps({
                    "port": api_port, "pid": os.getpid(), "state_dir": str(state_dir),
                    "workspace": str(ctx.workspace), "runtime": os.environ.get("F2W_RUNTIME") or "legacy",
                    "kg_mode": "splash", "factory": "app.api.main.create_api_app",
                    "tiled_url": inst.url, "tiled_version": "0.2.18", "dead_splash_port": dead_port,
                    "tiled_seed": {"esaf": ESAF_URI, "samples": list(SAMPLE_URIS), "ids": seed_ids},
                    "secret_sha256": obs.secret_sha256,
                }), encoding="utf-8")
                await task

            asyncio.run(_serve())
    finally:
        dead.close()
        receipt = inst.destroy()
        (state_dir / "tiled-destroy-receipt.json").write_text(json.dumps(receipt, indent=2, default=str),
                                                              encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
