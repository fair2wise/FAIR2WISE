"""Capability groups for scoping one agent turn (task T02, R-05).

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/profiles.py (BSD-3-Clause).
Copyright (c) 2026, The Regents of the University of California, through Lawrence
Berkeley National Laboratory. See the FORGE LICENSE for the full notice.

Kept from the donor: a profile is chosen per run, is never checkpointed, and
selects which tools a turn binds.

Changed: the donor's ``general``/``calibration`` profiles (with their own system
prompts and served-tool lists) are not ported. A profile here is only a named
capability group. It can *narrow* the capabilities of a request context for one
turn; it can never grant one. Privileges always come from the server-built
``RequestContext``. Group names below are T02 proposals, not approved policy
(plan section 10: curator/maintainer are capability groups, not allowlists).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional

from app.modules.contracts import ADMIN_CAPABILITIES, CAPABILITIES, CHAT_CAPABILITIES, RequestContext


@dataclass(frozen=True)
class CapabilityGroup:
    """A named capability set used to narrow one turn.

    Attributes:
        name: Group name.
        capabilities: Capabilities the group allows.
        description: What the group is for.
    """

    name: str
    capabilities: FrozenSet[str]
    description: str = ""

    def __post_init__(self) -> None:
        unknown = self.capabilities - CAPABILITIES
        if unknown:
            raise ValueError(f"capability group {self.name!r} names unknown capabilities {sorted(unknown)}")


#: Proposed groups (pending owner approval as named policy).
LITERATURE_READER = CapabilityGroup("literature_reader", frozenset(CHAT_CAPABILITIES),
                                    "Chat-scoped literature reading, search and evidence.")
CURATOR = CapabilityGroup("curator",
                          frozenset(CHAT_CAPABILITIES | {"curation.approve", "graph.edit", "json.build"}),
                          "Chat plus curation and candidate graph edits.")
MAINTAINER = CapabilityGroup("maintainer", frozenset(CHAT_CAPABILITIES | ADMIN_CAPABILITIES),
                             "Every application capability the context already holds.")

GROUPS: Dict[str, CapabilityGroup] = {g.name: g for g in (LITERATURE_READER, CURATOR, MAINTAINER)}


def get_group(name: Optional[str]) -> Optional[CapabilityGroup]:
    """Return the named group, ``None`` for no narrowing.

    Raises:
        ValueError: For an unknown name.
    """
    if not name:
        return None
    group = GROUPS.get(name.strip().lower())
    if group is None:
        raise ValueError(f"unknown capability group {name!r}; known: {sorted(GROUPS)}")
    return group


def turn_capabilities(ctx: RequestContext, group: Optional[CapabilityGroup] = None) -> FrozenSet[str]:
    """Capabilities for one turn: the context's, intersected with ``group`` if given."""
    caps = frozenset(ctx.capabilities)
    return caps & group.capabilities if group is not None else caps
