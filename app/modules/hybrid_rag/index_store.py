"""On-disk source index: JSONL chunks + in-memory BM25. One corpus per directory."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

from .evidence import CHUNKER_VERSION, LEXICAL_INDEX_VERSION

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+./_-]{1,}", re.IGNORECASE)
SCI_TOKEN_RE = re.compile(
    r"\b(?:p-?rsoxs|rsoxs|cyrsoxs|nrss|nexafs|giwaxs|axis-sxr-40|"
    r"11\.0\.1\.2|bl-?1101|m101|m103|m121|ophyd|"
    r"\d+(?:\.\d+)?\s*eV)\b",
    re.IGNORECASE,
)


def tokenize(text: str) -> list[str]:
    """Casefold `text` and split it into lexical tokens; None or empty yields []."""
    return [tok.casefold() for tok in TOKEN_RE.findall(text or "")]


class BM25Index:
    """Okapi BM25 scorer over pre-tokenized documents."""

    def __init__(self, documents: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs = [list(doc) for doc in documents]
        self.n = len(self.docs)
        self.doc_len = [len(doc) or 1 for doc in self.docs]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 1.0
        df: dict[str, int] = defaultdict(int)
        self.tf: list[Counter] = []
        for doc in self.docs:
            counts = Counter(doc)
            self.tf.append(counts)
            for token in counts:
                df[token] += 1
        self.idf = {
            token: math.log(1.0 + (self.n - freq + 0.5) / (freq + 0.5))
            for token, freq in df.items()
        }

    def scores(self, query_tokens: Sequence[str]) -> list[float]:
        """Return one BM25 score per indexed document, in document order."""
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
        self._chunks: list[dict[str, Any]] | None = None
        self._bm25: BM25Index | None = None

    @property
    def available(self) -> bool:
        """Whether both the chunks and meta files exist on disk."""
        return self.chunks_path.is_file() and self.meta_path.is_file()

    def meta(self) -> dict[str, Any]:
        """Return the index metadata, or {} if missing, unreadable or not a JSON object."""
        if not self.meta_path.is_file():
            return {}
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def load(self) -> list[dict[str, Any]]:
        """Load and cache chunks (skipping blank, malformed or textless lines) and build the BM25 index."""
        if self._chunks is not None:
            return self._chunks
        chunks: list[dict[str, Any]] = []
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

    def search(self, query: str, k: int = 20) -> list[tuple[dict[str, Any], float, str]]:
        """Return (chunk, score, retrieval_method) without mixing other corpora."""
        if not self.available:
            return []
        chunks = self.load()
        if not chunks or self._bm25 is None:
            return []
        q_tokens = tokenize(query)
        bm25_scores = self._bm25.scores(q_tokens)
        sci_hits = {tok.casefold() for tok in SCI_TOKEN_RE.findall(query or "")}
        ranked: list[tuple[float, int, str]] = []
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
        out: list[tuple[dict[str, Any], float, str]] = []
        for score, idx, method in ranked[: max(1, k)]:
            out.append((chunks[idx], score, method))
        return out


def write_index(
    root: Path,
    chunks: Sequence[dict[str, Any]],
    *,
    index_name: str,
    corpus_revision: str,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write `chunks` as chunks.jsonl plus meta.json under `root` and return the metadata.

    Args:
        root: Index directory; created if missing.
        chunks: Chunk records to persist.
        index_name: Name recorded in the metadata.
        corpus_revision: Corpus revision recorded in the metadata.
        extra_meta: Optional fields merged over the generated metadata.

    Returns:
        The metadata dict that was written.
    """
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
    existing: Iterable[dict[str, Any]],
    incoming: Iterable[dict[str, Any]],
    *,
    replace_doc_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return `existing` chunks minus those of `replace_doc_ids`, followed by `incoming`."""
    skip = {str(doc) for doc in (replace_doc_ids or [])}
    kept = [chunk for chunk in existing if str(chunk.get("doc_id") or "") not in skip]
    kept.extend(list(incoming))
    return kept
