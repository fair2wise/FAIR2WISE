"""app.modules.cli_output: command output through logging, byte-identical to ``print``."""

from __future__ import annotations

import contextlib
import importlib
import io
import logging
import sys

from app.modules import cli_output
from app.modules.cli_output import OUTPUT_LOGGER, output


def test_info_goes_to_stdout_bare_and_warnings_to_stderr(capsys):
    output.info("hello %s and 100% done")
    output.info("")
    output.warning("careful")
    output.error("broken")
    captured = capsys.readouterr()
    assert captured.out == "hello %s and 100% done\n\n"
    assert captured.err == "careful\nbroken\n"


def test_output_matches_print_for_the_same_text(capsys):
    text = "✓ résumé — 42 \x1b[32mgreen\x1b[0m"
    print(text)
    printed = capsys.readouterr().out
    output.info(text)
    assert capsys.readouterr().out == printed


def test_streams_are_resolved_when_the_record_is_emitted():
    buffer, errbuffer = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errbuffer):
        output.info("redirected")
        output.warning("redirected err")
    assert buffer.getvalue() == "redirected\n" and errbuffer.getvalue() == "redirected err\n"


def test_output_never_reaches_the_root_logger_or_another_format(capsys):
    seen = io.StringIO()
    handler = logging.StreamHandler(seen)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        output.info("plain")
    finally:
        root.removeHandler(handler)
    assert seen.getvalue() == "" and capsys.readouterr().out == "plain\n"
    assert logging.getLogger(OUTPUT_LOGGER).propagate is False


def _output_handlers():
    """The handlers this module installed (pytest adds its own capture handlers to the logger too)."""
    return [h for h in logging.getLogger(OUTPUT_LOGGER).handlers if hasattr(h, "_stream_name")]


def test_configuration_is_idempotent_across_reloads():
    before = _output_handlers()
    importlib.reload(cli_output)
    assert _output_handlers() == before and [h._stream_name for h in before] == ["stdout", "stderr"]


def test_a_host_can_silence_or_redirect_it(capsys):
    logger = logging.getLogger(OUTPUT_LOGGER)
    logger.setLevel(logging.WARNING)
    try:
        output.info("hidden")
        output.warning("shown")
    finally:
        logger.setLevel(logging.INFO)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "shown\n"
    assert sys.stdout is not None
