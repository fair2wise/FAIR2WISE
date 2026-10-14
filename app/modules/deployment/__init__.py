"""Deployment-time services for F2W (task T20B): the Tiled Graph server and its connection.

Import the submodules directly:

* ``app.modules.deployment.tiled_config``: validated ``managed`` (local, application-owned)
  and ``external`` (central, connect-only) configuration from trusted startup settings,
  the exact Tiled pin and the generated server configuration (no secrets in it).
* ``app.modules.deployment.tiled_capabilities``: version/capability checks against any
  running Tiled (C1-C4, C6 of ``docs/adr/tiled-deployment.md``) and ``connect_external``.
  It never imports the lifecycle module, so external mode cannot start or stop a server.
* ``app.modules.deployment.tiled_lifecycle``: explicit start/ready/status/stop of an
  application-managed server in its own Python environment, and owned-test cleanup.

This package ``__init__`` imports nothing, so importing it has no side effects.
"""
