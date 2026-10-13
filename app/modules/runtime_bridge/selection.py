"""Startup hook used by ``AgentPipelineService`` to select the read/QA runtime (task T03).

Kept tiny so ``f2w_agent/api.py`` can import it at module level without
loading the runtime: in the default legacy mode nothing beyond
:mod:`.mode` is imported and the hook returns ``None``.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .mode import RuntimeMode, runtime_mode


def attach_read_path(pipeline: Any, environ: Optional[Mapping[str, str]] = None) -> Optional[Any]:
    """Return the langgraph-mode read path for ``pipeline``, or ``None`` in legacy mode.

    Called once from ``AgentPipelineService.__init__``; ``F2W_RUNTIME`` is read here
    and never per request.

    Raises:
        RuntimeConfigError: Invalid ``F2W_RUNTIME``, or langgraph mode on a non-local profile.
    """
    if runtime_mode(environ) is RuntimeMode.LEGACY:
        return None
    from .legacy_adapter import build_legacy_read_path

    return build_legacy_read_path(pipeline, environ)


def attach_summary_path(pipeline: Any, environ: Optional[Mapping[str, str]] = None) -> Optional[Any]:
    """Return the langgraph-mode ``LegacySummaryPath`` for ``pipeline``, or ``None`` in legacy mode (H2).

    Called once from ``AgentPipelineService.__init__`` beside :func:`attach_read_path`. The
    adapter's executor registry holds ``lit_summarize_extracted_terms`` (T19B opt-in
    registration, ``build_read_registry(paper_summary=...)``); the adapter keeps its documented
    fallback for arguments outside the Tool's bounds.

    Raises:
        RuntimeConfigError: Invalid ``F2W_RUNTIME``, or langgraph mode on a non-local profile.
    """
    if runtime_mode(environ) is RuntimeMode.LEGACY:
        return None
    from app.modules.operations.summary_compat import build_legacy_summary_path

    return build_legacy_summary_path(pipeline, environ)


__all__ = ("attach_read_path", "attach_summary_path")
