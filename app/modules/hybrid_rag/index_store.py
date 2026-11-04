"""On-disk source index: JSONL chunks + in-memory BM25. One corpus per directory."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
import json
import logging
import math
import os
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any

from .evidence import CHUNKER_VERSION, LEXICAL_INDEX_VERSION

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TextIO

logger = logging.getLogger(__name__)

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
        self.tf: list[Counter[str]] = []
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


LOCK_NAME = ".index.lock"


@contextmanager
def index_lock(root: Path, *, timeout: float = 120.0, poll: float = 0.05) -> Iterator[None]:
    """Exclusive advisory lock over the index directory ``root`` (created if missing), across processes.

    Every read-modify-write of ``chunks.jsonl`` / ``meta.json`` (the literature builder and the library
    indexer share one directory) holds it. It is not re-entrant: do not nest it for the same ``root``.

    Raises:
        TimeoutError: Another writer held the lock for longer than ``timeout`` seconds.
    """
    dest = Path(root)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX platform: no advisory lock available
        yield
        return
    fd = os.open(dest / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"index lock busy: {dest}") from None
                time.sleep(poll)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _atomic_write(path: Path, write: Callable[[TextIO], object]) -> None:
    """Write ``path`` through a temporary sibling and ``os.replace`` (a reader never sees a torn file)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class SourceIndex:
    """Lexical index for a single corpus. Missing files are not an error.

    The loaded chunks and BM25 are cached and dropped when ``meta.json`` changes on disk (writers replace
    ``chunks.jsonl`` first and ``meta.json`` last, both atomically), so a long-lived reader follows rebuilds.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.chunks_path = self.root / "chunks.jsonl"
        self.meta_path = self.root / "meta.json"
        self._chunks: list[dict[str, Any]] | None = None
        self._bm25: BM25Index | None = None
        self._sig: tuple[int, int, int] | None = None

    def _meta_signature(self) -> tuple[int, int, int] | None:
        try:
            st = self.meta_path.stat()
        except OSError:  # no meta file yet
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

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
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("index meta unreadable or not JSON: %s", type(exc).__name__)
            return {}
        return data if isinstance(data, dict) else {}

    def load(self) -> list[dict[str, Any]]:
        """Load and cache chunks (skipping blank, malformed or textless lines) and build the BM25 index."""
        sig = self._meta_signature()
        if self._chunks is not None and sig == self._sig:
            return self._chunks
        chunks: list[dict[str, Any]] = []
        if not self.chunks_path.is_file():
            self._sig = sig
            self._bm25 = BM25Index([])
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
                    except json.JSONDecodeError as exc:
                        logger.debug("corrupt index line skipped: %s", type(exc).__name__)
                        continue
                    if isinstance(rec, dict) and rec.get("text"):
                        chunks.append(rec)
        except OSError:
            chunks = []
        # The signature was taken before reading: a writer landing meanwhile triggers one more reload.
        self._sig = sig
        self._bm25 = BM25Index([tokenize(chunk.get("text") or "") for chunk in chunks])
        self._chunks = chunks
        return chunks

    def search(self, query: str, k: int = 20) -> list[tuple[dict[str, Any], float, str]]:
        """Return (chunk, score, retrieval_method) without mixing other corpora."""
        if not self.available:
            return []
        chunks = self.load()
        bm25 = self._bm25
        if not chunks or bm25 is None or bm25.n != len(chunks):
            return []
        q_tokens = tokenize(query)
        bm25_scores = bm25.scores(q_tokens)
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
    preserve_meta_keys: Sequence[str] = (),
) -> dict[str, Any]:
    """Write `chunks` as chunks.jsonl plus meta.json under `root` and return the metadata.

    Both files are replaced atomically (chunks first, meta last). Callers that read-modify-write hold
    :func:`index_lock`.

    Args:
        root: Index directory; created if missing.
        chunks: Chunk records to persist.
        index_name: Name recorded in the metadata.
        corpus_revision: Corpus revision recorded in the metadata.
        extra_meta: Optional fields merged over the generated metadata.
        preserve_meta_keys: Keys of the existing ``meta.json`` carried over when ``extra_meta`` does not set
            them (the literature builder keeps the library indexer's ``docs`` record this way).

    Returns:
        The metadata dict that was written.
    """
    dest = Path(root)
    dest.mkdir(parents=True, exist_ok=True)
    chunks_path = dest / "chunks.jsonl"
    carried: dict[str, Any] = {}
    if preserve_meta_keys:
        old = SourceIndex(dest).meta()
        carried = {key: old[key] for key in preserve_meta_keys if key in old}

    def _write_chunks(handle: TextIO) -> None:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    _atomic_write(chunks_path, _write_chunks)
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
    meta.update(carried)
    if extra_meta:
        meta.update(extra_meta)
    _atomic_write(dest / "meta.json", lambda handle: handle.write(json.dumps(meta, indent=2, ensure_ascii=False) + "\n"))
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
