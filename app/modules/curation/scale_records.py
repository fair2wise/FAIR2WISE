"""Human-read figure-scale records (task T31, donor ``figure_scale_record``; register RT-54).

A record is a reviewer's reading of one figure panel's scale (nanometres per pixel, the
bar it was read from and where its length came from), kept in the curation database next
to the review cases. It is provenance for a reading a person confirmed, never a measurement
F2W made: the automatic readings stay on the figure results
(:class:`app.modules.vision.scale.ScaleFinding`) and a record here says who recorded what,
for which Work and figure. Records are append-only; a later reading of the same panel is a
new record, so a wrong one is never silently overwritten.

No image, crop, path or source text is stored: the identifiers are the Work id, a figure
id (the caller's label for the figure, for example ``fig3``) and the panel index.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/figure_scale.py (``record_figure_scale``:
human source, ``needs_review`` cleared by the human reading) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
import math
import time
from typing import Any
import uuid

from ..contracts.errors import ContractError, ErrorCode
from ..jobs.sqlite import OperationalDB

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS figure_scales (id TEXT PRIMARY KEY, corpus TEXT NOT NULL, work_id TEXT NOT NULL, "
    "figure_id TEXT NOT NULL, panel_index INTEGER NOT NULL, nm_per_px REAL NOT NULL, bar_length_px REAL, "
    "bar_length_nm REAL, length_source TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', recorded_by TEXT NOT NULL, "
    "created REAL NOT NULL);\n",
    "CREATE INDEX IF NOT EXISTS figure_scales_work ON figure_scales(corpus, work_id, created);\n",
)
#: Where the bar length of a human record may come from (the human read it, or confirmed a reader's value).
LENGTH_SOURCES = ("human", "caption", "text_layer", "ocr")
#: Columns a DTO carries, in order.
FIELDS = ("id", "corpus", "work_id", "figure_id", "panel_index", "nm_per_px", "bar_length_px", "bar_length_nm",
          "length_source", "note", "recorded_by", "created")
MAX_NOTE_CHARS = 600


def _finite(name: str, value: float | None, *, required: bool = False) -> float | None:
    if value is None:
        if required:
            raise ContractError(ErrorCode.INVALID_INPUT, f"{name} is required")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ContractError(ErrorCode.INVALID_INPUT, f"{name} must be a number") from None
    if not math.isfinite(number) or number <= 0:
        raise ContractError(ErrorCode.INVALID_INPUT, f"{name} must be a positive finite number")
    return number


class ScaleRecordStore:
    """Append-only figure-scale records over one operational database (the curation store's)."""

    def __init__(self, db: OperationalDB, *, clock: Callable[[], float] = time.time) -> None:
        """Create the table if missing; ``db`` is shared with the curation store."""
        self.db = db
        self._clock = clock
        with self.db.tx() as con:
            for statement in SCHEMA:
                con.execute(statement.rstrip(";\n"))

    @staticmethod
    def _row(row: Sequence[object]) -> dict[str, Any]:
        return dict(zip(FIELDS, tuple(row), strict=True))

    def record(self, *, corpus: str, work_id: str, figure_id: str, panel_index: int, nm_per_px: float,
               recorded_by: str, bar_length_px: float | None = None, bar_length_nm: float | None = None,
               length_source: str = "human", note: str = "") -> dict[str, Any]:
        """Append one record and return it.

        Raises:
            ContractError: ``INVALID_INPUT`` for a missing identifier, a non-positive or non-finite value,
                an unknown ``length_source`` or an over-long note.
        """
        for name, value in (("corpus", corpus), ("work_id", work_id), ("figure_id", figure_id)):
            if not isinstance(value, str) or not value.strip() or len(value) > 300:
                raise ContractError(ErrorCode.INVALID_INPUT, f"{name} must be a non-empty string")
        if not isinstance(panel_index, int) or isinstance(panel_index, bool) or panel_index < 0:
            raise ContractError(ErrorCode.INVALID_INPUT, "panel_index must be a non-negative integer")
        if length_source not in LENGTH_SOURCES:
            raise ContractError(ErrorCode.INVALID_INPUT, f"length_source must be one of {', '.join(LENGTH_SOURCES)}")
        if len(note) > MAX_NOTE_CHARS:
            raise ContractError(ErrorCode.INVALID_INPUT, f"note exceeds {MAX_NOTE_CHARS} characters")
        values = (
            "fsc-" + uuid.uuid4().hex, corpus.strip(), work_id.strip(), figure_id.strip(), panel_index,
            _finite("nm_per_px", nm_per_px, required=True), _finite("bar_length_px", bar_length_px),
            _finite("bar_length_nm", bar_length_nm), length_source, note, recorded_by, self._clock(),
        )
        with self.db.tx() as con:
            con.execute("INSERT INTO figure_scales VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        return self._row(values)

    def get(self, record_id: str, corpora: Iterable[str]) -> dict[str, Any]:
        """One record within ``corpora``; a record outside them is ``NOT_FOUND`` (no existence leak)."""
        allowed = set(corpora)
        row = self.db.one(f"SELECT {', '.join(FIELDS)} FROM figure_scales WHERE id=?", (record_id,))
        if row is None or row[1] not in allowed:
            raise ContractError(ErrorCode.NOT_FOUND, "figure-scale record not found")
        return self._row(row)

    def list_records(self, corpora: Iterable[str], *, work_id: str | None = None, figure_id: str | None = None,
                     limit: int = 100) -> list[dict[str, Any]]:
        """Newest-first records within ``corpora``, optionally for one Work and figure."""
        allowed = sorted(set(corpora))
        if not allowed:
            return []
        sql = f"SELECT {', '.join(FIELDS)} FROM figure_scales WHERE corpus IN ({', '.join('?' * len(allowed))})"
        params: list[Any] = list(allowed)
        if work_id:
            sql += " AND work_id=?"
            params.append(work_id)
        if figure_id:
            sql += " AND figure_id=?"
            params.append(figure_id)
        sql += " ORDER BY created DESC, id LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        return [self._row(r) for r in self.db.query(sql, params)]

    def corpora(self) -> list[str]:
        """Every corpus with at least one record."""
        return [r[0] for r in self.db.query("SELECT DISTINCT corpus FROM figure_scales ORDER BY corpus")]


__all__ = ("FIELDS", "LENGTH_SOURCES", "MAX_NOTE_CHARS", "SCHEMA", "ScaleRecordStore")
