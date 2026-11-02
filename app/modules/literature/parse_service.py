"""Versioned, resumable parsing of stored sources into stored parser artifacts (L-10/L-12).

For a source ``sha256:<hex>`` and a parser key ``<parser>-<version>-<config hash>``:

* ``parses/<hex>/<key>.json`` is the **parse record**, written last and
  atomically: the document ref and every asset ref (figure PNGs, figure text
  spans, table CSVs). Its presence means *parse complete*, and nothing else:
  extraction, validation and publication are later, separate stages.
* ``parses/<hex>/<key>.state.json`` marks a parse *in progress* (or *failed*). A
  marker without a record after a crash is an **interrupted parse**; the next
  call re-parses and reports ``recovered_from_interruption``.

A cached record is reused only when the document and every asset still verify
against their checksums; a lost or corrupt asset triggers a re-parse (cache
restore), and the old namespace is never silently reused for different output.
A new parser version or configuration writes a new key beside the old one, so
older outputs and any references to them survive.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_reading.py (``parse_pdf``: durable
parse stage, immutable version namespace, atomic manifest) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from .parsed_document import ParsedDocument, config_hash, document_from_dict
from .parsers import PublicationParser, default_parsers
from .store import AssetKind, FileDocumentStore, StoreError, atomic_write_json, parse_ref

logger = logging.getLogger(__name__)

PARSE_RECORD_VERSION = 1


class ParseFailed(ContractError):
    """No configured parser produced a document; ``failures`` names each parser's reason."""

    def __init__(self, code: ErrorCode, message: str, failures: Sequence[str] = ()) -> None:
        super().__init__(code, message, retryable=code is ErrorCode.UNAVAILABLE)
        self.failures = list(failures)


@dataclass
class ParseRecord:
    """A completed parse: the stage is ``parsed`` and nothing more is implied."""

    source_ref: str
    parser_key: str
    parser: str
    parser_version: str
    document_ref: str
    asset_refs: list[str] = field(default_factory=list)
    completed_at: float = 0.0
    stage: str = "parsed"
    schema_version: int = PARSE_RECORD_VERSION
    cache_hit: bool = False
    recovered_from_interruption: bool = False
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form (refs only, no paths)."""
        return asdict(self)


def parser_key(parser: PublicationParser) -> str:
    """Versioned output namespace of ``parser``."""
    return f"{parser.name}-{parser.version() or 'unknown'}-{config_hash(parser.config())}"


class ParseService:
    """Parse stored sources with pluggable parsers and store versioned outputs.

    Args:
        store: The content-addressed store holding sources and receiving assets.
        parsers: Parsers tried in order (default: Docling).
        state_dir: Where parse records and state markers live (default ``<store>/parses``).
        clock: Epoch seconds, injectable.
    """

    def __init__(
        self,
        store: FileDocumentStore,
        parsers: Sequence[PublicationParser] | None = None,
        *,
        state_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.parsers = tuple(parsers) if parsers is not None else default_parsers()
        self.state_dir = Path(state_dir) if state_dir is not None else store.root / "parses"
        self._clock = clock

    def _paths(self, source_ref: str, key: str) -> tuple[Path, Path]:
        hexdigest = parse_ref(source_ref)
        base = self.state_dir / hexdigest
        return base / f"{key}.json", base / f"{key}.state.json"

    # -- status ---------------------------------------------------------------------------------

    def status(self, ctx: RequestContext, source_ref: str) -> dict[str, str]:
        """``{parser_key: "parsed" | "interrupted" | "failed:<reason>"}`` for every known attempt."""
        self.store.describe(ctx, source_ref)
        base = self.state_dir / parse_ref(source_ref)
        out: dict[str, str] = {}
        if not base.is_dir():
            return out
        for path in sorted(base.glob("*.json")):
            if path.name.endswith(".state.json"):
                key = path.name[: -len(".state.json")]
                if key in out:
                    continue
                state = _read_json(path) or {}
                stage = str(state.get("stage") or "")
                out[key] = "interrupted" if stage == "parsing" else stage or "interrupted"
            else:
                out[path.stem] = "parsed"
        return out

    def _read_record(self, path: Path) -> ParseRecord | None:
        data = _read_json(path)
        if not data or data.get("schema_version") != PARSE_RECORD_VERSION:
            return None
        fields = {k: data[k] for k in ParseRecord.__dataclass_fields__ if k in data}
        try:
            return ParseRecord(**fields)
        except TypeError:
            return None

    def _verified(self, ctx: RequestContext, record: ParseRecord) -> bool:
        return all(self.store.verify(ctx, ref) for ref in [record.document_ref, *record.asset_refs])

    def latest(self, ctx: RequestContext, source_ref: str) -> ParseRecord | None:
        """The first configured parser's verified record for ``source_ref``, else ``None``."""
        for parser in self.parsers:
            record_path, _ = self._paths(source_ref, parser_key(parser))
            record = self._read_record(record_path)
            if record is not None and self._verified(ctx, record):
                return record
        return None

    # -- parse ----------------------------------------------------------------------------------

    def parse(self, ctx: RequestContext, source_ref: str, *, force: bool = False) -> ParseRecord:
        """Return a verified parse of ``source_ref``, parsing only when needed.

        Raises:
            StoreIntegrityError: The source bytes no longer match their address.
            ParseFailed: ``INVALID_INPUT`` (not a PDF), ``UNAVAILABLE`` (no parser
                installed) or ``INTERNAL`` (every parser failed).
        """
        pdf_path = self.store.local_path(ctx, source_ref, verify=True)
        with pdf_path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise ParseFailed(ErrorCode.INVALID_INPUT, "source is not a PDF")
        failures: list[str] = []
        for parser in self.parsers:
            key = parser_key(parser)
            record_path, state_path = self._paths(source_ref, key)
            if not force:
                cached = self._read_record(record_path)
                if cached is not None:
                    if self._verified(ctx, cached):
                        cached.cache_hit = True
                        return cached
                    logger.warning(
                        "parse %s of %s has missing or corrupt assets; re-parsing", key, source_ref[:19]
                    )
            interrupted = (_read_json(state_path) or {}).get("stage") == "parsing"
            record = self._run(ctx, parser, key, source_ref, pdf_path, record_path, state_path, failures)
            if record is not None:
                record.recovered_from_interruption = interrupted
                record.failures = list(failures)
                return record
        if failures and all(f.endswith(": not installed") for f in failures):
            raise ParseFailed(ErrorCode.UNAVAILABLE, "no PDF parser is installed", failures)
        raise ParseFailed(ErrorCode.INTERNAL, "every parser failed", failures)

    def _run(
        self,
        ctx: RequestContext,
        parser: PublicationParser,
        key: str,
        source_ref: str,
        pdf_path: Path,
        record_path: Path,
        state_path: Path,
        failures: list[str],
    ) -> ParseRecord | None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            state_path, {"stage": "parsing", "parser_key": key, "started_at": self._clock(), "pid": os.getpid()}
        )
        work_dir = Path(tempfile.mkdtemp(prefix="parse-", dir=str(self.store.root / "tmp")))
        try:
            try:
                parsed = parser.parse(pdf_path, work_dir, source_ref=source_ref)
            except ImportError:
                failures.append(f"{parser.name}: not installed")
                state_path.unlink(missing_ok=True)
                return None
            except Exception as exc:  # noqa: BLE001 - a parser fault must not lose the paper
                logger.warning("%s failed on %s (%s)", parser.name, source_ref[:19], type(exc).__name__)
                failures.append(f"{parser.name}: {type(exc).__name__}")
                atomic_write_json(
                    state_path,
                    {"stage": f"failed:{type(exc).__name__}", "parser_key": key, "failed_at": self._clock()},
                )
                return None
            stored, asset_refs = self._store_assets(ctx, parsed, key)
            doc_bytes = json.dumps(stored.to_record(), ensure_ascii=False, sort_keys=True).encode("utf-8")
            document_ref = self.store.put(
                ctx,
                doc_bytes,
                media_type="application/json",
                metadata={"kind": AssetKind.PARSER_DOCUMENT.value, "source_ref": source_ref, "parser_key": key},
            )
            record = ParseRecord(
                source_ref=source_ref,
                parser_key=key,
                parser=parsed.parser,
                parser_version=parsed.parser_version,
                document_ref=document_ref,
                asset_refs=asset_refs,
                completed_at=self._clock(),
            )
            payload = record.as_dict()
            for transient in ("cache_hit", "recovered_from_interruption", "failures"):
                payload.pop(transient)
            atomic_write_json(record_path, payload)  # the completion point
            state_path.unlink(missing_ok=True)
            return record
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _store_assets(self, ctx: RequestContext, parsed: ParsedDocument, key: str) -> tuple[ParsedDocument, list[str]]:
        refs: list[str] = []
        base = {"source_ref": parsed.source_ref, "parser_key": key}

        def put(path: Path | None, kind: AssetKind, media_type: str, ident: str) -> str:
            if path is None:
                return ""
            asset = self.store.put_file(
                ctx, path, media_type=media_type, metadata={"kind": kind.value, "element_id": ident, **base}
            )
            refs.append(asset.ref)
            return asset.ref

        figures = tuple(
            replace(
                f,
                asset_ref=put(f.path, AssetKind.FIGURE_IMAGE, "image/png", f.figure_id),
                spans_ref=put(f.spans_path, AssetKind.FIGURE_SPANS, "application/json", f.figure_id),
                path=None,
                spans_path=None,
            )
            for f in parsed.figures
        )
        tables = tuple(
            replace(t, data_ref=put(t.data_path, AssetKind.TABLE_DATA, "text/csv", t.table_id), data_path=None)
            for t in parsed.tables
        )
        return replace(parsed, figures=figures, tables=tables, pdf_path=None), refs

    # -- load -----------------------------------------------------------------------------------

    def load(self, ctx: RequestContext, record: ParseRecord) -> ParsedDocument:
        """The stored document of ``record`` (checksum-verified)."""
        try:
            data = json.loads(self.store.get(ctx, record.document_ref).decode("utf-8"))
        except (StoreError, ValueError) as exc:
            raise ParseFailed(ErrorCode.INTERNAL, "stored parse document is unreadable") from exc
        return document_from_dict(data)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


__all__ = ("PARSE_RECORD_VERSION", "ParseFailed", "ParseRecord", "ParseService", "parser_key")
