import importlib

import pytest
import yaml

from app.modules import project_config


def write_config(tmp_path, data):
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def reload_config(monkeypatch, config_path=None):
    project_config.load_project_config.cache_clear()
    if config_path is None:
        monkeypatch.delenv("FAIR2WISE_CONFIG", raising=False)
    else:
        monkeypatch.setenv("FAIR2WISE_CONFIG", str(config_path))
    return importlib.reload(project_config)


def test_load_project_config_reads_yaml_and_uses_cache(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"section": {"value": 1}})
    cfg = reload_config(monkeypatch, config_path)

    assert cfg.load_project_config() == {"section": {"value": 1}}
    config_path.write_text(yaml.safe_dump({"section": {"value": 2}}), encoding="utf-8")
    assert cfg.load_project_config() == {"section": {"value": 1}}

    cfg.load_project_config.cache_clear()
    assert cfg.load_project_config() == {"section": {"value": 2}}


def test_load_project_config_returns_empty_for_missing_or_empty_yaml(tmp_path, monkeypatch):
    missing = tmp_path / "missing.yml"
    cfg = reload_config(monkeypatch, missing)
    assert cfg.load_project_config() == {}

    empty = tmp_path / "empty.yml"
    empty.write_text("", encoding="utf-8")
    cfg = reload_config(monkeypatch, empty)
    assert cfg.load_project_config() == {}


def test_load_project_config_raises_yaml_error_for_invalid_yaml(tmp_path, monkeypatch):
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("root: [unterminated", encoding="utf-8")
    cfg = reload_config(monkeypatch, invalid)

    with pytest.raises(yaml.YAMLError):
        cfg.load_project_config()


def test_get_config_returns_nested_values_and_fallbacks(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"a": {"b": {"c": "found"}, "none": None}})
    cfg = reload_config(monkeypatch, config_path)

    assert cfg.get_config("a.b.c") == "found"
    assert cfg.get_config("a.b.missing", fallback="fallback") == "fallback"
    assert cfg.get_config("a.none.key", fallback="fallback") == "fallback"


def test_config_value_prefers_env_then_default_then_literal_then_fallback(tmp_path, monkeypatch):
    config_path = write_config(
        tmp_path,
        {
            "service": {
                "timeout": {"env": "TEST_TIMEOUT", "default": "30"},
                "literal": "from-yaml",
                "missing_default": {"env": "TEST_MISSING"},
            }
        },
    )
    cfg = reload_config(monkeypatch, config_path)

    assert cfg.config_value("service.timeout", cast=int) == 30
    monkeypatch.setenv("TEST_TIMEOUT", "45")
    assert cfg.config_value("service.timeout", cast=int) == 45
    assert cfg.config_value("service.literal") == "from-yaml"
    assert cfg.config_value("service.missing_default", fallback="fallback") == "fallback"


def test_config_value_uses_first_defined_env_name_even_if_empty(tmp_path, monkeypatch):
    config_path = write_config(
        tmp_path,
        {"service": {"model": {"env": ["PRIMARY_MODEL", "SECONDARY_MODEL"], "default": "default"}}},
    )
    cfg = reload_config(monkeypatch, config_path)
    monkeypatch.setenv("PRIMARY_MODEL", "")
    monkeypatch.setenv("SECONDARY_MODEL", "secondary")

    assert cfg.config_value("service.model") == ""


def test_config_value_returns_dict_literal_without_env_or_default(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"section": {"literal_dict": {"key": "value"}}})
    cfg = reload_config(monkeypatch, config_path)

    assert cfg.config_value("section.literal_dict") == {"key": "value"}


def test_env_names_accepts_string_list_and_rejects_other_shapes():
    assert tuple(project_config._env_names({"env": "ONE"})) == ("ONE",)
    assert tuple(project_config._env_names({"env": ["ONE", 2]})) == ("ONE", "2")
    assert tuple(project_config._env_names({"env": {"bad": "shape"}})) == ()
    assert tuple(project_config._env_names("bad")) == ()


def test_as_bool_coerces_common_values():
    true_values = [True, 1, 3.14, "1", "true", "YES", "y", "on"]
    false_values = [False, 0, 0.0, None, "0", "false", "no", "off", ""]

    assert all(project_config.as_bool(value) is True for value in true_values)
    assert all(project_config.as_bool(value) is False for value in false_values)


def test_secret_env_reads_only_environment_not_yaml_value(tmp_path, monkeypatch):
    config_path = write_config(
        tmp_path,
        {"secrets": {"api_key": {"env": ["PRIMARY_SECRET", "SECONDARY_SECRET"], "value": "yaml-secret"}}},
    )
    cfg = reload_config(monkeypatch, config_path)

    monkeypatch.delenv("PRIMARY_SECRET", raising=False)
    monkeypatch.delenv("SECONDARY_SECRET", raising=False)
    assert cfg.secret_env("secrets.api_key") is None

    monkeypatch.setenv("SECONDARY_SECRET", "runtime-secret")
    assert cfg.secret_env("secrets.api_key") == "runtime-secret"


def test_secret_env_skips_empty_primary_and_uses_secondary(tmp_path, monkeypatch):
    config_path = write_config(
        tmp_path,
        {"secrets": {"api_key": {"env": ["PRIMARY_SECRET", "SECONDARY_SECRET"]}}},
    )
    cfg = reload_config(monkeypatch, config_path)
    monkeypatch.setenv("PRIMARY_SECRET", "")
    monkeypatch.setenv("SECONDARY_SECRET", "secondary-secret")

    assert cfg.secret_env("secrets.api_key") == "secondary-secret"


def test_config_value_cast_skips_none_fallback(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"entry": {"env": "ABSENT_ENV"}})
    cfg = reload_config(monkeypatch, config_path)
    monkeypatch.delenv("ABSENT_ENV", raising=False)

    assert cfg.config_value("entry", cast=int) is None
