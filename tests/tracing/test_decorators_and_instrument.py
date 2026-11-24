"""``@traced`` (sync, async, errors) and the config-driven wrapping by name."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from pathlib import Path
import sys
import textwrap
import time

import pytest

from app.modules import tracing
from app.modules.tracing import defaults, instrument, runtime
from app.modules.tracing.safe import key_allowed, sanitize

from .conftest import SENTINEL, every_string


def names(exporter) -> list[str]:  # noqa: ANN001
    return [s.name for s in exporter.get_finished_spans()]


def test_traced_sync_async_and_signature(exporter) -> None:  # noqa: ANN001
    @tracing.traced("sync.add")
    def add(a: int, b: int = 2) -> int:
        """Add."""
        return a + b

    @tracing.traced("async.mul")
    async def mul(a: int, b: int) -> int:
        await asyncio.sleep(0)
        return a * b

    assert add(1) == 3 and asyncio.run(mul(3, 4)) == 12
    assert inspect.signature(add) == inspect.signature(add.__wrapped__)  # type: ignore[attr-defined]
    assert inspect.iscoroutinefunction(mul) and add.__doc__ == "Add."
    assert names(exporter) == ["sync.add", "async.mul"]


def test_nesting_follows_context(exporter) -> None:  # noqa: ANN001
    @tracing.traced("inner")
    def inner() -> None:
        return None

    with tracing.span("outer"):
        inner()
    inner_span, outer_span = exporter.get_finished_spans()
    assert inner_span.parent.span_id == outer_span.context.span_id
    assert inner_span.context.trace_id == outer_span.context.trace_id


def test_exception_is_recorded_by_type_only(exporter) -> None:  # noqa: ANN001
    @tracing.traced("boom")
    def boom() -> None:
        raise ValueError(f"leaks {SENTINEL}")

    with pytest.raises(ValueError):
        boom()
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR" and span.status.description == "ValueError"
    assert span.attributes["error.type"] == "ValueError"
    assert [e.name for e in span.events] == ["exception"]
    assert SENTINEL not in " ".join(every_string([span]))


def test_extractor_attributes_pass_the_allowlist(exporter) -> None:  # noqa: ANN001
    @tracing.traced("x", attributes=lambda text, n: {"f2w.paper_count": n, "f2w.prompt_text": text, "document.body": text},
                    result_attributes=lambda r: {"f2w.result_count": len(r)})
    def work(text: str, n: int) -> list[int]:
        return [1] * n

    work(SENTINEL, 3)
    attrs = exporter.get_finished_spans()[0].attributes
    assert attrs["f2w.paper_count"] == 3 and attrs["f2w.result_count"] == 3
    assert "f2w.prompt_text" not in attrs and "document.body" not in attrs
    assert SENTINEL not in " ".join(every_string(exporter.get_finished_spans()))


def test_an_extractor_that_raises_does_not_fail_the_call(exporter) -> None:  # noqa: ANN001
    @tracing.traced("x", attributes=lambda: 1 / 0)
    def work() -> int:
        return 7

    assert work() == 7


def test_disabled_wrapper_is_a_direct_call() -> None:
    @tracing.traced("x")
    def work() -> int:
        return 5

    assert work() == 5 and not runtime.is_enabled()


@pytest.mark.parametrize("key,ok", [
    ("f2w.paper_count", True), ("gen_ai.request.model", True), ("f2w.job_id", True), ("f2w.prompt_chars", True),
    ("f2w.prompt", False), ("f2w.query_text", False), ("f2w.api_key", False),
    ("document.text", False), ("f2w.email", False), ("f2w.token_count", True), ("f2w.user_token", False),
])
def test_key_allowlist(key: str, ok: bool) -> None:
    assert key_allowed(key) is ok


def test_value_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CBORG_API_KEY", "sk-live-abcdefghijkl")
    clean = sanitize({"f2w.job_id": "job-1", "f2w.stage": "line1\nline2", "f2w.name": "a@b.example",
                      "f2w.host_name": "x" * 500, "f2w.kind": "https://h/p?token=abc", "f2w.corpus_id": "sk-live-abcdefghijkl",
                      "f2w.size": 3, "f2w.flag_count": True, "f2w.list_count": [1]})
    assert clean == {"f2w.job_id": "job-1", "f2w.size": 3, "f2w.flag_count": True}


# ---------------------------------------------------------------------------- config-driven wrapping


#: Duration floor for the filter tests: far above any scheduler stall on a loaded CI runner (``fast`` takes
#: microseconds, so a 10 ms floor flaked under xdist) and well below ``slow``'s sleep.
FLOOR_MS = 150


@pytest.fixture
def sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A throwaway package ``app.modules._otel_sample`` with a class, module functions and an importer of them."""
    pkg = tmp_path / "app_sample"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "lib.py").write_text(textwrap.dedent('''
        import asyncio
        import time

        def public_one(x):
            return x + 1

        def _private(x):
            return x

        async def public_async(x):
            return x * 2

        def slow():
            time.sleep(0.4)

        def fast():
            return 1

        class Svc:
            def method(self, x):
                return x + 10

            @staticmethod
            def static(x):
                return x + 20

            @classmethod
            def klass(cls, x):
                return x + 30
        '''))
    (pkg / "user.py").write_text("from app_sample.lib import public_one\n\ndef call(x):\n    return public_one(x)\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "app_sample.lib"
    for name in [n for n in sys.modules if n.startswith("app_sample")]:
        del sys.modules[name]


def enable(monkeypatch: pytest.MonkeyPatch, exporter_obj, functions: str, modules: str = "") -> None:  # noqa: ANN001
    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setenv("F2W_OTEL_TRACE_FUNCTIONS", functions)
    if modules:
        monkeypatch.setenv("F2W_OTEL_TRACE_MODULES", modules)
    assert runtime.init(process="t", exporter=exporter_obj)


def memory():  # noqa: ANN201
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    return InMemorySpanExporter()


def test_wraps_qualname_methods_static_and_class(sample, monkeypatch) -> None:  # noqa: ANN001
    exp = memory()
    enable(monkeypatch, exp, f"{sample}:Svc.method,{sample}:Svc.static,{sample}:Svc.klass,{sample}:public_async")
    lib = importlib.import_module(sample)
    assert lib.Svc().method(1) == 11 and lib.Svc.static(1) == 21 and lib.Svc.klass(1) == 31
    assert asyncio.run(lib.public_async(2)) == 4
    assert lib.public_one(1) == 2  # not targeted
    assert [s.name for s in exp.get_finished_spans()] == [
        f"{sample}.Svc.method", f"{sample}.Svc.static", f"{sample}.Svc.klass", f"{sample}.public_async"]
    assert exp.get_finished_spans()[0].attributes["code.namespace"] == sample


def test_star_wraps_public_functions_only_and_rebinds_importers(sample, monkeypatch) -> None:  # noqa: ANN001
    user = importlib.import_module("app_sample.user")  # imported BEFORE init: holds the raw function
    exp = memory()
    monkeypatch.setattr(instrument, "REBIND_PREFIXES", ("app.", "app_sample."))
    enable(monkeypatch, exp, f"{sample}:*")
    lib = importlib.import_module(sample)
    assert getattr(lib.public_one, "__f2w_traced__", False) and not getattr(lib._private, "__f2w_traced__", False)
    assert not getattr(lib.Svc.method, "__f2w_traced__", False)
    assert user.call(1) == 2  # the from-import alias was re-bound to the wrapper
    assert [s.name for s in exp.get_finished_spans()] == [f"{sample}.public_one"]


def test_never_wraps_twice(sample, monkeypatch) -> None:  # noqa: ANN001
    exp = memory()
    enable(monkeypatch, exp, f"{sample}:public_one,{sample}:public_one,{sample}:*")
    lib = importlib.import_module(sample)
    assert instrument.wrap_target(f"{sample}:public_one") == []
    lib.public_one(1)
    assert len(exp.get_finished_spans()) == 1  # one span, not nested duplicates
    assert not hasattr(lib.public_one.__wrapped__, "__wrapped__")


def test_bad_targets_are_logged_not_raised(sample, monkeypatch, caplog) -> None:  # noqa: ANN001
    exp = memory()
    with caplog.at_level(logging.WARNING):
        enable(monkeypatch, exp, f"no.such.module:f,{sample}:Nope.x,{sample},{sample}:Svc.method@abc,{sample}:public_one")
    assert runtime.is_enabled()
    text = caplog.text
    assert "no.such.module:f" in text and "Nope" in text and "not module:qualname" in text and "bad @<ms>" in text
    assert getattr(importlib.import_module(sample).public_one, "__f2w_traced__", False)  # the good one still applied


def test_min_duration_filter(sample, monkeypatch) -> None:  # noqa: ANN001
    exp = memory()
    enable(monkeypatch, exp, f"{sample}:slow@{FLOOR_MS},{sample}:fast@{FLOOR_MS}")
    lib = importlib.import_module(sample)
    lib.slow()
    lib.fast()
    assert [s.name for s in exp.get_finished_spans()] == [f"{sample}.slow"]


def test_min_duration_never_hides_errors(sample, monkeypatch) -> None:  # noqa: ANN001
    exp = memory()
    enable(monkeypatch, exp, f"{sample}:fast@1000")
    lib = importlib.import_module(sample)
    lib.fast = lib.fast  # keep the wrapper
    with pytest.raises(TypeError):
        lib.fast(1)
    assert len(exp.get_finished_spans()) == 1


def test_module_prefix_with_global_floor(sample, monkeypatch) -> None:  # noqa: ANN001
    exp = memory()
    monkeypatch.setenv("F2W_OTEL_MIN_SPAN_MS", str(FLOOR_MS))
    enable(monkeypatch, exp, "", modules="app_sample")
    lib = importlib.import_module(sample)
    lib.slow()
    lib.fast()
    lib.public_one(1)
    assert [s.name for s in exp.get_finished_spans()] == [f"{sample}.slow"]


def test_every_default_target_resolves() -> None:
    """A rename must not leave a dead entry in the built-in list."""
    assert len(defaults.DEFAULT_TRACE_FUNCTIONS) == len(set(defaults.DEFAULT_TRACE_FUNCTIONS))
    for target in defaults.DEFAULT_TRACE_FUNCTIONS:
        module_name, _, qualname = target.partition(":")
        obj: object = importlib.import_module(module_name)
        for part in qualname.split("."):
            obj = inspect.getattr_static(obj, part)
        raw = obj.__func__ if isinstance(obj, staticmethod | classmethod) else obj
        assert inspect.isfunction(raw), target
        assert not inspect.isgeneratorfunction(raw) and not inspect.isasyncgenfunction(raw), target


def test_default_list_is_applied_when_functions_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    assert runtime.init(process="t", exporter=memory())
    assert instrument.applied() == frozenset(defaults.DEFAULT_TRACE_FUNCTIONS)
    from app.modules.f2w_agent.retrieval_agent import RetrievalAgent

    assert getattr(RetrievalAgent.query, "__f2w_traced__", False)
    runtime.reset()
    assert not getattr(RetrievalAgent.query, "__f2w_traced__", False)  # reset restores the originals
    time.sleep(0)
