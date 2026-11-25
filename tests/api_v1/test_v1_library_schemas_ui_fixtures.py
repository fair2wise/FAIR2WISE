"""The UI's schema fixtures (``ui/src/app/components/__fixtures__/schema_*.json``) pin the shapes the schema panel reads.

Each fixture is compared with what the real routes (or the real service for the preview) answer: same keys at every
level and the same JSON types for every value that is not ``null`` on either side. A change to a route that the
panel reads fails here, so the fixture and the TypeScript types are changed together.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.api_v1.test_v1_library_schemas import BASE, _install
from tests.library.test_schema_preview import REPLY, build as build_preview
from tests.library.test_user_schema_validation import GOOD

FIXTURES = Path(__file__).resolve().parents[2] / "ui" / "src" / "app" / "components" / "__fixtures__"


def fixture(name: str) -> Any:  # noqa: ANN401
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def shape(value: Any) -> Any:  # noqa: ANN401
    """Keys and JSON types; ``None`` is a wildcard (an optional value), a list is its first element's shape."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__


def same_shape(real: Any, pinned: Any, path: str = "$") -> None:  # noqa: ANN401
    if real is None or pinned is None:
        return
    if isinstance(real, dict):
        assert isinstance(pinned, dict) and set(real) == set(pinned), (path, sorted(real), sorted(pinned))
        for key in real:
            same_shape(real[key], pinned[key], f"{path}.{key}")
    elif isinstance(real, list):
        assert isinstance(pinned, list), path
        if real and pinned:
            same_shape(real[0], pinned[0], path + "[0]")
    else:
        assert type(real) is type(pinned), (path, type(real).__name__, type(pinned).__name__)


def test_the_validate_save_list_read_diff_and_error_shapes(local):
    c = local.client()
    bad = GOOD.replace("name: catfish", "name: eels")
    same_shape(c.post(f"{BASE}/validate", json={"text": bad}).json(), fixture("schema_report_invalid.json"))
    same_shape(c.post(f"{BASE}/validate", json={"text": GOOD}).json(), fixture("schema_report_ok.json"))
    refused = c.post(BASE, json={"name": "catfish", "text": bad})
    assert refused.status_code == 422
    same_shape(refused.json(), fixture("schema_save_invalid.json"))
    saved = c.post(BASE, json={"name": "catfish", "text": GOOD})
    same_shape(saved.json(), fixture("schema_save.json"))
    c.post(BASE, json={"name": "catfish", "text": GOOD.replace("A species of catfish.", "An armoured catfish.")})
    same_shape(c.get(f"{BASE}/user").json(), fixture("schema_user_list.json"))
    same_shape(c.get(f"{BASE}/user/catfish@v1").json(), fixture("schema_stored.json"))
    same_shape(c.get(f"{BASE}/user/catfish/diff", params={"from": 1, "to": 2}).json(), fixture("schema_diff.json"))
    # the chooser entry of a user schema, as the public listing serves it (no host path)
    mine = [s for s in c.get("/v1/library/schemas").json()["schemas"] if s.get("origin") == "user"]
    assert mine, "the user schema must be in the chooser"
    same_shape(mine[0], fixture("schema_choice_user.json"))
    assert mine[0]["name"].startswith("user/")  # the selection value the UI sends as `schema`


def test_the_generate_shape(local, tmp_path):
    _install(local, tmp_path, [GOOD.replace("    domain: Species\n", "    domain: Nope\n"), GOOD])
    out = local.client().post(f"{BASE}/generate", json={"description": "catfish", "model": "lbl/cborg-chat"})
    same_shape(out.json(), fixture("schema_generate.json"))


def test_the_preview_shapes(tmp_path):
    _world, preview, _fake, paper_id, _ = build_preview(tmp_path, [REPLY], pages=1)
    first = preview.preview(GOOD, paper_id, model="lbl/cborg-chat", backend=None, confirm=False, confirmation=None)
    same_shape(first, fixture("schema_preview_estimate.json"))
    second = preview.preview(GOOD, paper_id, model="lbl/cborg-chat", backend=None, confirm=True,
                             confirmation=first["confirmation"])
    same_shape(second, fixture("schema_preview_result.json"))


def test_the_prompt_size_shapes(tmp_path):
    from tests.library.test_user_schema_selection import user_world
    from tests.library.test_library_extract import _three

    world, _store, ident = user_world(tmp_path)
    ids = world.ids(*_three(world))
    same_shape(world.kg.estimate(ids, "m-1", ident)["schema_prompt"], fixture("schema_prompt_sizes.json"))
    from app.modules.jobs.run_summary import SCHEMA_PROMPT_KEYS

    assert SCHEMA_PROMPT_KEYS == ("schema_prompt_full_chars", "schema_prompt_used_chars", "schema_prompt_budget")
    assert set(fixture("schema_prompt_condensed.json")) == {"full_chars", "used_chars", "budget"}
