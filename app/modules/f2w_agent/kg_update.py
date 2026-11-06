"""Deprecated import path for the KG update helpers (T17B real port, H8).

The module lives in :mod:`app.modules.graph.kg_update`.

The old name stays importable because the retained legacy UI contract tests
(``tests/contracts/legacy_ui``) patch its attributes. The alias below makes
``app.modules.f2w_agent.kg_update`` *the same module object* as the new home,
so a patch at either path applies to both. New code imports the new path; this
file is removed with the other H8 shims.
"""

from __future__ import annotations

import sys

from app.modules.graph import kg_update as _kg_update

sys.modules[__name__] = _kg_update
