#!/usr/bin/env python3
r"""Fill missing authors and abstracts of already-harvested papers in the library manifests (P3b).

Console script: ``f2w-library-backfill`` (``scripts/library_backfill.py`` stays as a launch facade). Never runs by itself.

  f2w-library-backfill [--library-dir DIR] [--category NAME] [--limit N] [--delay SECONDS]            (dry run)
  f2w-library-backfill --apply [--library-dir DIR] [--category NAME] [--limit N] [--delay SECONDS]

For every row of ``<library>/<category>/manifest.json`` that has a DOI (asked of OpenAlex) or an arXiv id (asked of
arXiv) and lacks ``authors`` or ``abstract``, ``--apply`` fills only the missing fields and rewrites that manifest
atomically; a stored value is never replaced, rows without a DOI or arXiv id are reported as unfillable, and a rerun
skips every row that is already filled. The default is a dry run: no network, nothing written, a count of what
``--apply`` would ask. ``--library-dir`` defaults to ``F2W_LIBRARY_DIR``; only that directory is written.

Egress (``--apply`` only): ``api.openalex.org`` (``OPENALEX_API_KEY`` from the environment, sent as a header) and
``export.arxiv.org`` (one request per 3 s). Prints a JSON report of counts, failure codes and library-relative paths;
authors, abstracts and titles are never printed or logged. Exit status: 0 done, 1 stopped early (no key, quota
spent) or an unusable library directory.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import os
from pathlib import Path
import sys

from ..cli_output import output
from ..library.backfill import backfill, openalex_fetcher
from ..library.service import LIBRARY_DIR_ENV
from ..literature.openalex import OpenAlexSettings

EXIT_OK, EXIT_STOPPED = 0, 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="f2w-library-backfill",
        description="Fill missing authors/abstracts in the library manifests from OpenAlex (DOI) and arXiv (id)")
    parser.add_argument("--library-dir", type=Path, default=None,
                        help=f"the library directory (default: ${LIBRARY_DIR_ENV})")
    parser.add_argument("--category", default=None, help="only this category folder")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="count what --apply would ask; no network, no writes (default)")
    mode.add_argument("--apply", action="store_true", help="fetch from OpenAlex/arXiv and write the manifests")
    parser.add_argument("--limit", type=int, default=None, help="ask the sources for at most N rows (resumable: rerun)")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between rows asked of a source (default 1)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.delay < 0:
        parser.error("--delay must not be negative")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: run the backfill and print the report; exit status 0 or 1."""
    args = parse_args(argv)
    raw = args.library_dir or (Path(os.environ[LIBRARY_DIR_ENV]) if os.environ.get(LIBRARY_DIR_ENV, "").strip() else None)
    if raw is None or not raw.is_absolute() or not raw.is_dir():
        output.info(f"ERROR: the library directory must be an existing absolute path (--library-dir or {LIBRARY_DIR_ENV})")
        return EXIT_STOPPED
    contact = OpenAlexSettings.from_mapping(dict(os.environ)).polite_mailto or None
    report = backfill(raw, apply=args.apply, limit=args.limit, delay=args.delay, category=args.category,
                      openalex=openalex_fetcher(mailto=contact) if args.apply else None)
    output.info(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return EXIT_STOPPED if report.stopped else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
