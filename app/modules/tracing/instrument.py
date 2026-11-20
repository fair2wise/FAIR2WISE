"""Config-driven function tracing: wrap callables by name at startup, without editing their modules.

``F2W_OTEL_TRACE_FUNCTIONS`` is a comma list of targets:

* ``package.module:Class.method`` or ``package.module:function``: that callable;
* ``package.module:*``: every public (no leading underscore) function defined in the module;
* any target may end in ``@<ms>`` (``...:RetrievalAgent.query@5``): spans shorter than ``<ms>`` are dropped.

Unset means :data:`app.modules.tracing.defaults.DEFAULT_TRACE_FUNCTIONS`; set but empty means none.
``F2W_OTEL_TRACE_MODULES`` is a comma list of module or package prefixes whose public functions are all
wrapped, with the global floor ``F2W_OTEL_MIN_SPAN_MS``.

Wrapping is idempotent (a callable is never wrapped twice), a bad target is logged and skipped, and a
wrapped module-level function is also re-bound in every already-imported ``app.*`` module that did
``from module import function`` earlier, so import order does not matter.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import sys
import threading
from types import ModuleType
from typing import Any

from .config import TracingConfig
from .decorators import default_name, is_traced, wrap_callable
from .defaults import DEFAULT_TRACE_FUNCTIONS
from .runtime import register_min_duration

logger = logging.getLogger(__name__)

#: Module-name prefixes searched for ``from module import function`` aliases to re-bind.
REBIND_PREFIXES = ("app.",)
_APPLIED: set[str] = set()
_ORIGINALS: dict[str, tuple[object, str, object]] = {}
_LOCK = threading.Lock()


def parse_targets(raw: str) -> list[tuple[str, float]]:
    """Split a target list into ``(module:qualname, min_ms)`` pairs; malformed entries are logged and skipped."""
    out: list[tuple[str, float]] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        target, _, floor = text.partition("@")
        try:
            minimum = float(floor) if floor else 0.0
        except ValueError:
            logger.warning("trace target %r has a bad @<ms> suffix; skipped", text)
            continue
        if target.count(":") != 1 or not all(target.split(":")):
            logger.warning("trace target %r is not module:qualname; skipped", text)
            continue
        out.append((target, minimum))
    return out


def _resolve(module: ModuleType, qualname: str) -> tuple[object, str, object]:
    """Return ``(owner, attribute name, raw attribute)`` for ``qualname`` inside ``module``."""
    parts = qualname.split(".")
    owner: object = module
    for part in parts[:-1]:
        owner = getattr(owner, part)
    name = parts[-1]
    return owner, name, inspect.getattr_static(owner, name)


def _wrap_attribute(owner: object, name: str, raw: object, span_name: str, key: str) -> object | None:
    """Wrap a function, staticmethod or classmethod; return the original callable that was replaced."""
    if isinstance(raw, staticmethod):
        original = raw.__func__
        if is_traced(original):
            return None
        setattr(owner, name, staticmethod(wrap_callable(original, span_name)))
    elif isinstance(raw, classmethod):
        original = raw.__func__
        if is_traced(original):
            return None
        setattr(owner, name, classmethod(wrap_callable(original, span_name)))
    elif inspect.isfunction(raw):
        original = raw
        if is_traced(original):
            return None
        if inspect.isgeneratorfunction(original) or inspect.isasyncgenfunction(original):
            logger.info("trace target %s is a generator; skipped (a span would time only its creation)", key)
            return None
        setattr(owner, name, wrap_callable(original, span_name))
    else:
        raise TypeError(f"{key} is not a plain function or method")
    return original


def _rebind(module: ModuleType, name: str, original: object, wrapper: object) -> None:
    """Replace ``original`` by ``wrapper`` in every imported ``app.*`` module that imported it by name."""
    for mod_name, mod in list(sys.modules.items()):
        if mod is None or not mod_name.startswith(REBIND_PREFIXES):
            continue
        namespace = getattr(mod, "__dict__", {})
        for attr, value in list(namespace.items()):
            if value is original and mod is not module:
                namespace[attr] = wrapper


def wrap_target(target: str, minimum_ms: float = 0.0) -> list[str]:
    """Wrap ``module:qualname`` (or ``module:*``); return the span names added (empty when already wrapped).

    Errors (module missing, attribute missing, not a function) are logged at warning level and never raised.
    """
    module_name, _, qualname = target.partition(":")
    try:
        module = importlib.import_module(module_name)
        if qualname == "*":
            return wrap_module(module, minimum_ms)
        owner, name, raw = _resolve(module, qualname)
        return _wrap_one(module, owner, name, raw, qualname, minimum_ms)
    except Exception as exc:
        logger.warning("trace target %s skipped: %s: %s", target, type(exc).__name__, exc)
        return []


def _wrap_one(module: ModuleType, owner: object, name: str, raw: object, qualname: str, minimum_ms: float) -> list[str]:
    key = f"{module.__name__}:{qualname}"
    with _LOCK:
        if key in _APPLIED:
            return []
        probe = raw.__func__ if isinstance(raw, staticmethod | classmethod) else raw
        span_name = default_name(probe) if callable(probe) else key
        original = _wrap_attribute(owner, name, raw, span_name, key)
        _APPLIED.add(key)
        if original is None:
            return []
        _ORIGINALS[key] = (owner, name, raw)
    if owner is module:
        _rebind(module, name, original, getattr(module, name))
    register_min_duration(span_name, minimum_ms)
    return [span_name]


def wrap_module(module: ModuleType, minimum_ms: float = 0.0) -> list[str]:
    """Wrap every public function defined in ``module`` (``module:*``)."""
    added: list[str] = []
    for name, value in list(vars(module).items()):
        if name.startswith("_") or not inspect.isfunction(value) or value.__module__ != module.__name__:
            continue
        added += _wrap_one(module, module, name, value, name, minimum_ms)
    return added


def wrap_prefix(prefix: str, minimum_ms: float) -> list[str]:
    """Wrap the public functions of ``prefix`` and, when it is a package, of every submodule (imports them)."""
    added: list[str] = []
    try:
        root = importlib.import_module(prefix)
    except Exception as exc:
        logger.warning("trace module prefix %s skipped: %s: %s", prefix, type(exc).__name__, exc)
        return added
    added += wrap_module(root, minimum_ms)
    path = getattr(root, "__path__", None)
    if path is None:
        return added
    for info in pkgutil.walk_packages(path, prefix=f"{prefix}."):
        try:
            added += wrap_module(importlib.import_module(info.name), minimum_ms)
        except Exception as exc:
            logger.warning("trace module %s skipped: %s: %s", info.name, type(exc).__name__, exc)
    return added


def apply(config: TracingConfig) -> list[str]:
    """Wrap the configured functions (default list when ``F2W_OTEL_TRACE_FUNCTIONS`` is unset); return span names."""
    added: list[str] = []
    if config.trace_functions is None:
        targets = [(t, 0.0) for t in DEFAULT_TRACE_FUNCTIONS]
    else:
        targets = parse_targets(config.trace_functions)
    for target, minimum in targets:
        added += wrap_target(target, minimum)
    for prefix in config.trace_modules:
        added += wrap_prefix(prefix, config.min_span_ms)
    return added


def applied() -> frozenset[str]:
    """Targets wrapped so far in this process (``module:qualname``)."""
    return frozenset(_APPLIED)


def forget() -> None:
    """Undo every wrap (tests): restore the original attributes and clear the registry."""
    with _LOCK:
        for owner, name, raw in _ORIGINALS.values():
            setattr(owner, name, raw)
        _ORIGINALS.clear()
        _APPLIED.clear()


def describe(target: str) -> Any:  # noqa: ANN401  # Any: the resolved attribute, whatever it is (diagnostics and tests)
    """Resolve ``module:qualname`` to the current attribute (wrapped or not)."""
    module_name, _, qualname = target.partition(":")
    owner, name, _raw = _resolve(importlib.import_module(module_name), qualname)
    return getattr(owner, name)
