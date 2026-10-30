"""Cached, bounded term embeddings (C-04) behind an injected encoder.

The donor called an embedding service directly. Here the encoder is always injected
(``Callable[[Sequence[str]], array]``), so no model weights are downloaded or loaded by
this package, no provider default exists, and tests use :class:`DeterministicEncoder`.
Rules kept from the donor: cache by (input, model, revision); bound the request size; an
unavailable backend raises :class:`EmbeddingUnavailable` and is never turned into an empty
successful result.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/term_embeddings.py (BSD-3-Clause).
Rewritten: transport removed (adapter-injected encoder), SQLite cache replaced by an
in-memory bounded cache with JSON export. Retries/timeouts belong to the real F2W encoder adapter.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

Encoder = Callable[[Sequence[str]], "np.ndarray"]

DEFAULT_BATCH_SIZE = 64
DEFAULT_MAX_TEXTS = 20000


class EmbeddingUnavailable(RuntimeError):
    """The encoder failed or returned unusable output. Never treated as 'no candidates'."""


@dataclass(frozen=True)
class EmbeddingBatch:
    """Vectors for the requested texts, positionally aligned, plus cache accounting."""

    vectors: "np.ndarray"
    model: str
    revision: str
    cache_hits: int
    computed: int


class EmbeddingCache:
    """Bounded LRU cache keyed by (model, revision, text hash)."""

    def __init__(self, max_entries: int = 50000) -> None:
        self.max_entries = max(1, int(max_entries))
        self._data: "OrderedDict[Tuple[str, str, str], np.ndarray]" = OrderedDict()

    @staticmethod
    def _key(model: str, revision: str, text: str) -> Tuple[str, str, str]:
        return model, revision, hashlib.sha256(text.encode("utf-8")).hexdigest()

    def __len__(self) -> int:
        return len(self._data)

    def get_many(self, model: str, revision: str, texts: Sequence[str]) -> Dict[int, "np.ndarray"]:
        """Return cached vectors keyed by index into ``texts``, omitting misses.

        Hits are marked most recently used.
        """
        found: Dict[int, np.ndarray] = {}
        for i, text in enumerate(texts):
            key = self._key(model, revision, text)
            if key in self._data:
                self._data.move_to_end(key)
                found[i] = self._data[key]
        return found

    def put_many(self, model: str, revision: str, items: Sequence[Tuple[str, "np.ndarray"]]) -> None:
        """Store ``(text, vector)`` pairs, evicting least recently used entries beyond ``max_entries``."""
        for text, vec in items:
            self._data[self._key(model, revision, text)] = vec
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)


def embed_texts_cached(
    texts: Sequence[str],
    encoder: Encoder,
    *,
    model: str,
    revision: str = "",
    cache: Optional[EmbeddingCache] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_texts: int = DEFAULT_MAX_TEXTS,
) -> EmbeddingBatch:
    """Embed ``texts`` with caching, in bounded batches.

    Raises:
        ValueError: more than ``max_texts`` inputs (bound the request; the caller must chunk).
        EmbeddingUnavailable: the encoder raised, or returned the wrong number of rows.
    """
    texts = list(texts)
    if len(texts) > max_texts:
        raise ValueError(f"{len(texts)} texts exceeds the cap of {max_texts}; chunk the request")
    cache = cache if cache is not None else EmbeddingCache()
    found = cache.get_many(model, revision, texts)
    missing = [i for i in range(len(texts)) if i not in found]
    computed: Dict[int, np.ndarray] = {}
    step = max(1, int(batch_size))
    for start in range(0, len(missing), step):
        stop = start + step
        idx = missing[start:stop]
        batch = [texts[i] for i in idx]
        try:
            raw = np.asarray(encoder(batch), dtype=np.float32)
        except Exception as exc:  # noqa: BLE001 - any encoder fault is "unavailable", not "empty"
            raise EmbeddingUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if raw.ndim != 2 or raw.shape[0] != len(batch):
            raise EmbeddingUnavailable(f"encoder returned shape {raw.shape} for {len(batch)} texts")
        cache.put_many(model, revision, list(zip(batch, raw)))
        computed.update(zip(idx, raw))
    rows: List[np.ndarray] = [found[i] if i in found else computed[i] for i in range(len(texts))]
    matrix = np.vstack(rows) if rows else np.zeros((0, 0), dtype=np.float32)
    return EmbeddingBatch(matrix, model, revision, cache_hits=len(found), computed=len(computed))


class DeterministicEncoder:
    """Offline character-n-gram hashing encoder for tests and the measurement harness.

    Not a semantic model: it only sees spelling overlap. It exists so no weights are
    downloaded and results are reproducible. Production encoders are injected separately.
    """

    name = "deterministic-char-ngram-v1"

    def __init__(self, dim: int = 256, ngram: Tuple[int, ...] = (2, 3)) -> None:
        self.dim = dim
        self.ngram = ngram

    def __call__(self, texts: Sequence[str]) -> "np.ndarray":
        """Return an L2-normalized ``(len(texts), dim)`` float32 matrix of hashed n-gram counts."""
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            folded = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "
            for n in self.ngram:
                for i in range(len(folded) - n + 1):
                    j = i + n
                    h = int.from_bytes(hashlib.blake2b(folded[i:j].encode(), digest_size=4).digest(), "big")
                    out[row, h % self.dim] += 1.0
            norm = float(np.linalg.norm(out[row]))
            if norm:
                out[row] /= norm
        return out
