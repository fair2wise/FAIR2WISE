"""Tiled server launcher run by the *isolated Tiled interpreter*, never imported by F2W.

:mod:`.tiled_lifecycle` executes this file as ``<tiled-python> -I _tiled_entry.py ...``
with a scrubbed environment. ``-I`` keeps the app's checkout and site-packages off
``sys.path``, so the file must use only the standard library and ``tiled``.

Why not ``tiled serve config``: in 0.2.18 that command prints an ``?api_key=...`` URL to
stderr unless the key is passed as ``--api-key`` on the command line, where any local
user can read it with ``ps`` (``tiled/commandline/_serve.py:723-729``). This launcher
reads the key and authentication secrets from files, injects them into the parsed
configuration, and starts uvicorn without printing them.

``--compile-tags SRC --tags-db DB`` compiles access-tag definitions (JSON) into the SQLite
database ``TagBasedAccessPolicy`` reads (0.2.18 has no CLI for this), then exits.

Exit codes: 3 = installed tiled is not the expected version; 4 = configuration rejected.
"""

import argparse
import json
import sys
from pathlib import Path

EXIT_UNSUPPORTED_VERSION = 3
EXIT_CONFIG = 4


def _read(path):
    return Path(path).read_text(encoding="utf-8").strip()


def main(argv=None):
    """Validate the version, load configuration and secrets, and serve until terminated."""
    parser = argparse.ArgumentParser(prog="f2w-tiled-entry")
    parser.add_argument("--config")
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--instance", required=True, help="ownership nonce (not secret)")
    parser.add_argument("--api-key-file")
    parser.add_argument("--authn-secrets-file")
    parser.add_argument("--compile-tags")
    parser.add_argument("--tags-db")
    args = parser.parse_args(argv)

    import tiled

    if tiled.__version__ != args.expected_version:
        print(
            f"F2W_TILED_ERROR unsupported_version installed={tiled.__version__} "
            f"expected={args.expected_version}",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_UNSUPPORTED_VERSION
    if args.compile_tags:
        return _compile_tags(args.compile_tags, args.tags_db)
    if not args.config:
        parser.error("--config is required")

    from tiled.config import Config
    from tiled.utils import parse

    conf = parse(Path(args.config))
    auth = conf.setdefault("authentication", {})
    if args.api_key_file:
        auth["single_user_api_key"] = _read(args.api_key_file)
    if args.authn_secrets_file:
        data = json.loads(_read(args.authn_secrets_file))
        auth["secret_keys"] = list(data["secret_keys"])
        for provider in auth.get("providers") or []:
            if provider.get("provider") == data["provider"]:
                provider.setdefault("args", {})["users_to_passwords"] = dict(data["users_to_passwords"])
    try:
        config = Config.model_validate(conf)
    except Exception as exc:  # pydantic ValidationError; message names fields, not values
        print(f"F2W_TILED_ERROR config_invalid {type(exc).__name__}", file=sys.stderr, flush=True)
        return EXIT_CONFIG

    import uvicorn
    from tiled.commandline._serve import _setup_log_config
    from tiled.server.app import build_app_from_config

    host = config.uvicorn.get("host", "127.0.0.1")
    port = int(config.uvicorn.get("port", 8765))
    web_app = build_app_from_config(config)
    print(
        f"F2W_TILED_LAUNCH version={tiled.__version__} host={host} port={port} instance={args.instance}",
        file=sys.stderr,
        flush=True,
    )
    uvicorn.run(web_app, host=host, port=port, log_config=_setup_log_config(None, True))
    return 0


def _compile_tags(source, tags_db):
    from tiled.access_control.access_tags import AccessTagsCompiler
    from tiled.access_control.scopes import ALL_SCOPES

    def no_groups(name):
        raise KeyError(name)

    Path(tags_db).unlink(missing_ok=True)
    compiler = AccessTagsCompiler(ALL_SCOPES, json.loads(_read(source)), {"uri": str(tags_db)}, no_groups)
    compiler.load_tag_config()
    compiler.compile()
    compiler.connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
