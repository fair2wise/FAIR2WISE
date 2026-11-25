"""Library job kinds and their registration (A8, T34).

Four kinds run in the worker process on ``:local`` only:

* ``library_probe``: a no-op that completes at once (proves a worker claims and runs library jobs);
* ``library_verify`` (T35), ``library_index`` (T36), ``library_extract`` (T39), ``library_harvest`` (T45): stubs that fail closed (T35, T36 and T39 supply
  real handlers through ``handlers=``; a kind without one stays a stub)
  with ``unsupported_capability`` until their task supplies a handler through ``handlers=``.

Job identity is idempotent on identical inputs, so "rebuild" is a submission whose ``nonce``
parameter differs (:func:`library_inputs`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import logging
from typing import TYPE_CHECKING, Any

from ..contracts.errors import ContractError, ErrorCode
from ..jobs.keys import OperationInputs
from ..jobs.pipeline import Pipeline, StageContext, StageResult, StageSpec

if TYPE_CHECKING:
    from ..jobs.service import JobService
    from ..security.authorization import DeploymentSettings

logger = logging.getLogger(__name__)

LIBRARY_PROBE_KIND = "library_probe"
LIBRARY_VERIFY_KIND = "library_verify"
LIBRARY_INDEX_KIND = "library_index"
LIBRARY_EXTRACT_KIND = "library_extract"
LIBRARY_HARVEST_KIND = "library_harvest"
LIBRARY_PUBLISH_KIND = "library_publish"
LIBRARY_PASSAGES_KIND = "library_passages"
LIBRARY_RELATIONS_KIND = "library_relations"

#: Job kind to the capability that submits it (all local-operator only, ``LOCAL_ONLY_CAPABILITIES``).
LIBRARY_KIND_CAPABILITIES: Mapping[str, str] = {
    LIBRARY_PROBE_KIND: "library.read",
    LIBRARY_VERIFY_KIND: "library.upload",
    LIBRARY_INDEX_KIND: "library.index",
    LIBRARY_EXTRACT_KIND: "library.extract",
    LIBRARY_HARVEST_KIND: "library.harvest",
    LIBRARY_PUBLISH_KIND: "graph.publish",  # export, adopt and stage a publication plan (never approves)
    LIBRARY_PASSAGES_KIND: "library.extract",  # find supporting text for an existing KG bundle (no model call)
    LIBRARY_RELATIONS_KIND: "library.extract",  # repair the relations of a bundle on a user schema (no model call)
}

#: Name of the ``params`` entry that makes an otherwise identical submission a new job.
NONCE_PARAM = "nonce"

#: A stage body: ``StageContext -> StageResult``.
StageHandler = Callable[[StageContext], StageResult]

#: Which task supplies each stub kind's real handler (named in the failure message).
_OWNER_TASK: Mapping[str, str] = {LIBRARY_VERIFY_KIND: "T35", LIBRARY_INDEX_KIND: "T36",
                                  LIBRARY_EXTRACT_KIND: "T39", LIBRARY_HARVEST_KIND: "T45",
                                  LIBRARY_PUBLISH_KIND: "the publish wiring",
                                  LIBRARY_PASSAGES_KIND: "the passages wiring",
                                  LIBRARY_RELATIONS_KIND: "the relations wiring"}


def not_implemented(kind: str) -> StageHandler:
    """A stage that fails closed: the kind is registered and gated but has no implementation yet."""
    task = _OWNER_TASK.get(kind, "a later task")

    def run(ctx: StageContext) -> StageResult:
        ctx.log(f"{kind} is not implemented until {task}", level="warning")
        raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, f"{kind} is not implemented until {task}")

    return run


def _probe(ctx: StageContext) -> StageResult:
    """No-op: log one line and complete."""
    ctx.log("library probe ok")
    return StageResult(progress={"probed": 1}, data={"ok": True})


def library_inputs(params: Mapping[str, Any] | None = None, *, nonce: str | None = None,
                   corpus_scope: Sequence[str] = ("library",)) -> OperationInputs:
    """Inputs of a library job.

    Args:
        params: Kind-specific parameters (no secrets: :class:`OperationInputs` refuses them).
        nonce: A fresh value to force a rebuild; the same inputs without it name the same job.
        corpus_scope: Corpora the job touches.

    Returns:
        The immutable :class:`OperationInputs`.

    Raises:
        SecretInKey: A parameter name or value looks like a credential.
    """
    merged = dict(params or {})
    if nonce is not None:
        merged[NONCE_PARAM] = str(nonce)
    return OperationInputs(corpus_scope=tuple(corpus_scope), params=merged)


def register_library_jobs(job_service: JobService, settings: DeploymentSettings, *,
                          handlers: Mapping[str, StageHandler] | None = None,
                          pipelines: Mapping[str, Pipeline] | None = None) -> tuple[str, ...]:
    """Register the library job kinds on ``job_service`` (trusted wiring), on ``:local`` only.

    On an ALS profile nothing is registered, so submissions of these kinds are refused as unknown kinds.
    Kinds already registered are left alone (the API and a worker factory may both call this).

    Args:
        job_service: The :class:`~app.modules.jobs.service.JobService`.
        settings: Startup ``DeploymentSettings`` (``is_local`` decides).
        handlers: ``kind -> stage body`` overriding the fail-closed stubs (T35, T36, T39).
        pipelines: ``kind -> Pipeline`` for a kind with several stages (``library_publish``); wins over ``handlers``.

    Returns:
        The kinds this call registered (empty on ALS).
    """
    if not settings.is_local:
        logger.info("library job kinds are not registered on %s (local profile only)", settings.profile)
        return ()
    unknown = (set(handlers or ()) | set(pipelines or ())) - set(LIBRARY_KIND_CAPABILITIES)
    if unknown:
        raise ValueError(f"unknown library job kinds: {sorted(unknown)}")
    registered: list[str] = []
    for kind, capability in LIBRARY_KIND_CAPABILITIES.items():
        if job_service.has_kind(kind):
            continue
        custom = (pipelines or {}).get(kind)
        run = (handlers or {}).get(kind) or (_probe if kind == LIBRARY_PROBE_KIND else not_implemented(kind))
        pipeline = custom or Pipeline([StageSpec("run", run, phase="other", relevant=("corpus_scope", "params"),
                                                 required_capability=capability)])
        job_service.register(kind, pipeline, capability=capability)
        registered.append(kind)
    logger.info("registered library job kinds: %s", ", ".join(registered) or "(none new)")
    return tuple(registered)


__all__ = ("LIBRARY_EXTRACT_KIND", "LIBRARY_HARVEST_KIND", "LIBRARY_INDEX_KIND", "LIBRARY_KIND_CAPABILITIES", "LIBRARY_PASSAGES_KIND", "LIBRARY_PROBE_KIND", "LIBRARY_RELATIONS_KIND",
           "LIBRARY_PUBLISH_KIND", "LIBRARY_VERIFY_KIND", "NONCE_PARAM", "StageHandler", "library_inputs", "not_implemented",
           "register_library_jobs")
