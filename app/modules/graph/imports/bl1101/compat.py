"""Patch propagation for the legacy ``app.modules.bl1101_ingest`` import path.

The old single module held every function and constant in one namespace, so
``monkeypatch.setattr(bl1101_ingest, "git_sha", fake)`` changed what ``sync_repo`` called. After the split the
functions live in several modules that each bind the names they use. :func:`install` makes the legacy module a
module subclass whose ``setattr`` also rebinds a name in every listed module that still holds the *same object*,
so patches (and their undo) reach the code that uses them while the legacy path keeps exposing one namespace.
"""

from __future__ import annotations

from collections.abc import Iterable
import types

_MISSING = object()
_TARGETS = "__bl1101_targets__"


class PatchPropagatingModule(types.ModuleType):
    """Module whose attribute assignment is mirrored into the split modules that bind the same object."""

    def __setattr__(self, name: str, value: object) -> None:
        """Set ``name`` here and in every target module whose binding is the object being replaced."""
        old = self.__dict__.get(name, _MISSING)
        super().__setattr__(name, value)
        if old is _MISSING:
            return
        for target in self.__dict__.get(_TARGETS, ()):
            if target.__dict__.get(name, _MISSING) is old:
                target.__dict__[name] = value


def install(module: types.ModuleType, targets: Iterable[types.ModuleType]) -> None:
    """Turn ``module`` into a :class:`PatchPropagatingModule` mirroring assignments into ``targets``."""
    module.__dict__[_TARGETS] = tuple(targets)
    module.__class__ = PatchPropagatingModule
