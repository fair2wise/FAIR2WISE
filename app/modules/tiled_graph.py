"""Deprecated import path for the live Tiled lookup (T17B real port, H8).

The module lives in :mod:`app.modules.graph.backends.tiled_lookup`.

The old name stays importable because the retained legacy UI contract tests
(``tests/contracts/legacy_ui``) and external scripts reference it. The alias
below makes ``app.modules.tiled_graph`` *the same module object* as the new
home, so attribute patches at either path apply to both. New code imports the
new path; this file is removed with the other H8 shims.
"""

from __future__ import annotations

import sys

from app.modules.graph.backends import tiled_lookup as _tiled_lookup

sys.modules[__name__] = _tiled_lookup
