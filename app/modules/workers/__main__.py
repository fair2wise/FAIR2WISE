"""Owned worker process entry point: ``python -m app.modules.workers --db PATH --factory MODULE:CALLABLE``.

Started only through ``jobs.process_support.worker_command`` (allowlisted module, validated
factory path, no shell). SIGTERM/SIGINT trigger a graceful shutdown that returns running jobs to
the queue. ``--once`` exits when no job is queued or running (used by tests and one-shot runs).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import importlib
import re
import signal
import sys
import threading
from typing import Any, cast

from .. import metrics, tracing
from .executor import WorkerExecutor

_FACTORY = re.compile(r"^app\.modules(\.[A-Za-z_][A-Za-z0-9_]*)+:[A-Za-z_][A-Za-z0-9_]*$")


def load_factory(spec: str) -> Callable[[str], Any]:  # Any: the job service a worker factory builds
    """Import ``module:callable`` after validating it lies under ``app.modules``."""
    if not _FACTORY.match(spec):
        raise SystemExit("factory must be 'app.modules.<package>:<callable>'")
    module, name = spec.split(":")
    return cast(Callable[[str], Any], getattr(importlib.import_module(module), name))


def main(argv: Sequence[str] | None = None) -> int:
    """Run a worker until signalled (or idle with ``--once``)."""
    parser = argparse.ArgumentParser(prog="python -m app.modules.workers")
    parser.add_argument("--db", required=True)
    parser.add_argument("--factory", required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    tracing.init(process="worker")  # no-op unless F2W_OTEL_ENABLED; flushed at exit
    metrics.init(process="worker")  # no-op unless F2W_METRICS_ENABLED and an OTLP endpoint; flushed at exit
    service = load_factory(args.factory)(args.db)
    depth = getattr(service, "queue_depth", None)
    if callable(depth):  # the worker owns the queue, so it reports its depth (a factory may return a bare service)
        metrics.set_queue_depth_source(depth)
    executor = WorkerExecutor(service, max_concurrency=args.concurrency).start()
    stop = threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
    try:
        if args.once:
            executor.run_until_idle()
        else:
            stop.wait()
    finally:
        clean = executor.shutdown()
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
