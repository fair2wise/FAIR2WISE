"""TermMergeIndex: numpy fallback search, model loading seam, dedup and nearest-name queries."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.modules.term_extractor import merge_index as mi
from app.modules.term_extractor.merge_index import TermMergeIndex

VOCAB = {"alpha": [1.0, 0.0, 0.0], "alpha two": [0.9, 0.1, 0.0], "beta": [0.0, 1.0, 0.0], "gamma": [0.0, 0.0, 1.0]}


def encoder(texts):
    return np.array([VOCAB.get(t, [0.1, 0.1, 0.1]) for t in texts], dtype=np.float32)


def test_clean_term_and_normalisation():
    assert mi.clean_term("  a \n b ") == "a b" and mi.clean_term(None) == ""
    out = mi._l2_normalize(np.array([3.0, 4.0]))
    assert out.shape == (1, 2) and np.allclose(np.linalg.norm(out, axis=1), 1.0)
    zero = mi._l2_normalize(np.zeros((1, 3)))
    assert np.all(np.isfinite(zero)) and np.allclose(zero, 0.0)


def test_numpy_index_searches_by_inner_product_and_pads_missing_results():
    idx = mi._NumpyIPIndex(2)
    sims, ids = idx.search(np.array([1.0, 0.0]), 3)  # empty index
    assert ids.tolist() == [[-1, -1, -1]] and np.all(np.isneginf(sims))
    idx.add(np.array([1.0, 0.0]))  # a single 1-D vector is accepted
    idx.add(np.array([[0.0, 1.0], [0.6, 0.8]]))
    assert idx.ntotal == 3
    sims, ids = idx.search(np.array([[1.0, 0.0]]), 2)
    assert ids.tolist() == [[0, 2]] and sims[0][0] == pytest.approx(1.0) and sims[0][1] == pytest.approx(0.6)
    sims, ids = idx.search(np.array([[1.0, 0.0]]), 5)
    assert ids.shape == (1, 5) and ids[0].tolist()[3:] == [-1, -1] and np.isneginf(sims[0][4])
    assert idx.search(np.array([[1.0, 0.0]]), 0)[1].shape == (1, 0)


def test_open_index_honours_the_faiss_switch_and_missing_package(monkeypatch):
    monkeypatch.setenv("F2W_MERGE_FAISS", "off")
    assert isinstance(mi._open_faiss_index(3), mi._NumpyIPIndex)
    monkeypatch.setenv("F2W_MERGE_FAISS", "1")
    monkeypatch.setitem(sys.modules, "faiss", None)
    assert isinstance(mi._open_faiss_index(3), mi._NumpyIPIndex)
    fake = SimpleNamespace(IndexFlatIP=lambda d: ("faiss-index", d))
    monkeypatch.setitem(sys.modules, "faiss", fake)
    assert mi._open_faiss_index(5) == ("faiss-index", 5)


@pytest.fixture(autouse=True)
def numpy_backend(monkeypatch):
    monkeypatch.setenv("F2W_MERGE_FAISS", "0")


def test_ensure_dedupes_once_and_query_excludes_the_term_itself():
    index = TermMergeIndex(top_k=2, encoder=encoder)
    assert index.ntotal == 0 and index.query("alpha") == []
    index.ensure([])
    index.ensure(["alpha", " alpha ", "", "alpha two", "beta", "gamma"])
    assert index.ntotal == 4
    index.ensure(["ignored"])  # built once
    assert index.ntotal == 4
    assert index.query("alpha")[0] == "alpha two" and "alpha" not in index.query("alpha")
    # the term itself occupies one of the k nearest slots and is then filtered out, so k=1 finds nothing
    assert index.query("alpha", k=1) == [] and index.query("alpha", k=2) == ["alpha two"] and index.query("  ") == []
    assert len(index.query("unseen term", k=10)) <= 4


def test_add_extends_the_index_without_duplicates_and_can_start_empty():
    index = TermMergeIndex(encoder=encoder)
    index.add("  ")
    assert index.ntotal == 0
    index.add("alpha")
    index.add("alpha")
    index.add("beta")
    assert index.ntotal == 2 and index.query("alpha two")[0] == "alpha"


def test_top_k_is_at_least_one():
    assert TermMergeIndex(top_k=0, encoder=encoder).top_k == 1


def test_default_encoder_batches_through_the_embedding_model(monkeypatch):
    calls = []

    class Model:
        def encode(self, names, convert_to_numpy, normalize_embeddings, show_progress_bar, batch_size):
            calls.append(list(names))
            return np.array([VOCAB.get(n, [0.1, 0.1, 0.1]) for n in names], dtype=np.float32)

    index = TermMergeIndex(embed_model=Model())
    names = [f"term{i}" for i in range(600)] + ["alpha"]
    index.ensure(names)
    assert [len(c) for c in calls] == [512, 89] and index.ntotal == 601
    assert index.query("alpha two", k=1) == ["alpha"]


def test_model_is_loaded_lazily_on_cpu_when_not_injected(monkeypatch):
    loaded = []

    class Fake:
        def __init__(self, name, device):
            loaded.append((name, device))

        def encode(self, names, **kw):
            return np.ones((len(names), 3), dtype=np.float32)

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Fake))
    index = TermMergeIndex(model_name="tiny-model")
    index.ensure(["a", "b"])
    index.add("c")
    assert loaded == [("tiny-model", "cpu")] and index.ntotal == 3  # one load, reused


def test_merge_prompt_lists_only_the_candidates():
    prompt = mi.build_merge_prompt("GI-WAXS", ["GIWAXS", "SAXS"])
    assert '"GI-WAXS"' in prompt and "- GIWAXS\n- SAXS" in prompt
