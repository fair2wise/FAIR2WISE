"""The chat's multi-corpus graph set (``app/modules/f2w_agent/corpus_union.py``) with fake backends: no Tiled, no model."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.f2w_agent.corpus_union import ChatCorpusUnion, UnionLimits


class FakeBackend:
    def __init__(self, tmp: Path, corpus: str, snapshot: str, things: int, sha: str = "a" * 64) -> None:
        base = tmp / f"{corpus}-{snapshot}.json"
        base.write_text("x" * 100, encoding="utf-8")
        self.graph = {"things": [{"id": f"matkg:N{i}", "name": f"N{i}"} for i in range(things)],
                      "associations": []}
        self.t = SimpleNamespace(base_snapshot_id=snapshot, base_graph_sha256=sha, base_graph_path=base,
                                 journal=SimpleNamespace(path=tmp / f"{corpus}.jsonl"))
        self.calls = 0

    def effective_graph(self) -> dict:
        self.calls += 1
        return self.graph


class FakeProvider:
    def __init__(self) -> None:
        self.live: list[str] = []
        self.backends: dict[str, FakeBackend] = {}

    def live_corpora(self) -> list[str]:
        return list(self.live)

    def backend(self, corpus: str | None = None) -> FakeBackend:
        if corpus not in self.backends:
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, "not live", retryable=False)
        return self.backends[corpus]


def make(tmp_path: Path, **limits) -> tuple[ChatCorpusUnion, FakeProvider]:
    provider = FakeProvider()
    for name, snap, n in (("rsoxs", "v4", 5), ("opv", "v1", 7)):
        provider.live.append(name)
        provider.backends[name] = FakeBackend(tmp_path, name, snap, n)
    return ChatCorpusUnion(tmp_path / "chat", limits=UnionLimits(**limits) if limits else UnionLimits()), provider


def test_fewer_than_two_live_corpora_is_the_single_corpus_path(tmp_path):
    union, provider = make(tmp_path)
    provider.live = ["rsoxs"]
    assert union.refresh(provider) is None
    provider.live = []
    assert union.refresh(provider) is None
    assert union.refresh(object()) is None            # a provider without live_corpora (ALS, fakes): never a union


def test_union_lists_the_pointer_corpus_first_and_names_each_graph_after_its_corpus(tmp_path):
    union, provider = make(tmp_path)
    chosen = union.refresh(provider)
    assert [e.corpus for e in chosen.corpora] == ["rsoxs", "opv"]
    assert [e.graph_id for e in chosen.corpora] == ["rsoxs", "opv"]
    assert [e.snapshot_ref for e in chosen.corpora] == ["rsoxs/v4", "opv/v1"] and chosen.skipped == ()
    assert json.loads(chosen.corpora[1].path.read_text())["things"][0]["id"] == "matkg:N0"   # shared id stays per file
    assert chosen.describe()[0] == {"corpus": "rsoxs", "graph_id": "rsoxs", "snapshot": "rsoxs/v4", "nodes": 5, "edges": 0}


def test_cached_by_corpus_snapshot_sha_and_journal_stamp(tmp_path):
    union, provider = make(tmp_path)
    union.refresh(provider)
    union.refresh(provider)
    assert [b.calls for b in provider.backends.values()] == [1, 1]            # unchanged: no reload
    provider.backends["opv"] = FakeBackend(tmp_path, "opv", "v2", 9)          # a new publication of one corpus
    chosen = union.refresh(provider)
    assert [b.calls for b in provider.backends.values()] == [1, 1] and chosen.corpora[1].nodes == 9
    assert chosen.corpora[1].snapshot_ref == "opv/v2" and len(json.loads(chosen.corpora[1].path.read_text())["things"]) == 9
    (tmp_path / "rsoxs.jsonl").write_text("{}\n")                             # a journaled Save changes the effective record
    union.refresh(provider)
    assert provider.backends["rsoxs"].calls == 2
    chosen.corpora[0].path.unlink()                                           # a cleaned work directory is rebuilt
    assert union.refresh(provider).corpora[0].path.is_file()


def test_retired_corpus_files_are_removed_and_a_rollback_is_a_new_key(tmp_path):
    union, provider = make(tmp_path)
    first = union.refresh(provider)
    provider.live = ["rsoxs"]
    assert union.refresh(provider) is None and not first.corpora[1].path.exists() and first.corpora[0].path.exists()
    provider.live = ["rsoxs", "opv"]
    assert union.refresh(provider).corpora[1].path.is_file()


def test_a_corpus_over_a_limit_is_skipped_with_a_reason_and_never_loaded_when_the_file_is_huge(tmp_path):
    union, provider = make(tmp_path, max_corpus_nodes=6)
    chosen = union.refresh(provider)
    assert [e.corpus for e in chosen.corpora] == ["rsoxs"]
    assert chosen.skipped[0].corpus == "opv" and chosen.skipped[0].nodes == 7 and "limit 6" in chosen.skipped[0].reason
    assert "*Not searched: opv (" in chosen.notice()
    union, provider = make(tmp_path, max_corpus_bytes=50)                     # the frozen file is 100 bytes
    chosen = union.refresh(provider)
    assert chosen.corpora == () and {s.corpus for s in chosen.skipped} == {"rsoxs", "opv"}
    assert [b.calls for b in provider.backends.values()] == [0, 0]            # skipped before the (slow) effective record
    union, provider = make(tmp_path, max_union_nodes=10)                      # 5 + 7 > 10: the second is left out
    chosen = union.refresh(provider)
    assert [e.corpus for e in chosen.corpora] == ["rsoxs"] and "union" in chosen.skipped[0].reason


def test_unavailable_corpus_and_unsafe_names_are_skipped_not_raised(tmp_path):
    union, provider = make(tmp_path)
    provider.live.append("gone")                                              # live per the store, backend refuses
    provider.live.append("../etc")
    chosen = union.refresh(provider)
    assert [e.corpus for e in chosen.corpora] == ["rsoxs", "opv"]
    assert {s.corpus for s in chosen.skipped} == {"gone", "../etc"}


def test_limits_from_the_environment_ignore_garbage():
    limits = UnionLimits.from_env({"F2W_QA_MAX_CORPUS_NODES": "123", "F2W_QA_MAX_UNION_NODES": "x", "F2W_QA_MAX_CORPUS_MB": "-4"})
    assert limits.max_corpus_nodes == 123 and limits.max_union_nodes == 120_000 and limits.max_corpus_bytes == 150 * 1024 * 1024
