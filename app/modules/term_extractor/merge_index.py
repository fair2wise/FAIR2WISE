"""Nearest-neighbor index for LLM term merge (retrieval + verification).

``fuzzy_merge`` used to paste every registered display name into the LLM
prompt. This module embeds canonical/display terms with SentenceTransformers,
stores L2-normalized vectors in FAISS ``IndexFlatIP`` (cosine via inner
product), and returns only the top-k neighbors for the LLM to verify.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
import os
import re
import threading
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_EMBED_MODEL = os.environ.get(
    "F2W_MERGE_EMBED_MODEL", "paraphrase-MiniLM-L6-v2"
)
DEFAULT_MERGE_TOP_K = int(os.environ.get("F2W_MERGE_TOP_K", "12"))

Encoder = Callable[[Sequence[str]], np.ndarray]


def clean_term(text: str) -> str:
    """Strip and collapse whitespace on an extracted term string."""
    return re.sub(r"\s+", " ", str(text or "").strip())


def _l2_normalize(vecs: np.ndarray) -> np.ndarray:
    """Return float32 vectors with unit L2 norm (row-wise)."""
    arr = np.asarray(vecs, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    np.maximum(norms, 1e-12, out=norms)
    arr /= norms
    return arr


class _NumpyIPIndex:
    """Inner-product search used when FAISS is not importable."""

    def __init__(self, dim: int) -> None:
        self.d = dim
        self._xb = np.zeros((0, dim), dtype=np.float32)

    @property
    def ntotal(self) -> int:
        return int(self._xb.shape[0])

    def add(self, xb: np.ndarray) -> None:
        chunk = np.asarray(xb, dtype=np.float32)
        if chunk.ndim == 1:
            chunk = chunk.reshape(1, -1)
        if self._xb.size == 0:
            self._xb = np.ascontiguousarray(chunk)
        else:
            self._xb = np.vstack([self._xb, chunk])

    def search(self, xq: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        query = np.asarray(xq, dtype=np.float32)
        if query.ndim == 1:
            query = query.reshape(1, -1)
        nq = query.shape[0]
        if self.ntotal == 0 or k <= 0:
            return (
                np.full((nq, max(k, 0)), -np.inf, dtype=np.float32),
                np.full((nq, max(k, 0)), -1, dtype=np.int64),
            )
        take = min(int(k), self.ntotal)
        scores = query @ self._xb.T
        idx = np.argpartition(-scores, take - 1, axis=1)[:, :take]
        row = np.arange(nq)[:, None]
        ordered = np.argsort(-scores[row, idx], axis=1)
        idx = idx[row, ordered]
        sims = scores[row, idx]
        if take < k:
            pad_s = np.full((nq, k - take), -np.inf, dtype=np.float32)
            pad_i = np.full((nq, k - take), -1, dtype=np.int64)
            sims = np.hstack([sims.astype(np.float32), pad_s])
            idx = np.hstack([idx.astype(np.int64), pad_i])
        return sims.astype(np.float32), idx.astype(np.int64)


def _open_faiss_index(dim: int) -> Any:  # Any: faiss.IndexFlatIP or the numpy fallback (faiss is untyped)
    """Return FAISS IndexFlatIP, or numpy inner-product if FAISS is unsafe/missing."""
    if os.environ.get("F2W_MERGE_FAISS", "1").strip().lower() in {"0", "false", "no", "off"}:
        logger.info("F2W_MERGE_FAISS disabled; using numpy inner-product search")
        return _NumpyIPIndex(dim)
    try:
        import faiss
    except ImportError:
        logger.warning("faiss not installed; using numpy inner-product search for term merge")
        return _NumpyIPIndex(dim)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    index = faiss.IndexFlatIP(dim)
    return index


class TermMergeIndex:
    """Embedding index of existing display terms for merge candidate retrieval."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_EMBED_MODEL,
        top_k: int = DEFAULT_MERGE_TOP_K,
        encoder: Encoder | None = None,
        embed_model: object = None,
    ) -> None:
        self.model_name = model_name
        self.top_k = max(1, int(top_k))
        self._encoder = encoder
        self._embed_model = embed_model
        self._lock = threading.Lock()
        self._names: list[str] = []
        self._seen: set[str] = set()
        self._index: Any = None  # Any: faiss index or numpy fallback
        self._dim: int | None = None

    @property
    def ntotal(self) -> int:
        """Number of names currently indexed."""
        with self._lock:
            return len(self._names)

    def ensure(self, names: Sequence[str]) -> None:
        """Build the index once from *names* when it is still empty."""
        with self._lock:
            if self._names:
                return
            unique = []
            seen: set[str] = set()
            for raw in names:
                name = clean_term(raw)
                if not name or name in seen:
                    continue
                seen.add(name)
                unique.append(name)
            if not unique:
                return
            vecs = self._encode_unlocked(unique)
            self._index = _open_faiss_index(self._dim or vecs.shape[1])
            self._index.add(vecs)
            self._names = unique
            self._seen = seen
            logger.info(
                "Term merge index ready: %d names, dim=%d, top_k=%d, model=%s",
                len(unique),
                self._dim,
                self.top_k,
                self.model_name if self._encoder is None else "custom-encoder",
            )

    def add(self, name: str) -> None:
        """Append one new display name after it is registered as canonical."""
        cleaned = clean_term(name)
        if not cleaned:
            return
        with self._lock:
            if cleaned in self._seen:
                return
            vecs = self._encode_unlocked([cleaned])
            if self._index is None:
                self._index = _open_faiss_index(self._dim or vecs.shape[1])
            self._index.add(vecs)
            self._names.append(cleaned)
            self._seen.add(cleaned)

    def query(self, term: str, k: int | None = None) -> list[str]:
        """Return up to *k* nearest existing display names (not the full vocabulary)."""
        cleaned = clean_term(term)
        if not cleaned:
            return []
        take = max(1, int(k if k is not None else self.top_k))
        with self._lock:
            if not self._names or self._index is None:
                return []
            snapshot = list(self._names)
            vecs = self._encode_unlocked([cleaned])
            _scores, indices = self._index.search(vecs, min(take, len(snapshot)))
        out: list[str] = []
        seen: set[str] = set()
        for idx in indices[0].tolist():
            if idx < 0 or idx >= len(snapshot):
                continue
            candidate = snapshot[idx]
            if candidate == cleaned or candidate in seen:
                continue
            seen.add(candidate)
            out.append(candidate)
            if len(out) >= take:
                break
        return out

    def _encode_unlocked(self, texts: Sequence[str]) -> np.ndarray:
        if self._encoder is not None:
            vecs = _l2_normalize(self._encoder(texts))
        else:
            model = self._load_model_unlocked()
            chunks: list[np.ndarray] = []
            batch = 512
            names = list(texts)
            for start in range(0, len(names), batch):
                part = model.encode(
                    names[start : start + batch],
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    batch_size=64,
                )
                chunks.append(_l2_normalize(part))
            vecs = np.vstack(chunks) if len(chunks) > 1 else chunks[0]
        self._dim = int(vecs.shape[1])
        return np.ascontiguousarray(vecs, dtype=np.float32)

    def _load_model_unlocked(self) -> Any:  # Any: SentenceTransformer (untyped)
        if self._embed_model is not None:
            return self._embed_model
        from sentence_transformers import SentenceTransformer

        logger.info("Loading merge embedding model %s on cpu", self.model_name)
        self._embed_model = SentenceTransformer(self.model_name, device="cpu")
        return self._embed_model


def build_merge_prompt(term: str, candidates: Sequence[str]) -> str:
    """LLM verification prompt: new term plus a short candidate list only."""
    bullets = "\n".join(f"- {disp}" for disp in candidates)
    return f"""
We have just extracted a new term:    "{term}"
Below is a short list of the nearest already-registered terms (retrieval candidates only; not the full vocabulary):
{bullets}

You must decide whether "{term}" refers to exactly the same concept as one of these,
or if it is a distinct new concept. Follow these rules:

  1. **Ignore only trivial punctuation (spaces, hyphens, slashes, brackets, parentheses, capitalization)**
     when comparing.  For example, "GIWAXS" and "GI-WAXS" are the *same* technique and should be merged
     (choose the variant already in the list).  Likewise, "XRD" and "X-RD" (if it appeared) are identical.
     Anything beyond punctuation differences (letters, numbers, or added qualifiers) is not trivial.

  2. **Do NOT merge distinct instrument or method acronyms**.  Even if two acronyms share letters, if they are
     known to be different techniques or materials, keep them separate.
     Examples you must treat as always distinct:
       - "SEM" (scanning electron microscopy) vs. "TEM" (transmission electron microscopy)
       - "AFM" (atomic force microscopy)
       - "XPS" vs. "UPS"
       - "MoTe2" vs. "WTe2" (different compounds)
       - "Al2O3[0001]" (specific surface) vs. "Al2O3" (generic material)
     In other words, if two strings differ by more than punctuation—by letters, numbers
     or explicit qualifiers—they should not be merged.

  3. **Do NOT merge general vs. specific variants**.
     If one term is a broader concept (e.g. "band structure") and another is a specialized version
     (e.g. "Dirac-like band structure"), treat them as distinct.
     Similarly, if a term includes an added qualifier or context
     (e.g. surface orientation "[0001]" vs. generic material), do not merge into a more general term.

  4. **If the newly extracted term is an exact punctuation-agnostic match** to one of the existing
     terms—i.e., removing or changing only punctuation/brackets/spaces/case makes them identical—then respond
     with exactly that already-registered term (preserve its original casing/spelling).
     Otherwise, respond `"None"`.

  5. **DO merge terms if one is the acronym for the other term**, vice versa,
      or one term includes the acronym and the other doesn't
      For example, "angle-resolved photoelectric spectroscopy" and "ARPES" should merge to become:
      "angle-resolved photoemission spectroscopy (ARPES)".
      Another example: "resonant soft xray scattering" or "R-SoXS" should merge to become:
      "Resonant soft xray scattering (RSoXS)"

  6. **Your response must be exactly one line**: either the exact existing term (matching punctuation
     and case as it appears above) or the single word `None`. Don't output anything else—no quotes,
     no extra commentary.

Here are additional examples to illustrate:

  • If the new term is `"GI-WAXS"` and the list already contains `"GIWAXS"`, respond exactly `"GIWAXS"`.
  • If the new term is `"RSoXS"` and the list already contains `"R-SoXS"`,
    respond exactly `"RSoXS" as the correct term`.
  • If the new term is `"SEM"` and the list contains `"SEM"`, respond `"SEM"`, but if the list contains
    only `"TEM"`, respond `"None"` (distinct acronyms).
  • If the new term is `"MoTe2"` and the list has `"WTe2"`, respond `"None"` (different compound).
  • If the new term is `"Band-structure"` and the list has `"Dirac-like band structure"`, respond `"None"`
    (general vs. specific).
  • If the new term is `"Al2O3[0001]"` and the list has `"Al2O3"`, respond `"None"` (surface-specific vs. generic).
  • If the new term is `"photoemission"` and the list has `"angle-resolved photoemission spectroscopy (ARPES)"`,
    respond `"None"` (general process vs. specific technique).
  • If the new term is `"X-RD"` and the list has `"XRD"`, respond `"XRD"`
    (consistent acronym once punctuation is removed).
  • "organic solar cells" and "OSCs" should merge to become "Organic solar cells (OSCs)"

Now, having read the rules, please answer: which of the above existing terms is exactly the same concept
as "{term}"?  If none match, respond with `None`.
"""
