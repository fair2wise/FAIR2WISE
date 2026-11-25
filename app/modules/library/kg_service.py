"""Service behind ``/v1/library/{kgs,schemas,models,estimate}`` (A8, T38).

One object holds the bundle store, the release directory, the live model catalog, the optional paper
lookup (T35) and the optional price table, so the route module stays thin and tests use fakes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import re
from typing import Any

from . import bundles as B
from ..contracts.errors import ContractError, ErrorCode
from ..model_catalog import CachedModelCatalog
from ..ollama_catalog import OllamaListing, list_ollama_models
from ..schema.registry import SchemaRegistry
from .estimate import ModelPrice, PaperFacts, PaperLookup, estimate_extraction, verify_confirmation
from .schemas import list_schemas, manifest_block, resolve_schema
from .user_schemas import SCHEMAS_DIRNAME, UserSchemaStore

_SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.json$")
MAX_PAPER_IDS = 5000


class KgLibrary:
    """Bundles, schemas, models, estimate and export for the local operator.

    Args:
        store: Bundle store under ``<F2W_WORKDIR>/kgs``.
        release_dir: Release directory (or a callable returning it), default ``storage/kg``.
        catalog: Live model catalog (the judge model's cache is shared by the factory).
        papers: T35 paper lookup; ``None`` until T35 installs it (estimate then answers ``unavailable``).
        prices: Optional price table by model id.
        registry: Schema registry factory (default: discover ``storage/schema``).
        user_schemas: Store of user-written schemas (default ``<workdir>/schemas``, the sibling of the bundle directory).
        ollama: Lists the models of the deployment's Ollama server (default :func:`list_ollama_models`; tests inject).
    """

    def __init__(self, store: B.BundleStore, *, release_dir: Path | Callable[[], Path] | None = None,
                 catalog: CachedModelCatalog | None = None, papers: PaperLookup | None = None,
                 prices: Mapping[str, ModelPrice] | None = None,
                 registry: Callable[[], SchemaRegistry] = SchemaRegistry.discover,
                 ollama: Callable[[], OllamaListing] = list_ollama_models,
                 user_schemas: UserSchemaStore | None = None) -> None:
        self.store = store
        self.user_schemas = user_schemas or UserSchemaStore(store.root.parent / SCHEMAS_DIRNAME, bundles_root=store.root)
        self._release = release_dir
        self.catalog = catalog or CachedModelCatalog()
        self.papers = papers
        self.prices = dict(prices or {})
        self._registry = registry
        self._ollama = ollama

    # -------------------------------------------------------------- reads
    def release_dir(self) -> Path:
        """The configured release directory."""
        value = self._release() if callable(self._release) else self._release
        return Path(value) if value is not None else B.default_release_dir()

    def kgs(self) -> dict[str, Any]:
        """``{bundles, sources}``: bundle summaries plus the plain KG files that can be continued."""
        release = self.release_dir()
        sources: list[str] = []
        if release.is_dir():
            sources = sorted((p.name for p in release.iterdir()
                              if _SOURCE_RE.fullmatch(p.name) and p.is_file() and not p.is_symlink()),
                             key=str.lower)
        return {"bundles": [b.summary() for b in self.store.list()], "sources": sources}

    def resolve(self, schema: str) -> dict[str, Any]:
        """The choice for ``schema``: an offered registry schema or a stored user schema (``INVALID_INPUT`` otherwise)."""
        return resolve_schema(schema, self._registry(), self.user_schemas)

    def schemas(self) -> dict[str, Any]:
        """``{schemas}``: the registry's v0.3.x sets, then the user schemas (``origin: user``); no host paths."""
        return {"schemas": [{k: v for k, v in s.items() if k != "path"}
                            for s in list_schemas(self._registry(), self.user_schemas)]}

    def models(self, *, refresh: bool = False) -> dict[str, Any]:
        """The free models of the live list (model policy); ``available=false`` without a backend. Never writes settings.

        ``ollama`` is the status of the deployment's Ollama server (``state``, ``models``, ``error``; no URL), listed
        independently of CBORG so an Ollama model can be chosen when CBORG refuses or is unconfigured.
        """
        from ..model_policy import load_policy

        listing = self.catalog.get(refresh=refresh)
        policy = load_policy()
        # Free-model policy (owner decision 2026-11-09): the picker offers only the free ids of the live list.
        models = policy.filter(listing.models, listing) if listing.available else []
        return {"available": bool(listing.available), "models": models, "source": listing.source,
                "error": listing.error, "error_code": listing.error_code, "backend": listing.backend, "model_policy": policy.describe(),
                "ollama": self._ollama().to_dict()}

    # -------------------------------------------------------------- estimate
    def _facts(self, paper_ids: list[str]) -> list[PaperFacts]:
        if self.papers is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "the paper library is not installed")
        if len(paper_ids) > MAX_PAPER_IDS:
            raise ContractError(ErrorCode.INVALID_INPUT, "too many papers")
        facts = []
        for pid in paper_ids:
            fact = self.papers.paper_facts(pid)
            if fact is None:
                raise ContractError(ErrorCode.INVALID_INPUT, "unknown paper in the selection")
            facts.append(fact)
        return facts

    def estimate(self, paper_ids: list[str], model: str, schema: str) -> dict[str, Any]:
        """Deterministic estimate and confirmation token (the schema must be an offered one)."""
        self.resolve(schema)
        return estimate_extraction(self._facts(paper_ids), model, schema, self.prices)

    def check_confirmation(self, confirmation: str, paper_ids: list[str], model: str, schema: str) -> None:
        """For T39's start path: raise ``STALE_APPROVAL`` unless the token matches this exact selection."""
        self.resolve(schema)
        verify_confirmation(confirmation, self._facts(paper_ids), model, schema, self.prices)

    # -------------------------------------------------------------- bundles
    def export(self, name: str) -> dict[str, Any]:
        """Export bundle ``name`` to the release directory as ``matkg_<name>.json``."""
        bundle = self.store.get(name)
        choice = next((s for s in list_schemas(self._registry(), self.user_schemas) if s["name"] == bundle.schema), None)
        file_name = B.export_bundle(bundle, self.release_dir(),
                                    {"label": choice["label"]} if choice else None)
        size = (self.release_dir() / file_name).stat().st_size
        return {"name": name, "file_name": file_name, "size_bytes": size}

    def export_file(self, name: str) -> tuple[Path, str]:
        """The exported file of bundle ``name`` for download: ``(path, file name)``.

        The file name is derived from the validated bundle name (never from the client) and the file must be a regular
        file, not a link, inside the release directory.

        Raises:
            ContractError: ``NOT_FOUND`` when the bundle does not exist or has not been exported (nothing to download).
        """
        bundle = self.store.get(name)   # NOT_FOUND / INVALID_INPUT for a bad or unknown name
        file_name = B.export_file_name(bundle.name)
        release = self.release_dir()
        path = release / file_name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != release.resolve():
            raise ContractError(ErrorCode.NOT_FOUND, "this KG has not been exported yet", retryable=False)
        return path, file_name

    def continue_kg(self, name: str, *, source: str | None, parent: str | None, schema: str | None,
                    topic: str | None) -> dict[str, Any]:
        """Create bundle ``name`` by continuing a bundle (``parent``) or a plain KG file (``source``).

        A parent keeps its locked schema (a different ``schema`` is refused). A source KG needs ``schema``;
        it is reversed with ``kg_to_terms`` into a child bundle marked ``reconstructed``.
        """
        B.validate_name(name)
        if (source is None) == (parent is None):
            raise ContractError(ErrorCode.INVALID_INPUT, "give exactly one of source and parent")
        release = self.release_dir()
        if (release / B.export_file_name(name)).exists():
            raise ContractError(ErrorCode.CONFLICT, "a KG with this name exists in the release directory",
                retryable=False)
        if parent is not None:
            bundle = self.store.fork(parent, name, schema_id=schema, topic=topic)
            return {**bundle.summary(), "warnings": []}
        if not schema:
            raise ContractError(ErrorCode.INVALID_INPUT, "a schema is required to continue a KG file")
        if not _SOURCE_RE.fullmatch(source or ""):
            raise ContractError(ErrorCode.INVALID_INPUT, "invalid KG file name")
        path = release / str(source)
        if path.is_symlink() or not path.is_file():
            raise ContractError(ErrorCode.NOT_FOUND, "no such KG file")
        bundle, warnings = B.continue_from_kg(
            self.store, path, name, schema=manifest_block(self.resolve(schema)), topic=topic,
            source_label=str(source))
        return {**bundle.summary(), "warnings": warnings[:50]}


__all__ = ("KgLibrary",)
