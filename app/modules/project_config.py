"""Shared project configuration helpers.

Precedence for callers should be:
1. explicit CLI/function arguments
2. environment variables loaded from .env or the runtime
3. config.yml defaults
4. hard-coded fallback values
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yml"


@lru_cache(maxsize=1)
def load_project_config() -> dict[str, Any]:
    """Load root config.yml, returning an empty dict if it is absent."""
    config_path = Path(os.environ.get("FAIR2WISE_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def get_config(path: str, fallback: Any = None) -> Any:
    """Return a nested config value using a dotted path."""
    value: Any = load_project_config()
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return fallback
        value = value[part]
    return value


def _env_names(entry: Any) -> Iterable[str]:
    if not isinstance(entry, dict):
        return ()
    env = entry.get("env")
    if isinstance(env, str):
        return (env,)
    if isinstance(env, list):
        return tuple(str(name) for name in env)
    return ()


def _default_value(entry: Any, fallback: Any = None) -> Any:
    if isinstance(entry, dict):
        if "default" in entry:
            return entry["default"]
        if "env" in entry:
            return fallback
        return entry
    if entry is not None:
        return entry
    return fallback


def as_bool(value: Any) -> bool:
    """Coerce env/config values to bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def config_value(
    path: str,
    fallback: Any = None,
    *,
    cast: Callable[[Any], Any] | None = None,
) -> Any:
    """Resolve env override first, then config.yml default/value, then fallback."""
    entry = get_config(path)
    for name in _env_names(entry):
        value = os.environ.get(name)
        if value is not None:
            return cast(value) if cast else value
    value = _default_value(entry, fallback)
    return cast(value) if cast and value is not None else value


def secret_env(path: str) -> str | None:
    """Read a secret from the env var named in config.yml; never from YAML value."""
    entry = get_config(path, {})
    for name in _env_names(entry):
        value = os.environ.get(name)
        if value:
            return value
    return None
