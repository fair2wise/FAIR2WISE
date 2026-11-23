#!/usr/bin/env python3
r"""Check that this install can reach what it needs, and print one state per check (``f2w-doctor``).

Console script: ``f2w-doctor`` (``scripts/doctor.py`` stays as a launch facade). Read-only apart from one
temporary file created and removed in each writable-directory check.

  f2w-doctor [--json] [--timeout SECONDS] [--only NAME[,NAME]] [--skip NAME[,NAME]]

Checks and their states (``skipped`` is the state of a check whose setting is not configured):

  cborg      ok, ip_not_authorized, auth_failed, unreachable, no_key, timeout, error
  openalex   ok, unreachable, timeout, blocked_by_proxy, blocked_by_policy, no_key, rejected, rate_limited, error
  arxiv      ok, unreachable, timeout, blocked_by_proxy, blocked_by_policy, rate_limited, error
  ollama     ok, unreachable, timeout, error, skipped (``OLLAMA_URL`` unset)
  library    ok, not_writable, missing, skipped (``F2W_LIBRARY_DIR`` unset)
  state      ok, not_writable, missing, skipped (``F2W_WORKDIR`` unset)
  tiled_key  ok, too_open, missing, error, skipped (no managed-Tiled data dir or secret file configured)

The CBORG check makes one **keyed** ``GET {CBORG_BASE_URL}/models`` through the client and egress path the app
uses (address pinning, ``HTTP(S)_PROXY``), because CBORG answers 403 ``ip_not_authorized`` to a call without a key
even from an authorized address: a keyless call cannot test authorization, so with no key the state is
``no_key``, never ``ok``. OpenAlex and arXiv go through the hardened fetch helpers (egress policy, key only in
the ``Authorization`` header).

Output is one ``<name>: <state>`` line per check and nothing else (``--json``: ``{"checks": [{"name", "state"}],
"ok": bool}``). States come from a fixed vocabulary: no key, host, address, path, URL, proxy or error text is
ever printed. Exit status: 0 every check ok or skipped, 1 any other state, 2 usage error.

Run it where the app runs, with the app's environment: ``docker exec CONTAINER f2w-doctor`` shows the image
profile's values (library and state directories); ``docker run --rm --env-file F2W.ENV --entrypoint f2w-doctor
IMAGE`` sees only what the env file sets.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

import httpx

from ..cli_output import output

EXIT_OK, EXIT_NOT_OK, EXIT_USAGE = 0, 1, 2
DEFAULT_TIMEOUT = 8.0
#: Check names in the order they run and print.
CHECKS = ("cborg", "openalex", "arxiv", "ollama", "library", "state", "tiled_key")
#: States that do not fail the run.
PASSING = frozenset({"ok", "skipped"})

OPENALEX_PROBE_URL = "https://api.openalex.org/works?per-page=1&select=id"
ARXIV_PROBE_URL = "https://export.arxiv.org/api/query?search_query=all:electron&max_results=1"
STATE_DIR_ENV = "F2W_WORKDIR"
LIBRARY_DIR_ENV = "F2W_LIBRARY_DIR"
TILED_DATA_DIR_ENV = "F2W_TILED_DATA_DIR"
TILED_SECRET_FILE_ENV = "F2W_TILED_WRITER_SECRET_FILE"

#: Builds the CBORG client from ``(api_key, base_url)``; tests inject one on a mock transport.
CborgClientFactory = Callable[[str, str], Any]


@dataclass
class Context:
    """What the checks run against; every network layer is injectable so tests never touch the network.

    Attributes:
        environ: The environment the checks read (the process environment by default).
        timeout: Seconds each network call may take.
        cborg_client: Builds the OpenAI-compatible CBORG client; ``None`` builds the app's client
            (``openai_http_kwargs``: address pinning and proxy rules as in the app).
        http_client: ``httpx.Client`` for the OpenAlex and arXiv probes (``None``: the helper's default client).
        policy: Egress policy for those probes (``None``: the default ``NetworkPolicy``).
        ollama_fetch: ``fetch(url, timeout, max_bytes) -> (status, body)`` for Ollama (``None``: the app's).
    """

    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    timeout: float = DEFAULT_TIMEOUT
    cborg_client: CborgClientFactory | None = None
    http_client: httpx.Client | None = None
    policy: Any = None  # Any: a NetworkPolicy; imported lazily so that importing this module stays cheap
    ollama_fetch: Callable[[str, float, int], tuple[int, bytes]] | None = None


def _env(ctx: Context, name: str) -> str:
    return str(ctx.environ.get(name, "") or "").strip()


# ---------------------------------------------------------------------------------------------- CBORG

def _default_cborg_client(key: str, base_url: str, timeout: float) -> Any:  # noqa: ANN401  # Any: openai.OpenAI
    import openai

    from ..cborg_http import openai_http_kwargs

    return openai.OpenAI(api_key=key, base_url=base_url, max_retries=0, timeout=timeout,
                         **openai_http_kwargs(asynchronous=False))


def classify_cborg_error(exc: BaseException) -> str:
    """The doctor state for an exception from the CBORG model-list call (never uses the message)."""
    from ..cborg_errors import is_ip_not_authorized

    if is_ip_not_authorized(exc):
        return "ip_not_authorized"
    if isinstance(exc, httpx.TimeoutException) or type(exc).__name__ == "APITimeoutError":
        return "timeout"
    if isinstance(exc, httpx.TransportError) or type(exc).__name__ == "APIConnectionError":
        return "unreachable"
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return "auth_failed"
    return "error"


def check_cborg(ctx: Context) -> str:
    """One keyed model-list call through the app's client; classify the outcome."""
    key = _env(ctx, "CBORG_API_KEY")
    if not key:
        return "no_key"
    from ..model_catalog import DEFAULT_CBORG_BASE

    base = (_env(ctx, "CBORG_BASE_URL") or DEFAULT_CBORG_BASE).rstrip("/")
    try:
        client = ctx.cborg_client(key, base) if ctx.cborg_client else _default_cborg_client(key, base, ctx.timeout)
        client.models.list(timeout=ctx.timeout)
    except Exception as exc:  # noqa: BLE001 - every fault becomes a state; its text is never shown
        return classify_cborg_error(exc)
    return "ok"


# --------------------------------------------------------------------------------- OpenAlex and arXiv

def _web_state(url: str, ctx: Context, *, headers: dict[str, str], key_sent: bool) -> str:
    from ..literature.legacy_download import JSONFetchError, hardened_get_response

    try:
        answer = hardened_get_response(url, headers=headers, timeout=ctx.timeout, policy=ctx.policy,
                                       client=ctx.http_client, max_bytes=2_000_000)
    except JSONFetchError as exc:
        text = str(exc)
        if text.startswith("network error ("):
            kind = text[len("network error ("):-1]
            if "Timeout" in kind:
                return "timeout"
            return "blocked_by_proxy" if kind == "ProxyError" else "unreachable"
        return "blocked_by_policy" if text.startswith("blocked URL") else "error"
    except Exception:  # noqa: BLE001 - any other fault is a state, not a traceback
        return "error"
    status = answer.status
    if 200 <= status < 300:
        return "ok"
    if status == 407:
        return "blocked_by_proxy"
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "rejected" if key_sent else "no_key"
    return "error"


def check_openalex(ctx: Context) -> str:
    """OpenAlex reachable (key in the ``Authorization`` header only, never in the URL)."""
    from ..literature.legacy_download import JSON_HEADERS, openalex_api_key

    key = openalex_api_key(ctx.environ)
    headers = dict(JSON_HEADERS)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return _web_state(OPENALEX_PROBE_URL, ctx, headers=headers, key_sent=key is not None)


def check_arxiv(ctx: Context) -> str:
    """Check that the arXiv query endpoint answers (no key)."""
    from ..literature.legacy_download import ATOM_HEADERS

    state = _web_state(ARXIV_PROBE_URL, ctx, headers=dict(ATOM_HEADERS), key_sent=False)
    return "error" if state == "no_key" else state


# ------------------------------------------------------------------------------------------------ Ollama

def check_ollama(ctx: Context) -> str:
    """The Ollama model list answers; ``skipped`` when ``OLLAMA_URL`` is unset."""
    from ..ollama_catalog import _httpx_fetch, list_ollama_models

    seen: list[BaseException] = []
    inner = ctx.ollama_fetch or _httpx_fetch

    def fetch(url: str, timeout: float, max_bytes: int) -> tuple[int, bytes]:
        try:
            return inner(url, timeout, max_bytes)
        except Exception as exc:
            seen.append(exc)
            raise

    listing = list_ollama_models(ctx.environ, fetch=fetch, timeout=ctx.timeout)
    if listing.state == "unset":
        return "skipped"
    if listing.state in ("ok", "empty"):
        return "ok"
    if listing.state == "unreachable":
        timed_out = any(isinstance(e, (httpx.TimeoutException, TimeoutError)) for e in seen)
        return "timeout" if timed_out else "unreachable"
    return "error"


# --------------------------------------------------------------------------------------------- directories

def _directory_state(raw: str) -> str:
    if not raw:
        return "skipped"
    path = Path(raw)
    if not path.is_dir():
        return "missing"
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".f2w-doctor-"):
            pass  # created and removed on close
    except OSError:
        return "not_writable"
    return "ok"


def check_library(ctx: Context) -> str:
    """The library directory exists and a file can be created in it."""
    return _directory_state(_env(ctx, LIBRARY_DIR_ENV))


def check_state(ctx: Context) -> str:
    """The state (working) directory exists and a file can be created in it."""
    return _directory_state(_env(ctx, STATE_DIR_ENV))


def check_tiled_key(ctx: Context) -> str:
    """The managed Tiled writer key file passes the AUD-15 permission rule (state only)."""
    from ..deployment.tiled_config import TiledConfigError, check_private_file

    explicit = _env(ctx, TILED_SECRET_FILE_ENV)
    data_dir = _env(ctx, TILED_DATA_DIR_ENV)
    if explicit:
        path = Path(explicit)
    elif data_dir:
        path = Path(data_dir) / "secrets" / "writer.key"
    else:
        return "skipped"
    if not path.exists():
        return "missing"
    try:
        check_private_file(path)
    except TiledConfigError as exc:
        return "too_open" if "group- or world-accessible" in str(exc) else "error"
    return "ok"


_RUNNERS: dict[str, Callable[[Context], str]] = {
    "cborg": check_cborg, "openalex": check_openalex, "arxiv": check_arxiv, "ollama": check_ollama,
    "library": check_library, "state": check_state, "tiled_key": check_tiled_key,
}


def run_checks(names: Sequence[str], ctx: Context) -> list[tuple[str, str]]:
    """Run the named checks in :data:`CHECKS` order; a check that raises reports ``error``."""
    results: list[tuple[str, str]] = []
    for name in CHECKS:
        if name not in names:
            continue
        try:
            state = _RUNNERS[name](ctx)
        except Exception:  # noqa: BLE001 - a bug in one check must not hide the others, nor print its message
            state = "error"
        results.append((name, state))
    return results


def _names(value: str, parser: argparse.ArgumentParser) -> list[str]:
    names = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [n for n in names if n not in CHECKS]
    if not names or unknown:
        parser.error(f"unknown check name; choose from {', '.join(CHECKS)}")
    return names


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line (usage errors exit with status 2)."""
    parser = argparse.ArgumentParser(
        prog="f2w-doctor",
        description="Check CBORG, OpenAlex, arXiv, Ollama and the writable directories; print one state per check.")
    parser.add_argument("--json", action="store_true", help='machine output: {"checks": [{"name","state"}], "ok": bool}')
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"seconds each network call may take (default {DEFAULT_TIMEOUT:g})")
    parser.add_argument("--only", default=None, metavar="NAME[,NAME]", help=f"run only these checks ({', '.join(CHECKS)})")
    parser.add_argument("--skip", default=None, metavar="NAME[,NAME]", help="do not run these checks")
    args = parser.parse_args(argv)
    if not args.timeout > 0:
        parser.error("--timeout must be greater than 0")
    selected = _names(args.only, parser) if args.only is not None else list(CHECKS)
    skipped = _names(args.skip, parser) if args.skip is not None else []
    args.names = [n for n in selected if n not in skipped]
    return args


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None,
         context: Context | None = None) -> int:
    """Entry point: run the checks, print the states, return 0 (all ok or skipped), 1 or 2 (usage)."""
    try:
        args = parse_args(argv)
    except SystemExit as exit_:
        return int(exit_.code) if isinstance(exit_.code, int) else EXIT_USAGE
    if context is None:
        if environ is None:
            try:
                from ..dotenv_entry import load_entrypoint_dotenv

                load_entrypoint_dotenv(__file__)
            except OSError:
                output.warning("doctor: F2W_DOTENV does not name a readable file")
                return EXIT_USAGE
            environ = os.environ
        context = Context(environ=environ)
    context.timeout = args.timeout
    results = run_checks(args.names, context)
    everything_ok = all(state in PASSING for _, state in results)
    if args.json:
        output.info(json.dumps({"checks": [{"name": n, "state": s} for n, s in results], "ok": everything_ok}))
    else:
        for name, state in results:
            output.info(f"{name}: {state}")
    return EXIT_OK if everything_ok else EXIT_NOT_OK


if __name__ == "__main__":
    sys.exit(main())
