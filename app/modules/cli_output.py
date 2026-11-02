"""User-facing command output as logging, with the same bytes ``print`` produced.

Commands and agents here tell the user things (answers, progress, summaries, errors). That output is
not diagnostics, and several entry points already configure their own log format (timestamps, level
names), so it must not go through the application's ordinary loggers: a prefix would change what the
user sees. This module owns one logger, ``f2w.output``, with its own handlers that write the bare
message:

* ``info`` and ``debug`` records go to **stdout** (what ``print(...)`` did);
* ``warning`` and above go to **stderr** (what ``print(..., file=sys.stderr)`` did).

The handlers are installed when the module is first imported, so callers that never configure logging
(the API server, tests, library use) keep seeing exactly the old lines. ``propagate`` is off, so the
output never doubles through the root logger or picks up another module's format. Because it is a
logger, a host can still redirect or silence it (``logging.getLogger("f2w.output").setLevel(...)``,
``addHandler``, ``removeHandler``).

Standalone scripts whose stdout or stderr is a machine protocol (the Tiled entry script, the gateway
build check) stay on plain ``print`` and say so with ``noqa: T201``: they must not import project code.
"""

from __future__ import annotations

import logging
import sys

#: Name of the output logger (stable: hosts and tests address it by name).
OUTPUT_LOGGER = "f2w.output"


class _LiveStreamHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """A stream handler that writes to whatever ``sys.stdout``/``sys.stderr`` is at emit time.

    ``print`` looked the stream up on every call, so redirection (``contextlib.redirect_stdout``, a
    test's ``capsys``, a supervisor swapping the streams) worked. A handler holding the stream object
    from import time would not; this one resolves it per record.
    """

    def __init__(self, stream_name: str, *, below: int | None = None, at_least: int | None = None) -> None:
        """Create a handler for ``sys.<stream_name>``.

        Args:
            stream_name: ``"stdout"`` or ``"stderr"``.
            below: Handle only records under this level (exclusive).
            at_least: Handle only records at or above this level.
        """
        super().__init__()
        self._stream_name = stream_name
        self.addFilter(lambda record: (below is None or record.levelno < below)
                       and (at_least is None or record.levelno >= at_least))

    @property
    def stream(self) -> object:
        """The current ``sys`` stream."""
        return getattr(sys, self._stream_name)

    @stream.setter
    def stream(self, value: object) -> None:
        """Ignore assignment: the stream is always the live ``sys`` one."""


def _configure() -> logging.Logger:
    logger = logging.getLogger(OUTPUT_LOGGER)
    if getattr(logger, "_f2w_output_configured", False):
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(message)s")
    for handler in (
        _LiveStreamHandler("stdout", below=logging.WARNING),
        _LiveStreamHandler("stderr", at_least=logging.WARNING),
    ):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger._f2w_output_configured = True  # type: ignore[attr-defined]  # marks the handlers as installed
    return logger


#: The output logger: ``output.info(text)`` replaces ``print(text)``; ``output.warning(text)`` and
#: ``output.error(text)`` replace ``print(text, file=sys.stderr)``.
output = _configure()

__all__ = ("OUTPUT_LOGGER", "output")
