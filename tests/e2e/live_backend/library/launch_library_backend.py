"""Serve the migrated F2W API (``:local``) over a generated paper library for the real-backend browser smoke suite.

Backlog Tier 2 E (2026-11): the mocked Playwright suite in ``ui/e2e`` pins what the UI *expects*; this launcher gives the
same UI build a real server, so a route answer the UI misreads (``job_id`` against ``id``, a bare-hex run id, a dialog that
depends on a real response) fails in a browser, not on the pilot.

What runs is application code: ``app.api.main.create_api_app`` with every ``/v1`` route, the job store and a
``WorkerExecutor`` thread, a ``LibraryService`` over a folder of generated PDFs, the real ``Orchestrator`` (LangGraph tool loop,
passage locator and grounding, aliases, properties, relation judge) writing ``terms.json`` into a real bundle, Find supporting
text, Repair relations, export and download, the Harvester's filing and its Needs-a-PDF queue, the schema validator and the
user-schema store.

What is faked, so the process needs no key and makes no outbound connection (the T18A harness blocks every socket):

* the chat model behind the extractor: :class:`PageModel`, which registers the known terms of whichever generated page it is
  shown (page aware, so any number of extractions in one session give the same KG);
* the schema generator's chat client: a canned, valid user schema;
* the provider listing: one free model (``lbl/cborg-chat``);
* the paper probe runs in this process (the real reader; the default forks a child per PDF);
* the Harvester's two searchers and its downloader: a canned result set with one open-access PDF, one paywalled paper and one
  broken link;
* the legacy chat agents, as in the T18B/T18C suites (``_legacy_ui_harness``).

Usage (from a checkout with no ``.env``)::

    env -i PATH=<venv>/bin:/usr/bin:/bin HOME=<tmp> TMPDIR=<tmp> \\
        <venv>/bin/python tests/e2e/live_backend/library/launch_library_backend.py \\
        --state-dir <tmp>/state --ready-file <tmp>/ready.json

Stop it with SIGTERM/SIGINT.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sys
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
LEGACY_UI_DIR = REPO_ROOT / "tests" / "contracts" / "legacy_ui"
for extra in (REPO_ROOT, LEGACY_UI_DIR, LEGACY_UI_DIR / "runtime", LEGACY_UI_DIR / "closure"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import _legacy_ui_harness as H  # noqa: E402
from launch_full_backend import SecretScan  # noqa: E402
import launch_live_backend as T18B  # noqa: E402
import pytest  # noqa: E402  (MonkeyPatch only)

#: Not a credential: a marker string so the suite can prove no key-shaped value reaches a browser.
FAKE_CBORG_KEY = "t18e-fake-key-never-a-secret"
FREE_MODEL = "lbl/cborg-chat"
SCHEMA_NAME = "plecos"

# -- the generated library ------------------------------------------------------------------------------------------------
# Every sentence is over 120 characters, so it stands as a passage on its own; the key phrase of each is what the page-aware
# model looks for in the page it is shown.

S = {
    "purity": "Resonant soft X-ray scattering measures the domain purity of a polymer blend film at the carbon edge with a tunable photon energy.",
    "efficiency": "The domain purity of the blend was compared with the device efficiency measured on the same films after thermal annealing.",
    "orientation": "Grazing incidence wide angle X-ray scattering shows the face-on orientation of the polymer chains on the silicon substrate.",
    "mobility": "The face-on orientation correlates with the hole mobility of the film according to the transport measurements reported here.",
    "thickness": "Ellipsometry gave the film thickness of every sample, and the film thickness was used to scale the scattering intensity.",
    "intensity": "The scattering intensity rises with the domain purity, which the authors attribute to sharper interfaces between the two phases.",
    "reference": "A silicon reference was measured before every session to calibrate the ellipsometer and the detector distance of the camera.",
    "distance": "The detector distance determines the momentum transfer range that the camera covers in a single exposure of the sample holder.",
}

PAPERS: dict[str, list[list[str]]] = {
    "blend_scattering.pdf": [[S["purity"], S["efficiency"]], [S["orientation"], S["mobility"]]],
    "film_calibration.pdf": [[S["thickness"], S["intensity"]], [S["reference"], S["distance"]]],
}

#: ``sentence key -> register_term calls`` the model makes when that sentence is on the page it is shown.
REGISTRATIONS: dict[str, list[dict[str, Any]]] = {
    "purity": [
        {"term": "resonant soft X-ray scattering", "definition": "A scattering technique at an absorption edge.", "category": "Method",
         "relations": [{"relation": "measures", "related_term": "domain purity"}], "context": S["purity"]},
        {"term": "domain purity", "definition": "Purity of the phases of a blend.", "category": "Property", "context": S["purity"]},
    ],
    "efficiency": [
        {"term": "device efficiency", "definition": "Power conversion efficiency.", "category": "Property",
         "relations": [{"relation": "related_to", "related_term": "domain purity"}], "context": S["efficiency"]},
    ],
    "orientation": [
        {"term": "face-on orientation", "definition": "Chains lying flat on the substrate.", "category": "Structure", "context": S["orientation"]},
        {"term": "silicon substrate", "definition": "The wafer the film sits on.", "category": "Material", "context": S["orientation"]},
    ],
    "mobility": [
        {"term": "hole mobility", "definition": "Charge transport figure of merit.", "category": "Property",
         "relations": [{"relation": "related_to", "related_term": "face-on orientation"}],
         # a paraphrase, not the page's sentence: rejected by grounding, then located by the locator
         "context": "The hole mobility of the film was measured with the transport measurements reported here."},
    ],
    "thickness": [
        {"term": "film thickness", "definition": "Thickness of the film.", "category": "Property",
         "relations": [{"relation": "measured_by", "related_term": "ellipsometry"}], "context": S["thickness"]},
        {"term": "ellipsometry", "definition": "Optical thickness measurement.", "category": "Method", "context": S["thickness"]},
    ],
    "intensity": [
        {"term": "scattering intensity", "definition": "Intensity of the scattered beam.", "category": "Property",
         "relations": [{"relation": "related_to", "related_term": "domain purity"}], "context": S["intensity"]},
    ],
    "reference": [
        {"term": "silicon reference", "definition": "A calibration standard.", "category": "Material", "context": S["reference"]},
        {"term": "detector distance", "definition": "Sample to detector length.", "category": "Parameter", "context": S["reference"]},
    ],
    "distance": [
        {"term": "momentum transfer", "definition": "The scattering vector q.", "category": "Parameter",
         "relations": [{"relation": "related_to", "related_term": "detector distance"}], "context": S["distance"]},
        {"term": "unseen notion", "definition": "Named by the model, absent from every paper.", "category": "Thing"},
    ],
}

#: The canned reply of the schema generator (a valid user schema the save route accepts under ``SCHEMA_NAME``).
USER_SCHEMA = f"""\
id: https://example.org/user/{SCHEMA_NAME}
name: {SCHEMA_NAME}
description: Loricariid catfish.
version: 0.1.0
prefixes:
  linkml: https://w3id.org/linkml/
  ex: https://example.org/user/
default_prefix: ex
default_range: string
imports:
  - linkml:types
classes:
  Thing:
    description: A generic grouping for any identifiable entity (the fallback class).
    slots: [name, description]
  CodeSnippet:
    is_a: Thing
    description: Code block extracted from a paper (created by the pipeline, not chosen by the model).
  Species:
    is_a: Thing
    description: A species of catfish.
  Measurement:
    is_a: Thing
    description: A measured value.
  Locality:
    is_a: Thing
    description: A place.
slots:
  name:
    range: string
  description:
    range: string
  related_to:
    description: Broad fallback relation.
    domain: Thing
    range: Thing
    multivalued: true
  has_measurement:
    description: A measurement of a species.
    domain: Species
    range: Measurement
  country:
    description: Country of a locality.
    domain: Locality
    range: string
"""


def _norm(text: str) -> str:
    return " ".join(str(text).split())


# -- fakes --------------------------------------------------------------------------------------------------------------------


def make_page_model() -> Any:  # noqa: ANN401 - a BaseChatModel subclass built at call time (langchain import)
    """The page-aware scripted chat model: one tool-calling turn per page, then ``done``."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from tests.runtime.runtime_fakes import ScriptedChatModel

    keys = {key: _norm(sentence) for key, sentence in S.items()}

    class PageModel(ScriptedChatModel):
        def bind_tools(self, tools: Any, **kw: Any) -> PageModel:  # noqa: ANN401 - BaseTool objects or dicts
            return self

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any) -> ChatResult:  # noqa: ANN401
            self.calls.append(list(messages))
            last = messages[-1]
            if isinstance(last, ToolMessage) or not isinstance(last, HumanMessage):
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])
            page = _norm(last.content if isinstance(last.content, str) else "")
            calls = []
            for key, sentence in keys.items():
                if sentence in page:
                    for args in REGISTRATIONS[key]:
                        calls.append({"name": "register_term", "args": dict(args), "id": f"{key}-{len(calls)}", "type": "tool_call"})
            message = AIMessage(content="", tool_calls=calls) if calls else AIMessage(content="No terms on this page.")
            return ChatResult(generations=[ChatGeneration(message=message)])

    return PageModel()


def install_extractor_fakes(mp: pytest.MonkeyPatch) -> None:
    """The real ``Orchestrator`` with the page model behind it; no client, index or ChEBI service is built."""
    from app.modules.library import extraction
    from app.modules.term_extractor import orchestrator as orch_mod
    from app.modules.term_extractor.orchestrator import Orchestrator
    from app.modules.term_extractor.services import Services
    from tests.extraction.test_passage_capture_real_path import FakeMergeIndex

    model = make_page_model()
    mp.setattr(orch_mod, "TermMergeIndex", FakeMergeIndex)
    mp.setattr(orch_mod, "build_services", lambda chebi_obo_path: Services(formula_checker=None, chebi_lookup=None))
    mp.setattr(orch_mod, "make_chat_client", lambda **kw: SimpleNamespace(kw=kw))
    mp.setattr(Orchestrator, "_build_llm", lambda self: model)

    def factory(*, model: str, output_file: str, schema_path: str, backend: Any, user_schema: bool = False) -> Any:  # noqa: ANN401
        kwargs: dict[str, Any] = {"backend": "ollama", "chebi_obo_path": None, "max_workers": 1}
        if user_schema:
            kwargs["sanitize_schema_descriptions"] = True
            kwargs["schema_prompt_budget"] = extraction.PROMPT_BUDGETS["cborg"]
        return Orchestrator(model, output_file, schema_path=schema_path, **kwargs)

    mp.setattr(extraction, "default_orchestrator_factory", factory)


def install_schema_fake(mp: pytest.MonkeyPatch) -> None:
    from app.modules.library import schema_generate

    class CannedClient:
        def chat(self, prompt: str, **kw: Any) -> str:  # noqa: ANN401 - the client's keyword surface
            return USER_SCHEMA

    mp.setattr(schema_generate, "_default_factory", lambda **kwargs: CannedClient())


def fake_catalog() -> Any:  # noqa: ANN401 - CachedModelCatalog
    from app.modules.model_catalog import CachedModelCatalog, ModelListing

    return CachedModelCatalog(lister=lambda: ModelListing(backend="cborg", source="live", models=(FREE_MODEL,),
                                                           prices={FREE_MODEL: 0.0}))


def oa_pdf_bytes(tmp: Path) -> bytes:
    from tests.library.passages_fixtures import make_pdf

    path = tmp / "harvested.pdf"
    make_pdf(path, [[S["purity"], S["intensity"]]])
    return path.read_bytes()


def install_harvester_fakes(harvester: Any, pdf: bytes) -> None:  # noqa: ANN401 - Harvester
    """Three candidates for any topic: one open-access PDF, one paywalled paper, one broken link. Nothing leaves the process."""
    from app.modules.library.harvest import Candidate

    def search(topic: str, per_query: int) -> list[Candidate]:
        words = " ".join(topic.split())
        return [
            Candidate(title=f"An open access study of {words}", source="openalex", year=2021, doi="10.1000/oa-study",
                      abstract=f"We report {words} measured on polymer blend films.", landing_url="https://doi.org/10.1000/oa-study",
                      pdf_urls=["https://example.org/oa-study.pdf"], oa=True),
            Candidate(title=f"A paywalled study of {words}", source="openalex", year=2019, doi="10.1000/paywalled-study",
                      abstract=f"{words} behind a paywall.", landing_url="https://doi.org/10.1000/paywalled-study", oa=False),
            Candidate(title=f"A study of {words} with a broken link", source="openalex", year=2020, doi="10.1000/broken-link",
                      abstract=f"{words} whose PDF link answers 403.", landing_url="https://doi.org/10.1000/broken-link",
                      pdf_urls=["https://example.org/broken.pdf"], oa=True),
        ]

    def download(url: str, max_bytes: int) -> tuple:
        return (pdf, "ok") if url.endswith("oa-study.pdf") else (None, "HTTP 403")

    harvester._searchers = {"openalex": search, "arxiv": search}  # noqa: SLF001 - the test seam of the Harvester
    harvester._downloader = download  # noqa: SLF001
    harvester.delay = 0.0


class LibraryObservations(T18B.Observations):
    """T18B bookkeeping plus the fake-key scan."""

    def __init__(self, state_dir: Path, ctx: H.Ctx) -> None:
        super().__init__(state_dir, ctx)
        self.secret_hits: list[dict[str, Any]] = []

    def flush(self) -> None:
        data = {
            "fake_calls": dict(sorted(self.ctx.calls.items())),
            "retrieval_calls": self.retrieval_calls,
            "faults": self.faults,
            "requests": self.requests,
            "graph_sha256": self.graph_hashes(),
            "splash_entities": len(self.ctx.splash_entities),
            "splash_links": len(self.ctx.splash_links),
            "secret_hits": self.secret_hits,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


# -- main -----------------------------------------------------------------------------------------------------------------------


def build(mp: pytest.MonkeyPatch, state_dir: Path) -> tuple:
    """``(app, worker, ready-file payload)``: the seeded ``:local`` app with its worker started."""
    from app.modules.f2w_agent import kg_update
    from app.modules.library.probe import probe_pdf
    from app.modules.library.service import LibraryService
    from app.modules.literature.openalex import QuotaBreaker
    from tests.library.passages_fixtures import make_pdf
    from tests.security.security_fakes import local_settings

    real_rebuild = kg_update.rebuild_kg
    ctx = H.install(mp, state_dir / "ws", REPO_ROOT)
    # The harness replaces ``rebuild_kg`` with a fake that writes its seed graph (for the legacy chat flow). The library
    # extraction builds the bundle's kg.json through the same function, so the real builder is put back here.
    mp.setattr(kg_update, "rebuild_kg", real_rebuild)
    install_extractor_fakes(mp)
    install_schema_fake(mp)
    mp.setenv("CBORG_API_KEY", FAKE_CBORG_KEY)                      # selects the CBORG backend; never sent anywhere
    mp.setenv("F2W_DISCLOSE_SNIPPETS", "1")                          # node cards show passages, as the pilot opts in
    # Export writes into the workspace's storage/kg, the folder the graph view lists (the shipped default when no
    # release directory is configured), so an exported bundle can be opened in the KG Viewer.
    mp.setenv("F2W_KG_RELEASE_DIR", str(ctx.workspace / "storage" / "kg"))

    lib = state_dir / "papers"
    for name, pages in PAPERS.items():
        make_pdf(lib / "rsoxs" / name, pages)
    # The real PDF reader, in this process (the default forks a child per file); it reads the DOI a dropped PDF names.
    service = LibraryService(lib, state_dir / "library" / "catalog.sqlite", settings=local_settings(), probe=probe_pdf,
                             breaker=QuotaBreaker())
    service.refresh(max_probe=0)

    from app.api.main import create_api_app
    from app.modules.f2w_agent.coordinator import CoordinatorConfig
    from app.modules.validation_settings import JudgeModelService, JudgeModelStore, judge_settings_path
    from app.modules.workers.executor import WorkerExecutor

    workdir = ctx.workspace / "run"
    cfg = CoordinatorConfig(
        backend="cborg", model=FREE_MODEL, graph=str(Path("storage/kg") / H.GRAPH_FILENAME), kg_mode="splash",
        workdir=workdir, max_rounds=3, workflow_mode="agentic", extraction_mode="targeted", targeted_max_pages=6,
        download_delay_seconds=0.0, validate_downloads=False, workers=1)
    judge = JudgeModelService(JudgeModelStore(judge_settings_path(workdir)), catalog=fake_catalog())
    obs = LibraryObservations(state_dir, ctx)
    T18B.wrap_retrieval(mp, obs)
    app = create_api_app(cfg, library=service, judge_settings=judge, sse_poll_seconds=0.1)
    install_harvester_fakes(app.state.f2w_harvest, oa_pdf_bytes(state_dir))
    worker = WorkerExecutor(app.state.f2w_operations.jobs, max_concurrency=1, poll_interval=0.1).start()
    obs.flush()
    asgi = T18B.CaptureMiddleware(SecretScan(app, obs, FAKE_CBORG_KEY), obs, state_dir / "capture.jsonl")
    info = {
        "state_dir": str(state_dir), "workspace": str(ctx.workspace), "runtime": os.environ.get("F2W_RUNTIME") or "legacy",
        "kg_mode": "splash", "factory": "app.api.main.create_api_app", "library_dir": str(lib),
        "papers": sorted(PAPERS), "category": "rsoxs", "schema_name": SCHEMA_NAME, "free_model": FREE_MODEL,
        "secret_sha256": hashlib.sha256(FAKE_CBORG_KEY.encode()).hexdigest(),
    }
    return asgi, worker, info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    import uvicorn

    state_dir: Path = args.state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "capture.jsonl").write_text("", encoding="utf-8")

    # Bind before the harness blocks outbound sockets; the server only accepts.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]

    with pytest.MonkeyPatch.context() as mp:
        asgi, worker, info = build(mp, state_dir)
        server = uvicorn.Server(uvicorn.Config(asgi, log_level="warning", lifespan="off", access_log=False))

        def _stop(*_a: Any) -> None:  # noqa: ANN401 - signal handler
            server.should_exit = True
        signal.signal(signal.SIGTERM, _stop)

        async def _serve() -> Any:  # noqa: ANN401
            task = asyncio.create_task(server.serve(sockets=[sock]))
            while not server.started:
                if task.done():
                    return await task
                await asyncio.sleep(0.05)
            args.ready_file.write_text(json.dumps({"port": port, "pid": os.getpid(), **info}), encoding="utf-8")
            await task

        try:
            asyncio.run(_serve())
        finally:
            worker.shutdown(timeout=5.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
