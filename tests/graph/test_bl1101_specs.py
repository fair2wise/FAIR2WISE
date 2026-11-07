"""Tests for the ``graph.imports.bl1101.specs`` definitions slice (T17B/H8 P3 slice a)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from app.modules import bl1101_ingest as legacy
from app.modules.contracts.json_types import JsonObject, JsonValue
from app.modules.graph.imports.bl1101 import specs

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "bl1101_specs_golden.json"
GOLDEN: JsonObject = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _encode(value: JsonValue) -> JsonValue:
    """Encode a spec value into the JSON form stored in the golden file."""
    if isinstance(value, re.Pattern):
        return {"__re__": [value.pattern, value.flags]}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {k: _encode(v) for k, v in dataclasses.asdict(value).items()}
        return {"__dc__": type(value).__name__, **fields}
    if isinstance(value, set | frozenset):
        return {"__set__": sorted(value)}
    if isinstance(value, tuple):
        return {"__tuple__": [_encode(v) for v in value]}
    if isinstance(value, dict):
        return {"__dict__": [[k, _encode(v)] for k, v in value.items()]}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    return value


def _encode_class(cls: type) -> JsonObject:
    fields = dataclasses.fields(cls)
    return {
        "__class__": [f.name for f in fields],
        "defaults": {f.name: (None if f.default is dataclasses.MISSING else f.default) for f in fields},
        "frozen": cls.__dataclass_params__.frozen,  # type: ignore[attr-defined]
    }


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_spec_equals_golden_from_pre_port_module(name: str) -> None:
    """Every ported name equals the value captured from the pre-port ``bl1101_ingest``."""
    value = getattr(specs, name)
    encoded = _encode_class(value) if isinstance(value, type) else _encode(value)
    assert json.loads(json.dumps(encoded)) == GOLDEN[name]


def test_golden_covers_every_public_definition() -> None:
    """No definition in ``specs`` is missing from the golden file."""
    defined = {
        n
        for n, v in vars(specs).items()
        if n.isupper() or (isinstance(v, type) and v.__module__ == specs.__name__)
    }
    assert defined == set(GOLDEN)


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_legacy_module_reexports_same_object(name: str) -> None:
    """The legacy path exposes the very objects of the new module (patch-through stays valid)."""
    assert getattr(legacy, name) is getattr(specs, name)


def test_repo_specs_are_frozen_and_typed() -> None:
    """``RepoSpec`` is immutable and the shipped specs cover the eight upstream repos."""
    assert [s.name for s in specs.REPO_SPECS] == [
        "bcs2sim-ophyd",
        "ADAxisSXR40",
        "bl11012-bluesky",
        "bl11012-scan-replay",
        "bl11012-finch",
        "splash_tiled",
        "tiled",
        "als_computing_hub",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        specs.REPO_SPECS[0].name = "x"  # type: ignore[misc]
    assert all(s.url.startswith("https://github.com/als-computing/") for s in specs.REPO_SPECS)
    assert all(s.readme_only == (s.mode == "readme") for s in specs.REPO_SPECS)


def test_blueprint_dataclasses_keep_defaults() -> None:
    """Blueprint rows default their optional fields independently per instance."""
    a = specs.BlueprintStage("n", "s", "w", "d", "r")
    b = specs.BlueprintStage("n", "s", "w", "d", "r")
    a.device_ids.append("x")
    assert b.device_ids == []
    dev = specs.BlueprintDevice("1", "n", "motor", "Motor", "pv", "h", "o", "c", "i", "m", "d", "mean")
    assert dev.collection == ""


def test_stage_aliases_resolve_through_legacy_helper() -> None:
    """The legacy ``canonical_stage_name`` still resolves via the moved alias table."""
    assert legacy.canonical_stage_name("KB pair") == "M103"
    assert legacy.canonical_stage_name("Kirkpatrick-Baez") == "M103"
    assert legacy.canonical_stage_name("unknown thing") == "unknown thing"


def test_import_has_no_side_effects(tmp_path: Path) -> None:
    """Importing ``specs`` in a fresh interpreter pulls in no I/O or network modules."""
    code = (
        "import sys\n"
        "import app.modules.graph.imports.bl1101.specs\n"
        "bad = [m for m in ('urllib.request', 'subprocess', 'lxml', 'yaml', 'socket', 'requests', 'httpx')"
        " if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={"PYTHONPATH": str(root), "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    # app.modules/__init__ may import heavier modules; the check is that specs adds none of its own.
    baseline = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code.replace("import app.modules.graph.imports.bl1101.specs", "import app.modules")],
        cwd=tmp_path,
        env={"PYTHONPATH": str(root), "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == baseline.stdout


def test_specs_source_has_no_io_imports() -> None:
    """The definitions module imports only ``dataclasses`` and ``re``."""
    text = Path(specs.__file__).read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import) (\S+)", text, re.M)
    assert sorted(imports) == ["__future__", "dataclasses", "re"]
