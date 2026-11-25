"""The user schema store: names, immutable versions, concurrency, refusal with line numbers, diff, guarded delete."""
from __future__ import annotations

import json
import os
import stat
import threading

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.library import user_schemas as U
from tests.library.test_user_schema_validation import GOOD


@pytest.fixture
def store(tmp_path):
    return U.UserSchemaStore(tmp_path / "schemas")


def text_for(name: str, extra: str = "") -> str:
    return GOOD.replace("name: catfish", f"name: {name}") + extra


def test_save_writes_v1_then_v2_immutably_with_private_modes(store):
    one = store.save("catfish", text_for("catfish"))
    assert one.id == "user/catfish@v1" and one.version == 1 and one.relpath == "catfish/catfish@v1.yaml"
    two = store.save("catfish", text_for("catfish").replace("Where a species lives.", "Where it lives."))
    assert two.id == "user/catfish@v2" and two.sha256 != one.sha256
    assert store.get("user/catfish@v1").text == text_for("catfish")  # v1 untouched
    path = store.path_for("user/catfish@v1")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".")]  # no temp file left


def test_list_gives_latest_and_all_versions(store):
    store.save("catfish", text_for("catfish"))
    store.save("catfish", text_for("catfish"))
    store.save("eels", text_for("eels"))
    rows = store.list()
    assert [r["name"] for r in rows] == ["catfish", "eels"]
    assert rows[0]["latest"]["id"] == "user/catfish@v2" and rows[0]["versions"] == [1, 2]
    assert "text" not in rows[0]["latest"]
    assert U.UserSchemaStore(store.root.parent / "nothing").list() == []


@pytest.mark.parametrize("name", ["../x", "a/b", "A", "1a", "", "a b", "a.b", "x" * 65, "..", "a\x00b", "-a"])
def test_bad_names_are_refused_before_any_path_is_built(store, name):
    with pytest.raises(ContractError) as err:
        store.save(name, text_for("catfish"))
    assert err.value.code is ErrorCode.INVALID_INPUT
    assert not store.root.exists()


@pytest.mark.parametrize("ident", ["user/../x@v1", "user/a@v0", "user/a@v01", "user/a@1", "other/a@v1", "user/a/b@v1", "user/a@v1.yaml"])
def test_bad_ids_are_refused(store, ident):
    with pytest.raises(ContractError) as err:
        store.get(ident)
    assert err.value.code is ErrorCode.INVALID_INPUT


def test_missing_version_is_not_found(store):
    store.save("catfish", text_for("catfish"))
    with pytest.raises(ContractError) as err:
        store.get("user/catfish@v9")
    assert err.value.code is ErrorCode.NOT_FOUND


def test_invalid_text_is_refused_with_the_report_and_lines_and_nothing_is_written(store):
    bad = text_for("catfish").replace("    domain: Species\n", "    domain: Nope\n")
    with pytest.raises(U.SchemaInvalid) as err:
        store.save("catfish", bad)
    problem = next(p for p in err.value.report.problems if p.code == "unknown_reference")
    assert bad.split("\n")[problem.line - 1].strip() == "lives_in:"
    assert err.value.code is ErrorCode.INVALID_INPUT and err.value.reason == "schema_invalid"
    assert store.list() == []


def test_the_text_name_must_match_the_store_name_with_a_line(store):
    with pytest.raises(U.SchemaInvalid) as err:
        store.save("eels", text_for("catfish"))
    problem = err.value.report.problems[0]
    assert problem.code == "name_mismatch" and text_for("catfish").split("\n")[problem.line - 1] == "name: catfish"
    assert store.list() == []


def test_two_concurrent_saves_get_v1_and_v2(store):
    results, barrier = [], threading.Barrier(2)

    def go():
        barrier.wait()
        results.append(store.save("catfish", text_for("catfish")).version)

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [1, 2]
    assert store.versions("catfish") == [1, 2]


def test_a_save_never_overwrites_an_existing_file(store, monkeypatch):
    store.save("catfish", text_for("catfish"))
    first = store.path_for("user/catfish@v1")
    before = first.read_bytes()
    # A stale view says nothing exists (v1 is taken, so the link fails), then the retry sees v1.
    calls = iter([[], [1]])
    monkeypatch.setattr(store, "_versions", lambda name: next(calls))
    saved = store.save("catfish", text_for("catfish", "# changed\n"))
    assert saved.version == 2 and first.read_bytes() == before


def test_symlinked_version_is_not_read(store, tmp_path):
    store.save("catfish", text_for("catfish"))
    link = store.root / "catfish" / "catfish@v5.yaml"
    secret = tmp_path / "secret.yaml"
    secret.write_text("x")
    os.symlink(secret, link)
    with pytest.raises(ContractError):
        store.get("user/catfish@v5")
    assert store.versions("catfish") == [1]


def test_diff_between_versions(store):
    store.save("catfish", text_for("catfish"))
    store.save("catfish", text_for("catfish").replace("A species of catfish.", "A species of armoured catfish."))
    diff = store.diff("catfish", 1, 2)
    assert "--- user/catfish@v1" in diff and "+++ user/catfish@v2" in diff
    assert "-    description: A species of catfish." in diff and "+    description: A species of armoured catfish." in diff
    with pytest.raises(ContractError):
        store.diff("catfish", 1, 7)


def _bundle(root, name, schema):
    directory = root / "kgs" / name
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({"schema": {"id": schema}}))


def test_delete_only_when_no_manifest_references_it(tmp_path):
    store = U.default_store(tmp_path)
    store.save("catfish", text_for("catfish"))
    store.save("catfish", text_for("catfish"))
    _bundle(tmp_path, "run1", "user/catfish@v1")
    with pytest.raises(ContractError) as err:
        store.delete("user/catfish@v1")
    assert err.value.code is ErrorCode.CONFLICT and err.value.reason == "schema_in_use"
    store.delete("user/catfish@v2")  # unreferenced
    assert store.versions("catfish") == [1]
    _bundle(tmp_path, "run2", "matkg_schema@0.3.1")
    (tmp_path / "kgs" / "run1" / "manifest.json").write_text(json.dumps({"schema": {"id": "x"}}))
    store.delete("user/catfish@v1")
    assert store.list() == [] and not (store.root / "catfish").exists()


def test_delete_fails_closed_on_an_unreadable_manifest(tmp_path):
    store = U.default_store(tmp_path)
    store.save("catfish", text_for("catfish"))
    _bundle(tmp_path, "run1", "x")
    (tmp_path / "kgs" / "run1" / "manifest.json").write_text("{not json")
    with pytest.raises(ContractError) as err:
        store.delete("user/catfish@v1")
    assert err.value.code is ErrorCode.UNAVAILABLE and store.versions("catfish") == [1]


def test_manifest_relpath_is_checked(store):
    assert store.path_from_relpath("catfish/catfish@v1.yaml").name == "catfish@v1.yaml"
    for bad in ("../catfish@v1.yaml", "catfish/../x@v1.yaml", "a/b@v1.yaml", "catfish@v1.yaml", "/etc/passwd"):
        with pytest.raises(ContractError):
            store.path_from_relpath(bad)
