"""Operations CLI over the same :class:`~app.api.operations.F2WOperations` as ``/v1`` (task T14).

``python -m app.api.cli --root <dir> jobs list`` (and ``jobs get|events|progress|view|cancel|
resume <id>``, ``cases list|show <id>``, ``cases review <id> --revision R --accept|--reject``,
``audit status``, ``ops status``). It calls the shared operations directly with a CLI
context (``actor_kind=cli``), never the HTTP API, so it applies exactly the policy the
routes apply. It runs on the local profile only: ALS has no CLI sign-in, and
:meth:`F2WOperations.cli_context` refuses it.

The profile comes from the startup environment (``F2W_DEPLOYMENT_PROFILE``, default
``local``), read once in :func:`main`; nothing is read at import.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

from ..modules.contracts.errors import ContractError, ErrorCode
from .legacy_ui import deployment_settings_from
from .operations import F2WOperations, build_operations


def build_parser() -> argparse.ArgumentParser:
    """The argument parser."""
    p = argparse.ArgumentParser(prog="python -m app.api.cli", description=__doc__.split("\n\n")[0])
    p.add_argument("--root", required=True, help="Operations directory (jobs, curation and audit stores)")
    sub = p.add_subparsers(dest="area", required=True)

    jobs = sub.add_parser("jobs", help="Jobs")
    jsub = jobs.add_subparsers(dest="action", required=True)
    jl = jsub.add_parser("list")
    jl.add_argument("--state")
    jl.add_argument("--kind")
    jl.add_argument("--limit", type=int, default=100)
    for name in ("get", "events", "progress", "view", "cancel", "resume"):
        sp = jsub.add_parser(name)
        sp.add_argument("job_id")
        if name == "events":
            sp.add_argument("--after", type=int, default=-1)

    cases = sub.add_parser("cases", help="Curation review cases")
    csub = cases.add_subparsers(dest="action", required=True)
    cl = csub.add_parser("list")
    cl.add_argument("--state")
    cl.add_argument("--route")
    cl.add_argument("--limit", type=int, default=100)
    cs = csub.add_parser("show")
    cs.add_argument("case_id")
    cr = csub.add_parser("review")
    cr.add_argument("case_id")
    cr.add_argument("--revision", required=True, help="The case revision you reviewed")
    decision = cr.add_mutually_exclusive_group(required=True)
    decision.add_argument("--accept", action="store_true")
    decision.add_argument("--reject", action="store_true")
    cr.add_argument("--reason", default="")

    audit = sub.add_parser("audit", help="Audit path")
    audit.add_subparsers(dest="action", required=True).add_parser("status")
    ops = sub.add_parser("ops", help="Monitoring status")
    ops.add_subparsers(dest="action", required=True).add_parser("status")
    return p


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


def run(ops: F2WOperations, args: argparse.Namespace) -> Any:
    """Run one parsed command against ``ops`` and return its JSON-ready result."""
    ctx = ops.cli_context()
    area, action = args.area, args.action
    if area == "jobs":
        if action == "list":
            return _dump(ops.list_jobs(ctx, state=args.state, kind=args.kind, limit=args.limit))
        if action == "events":
            return _dump(ops.job_events(ctx, args.job_id, after=args.after))
        call = {"get": ops.get_job, "progress": ops.job_progress, "view": ops.job_view,
                "cancel": ops.cancel_job, "resume": ops.resume_job}[action]
        return _dump(call(ctx, args.job_id))
    if area == "cases":
        if action == "list":
            return ops.list_cases(ctx, state=args.state, route=args.route, limit=args.limit)
        if action == "show":
            return ops.get_case(ctx, args.case_id)
        return ops.review_case(ctx, args.case_id, bound_revision=args.revision, accept=bool(args.accept),
                               reason=args.reason)
    if area == "audit":
        return ops.audit_status(ctx)
    return ops.ops_status(ctx)


def main(argv: Optional[List[str]] = None, *, environ: Optional[Any] = None) -> int:
    """Entry point; prints JSON and returns ``0``, or ``2`` with ``{"code", "message"}`` on a refusal."""
    args = build_parser().parse_args(argv)
    try:
        settings = deployment_settings_from(environ)
        if not settings.is_local:  # ALS has no CLI sign-in (as F2WOperations.cli_context); refuse before any wiring
            raise ContractError(ErrorCode.DENIED, "the operations CLI runs on the local profile only")
        ops = build_operations(settings, args.root, owner="cli")
        print(json.dumps(run(ops, args), indent=2, sort_keys=True, default=str))
    except ContractError as exc:
        print(json.dumps({"code": exc.code.value, "message": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
