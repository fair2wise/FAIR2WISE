"""app.modules.operations: lazy public names resolve to the owning submodule, cache, and reject unknown names."""

from __future__ import annotations

import importlib

import pytest

import app.modules.operations as ops


def test_all_lists_every_export_sorted_and_unique():
    assert ops.__all__ == sorted(ops._EXPORTS)
    assert len(ops.__all__) == len(set(ops.__all__))


@pytest.mark.parametrize("name", sorted(ops._EXPORTS))
def test_every_export_resolves_to_its_declared_module_attribute(name):
    module, attr = ops._EXPORTS[name]
    expected = getattr(importlib.import_module(f"app.modules.operations.{module}"), attr)
    got = getattr(ops, name)
    assert got is expected
    assert ops.__dict__[name] is expected  # cached: __getattr__ is not hit again


def test_unknown_name_raises_attribute_error_naming_module():
    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        ops.nope  # noqa: B018
    assert not hasattr(ops, "nope")


def test_dir_includes_lazy_names_and_is_sorted():
    listing = ops.__dir__()
    assert listing == sorted(listing)
    assert set(ops.__all__) <= set(listing)
