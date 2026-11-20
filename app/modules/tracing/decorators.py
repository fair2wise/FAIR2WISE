"""``@traced``: one span per call of a function, sync or async, with the signature preserved.

A traced function records its duration and, when it raises, an error status with the exception *type*
only. Attributes come from optional extractor callables and pass the content-disclosure filter
(:mod:`app.modules.tracing.safe`), so an extractor that returns prompt text has that key dropped.
When tracing is off the wrapper is one boolean check and a direct call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import functools
import inspect
import logging
from typing import Any, cast

from .runtime import SpanHandle, is_enabled, start

logger = logging.getLogger(__name__)

#: Attribute set on every wrapper; :func:`is_traced` reads it so a function is never wrapped twice.
MARKER = "__f2w_traced__"
_PREFIX = "app.modules."

AttributeExtractor = Callable[..., Mapping[str, object]]
ResultExtractor = Callable[[Any], Mapping[str, object]]


def default_name(fn: Callable[..., object]) -> str:
    """Span name for ``fn``: ``<module without app.modules.>.<qualname>``."""
    module = getattr(fn, "__module__", "") or ""
    return f"{module.removeprefix(_PREFIX)}.{getattr(fn, '__qualname__', repr(fn))}"


def is_traced(fn: object) -> bool:
    """Whether ``fn`` is already a tracing wrapper."""
    return bool(getattr(fn, MARKER, False))


def _call_extractor(extractor: Callable[..., Mapping[str, object]] | None, *args: object, **kwargs: object) -> Mapping[str, object]:
    if extractor is None:
        return {}
    try:
        return extractor(*args, **kwargs)
    except Exception as exc:  # an attribute bug must never fail the traced call
        logger.debug("span attribute extractor failed: %s", type(exc).__name__)
        return {}


def _static_attributes(fn: Callable[..., object]) -> dict[str, object]:
    return {"code.function": getattr(fn, "__qualname__", ""), "code.namespace": getattr(fn, "__module__", "")}


def wrap_callable[**P, R](
    fn: Callable[P, R],
    name: str,
    attributes: AttributeExtractor | None = None,
    result_attributes: ResultExtractor | None = None,
) -> Callable[P, R]:
    """Return ``fn`` wrapped in a span named ``name`` (the machinery behind :func:`traced` and the config list)."""
    static = _static_attributes(fn)

    def attrs(args: tuple[object, ...], kwargs: dict[str, object]) -> dict[str, object]:
        return {**static, **_call_extractor(attributes, *args, **kwargs)}

    def done(handle: SpanHandle, result: object) -> None:
        if result_attributes is not None:
            handle.set_attributes(_call_extractor(result_attributes, result))

    wrapper: Callable[P, R]
    if inspect.iscoroutinefunction(fn):
        async_fn = cast(Callable[P, Awaitable[Any]], fn)

        @functools.wraps(fn)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:  # noqa: ANN401  # Any: returns whatever the wrapped coroutine returns
            if not is_enabled():
                return await async_fn(*args, **kwargs)
            with start(name, attrs(args, kwargs)) as handle:
                result = await async_fn(*args, **kwargs)
                done(handle, result)
                return result

        wrapper = cast(Callable[P, R], async_wrapper)
    else:

        @functools.wraps(fn)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if not is_enabled():
                return fn(*args, **kwargs)
            with start(name, attrs(args, kwargs)) as handle:
                result = fn(*args, **kwargs)
                done(handle, result)
                return result

        wrapper = sync_wrapper
    setattr(wrapper, MARKER, True)
    return wrapper


def traced[**P, R](
    name: str | None = None,
    *,
    attributes: AttributeExtractor | None = None,
    result_attributes: ResultExtractor | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: record each call of the function as a span.

    Args:
        name: Span name (a fixed word); default ``<module>.<qualname>``.
        attributes: Called with the call's own ``*args, **kwargs``; returns attributes to record. Keys and
            values pass the content-disclosure filter, so only ids, names, counts, sizes and statuses survive.
        result_attributes: Called with the return value; same rules.

    Returns:
        A decorator that keeps the function's signature, ``__wrapped__`` and coroutine-ness.
    """

    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        if is_traced(fn):
            return fn
        return wrap_callable(fn, name or default_name(fn), attributes, result_attributes)

    return decorate
