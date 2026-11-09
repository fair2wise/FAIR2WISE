"""Parity tests for the bl1101 parser slice (T17B/H8 P3 slice b): fetch, parse_als, parse_blueprint, symbols.

``fixtures/bl1101/parsers_golden.json`` was captured from the pre-port ``bl1101_ingest`` module at ``forge_merge``
on frozen HTML (the cached ALS page, the checked-in blueprint export) and a synthetic source tree. No network.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path
import shutil
import types

import pytest

from app.modules import bl1101_ingest as legacy
from app.modules.contracts.json_types import JsonObject, JsonValue
from app.modules.graph.imports.bl1101 import common, compat, fetch, parse_als, parse_blueprint, paths, symbols

FIXTURES = Path(__file__).parent / "fixtures" / "bl1101"
GOLDEN: JsonObject = json.loads((FIXTURES / "parsers_golden.json").read_text(encoding="utf-8"))
PKG = Path(common.__file__).parent


def _plain(value: object) -> JsonValue:
    """Round-trip through JSON (dataclasses become dicts, tuples lists) as the golden file was written."""
    return json.loads(json.dumps(value, default=dataclasses.asdict))  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def als_html() -> str:
    return (FIXTURES / "als_11-0-1-2.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def blueprint_html() -> str:
    return paths.BLUEPRINT_PATH.read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "repo", root)
    (root / ".env").write_text("secret=1")
    (root / ".env.example").write_text("k=v")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "skip.js").write_text("x")
    (root / "pkg" / "blob.png").write_bytes(b"png")
    return root


def test_als_page_matches_golden(als_html: str) -> None:
    page = parse_als.parse_als_page(als_html)
    assert _plain(page) == GOLDEN["als_page"]
    assert _plain(parse_als.parse_als_contact_people(page["facts"], als_html)) == GOLDEN["als_people"]
    assert parse_als.parse_als_contact_people({}, "") == GOLDEN["als_people_none"] == []


def test_blueprint_matches_golden(blueprint_html: str) -> None:
    blueprint = parse_blueprint.parse_blueprint_html(blueprint_html)
    assert _plain(blueprint) == GOLDEN["blueprint"]
    assert _plain(parse_blueprint.parse_blueprint_people(blueprint, blueprint_html)) == GOLDEN["blueprint_people"]


def test_beam_path_helpers_match_golden() -> None:
    stage_names = GOLDEN["stage_names"]
    assert isinstance(stage_names, dict)
    for name, expected in stage_names.items():
        assert parse_blueprint.canonical_stage_name(name) == expected
    arrows = GOLDEN["arrows"]
    assert isinstance(arrows, dict)
    for text, expected in arrows.items():
        assert parse_blueprint.parse_arrow_sequence(text) == expected
    paths_in = [["M101", "Exit Slits", "M103", "Sample"], ["Exit Slits", "M103"], [], ["a", "b"], ["M103", "M101"]]
    assert [parse_blueprint.finalize_beam_path(p) for p in paths_in] == GOLDEN["finalize"]
    assert [parse_blueprint.ensure_m103_before_exit_slits(p) for p in paths_in] == GOLDEN["ensure_m103"]
    cases = [
        (["M101", "Exit Slits"], [], [], []),
        ([], ["A", "B"], [], []),
        ([], [], ["X", "Y"], ["S1"]),
        ([], [], [], ["S1", "S2"]),
    ]
    got = [
        parse_blueprint.resolve_beam_path_order(svg_names=a, caption_names=b, bcs2sim_names=c, stage_names=d)
        for a, b, c, d in cases
    ]
    assert got == GOLDEN["resolve"]


def test_symbols_match_golden(repo: Path) -> None:
    rel = lambda found: [str(p.relative_to(repo)) for p in found]  # noqa: E731
    assert rel(symbols.expand_globs(repo, ["**/*", "pkg/*.py", "README.md"])) == GOLDEN["expand_globs"]
    syms, snips = symbols.extract_python_symbols(repo / "pkg" / "devices.py", "bl11012-test", "abc123")
    assert (syms, snips) == (GOLDEN["py_symbols"], GOLDEN["py_snippets"])
    assert _plain(symbols.extract_python_symbols(repo / "pkg" / "devices.py", "bl11012-test")) == GOLDEN["py_nocommit"]
    assert _plain(symbols.extract_python_symbols(repo / "pkg" / "broken.py", "r")) == GOLDEN["py_broken"] == [[], []]
    assert _plain(symbols.extract_python_symbols(repo / "pkg" / "none.py", "r")) == GOLDEN["py_missing"]
    assert symbols.parse_compose_services(repo / "docker-compose.yml") == GOLDEN["compose"]
    assert symbols.parse_compose_services(repo / "compose-fallback.yml") == GOLDEN["compose_fallback"]
    assert symbols.extract_markdown_software(repo / "README.md", "r") == GOLDEN["md"]
    assert symbols.extract_markdown_software(repo / "README.md", "r", limit=20) == GOLDEN["md_short"]
    assert symbols.extract_markdown_software(repo / "none.md", "r") == GOLDEN["md_missing"] == ""
    assert symbols.extract_ts_fences(repo / "README.md", "r", "c0ffee") == GOLDEN["ts"]
    assert symbols.extract_ts_fences(repo / "none.md", "r") == GOLDEN["ts_missing"] == []


def test_secret_rules_match_golden() -> None:
    secrets = GOLDEN["secrets"]
    assert isinstance(secrets, dict)
    for name, expected in secrets.items():
        assert common.is_secret_path(Path(name)) is expected


def test_symbols_golden_content_is_meaningful() -> None:
    """The synthetic tree must exercise device, plan and loader branches and skip rules."""
    names = {s["name"]: s["category"] for s in GOLDEN["py_symbols"]}  # type: ignore[union-attr,index]
    assert names["Endstation"] == "Endstation" and names["AreaDetectorCCD"] == "Detector"
    assert names["load_sim_devices"] == "Method" and names["rel_scan"] == "BlueskyPlan"
    assert "BadError" not in names and "_private_scan" not in names and "unrelated" not in names
    listing = GOLDEN["expand_globs"]
    assert ".env" not in listing and "node_modules/skip.js" not in listing and ".env.example" in listing  # type: ignore[operator]


@pytest.mark.parametrize(
    ("module", "names"),
    [
        (common, ["utc_now", "sha256_bytes", "sha256_file", "slug", "beam_id", "strip_tags", "should_skip_file"]),
        (fetch, ["fetch_url", "git_sha", "_run_git", "sync_repo"]),
        (parse_als, ["parse_als_page", "parse_als_contact_people"]),
        (parse_blueprint, ["parse_blueprint_html", "canonical_stage_name", "resolve_beam_path_order"]),
        (symbols, ["expand_globs", "extract_python_symbols", "extract_ts_fences"]),
        (paths, ["REPO_ROOT", "BLUEPRINT_PATH", "TERMS_DIR", "CACHE_DIR"]),
    ],
)
def test_legacy_path_reexports_same_objects(module: types.ModuleType, names: list[str]) -> None:
    for name in names:
        assert getattr(legacy, name) is getattr(module, name)


def test_repo_root_is_the_checkout_root() -> None:
    assert (paths.REPO_ROOT / "app" / "modules" / "graph" / "imports" / "bl1101").is_dir()
    assert paths.REPO_ROOT == Path(legacy.__file__).resolve().parents[2]


# ----------------------------------------------------------------------------- patch propagation (compat)
def test_legacy_patch_reaches_split_module_and_undo_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    original = fetch.git_sha
    with monkeypatch.context() as m:
        m.setattr(legacy, "git_sha", lambda p: "f" * 40)
        assert fetch.git_sha(Path()) == "f" * 40 and legacy.git_sha is fetch.git_sha
    assert fetch.git_sha is original and legacy.git_sha is original


def test_patch_does_not_touch_modules_binding_a_different_object() -> None:
    module = types.ModuleType("probe")
    other = types.ModuleType("other")
    other.__dict__["x"] = "theirs"
    module.__dict__["x"] = "mine"
    compat.install(module, [other])
    module.x = "patched"  # type: ignore[attr-defined]
    assert other.x == "theirs"  # type: ignore[attr-defined]
    new = types.ModuleType("new")
    compat.install(new, [other])
    new.__dict__["y"] = 1
    other.__dict__["y"] = 1
    new.y = 2  # type: ignore[attr-defined]
    assert other.y == 2  # type: ignore[attr-defined]


# ----------------------------------------------------------------------------- boundaries
def test_only_fetch_touches_network_or_subprocess() -> None:
    offenders: dict[str, set[str]] = {}
    for file in sorted(PKG.glob("*.py")):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
        hit = found & {"subprocess", "urllib", "requests", "httpx", "socket"}
        if hit:
            offenders[file.name] = hit
    assert offenders == {"fetch.py": {"subprocess", "urllib"}}
