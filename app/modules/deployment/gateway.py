"""The ``/fair2wise`` public prefix contract shared by the UI build, the gateway and the API (task T24C).

``docs/migration/companion/DEPLOYMENT_STRATEGY.md`` section 3, D26. One public prefix,
stripped exactly once:

======================================  =====================================================
Public route                            Behaviour
======================================  =====================================================
``/fair2wise``                          301 to ``/fair2wise/`` (query kept)
``/fair2wise/`` and UI deep links       built UI; SPA fallback only inside the UI namespace
``/fair2wise/assets/...``               built assets; a missing asset is 404, never HTML
``/fair2wise/docs/...``                 MkDocs site built into the image (owner O-A7)
``/fair2wise/api/<path>``               the gateway removes ``/fair2wise/api`` once and
                                        forwards the raw ``/<path>?<query>`` to the API,
                                        which runs with ASGI ``root_path=/fair2wise/api``
``/fair2wise/model-api/<path>``         the model-compatible API (owner B7, O-A9): the
                                        gateway forwards the raw ``/model-api/<path>`` to the
                                        same API process, which mounts it there and publishes
                                        its generated URLs under ``/fair2wise/model-api``
``/fair2wise/healthz``, ``/healthz``    gateway health (no secrets, no corpus)
======================================  =====================================================

The gateway listens on :data:`GATEWAY_PORT_ENV` (default :data:`DEFAULT_GATEWAY_PORT`, 8080,
the T24A one-ingress contract): an unprivileged port, so the image's nginx needs no file
capability.

The gateway (``docker/nginx.conf``) is the only component that strips the prefix. The API
keeps its rooted routes and only learns ``root_path`` so that generated URLs (FastAPI
``/docs`` -> ``openapi.json``, OpenAPI ``servers``, redirects) carry the prefix; the UI is
built with Vite ``base=/fair2wise/`` and same-origin API base ``/fair2wise/api``
(``ui/vite.config.ts``, which writes :data:`BUILD_MANIFEST_NAME` into the bundle).

Without ``F2W_PUBLIC_PREFIX`` everything stays as before: the API has no ``root_path``, the
Vite dev server serves ``/`` and the browser talks to ``http://127.0.0.1:8090``.

A built bundle cannot be re-prefixed at runtime, so :func:`check_build_agreement` refuses a
runtime prefix that differs from the one the UI was built with, and
:func:`render_nginx_conf` refuses a gateway template that does not route the runtime prefix.

Standard library only, so the image build can run this file directly
(``python app/modules/deployment/gateway.py ...``). Importing it has no side effects.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re
import sys
from typing import Any

#: Trusted startup setting naming the public prefix. Unset or empty: unprefixed (developer).
PREFIX_ENV = "F2W_PUBLIC_PREFIX"
#: The prefix every deployment image serves under (D26).
DEFAULT_PUBLIC_PREFIX = "/fair2wise"
#: Gateway template placeholder for the private API upstream (``host:port``).
API_UPSTREAM_ENV = "F2W_API_UPSTREAM"
#: The API listens on private loopback inside the combined image (DEPLOYMENT_STRATEGY s.2).
DEFAULT_API_UPSTREAM = "127.0.0.1:8090"
#: Gateway template placeholder and trusted startup setting for the gateway's listen port (C6).
GATEWAY_PORT_ENV = "F2W_GATEWAY_PORT"
#: The one container ingress port of the T24A contract (``docker_profile_contract.md``). Unprivileged,
#: so nginx needs no ``cap_net_bind_service``.
DEFAULT_GATEWAY_PORT = 8080
#: Mount path of the model-compatible API inside the UI API process (owner decision B7).
MODEL_API_MOUNT = "/model-api"
#: Written by ``ui/vite.config.ts`` into the build output (``dist/f2w-build.json``).
BUILD_MANIFEST_NAME = "f2w-build.json"

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class PrefixConfigError(ValueError):
    """An invalid prefix/upstream setting, or build/gateway/runtime prefix disagreement."""


def normalize_prefix(raw: str | None) -> str:
    """Return the canonical prefix (``""`` or ``/a/b`` without a trailing slash).

    Raises:
        PrefixConfigError: For anything but a rooted path of plain segments (no scheme,
            host, query, ``.``/``..`` or empty segments).
    """
    value = (raw or "").strip()
    if value in ("", "/"):
        return ""
    if not value.startswith("/"):
        raise PrefixConfigError(f"{PREFIX_ENV} must be a rooted path like /fair2wise, got {raw!r}")
    value = value.rstrip("/")
    segments = value[1:].split("/")
    for segment in segments:
        if not _SEGMENT.match(segment):
            raise PrefixConfigError(f"{PREFIX_ENV} has an invalid path segment {segment!r} in {raw!r}")
    return value


@dataclass(frozen=True)
class PublicLayout:
    """Every public URL derived from one prefix. ``prefix == ""`` is the unprefixed developer layout."""

    prefix: str = ""

    @property
    def prefixed(self) -> bool:
        """Whether a public prefix is set (False for the unprefixed developer layout)."""
        return bool(self.prefix)

    @property
    def ui_base(self) -> str:
        """Vite ``base`` (always ends with ``/``)."""
        return f"{self.prefix}/"

    @property
    def api_root_path(self) -> str:
        """ASGI ``root_path`` of the UI API behind the gateway (``""`` when unprefixed)."""
        return f"{self.prefix}/api" if self.prefixed else ""

    @property
    def model_api_root_path(self) -> str:
        """Public root of the model-compatible API (``app/api/main.py`` mounts it at ``/model-api``).

        ``/fair2wise/model-api`` behind the gateway; ``/model-api`` on an unprefixed developer
        launch (the mount path itself). Never ``""``: the model API is always one segment below
        the UI API's process root.
        """
        return f"{self.prefix}{MODEL_API_MOUNT}"

    @property
    def docs_url(self) -> str:
        """MkDocs site URL (the Vite dev plugin serves ``/docs/`` unprefixed)."""
        return f"{self.prefix}/docs/"

    @property
    def cookie_path(self) -> str:
        """Cookie ``Path`` for the ALS session cookie (``app/api/sessions.py``, AUTH-09)."""
        return self.prefix or "/"

    @property
    def health_path(self) -> str:
        """Public path of the health endpoint under the prefix."""
        return f"{self.prefix}/healthz"

    def as_manifest(self) -> dict[str, str]:
        """The values the UI build records in :data:`BUILD_MANIFEST_NAME`."""
        return {"public_prefix": self.prefix, "ui_base": self.ui_base,
                "api_base": self.api_root_path, "docs_url": self.docs_url}


def layout_from_env(env: Mapping[str, str]) -> PublicLayout:
    """Read :data:`PREFIX_ENV` from trusted startup settings (never from a request)."""
    return PublicLayout(normalize_prefix(env.get(PREFIX_ENV)))


def uvicorn_options(layout: PublicLayout) -> dict[str, Any]:
    """Extra ``uvicorn.run`` keyword arguments for the UI API.

    Only ``root_path``. Forwarded headers stay at uvicorn's default trust
    (``FORWARDED_ALLOW_IPS``, else ``127.0.0.1``): only the loopback gateway is believed.
    """
    return {"root_path": layout.api_root_path} if layout.prefixed else {}


def validate_upstream(value: str) -> str:
    """Return ``host:port`` for the gateway's private API upstream.

    Raises:
        PrefixConfigError: For a scheme, path, credentials or a bad host/port.
    """
    text = (value or "").strip()
    match = re.match(r"^(?:\[(?P<v6>[0-9A-Fa-f:.]+)\]|(?P<host>[^:/\[\]@\s]+)):(?P<port>\d{1,5})$", text)
    if not match:
        raise PrefixConfigError(f"{API_UPSTREAM_ENV} must be host:port (no scheme or path), got {value!r}")
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise PrefixConfigError(f"{API_UPSTREAM_ENV} port out of range: {value!r}")
    if match.group("v6"):
        try:
            ipaddress.IPv6Address(match.group("v6"))
        except ValueError as exc:
            raise PrefixConfigError(f"{API_UPSTREAM_ENV} has an invalid IPv6 address: {value!r}") from exc
    elif not _HOSTNAME.match(match.group("host")):
        raise PrefixConfigError(f"{API_UPSTREAM_ENV} has an invalid host: {value!r}")
    return text


def validate_listen_port(value: Any) -> int:
    """Return the gateway listen port as an int (``1..65535``).

    Raises:
        PrefixConfigError: For anything but a plain decimal port in range.
    """
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"\d{1,5}", text) or not 1 <= int(text) <= 65535:
        raise PrefixConfigError(f"{GATEWAY_PORT_ENV} must be a port number 1-65535, got {value!r}")
    return int(text)


def gateway_port_from_env(env: Mapping[str, str]) -> int:
    """Read :data:`GATEWAY_PORT_ENV` from trusted startup settings (default :data:`DEFAULT_GATEWAY_PORT`)."""
    raw = env.get(GATEWAY_PORT_ENV)
    return validate_listen_port(raw) if raw is not None and str(raw).strip() else DEFAULT_GATEWAY_PORT


def render_nginx_conf(template: str, *, upstream: str = DEFAULT_API_UPSTREAM,
                      layout: PublicLayout = PublicLayout(DEFAULT_PUBLIC_PREFIX),
                      listen_port: Any = DEFAULT_GATEWAY_PORT) -> str:
    """Fill ``${F2W_API_UPSTREAM}`` and ``${F2W_GATEWAY_PORT}`` in ``docker/nginx.conf`` and check it.

    The official nginx image does the same substitution with ``envsubst`` when the file is
    installed as ``/etc/nginx/templates/default.conf.template``; this function is the
    validated equivalent for other process managers and for tests.

    Raises:
        PrefixConfigError: Unknown placeholders, a bad upstream or port, a template whose
            ``listen`` is not the port placeholder, or a template whose API locations are not
            ``<prefix>/api/`` and ``<prefix>/model-api/`` for the runtime prefix.
    """
    if not layout.prefixed:
        raise PrefixConfigError("the gateway always serves under a prefix; set F2W_PUBLIC_PREFIX")
    unknown = sorted({m.group(1) for m in _PLACEHOLDER.finditer(template)} - {API_UPSTREAM_ENV, GATEWAY_PORT_ENV})
    if unknown:
        raise PrefixConfigError(f"unknown gateway template placeholders: {', '.join(unknown)}")
    if f"location ^~ {layout.api_root_path}/ " not in template:
        raise PrefixConfigError(
            f"gateway template does not route {layout.api_root_path}/; the gateway, the UI build and "
            f"{PREFIX_ENV}={layout.prefix} must agree (rebuild for a different prefix)")
    if f"location ^~ {layout.model_api_root_path}/ " not in template:
        raise PrefixConfigError(
            f"gateway template does not route {layout.model_api_root_path}/ (owner decision B7); the gateway "
            f"and {PREFIX_ENV}={layout.prefix} must agree")
    listens = re.findall(r"^\s*listen\s+([^;]*);", template, flags=re.M)
    if listens != ["${" + GATEWAY_PORT_ENV + "}"]:
        raise PrefixConfigError(f"gateway template must have exactly one 'listen ${{{GATEWAY_PORT_ENV}}};' "
                                f"(found {listens}); the listen port is a startup setting")
    port = validate_listen_port(listen_port)
    return (template.replace("${" + API_UPSTREAM_ENV + "}", validate_upstream(upstream))
            .replace("${" + GATEWAY_PORT_ENV + "}", str(port)))


def read_build_manifest(path: Path) -> dict[str, str]:
    """Load the UI build's :data:`BUILD_MANIFEST_NAME`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PrefixConfigError(f"{path} is not a JSON object")
    return {str(k): str(v) for k, v in data.items()}


def check_build_agreement(manifest: Mapping[str, str], layout: PublicLayout) -> None:
    """Refuse a runtime prefix that differs from the one the UI bundle was built with.

    Raises:
        PrefixConfigError: With the two values and the fix (rebuild the UI).
    """
    expected = layout.as_manifest()
    built = {key: manifest.get(key) for key in expected}
    if built != expected:
        raise PrefixConfigError(
            f"UI bundle was built for prefix {manifest.get('public_prefix')!r} (api {manifest.get('api_base')!r}) "
            f"but {PREFIX_ENV}={layout.prefix!r}; a built Vite bundle cannot be re-prefixed at runtime: "
            f"rebuild the UI with F2W_PUBLIC_PREFIX={layout.prefix or '/'}")


def mkdocs_image_config(source: str) -> str:
    """Return ``mkdocs/mkdocs.yml`` for the image build: no compiled site URL or FQDN.

    Pages then link relatively and work under ``/fair2wise/docs/`` on any host. (MkDocs only
    uses ``site_url`` for canonical links, the sitemap and the 404 page; the gateway answers
    a missing docs page with its own 404.)

    The ``watch:`` block is dropped too: it only drives ``mkdocs serve`` reloads, and its
    ``../app``-style paths do not exist in the docs build stage, which copies ``mkdocs/`` alone.
    """
    patched, count = re.subn(r"^site_url:.*$", 'site_url: ""', source, count=1, flags=re.M)
    if count != 1:
        raise PrefixConfigError("mkdocs.yml has no site_url line to replace")
    return re.sub(r"^watch:[ \t]*\n(?:[ \t]+-.*\n?)*", "", patched, flags=re.M)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gateway CLI (``render-nginx``, ``check-build``, ``mkdocs-config``).

    Args:
        argv: Command-line arguments; ``sys.argv`` when ``None``.

    Returns:
        0 on success, 2 when the prefix configuration is rejected.
    """
    parser = argparse.ArgumentParser(prog="gateway", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render-nginx", help="fill the gateway template and check the prefix")
    render.add_argument("--template", type=Path, required=True)
    render.add_argument("--upstream", default=DEFAULT_API_UPSTREAM)
    render.add_argument("--prefix", default=DEFAULT_PUBLIC_PREFIX)
    render.add_argument("--listen-port", default=str(DEFAULT_GATEWAY_PORT))
    render.add_argument("--out", type=Path)
    check = sub.add_parser("check-build", help="compare the UI build manifest with a runtime prefix")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--prefix", default=DEFAULT_PUBLIC_PREFIX)
    docs = sub.add_parser("mkdocs-config", help="write the image MkDocs config (no compiled site URL)")
    docs.add_argument("--source", type=Path, required=True)
    docs.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "render-nginx":
            text = render_nginx_conf(args.template.read_text(encoding="utf-8"), upstream=args.upstream,
                                     layout=PublicLayout(normalize_prefix(args.prefix)),
                                     listen_port=args.listen_port)
            if args.out:
                args.out.write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
        elif args.command == "check-build":
            check_build_agreement(read_build_manifest(args.manifest), PublicLayout(normalize_prefix(args.prefix)))
        else:
            args.out.write_text(mkdocs_image_config(args.source.read_text(encoding="utf-8")), encoding="utf-8")
    except PrefixConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201 - build-time script
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
