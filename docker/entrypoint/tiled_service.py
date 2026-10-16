#!/usr/bin/env python3
"""Foreground managed-Tiled service for the ``:local`` image (task T24B).

The supervisor runs this as its ``tiled`` service. It is a thin wrapper over the T20B
lifecycle (``app.modules.deployment.tiled_lifecycle``), so the image uses the same start,
readiness and stop path as ``scripts/tiled_server.py`` and the disposable test fixture:

1. :func:`load_tiled_settings` from the supervisor's fixed environment; the profile must be
   managed (``local``, or ``test`` for the fixture-backed tests).
2. :func:`start_managed`: version probe of the isolated ``/opt/tiled/venv`` (Tiled 0.2.18,
   O-T1), ownership manifest, generated server config without secrets, writer key created
   once in the mounted data dir, wait for health and capabilities C1-C4/C6.
3. Stay in the foreground while the Tiled process lives. SIGTERM/SIGINT ->
   :func:`stop_managed` (process-group SIGTERM, then SIGKILL; data is always kept) -> exit 0.

Nothing is seeded, imported or migrated: an older catalog stops with the explicit
``scripts/tiled_server.py upgrade-catalog`` instruction.

Exit codes: 0 stopped on request; 1 Tiled exited or failed to start (the supervisor may
restart it); 78 permanent configuration/environment problem (wrong profile or mode, missing
data mount, wrong Tiled version, catalog upgrade needed): the supervisor does not restart.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from typing import Mapping, Optional

EXIT_RESTARTABLE = 1
EXIT_PERMANENT = 78


class _Terminate(BaseException):
    """Raised from the signal handler so a blocking start is unwound through its cleanup."""


def _handler(signum, _frame):  # pragma: no cover - exercised through the process tests
    raise _Terminate(signum)


def run(environ: Mapping[str, str], *, poll: float = 0.5, ready_timeout: Optional[float] = None) -> int:
    """Start the managed server and hold it until terminated; returns the exit code."""
    from app.modules.deployment import tiled_lifecycle as lifecycle
    from app.modules.deployment.tiled_config import TiledConfigError, TiledDeploymentError, load_tiled_settings

    permanent = (TiledConfigError, lifecycle.TiledEnvironmentError, lifecycle.TiledCatalogUpgradeNeeded,
                 lifecycle.TiledAlreadyRunning, lifecycle.TiledNotOwned)
    try:
        settings = load_tiled_settings(environ)
    except TiledConfigError as exc:
        print(f"f2w-tiled: refused code={exc.code} {exc}", file=sys.stderr, flush=True)
        return EXIT_PERMANENT
    if settings.managed is None:
        print(f"f2w-tiled: profile {settings.profile} is external (connect-only); no managed server",
              file=sys.stderr, flush=True)
        return EXIT_PERMANENT
    cfg = settings.managed
    timeout = ready_timeout if ready_timeout is not None else float(environ.get("F2W_TILED_READY_TIMEOUT") or 90)
    server = None
    try:
        server = lifecycle.start_managed(cfg, ready_timeout=timeout)
        print(f"f2w-tiled: ready url={server.url} pid={server.pid} data_dir={cfg.data_dir}",
              file=sys.stderr, flush=True)
        while server.process is not None and server.process.poll() is None:
            time.sleep(poll)
        code = server.process.returncode if server.process is not None else None
        print(f"f2w-tiled: server exited code={code}; log={server.log_path}", file=sys.stderr, flush=True)
        lifecycle.stop_managed(server)  # records the manifest state; the group is already gone
        return EXIT_RESTARTABLE
    except _Terminate:
        if server is not None:
            receipt = lifecycle.stop_managed(server)
            print(f"f2w-tiled: stopped signal={receipt.get('signal')} data_retained=True",
                  file=sys.stderr, flush=True)
        return 0
    except permanent as exc:
        print(f"f2w-tiled: refused code={exc.code} {exc}", file=sys.stderr, flush=True)
        if exc.detail.get("log_tail"):
            print(exc.detail["log_tail"], file=sys.stderr, flush=True)
        return EXIT_PERMANENT
    except TiledDeploymentError as exc:
        print(f"f2w-tiled: start failed code={exc.code} {exc}", file=sys.stderr, flush=True)
        if exc.detail.get("log_tail"):
            print(exc.detail["log_tail"], file=sys.stderr, flush=True)
        return EXIT_RESTARTABLE


def main() -> int:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handler)
    return run(os.environ)


if __name__ == "__main__":
    sys.exit(main())
