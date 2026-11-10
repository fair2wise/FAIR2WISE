"""The chat's graph set on ``:local``: the union of the effective records of every corpus live on the Save target.

Owner decision 2026-11-10 (twenty-second set): the demo corpora (the RSoXS KG and the 580-paper OPV KG) are both
live on one target and the chat must answer over both, with the corpus named on every citation. Before this module
the chat read the session graph only, i.e. the effective record of the corpus the active publication pointer names.

Design (``docs/migration/demo/QA_READINESS.md`` section 10):

* the set is read from the publication state **per turn** (:meth:`ChatCorpusUnion.refresh`): every live corpus
  (``LocalSaveProvider.live_corpora``) contributes its effective record (active snapshot plus in-flight journal
  entries, ``TiledSaveBackend.effective_graph``), so a new publication, a rollback, a retirement or a journaled Save is
  picked up on the next question without a restart;
* each corpus is written once to ``<directory>/<corpus>/matkg_<corpus>.json`` and **cached by
  ``(corpus, snapshot, sha256, journal stamp)``**; an unchanged corpus costs one ``stat`` per turn, a changed corpus
  replaces its own file only (no leak of retired versions);
* the retrieval agent loads each file as its own graph (``graph_id`` is the corpus name): graphs are never
  concatenated, hits are identified by ``(graph_id, node id)``, so two corpora that share a node id cannot collide,
  and the response's query sub-graph prefixes a colliding id with ``<graph_id>:`` (``query_graph_payload``);
* memory and time are bounded: a corpus over ``F2W_QA_MAX_CORPUS_NODES`` things (default 60000) or whose frozen graph
  is over ``F2W_QA_MAX_CORPUS_MB`` (default 150) is **not loaded**, and neither is a corpus that would take the union
  over ``F2W_QA_MAX_UNION_NODES`` (default 120000); the answer says which corpus was left out and why.

With fewer than two live corpora the set is ``None`` and the chat keeps its single-corpus path exactly as before.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any, Protocol

from ..contracts.errors import ContractError
from ..graph.legacy_save import TiledSaveBackend
from .multi_kg import graph_id_for_path

logger = logging.getLogger(__name__)

DEFAULT_MAX_CORPUS_NODES = 60_000
DEFAULT_MAX_UNION_NODES = 120_000
DEFAULT_MAX_CORPUS_MB = 150
_SAFE_CORPUS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _env_int(environ: dict[str, str] | os._Environ[str], name: str, default: int) -> int:
    try:
        value = int(str(environ.get(name, "")).strip())
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class UnionLimits:
    """Size bounds of the chat's graph set."""

    max_corpus_nodes: int = DEFAULT_MAX_CORPUS_NODES
    max_union_nodes: int = DEFAULT_MAX_UNION_NODES
    max_corpus_bytes: int = DEFAULT_MAX_CORPUS_MB * 1024 * 1024

    @classmethod
    def from_env(cls, environ: dict[str, str] | os._Environ[str] | None = None) -> UnionLimits:
        """The limits of ``F2W_QA_MAX_CORPUS_NODES``, ``F2W_QA_MAX_UNION_NODES`` and ``F2W_QA_MAX_CORPUS_MB``."""
        env = os.environ if environ is None else environ
        return cls(
            max_corpus_nodes=_env_int(env, "F2W_QA_MAX_CORPUS_NODES", DEFAULT_MAX_CORPUS_NODES),
            max_union_nodes=_env_int(env, "F2W_QA_MAX_UNION_NODES", DEFAULT_MAX_UNION_NODES),
            max_corpus_bytes=_env_int(env, "F2W_QA_MAX_CORPUS_MB", DEFAULT_MAX_CORPUS_MB) * 1024 * 1024,
        )


class UnionProvider(Protocol):
    """What the union needs of the ``:local`` Save provider (``LocalSaveProvider``)."""

    def live_corpora(self) -> list[str]:
        """Every live corpus of the target, the active pointer's corpus first."""
        ...

    def backend(self, corpus: str | None = None) -> TiledSaveBackend:
        """The Save backend of ``corpus``'s live version."""
        ...


@dataclass(frozen=True)
class CorpusEntry:
    """One corpus of the chat's graph set."""

    corpus: str
    graph_id: str
    snapshot_ref: str
    sha256: str
    nodes: int
    edges: int
    path: Path

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe description (progress event, trace, tests)."""
        return {"corpus": self.corpus, "graph_id": self.graph_id, "snapshot": self.snapshot_ref,
                "nodes": self.nodes, "edges": self.edges}


@dataclass(frozen=True)
class SkippedCorpus:
    """A live corpus the chat did not load, and why."""

    corpus: str
    reason: str
    nodes: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe description."""
        return {"corpus": self.corpus, "reason": self.reason, "nodes": self.nodes}


@dataclass(frozen=True)
class ChatGraphSet:
    """The corpora a chat turn searches (the first one is the active pointer's) and those it left out."""

    corpora: tuple[CorpusEntry, ...]
    skipped: tuple[SkippedCorpus, ...] = ()

    @property
    def files(self) -> list[str]:
        """Graph file paths in retrieval order."""
        return [str(entry.path) for entry in self.corpora]

    @property
    def primary(self) -> Path:
        """The file of the first corpus (what single-graph code paths are given)."""
        return self.corpora[0].path

    def entry_for_graph(self, graph_id: str) -> CorpusEntry | None:
        """The corpus whose graph id is ``graph_id``."""
        return next((entry for entry in self.corpora if entry.graph_id == graph_id), None)

    def describe(self) -> list[dict[str, Any]]:
        """``[{corpus, graph_id, snapshot, nodes, edges}]`` of the searched corpora."""
        return [entry.as_dict() for entry in self.corpora]

    def notice(self) -> str:
        """One italic sentence naming the corpora that were not searched (empty when none)."""
        if not self.skipped:
            return ""
        parts = [f"{item.corpus} ({item.reason})" for item in self.skipped]
        return f"*Not searched: {'; '.join(parts)}.*"


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


@dataclass
class _Cached:
    key: tuple[str, str, str, tuple[int, int] | None]
    result: CorpusEntry | SkippedCorpus


class ChatCorpusUnion:
    """Builds and caches the chat's graph set from the live corpora of the Save target.

    Args:
        directory: Where the per-corpus effective records are written (``<workdir>/chat_corpora``).
        limits: Size bounds; default from the environment.
    """

    def __init__(self, directory: Path, *, limits: UnionLimits | None = None) -> None:
        self.directory = Path(directory)
        self.limits = limits or UnionLimits.from_env()
        self._cache: dict[str, _Cached] = {}
        self._lock = threading.Lock()

    def refresh(self, provider: UnionProvider) -> ChatGraphSet | None:
        """The current graph set, or ``None`` when fewer than two corpora are live (the single-corpus path applies).

        With two or more live corpora the set is returned even when every corpus was left out (``corpora`` empty,
        ``skipped`` says why), so the chat can say so instead of silently answering from one corpus.

        Cheap when nothing changed (one stat per corpus). Never raises for a corpus that cannot be read: it is
        skipped with a reason (``ContractError`` of its backend, unreadable file, over a limit).
        """
        live_fn = getattr(provider, "live_corpora", None)
        if not callable(live_fn):
            return None
        try:
            live = [str(name) for name in live_fn()]
        except ContractError:
            live = []
        with self._lock:
            for gone in [name for name in self._cache if name not in live]:     # retired or rolled off the target
                del self._cache[gone]
                self._remove_files(gone)
            if len(live) < 2:
                return None
            results = [self._entry(provider, corpus) for corpus in live]
            entries: list[CorpusEntry] = []
            skipped: list[SkippedCorpus] = []
            total = 0
            taken: set[str] = set()
            for result in results:
                if isinstance(result, SkippedCorpus):
                    skipped.append(result)
                    continue
                if total + result.nodes > self.limits.max_union_nodes:
                    skipped.append(SkippedCorpus(result.corpus, f"{result.nodes} things would take the union over "
                                                 f"{self.limits.max_union_nodes}", result.nodes))
                    continue
                gid = result.graph_id
                base, suffix = gid, 2
                while gid in taken:                         # the retrieval agent suffixes colliding ids the same way
                    gid = f"{base}_{suffix}"
                    suffix += 1
                taken.add(gid)
                entries.append(result if gid == result.graph_id else CorpusEntry(
                    result.corpus, gid, result.snapshot_ref, result.sha256, result.nodes, result.edges, result.path))
                total += result.nodes
        return ChatGraphSet(tuple(entries), tuple(skipped))

    def _entry(self, provider: UnionProvider, corpus: str) -> CorpusEntry | SkippedCorpus:
        if not _SAFE_CORPUS.fullmatch(corpus):
            return SkippedCorpus(corpus, "unsafe corpus name")
        try:
            backend = provider.backend(corpus)
        except ContractError as exc:
            return SkippedCorpus(corpus, f"not available: {exc}"[:160])
        target = backend.t
        key = (corpus, target.base_snapshot_id, target.base_graph_sha256, _stamp(Path(target.journal.path)))
        cached = self._cache.get(corpus)
        if cached is not None and cached.key == key and (
                isinstance(cached.result, SkippedCorpus) or cached.result.path.is_file()):
            return cached.result
        result = self._build(corpus, backend, f"{corpus}/{target.base_snapshot_id}", target.base_graph_sha256)
        self._cache[corpus] = _Cached(key, result)
        return result

    def _build(self, corpus: str, backend: TiledSaveBackend, snapshot_ref: str, sha256: str) -> CorpusEntry | SkippedCorpus:
        limits = self.limits
        try:
            size = backend.t.base_graph_path.stat().st_size
        except OSError as exc:
            return SkippedCorpus(corpus, f"snapshot unreadable: {type(exc).__name__}")
        if size > limits.max_corpus_bytes:
            return SkippedCorpus(corpus, f"{size // (1024 * 1024)} MB frozen graph, limit "
                                 f"{limits.max_corpus_bytes // (1024 * 1024)} MB")
        try:
            graph = backend.effective_graph()
        except (ContractError, OSError, ValueError) as exc:
            logger.warning("chat corpus %s not loaded: %s", corpus, exc)
            return SkippedCorpus(corpus, f"effective record unavailable: {type(exc).__name__}")
        things = graph.get("things") or []
        edges = graph.get("associations") or []
        if len(things) > limits.max_corpus_nodes:
            return SkippedCorpus(corpus, f"{len(things)} things, limit {limits.max_corpus_nodes}", len(things))
        path = self.directory / corpus / f"matkg_{corpus}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(graph, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.warning("chat corpus %s not written: %s", corpus, exc)
            return SkippedCorpus(corpus, f"cannot be staged: {type(exc).__name__}")
        return CorpusEntry(corpus, graph_id_for_path(str(path)), snapshot_ref, sha256, len(things), len(edges), path)

    def _remove_files(self, corpus: str) -> None:
        folder = self.directory / corpus
        for item in (folder / f"matkg_{corpus}.json", folder / f"matkg_{corpus}.json.tmp"):
            try:
                item.unlink()
            except OSError:
                pass
        try:
            folder.rmdir()
        except OSError:
            pass


__all__ = ("ChatCorpusUnion", "ChatGraphSet", "CorpusEntry", "SkippedCorpus", "UnionLimits", "UnionProvider")
