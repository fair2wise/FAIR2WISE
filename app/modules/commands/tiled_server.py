#!/usr/bin/env python3
r"""Operator CLI for the F2W Tiled server (T20B): a thin facade over the deployment modules.

Console script: ``f2w-tiled-server`` (``scripts/tiled_server.py`` stays as a launch facade).

Not an agent tool and not a general shell: it exposes only the explicit lifecycle
operations of ``app.modules.deployment``. Settings come from the trusted startup
environment (``F2W_DEPLOYMENT_PROFILE``, ``F2W_TILED_*``; see
``app/modules/deployment/tiled_config.py``); flags override them for this invocation.
No ``.env`` is read. Output is JSON on stdout; errors are JSON on stderr with a stable
``code`` and exit status 2.

Commands::

    start            start the managed server and wait until ready (managed mode)
    status           report the managed server state without changing anything
    stop             stop the managed server; data is kept
    check            run the read-only version/capability checks (either mode; never starts anything)
    upgrade-catalog  explicitly upgrade an older catalog (server must be stopped; back up first)

Examples::

    F2W_DEPLOYMENT_PROFILE=local F2W_TILED_DATA_DIR=/var/lib/fair2wise/tiled \
        F2W_TILED_PYTHON=/opt/tiled/venv/bin/python python scripts/tiled_server.py start
    F2W_DEPLOYMENT_PROFILE=als-dev F2W_TILED_WRITER_SECRET_FILE=/run/secrets/tiled_writer \
        python scripts/tiled_server.py check
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from ..cli_output import output
from ..deployment import tiled_capabilities as caps
from ..deployment.tiled_config import TiledDeploymentError, load_tiled_settings

FLAG_TO_ENV = {
    "profile": "F2W_DEPLOYMENT_PROFILE",
    "mode": "F2W_TILED_MODE",
    "url": "F2W_TILED_URL",
    "data_dir": "F2W_TILED_DATA_DIR",
    "python": "F2W_TILED_PYTHON",
    "port": "F2W_TILED_PORT",
    "writer_secret_file": "F2W_TILED_WRITER_SECRET_FILE",
    "ca_bundle": "F2W_TILED_CA_BUNDLE",
}


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for the facade."""
    parser = argparse.ArgumentParser(prog="tiled_server.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["start", "status", "stop", "check", "upgrade-catalog"])
    for flag, env in FLAG_TO_ENV.items():
        parser.add_argument(f"--{flag.replace('_', '-')}", dest=flag, help=f"overrides {env}")
    parser.add_argument("--timeout", type=float, default=60.0, help="readiness timeout in seconds (start)")
    return parser


def _settings_env(args: argparse.Namespace, environ: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in environ.items() if k == "F2W_DEPLOYMENT_PROFILE" or k.startswith("F2W_TILED_")}
    for flag, name in FLAG_TO_ENV.items():
        value = getattr(args, flag)
        if value is not None:
            env[name] = str(value)
    return env


def run(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Execute one command and return its JSON-ready result (raises TiledDeploymentError)."""
    args = build_parser().parse_args(argv)
    settings = load_tiled_settings(_settings_env(args, dict(os.environ if environ is None else environ)))
    if args.command == "check":
        if settings.external is not None:
            return {"mode": "external", "capabilities": caps.connect_external(settings.external).to_dict()}
        from ..deployment import tiled_lifecycle as lifecycle

        if settings.managed is None:
            raise TiledDeploymentError("no managed or external Tiled target is configured", code="not_managed")
        status = lifecycle.managed_status(settings.managed)
        if status["state"] != "running":
            raise TiledDeploymentError(f"Managed Tiled is {status['state']}", code="target_unavailable",
                                       detail=status)
        report = caps.check_capabilities(status["url"], credential=lifecycle.writer_credential(settings.managed),
                                         expected_version=settings.managed.expected_version)
        return {"mode": "managed", "capabilities": report.to_dict()}
    if settings.managed is None:
        raise TiledDeploymentError(f"'{args.command}' manages a server; profile {settings.profile} is "
                                   "external (connect-only)", code="not_managed")
    from ..deployment import tiled_lifecycle as lifecycle

    cfg = settings.managed
    if args.command == "start":
        server = lifecycle.start_managed(cfg, ready_timeout=args.timeout)
        return {"state": "running", "url": server.url, "pid": server.pid, "log": str(server.log_path),
                "capabilities": server.capabilities.to_dict() if server.capabilities else None}
    if args.command == "status":
        return lifecycle.managed_status(cfg)
    if args.command == "stop":
        found = lifecycle.find_managed(cfg)
        if found is None:
            return {"state": lifecycle.managed_status(cfg)["state"], "stopped": False}
        return lifecycle.stop_managed(found)
    return lifecycle.upgrade_catalog(cfg)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    try:
        result = run(argv)
    except TiledDeploymentError as exc:
        detail = {k: v for k, v in exc.detail.items() if k != "log_tail"}
        output.error(json.dumps({"error": exc.code, "message": str(exc), "detail": detail}, default=str))
        if exc.detail.get("log_tail"):
            output.error(str(exc.detail["log_tail"]))
        return 2
    output.info(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
