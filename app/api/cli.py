"""Operations CLI over the same :class:`~app.api.operations.F2WOperations` as ``/v1`` (tasks T14, T19E).

``python -m app.api.cli --root <dir> jobs list`` (and ``jobs get|events|progress|view|cancel|
resume <id>``, ``cases list|show <id>``, ``cases review <id> --revision R --accept|--reject``,
``audit status``, ``ops status``). It calls the shared operations directly with a CLI
context (``actor_kind=cli``), never the HTTP API, so it applies exactly the policy the
routes apply. It runs on the local profile only: ALS has no CLI sign-in, and
:meth:`F2WOperations.cli_context` refuses it.

Knowledge Operations (T19E; the CLI wrappers of the five T19C operations, which replace
``scripts/analyze_kgs.py`` and ``scripts/corpus_qa.py`` as entry points while those scripts
stay as the parity oracle): ``ops statistics --graph G``, ``ops compare --graph A --graph B``,
``ops paper-subgraph --graph G --paper P``, ``ops recent --graph G [--since T] [--limit N]``,
``ops corpus-audit --corpus C [--section S ...]``, ``ops submit --operation NAME --args JSON``
(a ``knowledge_operation`` job) and ``ops result <job_id>``. Graphs come from
``<project-root>/storage/kg`` (or ``--graph-file`` overrides) and corpora from
``<project-root>/storage/terminology``; the output is the bounded typed result the agent
Tool and ``/v1/operations`` return (``status`` ``ok`` or ``partial``).

The profile comes from the startup environment (``F2W_DEPLOYMENT_PROFILE``, default
``local``), read once in :func:`main`; nothing is read at import.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from ..modules.contracts.errors import ContractError, ErrorCode
from .legacy_ui import deployment_settings_from
from .operations import F2WOperations, build_operations


def build_parser() -> argparse.ArgumentParser:
    """The argument parser."""
    p = argparse.ArgumentParser(prog="python -m app.api.cli", description=__doc__.split("\n\n")[0])
    p.add_argument("--root", required=True, help="Operations directory (jobs, curation and audit stores)")
    p.add_argument("--project-root", default=".",
                   help="Directory holding storage/kg (graphs) and storage/terminology (corpora); default: cwd")
    p.add_argument("--graph-file", action="append", default=None,
                   help="JSON graph file for the knowledge operations (repeatable; overrides storage/kg)")
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
    ops = sub.add_parser("ops", help="Monitoring status and knowledge operations (T19E)")
    osub = ops.add_subparsers(dest="action", required=True)
    osub.add_parser("status")
    st = osub.add_parser("statistics", help="kg_compute_statistics of one graph")
    st.add_argument("--graph", required=True, dest="graph_id")
    cmp_ = osub.add_parser("compare", help="kg_compare_snapshots over graphs in the given order")
    cmp_.add_argument("--graph", required=True, action="append", dest="graph_ids")
    sg = osub.add_parser("paper-subgraph", help="kg_paper_subgraph: what one graph holds about one Work")
    sg.add_argument("--graph", required=True, dest="graph_id")
    sg.add_argument("--paper", required=True)
    sg.add_argument("--max-nodes", type=int, default=None)
    sg.add_argument("--max-edges", type=int, default=None)
    rc = osub.add_parser("recent", help="kg_recent_entities of a live (Tiled) graph")
    rc.add_argument("--graph", required=True, dest="graph_id")
    rc.add_argument("--since", default=None)
    rc.add_argument("--limit", type=int, default=None)
    ca = osub.add_parser("corpus-audit", help="curate_audit_corpus of one configured corpus")
    ca.add_argument("--corpus", required=True)
    ca.add_argument("--section", action="append", default=None, dest="sections")
    sm = osub.add_parser("submit", help="Submit a knowledge_operation job (whole population)")
    sm.add_argument("--operation", required=True)
    sm.add_argument("--args", default="{}", help="JSON object of Tool arguments")
    sm.add_argument("--scope", action="append", default=None, help="corpus_scope entry (repeatable)")
    rs = osub.add_parser("result", help="Handle and bounded report of a knowledge_operation job")
    rs.add_argument("job_id")
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
    if action == "status":
        return ops.ops_status(ctx)
    if action == "statistics":
        return _dump(ops.graph_statistics(ctx, args.graph_id))
    if action == "compare":
        return _dump(ops.compare_graphs(ctx, args.graph_ids))
    if action == "paper-subgraph":
        return _dump(ops.paper_subgraph(ctx, args.graph_id, args.paper, max_nodes=args.max_nodes,
                                        max_edges=args.max_edges))
    if action == "recent":
        return _dump(ops.recent_entities(ctx, args.graph_id, since=args.since, limit=args.limit))
    if action == "corpus-audit":
        return _dump(ops.audit_corpus(ctx, args.corpus, sections=args.sections))
    if action == "submit":
        try:
            tool_args: dict[str, Any] = json.loads(args.args)
        except ValueError:
            raise ContractError(ErrorCode.INVALID_INPUT, "--args must be a JSON object") from None
        if not isinstance(tool_args, dict):
            raise ContractError(ErrorCode.INVALID_INPUT, "--args must be a JSON object")
        return _dump(ops.submit_knowledge_operation(ctx, args.operation, tool_args, corpus_scope=args.scope))
    return ops.knowledge_job_result(ctx, args.job_id)


def build_knowledge(project_root: str, graph_files: list[str] | None = None) -> Any:
    """Build the CLI's ``KnowledgeOperations``.

    Reads ``storage/kg`` or the explicit graph files, plus the ``storage/terminology`` corpora.
    """
    from ..modules.operations.corpus import CorpusAuditTool
    from ..modules.operations.knowledge import KnowledgeOperations, directory_corpus_resolver

    root = Path(project_root)
    if not graph_files:
        return KnowledgeOperations.from_storage(root)
    files = [str(Path(f)) for f in graph_files]
    corpus = CorpusAuditTool(directory_corpus_resolver(root / "storage" / "terminology"))
    return KnowledgeOperations.from_graph_files(lambda: files, corpus=corpus)


def main(argv: list[str] | None = None, *, environ: Any | None = None) -> int:
    """Entry point; prints JSON and returns ``0``, or ``2`` with ``{"code", "message"}`` on a refusal."""
    args = build_parser().parse_args(argv)
    try:
        settings = deployment_settings_from(environ)
        if not settings.is_local:  # ALS has no CLI sign-in (as F2WOperations.cli_context); refuse before any wiring
            raise ContractError(ErrorCode.DENIED, "the operations CLI runs on the local profile only")
        ops = build_operations(settings, args.root, owner="cli",
                               knowledge=build_knowledge(args.project_root, args.graph_file))
        if args.area == "ops" and args.action == "submit":
            from ..modules.operations.knowledge import knowledge_job_context

            ops.register_knowledge_jobs(knowledge_job_context(settings))  # the kind the worker runs (same store)
        print(json.dumps(run(ops, args), indent=2, sort_keys=True, default=str))
    except ContractError as exc:
        print(json.dumps({"code": exc.code.value, "message": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
