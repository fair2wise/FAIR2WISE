"""The ``library_extract`` job (A8, T39): term extraction over chosen library PDFs into a KG bundle.

One run, in the worker process:

1. validate the inputs that ride in ``OperationInputs.params`` (``bundle``, ``papers``, ``model``,
   ``schema``, ``topic``, ``continue_from``, ``confirmation``): the model never goes through
   ``PUT /settings``;
2. fail closed without a configured LLM backend (no client is built, so no network call);
3. check the cost confirmation token (:meth:`KgLibrary.check_confirmation`);
4. take the per-bundle lock (one extraction per bundle, across processes);
5. create the bundle (or fork ``continue_from``), or reuse an existing one whose schema is locked;
6. one checkpointed :class:`~app.modules.jobs.stages.WorkUnit` per PDF through
   ``Orchestrator.process_pdf``; the unit key is the PDF sha256 plus extractor, schema and model revision,
   so a changed model or schema re-runs while a resumed job redoes nothing finished;
7. ``assign_importance``, ``TermStore.save``, ``kg_update.rebuild_kg`` and the bundle manifest.

``processed_pdfs.json`` and ``extraction_manifest.json`` have the shape the coordinator writes. A PDF that
fails is recorded as a failed unit and the others finish; cancel stops between PDFs and keeps what finished.
The standard-library logs of the extractor are forwarded to the job log (T33 ``capture_job_logs``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
from typing import Any

from . import bundles as B
from ..contracts.errors import ContractError, ErrorCode
from ..jobs.log_capture import CaptureBusy, capture_job_logs
from ..jobs.pipeline import JobInterrupted, JobStopped, StageContext, StageResult
from ..jobs.stages import WorkUnit, chunked_stage
from .catalog import sha256_file
from .kg_service import KgLibrary
from .schemas import manifest_block, resolve_schema

logger = logging.getLogger(__name__)

#: Bumped when the extraction procedure changes in a way that should re-run finished PDFs.
EXTRACTOR_REVISION = "library-extract-1"
#: Environment selecting the backend (``cborg`` or ``ollama``); ``cborg`` is implied by ``CBORG_API_KEY``.
BACKEND_ENV = "F2W_EXTRACT_BACKEND"
OLLAMA_URL_ENV = "OLLAMA_URL"
#: Loggers whose records become job log lines.
CAPTURED_LOGGERS = ("app.modules.term_extractor", "app.modules.library.extraction", "f2w.output")
MAX_PAPERS = 5000
LOCK_DIRNAME = ".locks"


class BackendUnavailable(Exception):
    """No LLM backend is configured for extraction."""


def resolve_backend(env: Mapping[str, str]) -> dict[str, str] | None:
    """The configured extraction backend as ``{"name": ..., ...}``, or ``None`` when there is none.

    ``cborg`` needs ``CBORG_API_KEY``; ``ollama`` needs ``F2W_EXTRACT_BACKEND=ollama`` (the URL defaults to
    the local daemon). Nothing is read from a dotenv file and nothing is contacted here.
    """
    chosen = (env.get(BACKEND_ENV) or "").strip().lower()
    if chosen == "ollama":
        return {"name": "ollama", "ollama_url": (env.get(OLLAMA_URL_ENV) or "http://localhost:11434").strip()}
    if chosen in ("", "cborg") and (env.get("CBORG_API_KEY") or "").strip():
        out = {"name": "cborg", "cborg_api_key": env["CBORG_API_KEY"].strip()}
        if (env.get("CBORG_BASE_URL") or "").strip():
            out["cborg_base"] = env["CBORG_BASE_URL"].strip()
        return out
    return None


#: ``factory(model=, output_file=, schema_path=, backend=<resolve_backend dict>) -> Orchestrator``.
OrchestratorFactory = Callable[..., Any]


def default_orchestrator_factory(*, model: str, output_file: str, schema_path: str, backend: Mapping[str, str]) -> Any:
    """Build the real :class:`~app.modules.term_extractor.Orchestrator` (the only place a client is made)."""
    from ..f2w_agent.coordinator import default_workers
    from ..term_extractor import Orchestrator

    kwargs: dict[str, Any] = {"backend": backend["name"], "max_workers": default_workers()}
    for key in ("cborg_base", "cborg_api_key", "ollama_url"):
        if backend.get(key):
            kwargs[key] = backend[key]
    return Orchestrator(model=model, output_file=output_file, schema_path=schema_path, **kwargs)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, data: Any) -> None:
    B._write_json(path, data)  # noqa: SLF001 - the bundle package's atomic writer


@contextmanager
def bundle_lock(store: B.BundleStore, name: str) -> Iterator[None]:
    """One extraction per bundle: an advisory ``flock`` that dies with the process (a killed worker frees it).

    Raises:
        ContractError: ``CONFLICT`` (retryable) while another run holds the bundle.
    """
    import fcntl

    B.validate_name(name)
    lock_dir = store.root / LOCK_DIRNAME
    lock_dir.mkdir(parents=True, exist_ok=True)
    handle = open(lock_dir / f"{name}.lock", "a+")  # noqa: SIM115, PTH123 - held across the yield
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ContractError(ErrorCode.CONFLICT, "another extraction is running for this KG bundle",
                                retryable=True) from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class LibraryExtractor:
    """Runs ``library_extract`` over a :class:`~app.modules.library.service.LibraryService`.

    Args:
        service: The library catalog and folder (T35).
        store: Bundle store under ``<F2W_WORKDIR>/kgs``.
        env: Startup environment (backend selection); never read from ``os.environ`` implicitly.
        factory: Orchestrator builder (tests inject a fake LLM here); default builds the real one.
        kg: Confirmation checker; default a :class:`KgLibrary` over ``store`` and ``service``.
    """

    def __init__(self, service: Any, store: B.BundleStore, *, env: Mapping[str, str] | None = None,
                 factory: OrchestratorFactory | None = None, kg: KgLibrary | None = None) -> None:
        self.service = service
        self.store = store
        self.env: Mapping[str, str] = dict(os.environ if env is None else env)
        self.factory = factory or default_orchestrator_factory
        self.kg = kg or KgLibrary(store, papers=service)

    # -- inputs ----------------------------------------------------------------------------------

    def parse(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Validate ``params``; ``INVALID_INPUT`` names the problem (no secret in it)."""
        def text(key: str, *, required: bool = True) -> str | None:
            value = params.get(key)
            if value is None or value == "":
                if required:
                    raise ContractError(ErrorCode.INVALID_INPUT, f"{key} is required")
                return None
            if not isinstance(value, str):
                raise ContractError(ErrorCode.INVALID_INPUT, f"{key} must be a string")
            return value.strip()

        name = B.validate_name(text("bundle"))
        model = text("model") or ""
        schema = text("schema") or ""
        topic = text("topic", required=False)
        parent = text("continue_from", required=False)
        confirmation = text("confirmation") or ""
        papers = params.get("papers")
        if not isinstance(papers, (list, tuple)) or not papers or not all(isinstance(p, str) and p for p in papers):
            raise ContractError(ErrorCode.INVALID_INPUT, "papers must be a non-empty list of paper ids")
        if len(papers) > MAX_PAPERS:
            raise ContractError(ErrorCode.INVALID_INPUT, "too many papers")
        if parent is not None:
            B.validate_name(parent)
        return {"bundle": name, "model": model, "schema": schema, "topic": topic, "continue_from": parent,
                "confirmation": confirmation, "papers": list(dict.fromkeys(papers))}

    def resolve_papers(self, refs: Sequence[str]) -> list[dict[str, Any]]:
        """Catalog rows for paper ids or relative paths, sorted by relpath; unknown or duplicate names refused."""
        catalog = self.service.catalog
        rows: dict[str, dict[str, Any]] = {}
        for ref in refs:
            row = catalog.paper_by_id(ref) or catalog.paper(ref)
            if row is None:
                raise ContractError(ErrorCode.INVALID_INPUT, "unknown paper in the selection")
            rows[row["relpath"]] = row
        ordered = [rows[k] for k in sorted(rows)]
        names = [Path(r["relpath"]).name for r in ordered]
        if len(set(names)) != len(names):
            raise ContractError(ErrorCode.INVALID_INPUT, "two selected papers share a file name")
        return ordered

    def backend(self) -> dict[str, str]:
        """The configured backend or a fail-closed ``unsupported_capability`` error (nothing was contacted)."""
        backend = resolve_backend(self.env)
        if backend is None:
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                "no LLM backend is configured for extraction (set CBORG_API_KEY, or "
                                f"{BACKEND_ENV}=ollama)")
        return backend

    # -- the bundle -------------------------------------------------------------------------------

    def ensure_bundle(self, args: Mapping[str, Any], schema_choice: Mapping[str, Any]) -> B.Bundle:
        """The bundle to extract into: existing (schema locked), a fork of ``continue_from``, or a new one."""
        name, schema_id = args["bundle"], args["schema"]
        if self.store.exists(name):
            bundle = self.store.get(name)
            B.ensure_schema_locked(bundle.manifest, schema_id)
            return bundle
        if args["continue_from"]:
            return self.store.fork(args["continue_from"], name, schema_id=schema_id, topic=args["topic"])
        return self.store.create(name, schema=manifest_block(dict(schema_choice)), topic=args["topic"])

    # -- bookkeeping (the shape the coordinator writes) ----------------------------------------------

    @staticmethod
    def _load_processed(bundle: B.Bundle) -> set[str]:
        from ..f2w_agent.coordinator import _load_processed_pdfs

        return _load_processed_pdfs(bundle.file(B.PROCESSED_FILE))

    @staticmethod
    def _load_manifest(bundle: B.Bundle) -> dict[str, Any]:
        from ..f2w_agent.coordinator import _load_extraction_manifest

        return _load_extraction_manifest(bundle.file(B.EXTRACTION_MANIFEST_FILE))

    def record_pdf(self, bundle: B.Bundle, name: str, sha256: str, pages_total: int, pages_with_terms: int) -> None:
        """Mark one PDF processed and fully extracted (same entry shape as ``Coordinator._record_full_extraction``)."""
        from ..f2w_agent.coordinator import _save_extraction_manifest, _save_processed_pdfs

        processed = self._load_processed(bundle)
        processed.add(name)
        _save_processed_pdfs(bundle.file(B.PROCESSED_FILE), processed)
        manifest = self._load_manifest(bundle)
        papers = manifest.setdefault("papers", {})
        now = _now()
        terms_json = str(bundle.file(B.TERMS_FILE))
        entry = dict(papers.get(name) or {})
        entry.update({"filename": name, "pdf_sha256": sha256, "extraction_state": "full", "timestamp": now,
                      "terms_json": terms_json, "processed_pages_total": pages_total,
                      "processed_pages_with_terms": pages_with_terms})
        entry["full"] = {"timestamp": now, "terms_json": terms_json, "processed_pages_total": pages_total,
                         "processed_pages_with_terms": pages_with_terms}
        papers[name] = entry
        _save_extraction_manifest(bundle.file(B.EXTRACTION_MANIFEST_FILE), manifest)

    def _is_recorded(self, bundle: B.Bundle, row: Mapping[str, Any]) -> bool:
        name = Path(row["relpath"]).name
        entry = self._load_manifest(bundle)["papers"].get(name) or {}
        return name in self._load_processed(bundle) and entry.get("pdf_sha256") == row["sha256"]

    # -- the job ----------------------------------------------------------------------------------

    @staticmethod
    def unit_key(row: Mapping[str, Any], schema_block: Mapping[str, Any], model: str) -> str:
        """PDF sha256 plus extractor, schema (id and file hash) and model revision."""
        return ":".join([str(row["sha256"]), EXTRACTOR_REVISION, str(schema_block.get("id")),
                         str(schema_block.get("sha256") or schema_block.get("version") or ""), model])

    def run(self, ctx: StageContext) -> StageResult:
        """The ``library_extract`` stage body (see the module docstring)."""
        args = self.parse(ctx.inputs.params)
        backend = self.backend()
        root = self.service._require_root()  # noqa: SLF001 - one trusted package
        self.service.refresh(max_probe=0)  # hash new and changed files; no probe, no network
        rows = self.resolve_papers(args["papers"])
        choice = resolve_schema(args["schema"], self.kg._registry())  # noqa: SLF001 - same-package access to the registry of the KG owned by this service
        schema_block = manifest_block(choice)
        self.kg.check_confirmation(args["confirmation"], [r["id"] for r in rows], args["model"], args["schema"])
        try:
            with bundle_lock(self.store, args["bundle"]):
                return self._run_locked(ctx, args, backend, root, rows, choice, schema_block)
        except CaptureBusy as exc:
            raise ContractError(ErrorCode.CONFLICT, str(exc), retryable=True) from exc

    def _run_locked(self, ctx: StageContext, args: Mapping[str, Any], backend: Mapping[str, str], root: Path,
                    rows: Sequence[dict[str, Any]], choice: Mapping[str, Any],
                    schema_block: Mapping[str, Any]) -> StageResult:
        from ..f2w_agent import kg_update

        model = args["model"]
        bundle = self.ensure_bundle(args, choice)
        schema_block = dict(bundle.manifest["schema"])  # the locked block of an existing bundle wins
        schema_path = str(schema_block.get("path") or choice["path"])
        terms_path = bundle.file(B.TERMS_FILE)
        # A paper already extracted into this bundle with the same bytes and model is not redone.
        same_model = bundle.manifest.get("model") == model
        todo = [r for r in rows if not (same_model and self._is_recorded(bundle, r))]
        skipped = len(rows) - len(todo)
        ctx.log(f"extracting {len(todo)} papers into bundle {bundle.name} with {model} ({skipped} already done)")
        orch = self.factory(model=model, output_file=str(terms_path), schema_path=schema_path, backend=backend)
        by_id = {r["id"]: r for r in todo}
        counts = {"extracted": 0, "pages": 0, "pages_with_terms": 0}

        def units(_sc: StageContext) -> list[WorkUnit]:
            return [WorkUnit(unit_id=r["id"], unit_key=self.unit_key(r, schema_block, model), payload=r["id"])
                    for r in todo]

        def process(_sc: StageContext, unit: WorkUnit) -> Mapping[str, Any]:
            row = by_id[unit.payload]
            rel = str(row["relpath"])
            path = self.service.roots.policy("library").require_allowed(root / rel)
            sha = sha256_file(path)  # the bytes read now, not the catalog's earlier hash
            before = dict(orch.store.metadata)
            logger.info("extracting %s", rel)  # a log line (captured); ctx.log here would turn a cancel into a failure
            with_terms = int(orch.process_pdf(str(path)) or 0)
            pages = int(orch.store.metadata.get("processed_pages_total", 0)) - int(before.get("processed_pages_total", 0))
            if pages <= 0:
                raise RuntimeError("the PDF could not be read")
            self.record_pdf(bundle, Path(rel).name, sha, pages, with_terms)
            counts["extracted"] += 1
            counts["pages"] += pages
            counts["pages_with_terms"] += with_terms
            return {"relpath": rel, "sha256": sha, "pages": pages, "pages_with_terms": with_terms}

        def finalize() -> int:
            """Importance, terms, KG and manifest for what is finished (also after a cancel)."""
            orch.store.assign_importance()
            orch.store.save()
            kg_update.rebuild_kg(str(terms_path), str(bundle.file(B.KG_FILE)))
            done = [r for r in rows if self._is_recorded(bundle, r)]
            self.store.record_extraction(
                bundle.name, schema_id=str(schema_block["id"]), model=model, backend=backend["name"],
                papers=[B.PaperRef(Path(r["relpath"]).name, r["sha256"], int(r["pages"]) if r.get("pages") else None)
                        for r in done])
            return len(done)

        with capture_job_logs(ctx, CAPTURED_LOGGERS):  # CaptureBusy: surfaced by run() as a retryable conflict
            try:
                result = chunked_stage(units, process)(ctx)
            except (JobStopped, JobInterrupted):
                finalize()
                raise
            finalized = finalize()
        ctx.log(f"extraction finished: {len(rows) - len(result.incomplete)} of {len(rows)} papers in bundle "
                f"{bundle.name}, {len(result.incomplete)} failed")
        return StageResult(progress={**dict(result.progress), **counts, "skipped": skipped, "papers_in_bundle": finalized},
                           data={"bundle": bundle.name, "model": model, "failed": len(result.incomplete)},
                           incomplete=result.incomplete)


__all__ = ("BACKEND_ENV", "CAPTURED_LOGGERS", "EXTRACTOR_REVISION", "LibraryExtractor", "bundle_lock",
           "default_orchestrator_factory", "resolve_backend")
