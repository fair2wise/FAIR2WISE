"""Skills: task-shaped groupings over one tool registry (task T02, R-02).

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/skills.py (BSD-3-Clause).
Copyright (c) 2026, The Regents of the University of California, through Lawrence
Berkeley National Laboratory. See the FORGE LICENSE for the full notice.

Kept from the donor: register-time referential integrity (a skill may only name
tools in its registry), the coverage check in ``verify()`` (every tool belongs to
a skill), ``resolve`` and a generated prompt section that describes when each
skill applies without listing tool names.

Changed: no singleton and no import of a global tool catalog; the donor's
``general``/``calibration`` profiles are replaced by capability-based exposure
(a skill is visible to a turn only when the request context holds its required
capabilities and at least one of its tools is visible). Skills carry guidance,
never authorization: the executor still checks every call.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from ..contracts import CAPABILITIES, RequestContext

from .registry import FORBIDDEN_NAME_PREFIXES, RegisteredTool, ToolRegistry, effective_capabilities

SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*[a-z0-9]$", re.ASCII)

#: Skill-name fragments that stay in FORGE (simulation, calibration, fitting measured data).
#: ``analyze_paper_figures`` is a planned literature skill, so "analy" is not banned by name.
FORBIDDEN_SKILL_WORDS: Tuple[str, ...] = ("simulat", "calibrat", "fit_measured")


class SkillRegistryError(Exception):
    """Raised when skill registration or verification fails."""


@dataclass(frozen=True)
class Skill:
    """One job and the tools it needs.

    Attributes:
        name: snake_case identifier.
        description: What the skill accomplishes, for a model choosing between skills.
        when_to_use: Guidance for that choice.
        tools: Canonical registry tool names, in preferred order.
        required_capabilities: Capabilities a context needs to see this skill at all,
            in addition to each tool's own requirements.
    """

    name: str
    description: str
    when_to_use: str
    tools: Tuple[str, ...] = ()
    required_capabilities: FrozenSet[str] = field(default_factory=frozenset)


class SkillRegistry:
    """Skills over one :class:`ToolRegistry`.

    Args:
        tool_registry: The registry every named tool must exist in.
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        if not isinstance(tool_registry, ToolRegistry):
            raise SkillRegistryError("SkillRegistry needs an explicit ToolRegistry instance")
        self._tools = tool_registry
        self._skills: Dict[str, Skill] = {}
        self._frozen = False
        self._lock = threading.Lock()

    @property
    def tool_registry(self) -> ToolRegistry:
        """The registry this skill set is bound to."""
        return self._tools

    def register(self, skill: Skill) -> None:
        """Register ``skill``.

        Raises:
            SkillRegistryError: If frozen, malformed, duplicated, experimental,
                missing prose or tools, naming an absent tool, or requiring an
                unknown capability.
        """
        with self._lock:
            if self._frozen:
                raise SkillRegistryError("skill registry is frozen")
            if not SKILL_NAME_PATTERN.match(skill.name):
                raise SkillRegistryError(f"skill name {skill.name!r} must be snake_case")
            if any(word in skill.name for word in FORBIDDEN_SKILL_WORDS):
                raise SkillRegistryError(f"skill {skill.name!r} is an experimental FORGE skill")
            if skill.name in self._skills:
                raise SkillRegistryError(f"skill {skill.name!r} is already registered")
            if not skill.description.strip():
                raise SkillRegistryError(f"skill {skill.name!r} has an empty description")
            if not skill.when_to_use.strip():
                raise SkillRegistryError(f"skill {skill.name!r} has an empty when_to_use")
            if not skill.tools:
                raise SkillRegistryError(f"skill {skill.name!r} names no tools")
            experimental = [t for t in skill.tools if t.startswith(FORBIDDEN_NAME_PREFIXES)]
            if experimental:
                raise SkillRegistryError(f"skill {skill.name!r} names experimental tools: {experimental}")
            missing = [t for t in skill.tools if self._tools.get(t) is None]
            if missing:
                raise SkillRegistryError(f"skill {skill.name!r} names tools absent from the registry: {missing}")
            duplicated = sorted({t for t in skill.tools if skill.tools.count(t) > 1})
            if duplicated:
                raise SkillRegistryError(f"skill {skill.name!r} names the same tool twice: {duplicated}")
            unknown = skill.required_capabilities - CAPABILITIES
            if unknown:
                raise SkillRegistryError(f"skill {skill.name!r} requires unknown capabilities {sorted(unknown)}")
            self._skills[skill.name] = skill

    def freeze(self, *, require_coverage: bool = True) -> "SkillRegistry":
        """Verify, then refuse every further registration. Returns ``self``."""
        self.verify(require_coverage=require_coverage)
        with self._lock:
            self._frozen = True
        return self

    def get(self, name: str) -> Optional[Skill]:
        """Return the skill called ``name`` or ``None``."""
        return self._skills.get(name)

    def list_skills(self) -> List[Skill]:
        """All skills, sorted by name."""
        return sorted(self._skills.values(), key=lambda s: s.name)

    def skills_for_tool(self, tool_name: str) -> List[Skill]:
        """Every skill that includes ``tool_name``, sorted by name."""
        return sorted((s for s in self._skills.values() if tool_name in s.tools), key=lambda s: s.name)

    def resolve(self, name: str) -> List[RegisteredTool]:
        """Return the registry entries behind a skill, in declared order.

        Raises:
            SkillRegistryError: If the skill is unknown or a tool has disappeared.
        """
        skill = self._skills.get(name)
        if skill is None:
            raise SkillRegistryError(f"unknown skill {name!r}; registered: {sorted(self._skills)}")
        resolved = []
        for tool_name in skill.tools:
            entry = self._tools.get(tool_name)
            if entry is None:  # pragma: no cover - defensive; register() proved existence
                raise SkillRegistryError(f"skill {name!r} names {tool_name!r}, which is no longer registered")
            resolved.append(entry)
        return resolved

    def verify(self, *, require_coverage: bool = True) -> None:
        """Check referential integrity and, by default, that every tool belongs to a skill."""
        for key, skill in self._skills.items():
            if key != skill.name:
                raise SkillRegistryError(f"skill key {key!r} does not match its name {skill.name!r}")
            dangling = [t for t in skill.tools if self._tools.get(t) is None]
            if dangling:
                raise SkillRegistryError(f"skill {key!r} names unregistered tools: {dangling}")
        if require_coverage:
            claimed = {t for s in self._skills.values() for t in s.tools}
            orphans = sorted({s.name for s in self._tools.list()} - claimed)
            if orphans:
                raise SkillRegistryError(f"{len(orphans)} registered tool(s) belong to no skill: {orphans}")

    def visible_to(self, ctx: RequestContext, *, scope: Optional[FrozenSet[str]] = None) -> List[Skill]:
        """Skills a turn may see: required capabilities held and at least one tool visible.

        Each returned skill lists only the tools visible to ``ctx``; registration
        order is kept so the rendered prompt reads as a capability map.
        """
        caps = effective_capabilities(ctx, scope)
        visible_tools = {e.name for e in self._tools.visible_to(ctx, scope=scope)}
        out = []
        for skill in self._skills.values():
            if not skill.required_capabilities <= caps:
                continue
            tools = tuple(t for t in skill.tools if t in visible_tools)
            if tools:
                out.append(Skill(skill.name, skill.description, skill.when_to_use, tools,
                                 skill.required_capabilities))
        return out

    def render_prompt_section(self, ctx: RequestContext, *, scope: Optional[FrozenSet[str]] = None) -> str:
        """Prompt text for the skills visible to ``ctx``; empty when none are visible."""
        return render_prompt_section(self.visible_to(ctx, scope=scope))


def render_prompt_section(skills: List[Skill]) -> str:
    """Render skills as prompt text (description, when-to-use, tool count; no tool names)."""
    if not skills:
        return ""
    lines = ["Available skills, and when each applies:", ""]
    for skill in skills:
        count = len(skill.tools)
        lines.append(f"- {skill.name} ({count} {'tool' if count == 1 else 'tools'}): {skill.description}")
        lines.append(f"  When: {skill.when_to_use}")
    return "\n".join(lines)
