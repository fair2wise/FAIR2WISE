#!/usr/bin/env python3
"""F2W API service for the images (task T24B): the T14 API behind the T24C gateway.

Serves :func:`app.api.main.create_api_app` (the 19 legacy UI routes plus ``/v1`` jobs,
review, publication and status) with the ``/fair2wise/api`` ASGI ``root_path`` from
``F2W_PUBLIC_PREFIX`` (``app.modules.deployment.gateway.uvicorn_options``). The upstream
``python -m app.modules.f2w_agent.cli api --jobs-api`` serves the same app but without
``root_path`` (``app/api/main.py:run_api``), so generated URLs (``/docs`` -> OpenAPI)
would miss the prefix behind the gateway.

Arguments are the ``f2w-agent`` CLI arguments (``app.modules.f2w_agent.cli.build_parser``),
ending with ``api --host 127.0.0.1 --port 8090``. The API binds loopback only; the gateway
is the one public listener. CORS origins come from ``F2W_CORS_ORIGINS`` (comma-separated)
or the API's development defaults; the gateway is same-origin and needs none.

No ``.env`` is read here: the supervisor already applied an explicit ``F2W_DOTENV`` and
passes ``F2W_DOTENV=off``.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

LOOPBACK = ("127.0.0.1", "::1", "localhost")


def build(argv: Sequence[str], environ: Mapping[str, str]) -> Tuple[Any, Dict[str, Any]]:
    """Return ``(app, uvicorn_kwargs)`` without serving.

    Raises:
        SystemExit: For a non-``api`` command or a non-loopback bind.
    """
    from app.modules.deployment.gateway import layout_from_env, uvicorn_options
    from app.modules.f2w_agent import cli

    args = cli.build_parser().parse_args(list(argv))
    if args.command != "api":
        raise SystemExit("api_service runs only the 'api' command")
    if args.host not in LOOPBACK:
        raise SystemExit(f"the image API binds loopback only (got --host {args.host}); the gateway is public")
    cfg = cli._cfg(args)
    origins: Optional[List[str]] = [o.strip() for o in (environ.get("F2W_CORS_ORIGINS") or "").split(",")
                                    if o.strip()] or args.cors_origin
    from app.api.main import create_api_app

    app = create_api_app(cfg, cors_origins=origins)
    options = {"host": args.host, "port": args.port, **uvicorn_options(layout_from_env(environ))}
    return app, options


def main(argv: Optional[Sequence[str]] = None) -> int:
    import uvicorn

    app, options = build(sys.argv[1:] if argv is None else argv, os.environ)
    uvicorn.run(app, **options)
    return 0


if __name__ == "__main__":
    sys.exit(main())
