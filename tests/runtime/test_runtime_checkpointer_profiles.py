"""T02: checkpointer lifecycle, thread scoping and capability groups (R-05/R-07)."""

from __future__ import annotations

import importlib.util

import pytest

from app.modules.agent_runtime.checkpointer import (
    CheckpointerBusyError,
    CheckpointerConfig,
    ManagedCheckpointer,
    owner_key,
    scoped_thread_id,
    thread_config,
)
from app.modules.agent_runtime.profiles import (
    CURATOR,
    LITERATURE_READER,
    CapabilityGroup,
    get_group,
    turn_capabilities,
)
from tests.runtime.runtime_fakes import als_ctx, local_ctx


def test_memory_checkpointer_lifecycle_and_busy_guard():
    managed = ManagedCheckpointer()
    saver = managed.open()
    assert managed.open() is saver and managed.saver is saver
    with managed.run_scope("t1"):
        assert managed.active_threads() == {"t1": 1}
        with pytest.raises(CheckpointerBusyError):
            managed.reset()
        with pytest.raises(CheckpointerBusyError):
            managed.delete_thread("t1")
    managed.delete_thread("t1")
    managed.close()
    assert managed.open() is not saver  # a fresh saver after close


def test_two_checkpointers_are_independent():
    a, b = ManagedCheckpointer(), ManagedCheckpointer()
    assert a.saver is not b.saver


@pytest.mark.skipif(importlib.util.find_spec("langgraph.checkpoint.sqlite") is not None,
                    reason="sqlite saver installed; error path not reachable")
def test_sqlite_needs_extra(tmp_path):
    with pytest.raises(ImportError, match="langgraph-checkpoint-sqlite"):
        ManagedCheckpointer(CheckpointerConfig(kind="sqlite", path=tmp_path / "c.db")).open()
    with pytest.raises(ValueError):
        ManagedCheckpointer(CheckpointerConfig(kind="sqlite")).open()


def test_thread_ids_scoped_by_principal_and_session():
    alice, bob = als_ctx("alice"), als_ctx("bob")
    assert scoped_thread_id(alice, "t1") == scoped_thread_id(als_ctx("alice"), "t1")
    assert scoped_thread_id(alice, "t1") != scoped_thread_id(bob, "t1")
    assert scoped_thread_id(alice, "t1") != scoped_thread_id(als_ctx("alice", session_id="s2"), "t1")
    assert scoped_thread_id(local_ctx(), "t1") != scoped_thread_id(alice, "t1")
    cfg = thread_config(alice, "t1", recursion_limit=10)
    assert cfg == {"configurable": {"thread_id": scoped_thread_id(alice, "t1")}, "recursion_limit": 10}
    assert owner_key(alice) != owner_key(bob)


def test_capability_groups_only_narrow():
    chat = als_ctx(admin=False)
    assert turn_capabilities(chat, CURATOR) == chat.capabilities & CURATOR.capabilities
    assert "graph.edit" not in turn_capabilities(chat, CURATOR)  # a group never grants
    assert turn_capabilities(local_ctx(), LITERATURE_READER) == frozenset({"chat.ask"})
    assert turn_capabilities(local_ctx()) == local_ctx().capabilities
    assert get_group(None) is None and get_group("Curator") is CURATOR
    with pytest.raises(ValueError):
        get_group("calibration")
    with pytest.raises(ValueError):
        CapabilityGroup("bad", frozenset({"not.a.capability"}))
