"""On-disk source index: JSONL chunks + in-memory BM25. One corpus per directory."""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .evidence import CHUNKER_VERSION, LEXICAL_INDEX_VERSION

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+./_-]{1,}", re.IGNORECASE)
SCI_TOKEN_RE = re.compile(
    r"\b(?:p-?rsoxs|rsoxs|cyrsoxs|nrss|nexafs|giwaxs|axis-sxr-40|"
    r"11\.0\.1\.2|bl-?1101|m101|m103|m121|ophyd|"
    r"\d+(?:\.\d+)?\s*eV)\b",
    re.IGNORECASE,
)


def tokenize(text: str) -> List[str]:
    return [tok.casefold() for tok in TOKEN_RE.findall(text or "")]


class BM25Index:
    def __init__(self, documents: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs = [list(doc) for doc in documents]
        self.n = len(self.docs)
        self.doc_len = [len(doc) or 1 for doc in self.docs]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 1.0
        df: Dict[str, int] = defaultdict(int)
        self.tf: List[Counter] = []
        for doc in self.docs:
            counts = Counter(doc)
            self.tf.append(counts)
            for token in counts:
                df[token] += 1
        self.idf = {
            token: math.log(1.0 + (self.n - freq + 0.5) / (freq + 0.5))
            for token, freq in df.items()
        }

    def scores(self, query_tokens: Sequence[str]) -> List[float]:
        if not self.docs:
            return []
        qtf = Counter(query_tokens)
        out = [0.0] * self.n
        for i, tf in enumerate(self.tf):
            dl = self.doc_len[i]
            score = 0.0
            for token, qf in qtf.items():
                if token not in tf:
                    continue
                idf = self.idf.get(token, 0.0)
                freq = tf[token]
                denom = freq + self.k1 * (1.0 - self.b + self.b * dl / self.avgdl)
                score += idf * freq * (self.k1 + 1.0) / denom * qf
            out[i] = score
        return out


class SourceIndex:
    """Lexical index for a single corpus. Missing files are not an error."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.chunks_path = self.root / "chunks.jsonl"
        self.meta_path = self.root / "meta.json"
        self._chunks: Optional[List[Dict[str, Any]]] = None
        self._bm25: Optional[BM25Index] = None

    @property
    def available(self) -> bool:
        return self.chunks_path.is_file() and self.meta_path.is_file()

    def meta(self) -> Dict[str, Any]:
        if not self.meta_path.is_file():
            return {}
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def load(self) -> List[Dict[str, Any]]:
        if self._chunks is not None:
            return self._chunks
        chunks: List[Dict[str, Any]] = []
        if not self.chunks_path.is_file():
            self._chunks = chunks
            return chunks
        try:
            with self.chunks_path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict) and rec.get("text"):
                        chunks.append(rec)
        except OSError:
            chunks = []
        self._chunks = chunks
        self._bm25 = BM25Index([tokenize(chunk.get("text") or "") for chunk in chunks])
        return chunks

    def search(self, query: str, k: int = 20) -> List[Tuple[Dict[str, Any], float, str]]:
        """Return (chunk, score, retrieval_method) without mixing other corpora."""
        if not self.available:
            return []
        chunks = self.load()
        if not chunks or self._bm25 is None:
            return []
        q_tokens = tokenize(query)
        bm25_scores = self._bm25.scores(q_tokens)
        sci_hits = {tok.casefold() for tok in SCI_TOKEN_RE.findall(query or "")}
        ranked: List[Tuple[float, int, str]] = []
        for i, chunk in enumerate(chunks):
            method = "bm25"
            score = float(bm25_scores[i]) if i < len(bm25_scores) else 0.0
            text_l = (chunk.get("text") or "").casefold()
            if sci_hits:
                exact = sum(1 for tok in sci_hits if tok.casefold() in text_l)
                if exact:
                    score += 1.5 * exact
                    method = "exact_token" if score and not bm25_scores[i] else "hybrid"
            if score > 0:
                ranked.append((score, i, method))
        ranked.sort(key=lambda item: item[0], reverse=True)
        out: List[Tuple[Dict[str, Any], float, str]] = []
        for score, idx, method in ranked[: max(1, k)]:
            out.append((chunks[idx], score, method))
        return out


def write_index(
    root: Path,
    chunks: Sequence[Dict[str, Any]],
    *,
    index_name: str,
    corpus_revision: str,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    dest = Path(root)
    dest.mkdir(parents=True, exist_ok=True)
    chunks_path = dest / "chunks.jsonl"
    with chunks_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    docs = sorted({str(chunk.get("doc_id") or "") for chunk in chunks if chunk.get("doc_id")})
    corpora = sorted({str(chunk.get("corpus") or "") for chunk in chunks})
    meta = {
        "index_name": index_name,
        "chunker_version": CHUNKER_VERSION,
        "lexical_index_version": LEXICAL_INDEX_VERSION,
        "embedding_model": None,
        "corpus_revision": corpus_revision,
        "n_chunks": len(chunks),
        "n_docs": len(docs),
        "corpora": corpora,
        "doc_ids": docs,
    }
    if extra_meta:
        meta.update(extra_meta)
    (dest / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return meta


def merge_chunks(
    existing: Iterable[Dict[str, Any]],
    incoming: Iterable[Dict[str, Any]],
    *,
    replace_doc_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    skip = {str(doc) for doc in (replace_doc_ids or [])}
    kept = [chunk for chunk in existing if str(chunk.get("doc_id") or "") not in skip]
    kept.extend(list(incoming))
    return kept
