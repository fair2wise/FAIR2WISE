"""Access control for F2W (task T25B): deployment profiles, request context and effect guards.

Import the submodules directly:

* ``app.modules.security.authorization``: startup settings, ``resolve_context``,
  ``require``/``requires``, the per-route policy, worker authority and approvals.
* ``app.modules.security.tiled_identity``: the ALS ``TiledIdentityPort`` and its
  tiled 0.2.18 ``whoami`` adapter.

This package ``__init__`` imports nothing, so importing it has no side effects.
"""
