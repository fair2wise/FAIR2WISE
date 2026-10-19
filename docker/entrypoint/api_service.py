#!/usr/bin/env python3
"""F2W API service for the images (task T24B): the T14 API behind the T24C gateway.

Runs ``python -m app.modules.f2w_agent.cli ... api --jobs-api``, i.e.
:func:`app.api.main.run_api`, which serves :func:`app.api.main.create_api_app` (the 19
legacy UI routes plus ``/v1`` jobs, review, publication and status) with the
``/fair2wise/api`` ASGI ``root_path`` derived from ``F2W_PUBLIC_PREFIX`` (C6). This wrapper
adds only the image rules below; the configuration is built by the CLI itself.

Arguments are the ``f2w-agent`` CLI arguments (``app.modules.f2w_agent.cli.build_parser``),
ending with ``api --host 127.0.0.1 --port 8090``. The API binds loopback only; the gateway
is the one public listener. CORS origins come from ``F2W_CORS_ORIGINS`` (comma-separated,
passed on as ``--cors-origin``), or ``--cors-origin`` arguments, not both; with neither the
API keeps its development defaults. The gateway is same-origin and needs none.

No ``.env`` is read here: the supervisor already applied an explicit ``F2W_DOTENV`` and
passes ``F2W_DOTENV=off``.
"""

from __future__ import annotations

import os
import sys
from typing import List, Mapping, Optional, Sequence

LOOPBACK = ("127.0.0.1", "::1", "localhost")
CORS_ENV = "F2W_CORS_ORIGINS"


def cors_origins(environ: Mapping[str, str]) -> List[str]:
    """Origins from :data:`CORS_ENV` (comma-separated; blanks dropped)."""
    return [o.strip() for o in (environ.get(CORS_ENV) or "").split(",") if o.strip()]


def command(argv: Sequence[str], environ: Mapping[str, str]) -> List[str]:
    """Return the ``f2w-agent`` CLI argv this service runs (``api --jobs-api``), without running it.

    Raises:
        SystemExit: For a non-``api`` command, a non-loopback bind, or CORS origins given both in
            the environment and as arguments.
    """
    from app.modules.f2w_agent import cli

    args = cli.build_parser().parse_args(list(argv))
    if args.command != "api":
        raise SystemExit("api_service runs only the 'api' command")
    if args.host not in LOOPBACK:
        raise SystemExit(f"the image API binds loopback only (got --host {args.host}); the gateway is public")
    origins = cors_origins(environ)
    if origins and args.cors_origin:
        raise SystemExit(f"set {CORS_ENV} or --cors-origin, not both")
    out = list(argv)
    if not args.jobs_api:
        out.append("--jobs-api")
    for origin in origins:
        out += ["--cors-origin", origin]
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    from app.modules.f2w_agent import cli

    return cli.main(command(sys.argv[1:] if argv is None else argv, os.environ))


if __name__ == "__main__":
    sys.exit(main())
