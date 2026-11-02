"""Instance-scoped tool registry for the literature runtime (task T02, R-01).

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/registry.py (BSD-3-Clause).
Copyright (c) 2026, The Regents of the University of California, through Lawrence
Berkeley National Laboratory. See the FORGE LICENSE for the full notice.

Kept from the donor: register-time rejection of malformed or duplicate names,
``get``/``list``/``list_by_domain``, and a ``verify()`` integrity pass.

Changed:

- No singleton and no module-level registry. Each application or runtime builds
  its own instance through ``app.modules.tools.bootstrap.build_registry``; two
  registries in one process never share state.
- Entries are :class:`~app.modules.contracts.ToolSpec` plus an implementation,
  not ad hoc dicts. Exposure is by capability (``visible_to``), not domain.
- The donor's ``{catalog, analysis, sim, ...}`` domain set is replaced by a
  literature-only allowlist; experimental names and FORGE implementations are
  refused outright.
- ``freeze()`` after startup: any later mutation raises.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import threading
from typing import Any

from ..contracts import CAPABILITIES, RequestContext, ToolSpec

#: Domains a literature runtime may register. Experimental FORGE domains are absent.
LITERATURE_DOMAINS: frozenset[str] = frozenset({
    "literature", "graph", "retrieval", "extraction", "canonicalization", "curation", "vision", "science",
    "operations",
})

#: Domains and name prefixes that stay in FORGE (simulation, analysis, calibration, catalog).
FORBIDDEN_DOMAINS: frozenset[str] = frozenset({"sim", "simulation", "analysis", "calibration", "catalog"})
FORBIDDEN_NAME_PREFIXES: tuple[str, ...] = ("sim_", "analysis_", "calibration_", "catalog_")
FORBIDDEN_MODULE_ROOTS: frozenset[str] = frozenset({"forge", "als_knowledge_agent", "reduction_api"})


class ToolRegistryError(Exception):
    """Raised when registration or verification fails."""


@dataclass(frozen=True)
class RegisteredTool:
    """One registry entry.

    Attributes:
        spec: The tool's contract.
        fn: Implementation, called by the executor as ``fn(invocation)``; may be
            sync or ``async``. Returns a ``ToolResult`` or a JSON-able mapping.
    """

    spec: ToolSpec
    fn: Callable[..., Any]

    @property
    def name(self) -> str:
        """Canonical tool name."""
        return self.spec.name


def _module_root(fn: Any) -> str:
    module = getattr(fn, "__module__", None) or getattr(type(fn), "__module__", "") or ""
    return module.split(".")[0]


def _check_schema(spec: ToolSpec) -> None:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    for label, schema in (("input_schema", spec.input_schema), ("output_schema", spec.output_schema)):
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ToolRegistryError(f"tool {spec.name!r} has an invalid {label}: {exc.message}") from exc


class ToolRegistry:
    """Registry of tools for one application/runtime instance.

    Args:
        name: Label used in error messages (for example the application name).
        allowed_domains: Domains this registry accepts; defaults to
            :data:`LITERATURE_DOMAINS`. Forbidden domains are refused regardless.
    """

    def __init__(self, *, name: str = "f2w", allowed_domains: Iterable[str] | None = None) -> None:
        self.name = name
        self._allowed = frozenset(allowed_domains) if allowed_domains is not None else LITERATURE_DOMAINS
        bad = self._allowed & FORBIDDEN_DOMAINS
        if bad:
            raise ToolRegistryError(f"experimental domains cannot be allowed: {sorted(bad)}")
        self._tools: dict[str, RegisteredTool] = {}
        self._aliases: dict[str, str] = {}
        self._frozen = False
        self._lock = threading.Lock()

    # -- registration ---------------------------------------------------------

    def register(self, spec: ToolSpec, fn: Callable[..., Any]) -> RegisteredTool:
        """Register ``spec`` with implementation ``fn``.

        Raises:
            ToolRegistryError: If frozen, the name or an alias collides, the
                domain is not allowed, the name is experimental, ``fn`` is not
                callable or comes from FORGE, a capability is outside the frozen
                vocabulary, or a schema is invalid.
        """
        if not isinstance(spec, ToolSpec):
            raise ToolRegistryError("register() needs a contracts.ToolSpec")
        with self._lock:
            if self._frozen:
                raise ToolRegistryError(
                    f"registry {self.name!r} is frozen; register tools before startup completes")
            if spec.domain in FORBIDDEN_DOMAINS or spec.name.startswith(FORBIDDEN_NAME_PREFIXES):
                raise ToolRegistryError(f"tool {spec.name!r} belongs to an experimental FORGE domain")
            if spec.domain not in self._allowed:
                raise ToolRegistryError(
                    f"tool {spec.name!r} has domain {spec.domain!r}; allowed: {sorted(self._allowed)}"
                )
            if not callable(fn):
                raise ToolRegistryError(f"tool {spec.name!r} implementation is not callable")
            if _module_root(fn) in FORBIDDEN_MODULE_ROOTS:
                raise ToolRegistryError(f"tool {spec.name!r} implementation is imported from FORGE")
            unknown = spec.required_capabilities - CAPABILITIES
            if unknown:
                raise ToolRegistryError(f"tool {spec.name!r} requires unknown capabilities {sorted(unknown)}")
            taken = set(self._tools) | set(self._aliases)
            names = (spec.name, *spec.aliases)
            clash = sorted(n for n in names if n in taken)
            if clash or len(set(names)) != len(names):
                raise ToolRegistryError(f"tool {spec.name!r} collides with registered names: {clash or names}")
            _check_schema(spec)
            entry = RegisteredTool(spec=spec, fn=fn)
            self._tools[spec.name] = entry
            for alias in spec.aliases:
                self._aliases[alias] = spec.name
            return entry

    def freeze(self) -> ToolRegistry:
        """Verify, then refuse every further mutation. Returns ``self``."""
        self.verify()
        with self._lock:
            self._frozen = True
        return self

    @property
    def frozen(self) -> bool:
        """Whether :meth:`freeze` has been called."""
        return self._frozen

    # -- reads ----------------------------------------------------------------

    def get(self, name: str) -> RegisteredTool | None:
        """Return the entry for canonical ``name`` (aliases are not resolved here)."""
        return self._tools.get(name)

    def resolve_alias(self, name: str) -> str | None:
        """Return the canonical name for a legacy alias, for compatibility boundaries only."""
        return name if name in self._tools else self._aliases.get(name)

    def list(self) -> builtins.list[ToolSpec]:
        """All specs, sorted by name."""
        return [self._tools[n].spec for n in sorted(self._tools)]

    def list_by_domain(self, domain: str) -> builtins.list[ToolSpec]:
        """Specs in ``domain``, sorted by name."""
        return [s for s in self.list() if s.domain == domain]

    def visible_to(self, ctx: RequestContext, *, scope: frozenset[str] | None = None) -> builtins.list[RegisteredTool]:
        """Entries whose every required capability ``ctx`` holds (narrowed by ``scope``)."""
        caps = effective_capabilities(ctx, scope)
        return [self._tools[n] for n in sorted(self._tools) if self._tools[n].spec.required_capabilities <= caps]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # -- integrity ------------------------------------------------------------

    def verify(self) -> None:
        """Check every invariant; raises :class:`ToolRegistryError` on the first violation."""
        seen_aliases: dict[str, str] = {}
        for key, entry in self._tools.items():
            spec = entry.spec
            if key != spec.name:
                raise ToolRegistryError(f"registry key {key!r} does not match tool name {spec.name!r}")
            if not callable(entry.fn):
                raise ToolRegistryError(f"tool {key!r} implementation is not callable")
            if spec.domain not in self._allowed or spec.domain in FORBIDDEN_DOMAINS:
                raise ToolRegistryError(f"tool {key!r} has disallowed domain {spec.domain!r}")
            if spec.required_capabilities - CAPABILITIES:
                raise ToolRegistryError(f"tool {key!r} requires unknown capabilities")
            for alias in spec.aliases:
                if alias in self._tools or alias in seen_aliases:
                    raise ToolRegistryError(f"alias {alias!r} of {key!r} collides")
                seen_aliases[alias] = key
        if seen_aliases != self._aliases:
            raise ToolRegistryError("alias index is out of sync with registered tools")


def effective_capabilities(ctx: RequestContext, scope: frozenset[str] | None = None) -> frozenset[str]:
    """``ctx.capabilities`` narrowed (never widened) by an optional per-turn ``scope``."""
    caps = frozenset(ctx.capabilities)
    return caps & frozenset(scope) if scope is not None else caps
