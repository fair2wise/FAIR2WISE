"""Checkpointer lifecycle and thread identity (task T02, R-07).

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/checkpointer.py (BSD-3-Clause).
Copyright (c) 2026, The Regents of the University of California, through Lawrence
Berkeley National Laboratory. See the FORGE LICENSE for the full notice.

Kept from the donor: the checkpointer is constructed once by the owner and
injected into the graph; a SQLite saver's ``from_conn_string`` is a context
manager that must be entered and later exited (closing its connection).

Changed: no process-wide singleton, no ``FORGE_CHECKPOINT_DB`` environment read
and no default path next to the module. The path is passed explicitly. The
default is LangGraph's ``InMemorySaver``. SQLite is optional and imported lazily;
``langgraph-checkpoint-sqlite`` is not in the current F2W lock, so selecting it
raises ``ImportError`` until an extra adds it. Reset refuses while runs are
active (no destructive reset during active jobs). Thread IDs are namespaced by
principal and session so two principals cannot address the same thread.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import threading
from typing import Any, Literal

from ..contracts import RequestContext


@dataclass(frozen=True)
class CheckpointerConfig:
    """How to build a checkpointer.

    Attributes:
        kind: ``"memory"`` (default) or ``"sqlite"``.
        path: SQLite file path; required for ``"sqlite"``. Its parent must exist.
    """

    kind: Literal["memory", "sqlite"] = "memory"
    path: Path | None = None


class CheckpointerBusyError(RuntimeError):
    """Raised when a reset or close is attempted while runs are active."""


class ManagedCheckpointer:
    """Owns one LangGraph checkpoint saver and its lifecycle.

    Use as a context manager, or call :meth:`open`/:meth:`close`. ``saver`` is
    what ``build_literature_supervisor(checkpointer=...)`` takes.

    Args:
        config: :class:`CheckpointerConfig`; defaults to in-memory.
    """

    def __init__(self, config: CheckpointerConfig | None = None) -> None:
        self.config = config or CheckpointerConfig()
        self._saver: Any = None
        self._cm: Any = None
        self._active: dict[str, int] = {}
        self._lock = threading.Lock()

    def open(self) -> Any:
        """Construct the saver (idempotent) and return it."""
        with self._lock:
            if self._saver is not None:
                return self._saver
            if self.config.kind == "memory":
                from langgraph.checkpoint.memory import InMemorySaver

                self._saver = InMemorySaver()
            elif self.config.kind == "sqlite":
                if self.config.path is None:
                    raise ValueError("sqlite checkpointer needs an explicit path")
                try:
                    from langgraph.checkpoint.sqlite import SqliteSaver
                except ImportError as exc:
                    raise ImportError(
                        "durable SQLite checkpoints need langgraph-checkpoint-sqlite, which is not in the "
                        "current F2W lock; add it through the agents extra (integration lead)"
                    ) from exc
                self._cm = SqliteSaver.from_conn_string(str(self.config.path))
                self._saver = self._cm.__enter__()
            else:
                raise ValueError(f"unknown checkpointer kind {self.config.kind!r}")
            return self._saver

    @property
    def saver(self) -> Any:
        """The open saver; opens it on first access."""
        return self._saver if self._saver is not None else self.open()

    @contextmanager
    def run_scope(self, thread_id: str) -> Iterator[None]:
        """Mark a run on ``thread_id`` active for the duration of the block."""
        with self._lock:
            self._active[thread_id] = self._active.get(thread_id, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._active[thread_id] -= 1
                if not self._active[thread_id]:
                    del self._active[thread_id]

    def active_threads(self) -> dict[str, int]:
        """Threads with active runs and their counts."""
        with self._lock:
            return dict(self._active)

    def delete_thread(self, thread_id: str) -> None:
        """Delete one thread's checkpoints; refuses while that thread has an active run."""
        with self._lock:
            if self._active.get(thread_id):
                raise CheckpointerBusyError(f"thread {thread_id!r} has an active run")
        self.saver.delete_thread(thread_id)

    def close(self, *, force: bool = False) -> None:
        """Close the saver; refuses while any run is active unless ``force``."""
        with self._lock:
            if self._active and not force:
                raise CheckpointerBusyError(f"{len(self._active)} thread(s) have active runs")
            cm, self._cm, self._saver = self._cm, None, None
        if cm is not None:
            cm.__exit__(None, None, None)

    reset = close

    def __enter__(self) -> ManagedCheckpointer:
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close(force=True)


def scoped_thread_id(ctx: RequestContext, thread_id: str) -> str:
    """Namespace ``thread_id`` by profile, principal and session.

    The result is stable for the same principal/session and differs for any
    other, so a client-supplied thread name cannot reach another user's state.
    """
    owner = "|".join((ctx.profile.value, ctx.principal.issuer or "", ctx.principal.subject, ctx.session_id or ""))
    digest = hashlib.sha256(owner.encode()).hexdigest()[:16]
    return f"{digest}:{thread_id}"


def thread_config(ctx: RequestContext, thread_id: str, *, recursion_limit: int | None = None) -> dict[str, Any]:
    """LangGraph ``config`` for one scoped thread."""
    config: dict[str, Any] = {"configurable": {"thread_id": scoped_thread_id(ctx, thread_id)}}
    if recursion_limit is not None:
        config["recursion_limit"] = recursion_limit
    return config


def owner_key(ctx: RequestContext) -> str:
    """Opaque key identifying who owns a thread (profile, issuer, subject, session)."""
    return scoped_thread_id(ctx, "").rstrip(":")
