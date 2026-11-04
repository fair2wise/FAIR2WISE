"""Explicit tool-registry bootstrap (task T02, R-09).

Replaces the donor's import-time registration in
``src/forge/agents/tools/__init__.py`` (als-computing/forge_rsoxs_analysis,
BSD-3-Clause), which registered every FORGE domain when imported. Nothing here
runs at import: the application calls :func:`build_registry` once at startup and
gets a fresh, frozen registry of its own.

Only fake/demo literature-read tools exist so far, backed by an in-memory corpus
passed in by the caller. Real retrieval/QA tools arrive in T03.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..agent_runtime.registry import ToolRegistry, ToolRegistryError
from ..agent_runtime.skills import Skill, SkillRegistry
from ..contracts import CostClass, Effect, RequestContext, ToolResult, ToolSpec, ToolStatus

if TYPE_CHECKING:
    from ..agent_runtime.executor import ToolInvocation

DEMO_LITERATURE = "demo_literature"
KNOWN_GROUPS = frozenset({DEMO_LITERATURE})

_READ_CAPS = frozenset({"chat.ask"})

DEMO_SEARCH = ToolSpec(
    name="demo_lit_search", version="0.1.0", domain="literature",
    description="Search a small in-memory demo corpus by title substring (test fixture, not real retrieval).",
    when_to_use="Tests and demos of the runtime only.",
    when_not_to_use="Any real literature question; T03 supplies real retrieval tools.",
    input_schema={"type": "object", "properties": {"query": {"type": "string", "minLength": 1},
                                                    "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                  "required": ["query"], "additionalProperties": False},
    output_schema={"type": "object", "properties": {"hits": {"type": "array"}}, "required": ["hits"]},
    effects=frozenset({Effect.READ_CORPUS}), cost_class=CostClass.FREE, required_capabilities=_READ_CAPS,
)

DEMO_READ = ToolSpec(
    name="demo_lit_read", version="0.1.0", domain="literature",
    description="Return the abstract of one demo-corpus paper by id (test fixture).",
    when_to_use="After demo_lit_search returned an id.",
    when_not_to_use="Any real literature question.",
    input_schema={"type": "object", "properties": {"paper_id": {"type": "string", "minLength": 1}},
                  "required": ["paper_id"], "additionalProperties": False},
    output_schema={"type": "object",
                   "properties": {"paper_id": {"type": "string"}, "abstract": {"type": "string"}},
                   "required": ["paper_id", "abstract"]},
    effects=frozenset({Effect.READ_CORPUS}), cost_class=CostClass.CHEAP, required_capabilities=_READ_CAPS,
)

DEMO_SKILL = Skill(
    name="search_literature_demo",
    description="Find and read papers in the demo corpus.",
    when_to_use="Runtime tests and demos only.",
    tools=("demo_lit_search", "demo_lit_read"),
)


def _demo_tools(corpus: Mapping[str, Mapping[str, str]]) -> Sequence[tuple[ToolSpec, Callable[..., Any]]]:
    papers = {str(k): dict(v) for k, v in corpus.items()}

    def search(inv: ToolInvocation) -> ToolResult:
        needle = inv.args["query"].lower()
        hits = [{"paper_id": pid, "title": p.get("title", "")} for pid, p in sorted(papers.items())
                if needle in p.get("title", "").lower()][: inv.args.get("limit", 10)]
        if not hits:
            return ToolResult(tool=DEMO_SEARCH.name, status=ToolStatus.EMPTY)
        return ToolResult(tool=DEMO_SEARCH.name, status=ToolStatus.OK, data={"hits": hits})

    def read(inv: ToolInvocation) -> ToolResult:
        paper = papers.get(inv.args["paper_id"])
        if paper is None:
            return ToolResult(tool=DEMO_READ.name, status=ToolStatus.EMPTY)
        return ToolResult(tool=DEMO_READ.name, status=ToolStatus.OK,
                          data={"paper_id": inv.args["paper_id"], "abstract": paper.get("abstract", "")})

    return ((DEMO_SEARCH, search), (DEMO_READ, read))


def build_registry(
    ctx_or_config: Any = None,
    *,
    include: Iterable[str] = (DEMO_LITERATURE,),
    demo_corpus: Mapping[str, Mapping[str, str]] | None = None,
    extra: Iterable[tuple[ToolSpec, Callable[..., Any]]] = (),
    name: str = "f2w",
    freeze: bool = True,
) -> ToolRegistry:
    """Build a fresh tool registry for one application instance.

    Args:
        ctx_or_config: A ``RequestContext``, a settings mapping, or ``None``. It
            does not filter registration: exposure is decided per turn from the
            request context. Accepted so later tasks can read non-secret settings
            here; nothing is read from the environment.
        include: Tool groups to register; only ``"demo_literature"`` exists in T02.
        demo_corpus: In-memory papers for the demo tools ``{id: {"title", "abstract"}}``.
        extra: Additional ``(ToolSpec, fn)`` pairs registered before freezing (tests,
            later tasks).
        name: Registry label.
        freeze: Freeze (and verify) before returning (default).

    Raises:
        ToolRegistryError: On an unknown group or any registration failure.
    """
    if ctx_or_config is not None and not isinstance(ctx_or_config, (RequestContext, Mapping)):
        raise TypeError("ctx_or_config must be a RequestContext, a settings mapping or None")
    groups = set(include)
    unknown = groups - KNOWN_GROUPS
    if unknown:
        raise ToolRegistryError(f"unknown tool groups {sorted(unknown)}; known: {sorted(KNOWN_GROUPS)}")
    registry = ToolRegistry(name=name)
    if DEMO_LITERATURE in groups:
        for spec, fn in _demo_tools(demo_corpus or {}):
            registry.register(spec, fn)
    for spec, fn in extra:
        registry.register(spec, fn)
    return registry.freeze() if freeze else registry


def build_skill_registry(
    registry: ToolRegistry, *, skills: Iterable[Skill] = (), require_coverage: bool = True,
) -> SkillRegistry:
    """Build and freeze the skills for ``registry`` (demo skill included when its tools are)."""
    out = SkillRegistry(registry)
    if all(t in registry for t in DEMO_SKILL.tools):
        out.register(DEMO_SKILL)
    for skill in skills:
        out.register(skill)
    return out.freeze(require_coverage=require_coverage)


__all__: tuple[str, ...] = ("DEMO_LITERATURE", "DEMO_READ", "DEMO_SEARCH", "DEMO_SKILL", "build_registry",
                            "build_skill_registry")
