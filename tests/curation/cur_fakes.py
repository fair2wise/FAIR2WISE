"""Fakes for curation tests: model port, admission, contexts."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.modules.canonicalization.vocabulary import VocabEntry, Vocabulary
from app.modules.contracts.context import ActorKind
from app.modules.curation.model_port import ModelCallError, ModelReply
from app.modules.curation.models import UncertaintyCase
from tests.jobs.jobs_fakes import local_ctx

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=2)

VOCAB = Vocabulary(
    1,
    [
        VocabEntry(slug="xrr", display_name="X-ray reflectivity", category="technique", aliases=("XRR",)),
        VocabEntry(slug="silicon", display_name="silicon", category="material", aliases=("Si wafer",)),
    ],
)

EVIDENCE = "We measured X-ray reflectivity (XRR) of the film. Further, GIWAXS patterns were collected."


def case(**kw: Any) -> UncertaintyCase:
    base: Dict[str, Any] = dict(
        corpus="rsoxs",
        publication_id="10.1/x",
        document_hash="d" * 64,
        source_kind="chunk",
        source_locator="c1",
        case_type="unresolved_term",
        reason="unknown_term",
        evidence=EVIDENCE,
        payload={"surface": "GIWAXS", "category": "technique"},
        vocabulary_revision="v1",
        schema_revision="s1",
    )
    base.update(kw)
    return UncertaintyCase(**base)


def human(*caps: str, **kw: Any):
    return local_ctx(*(caps or ("curation.approve",)), **kw)


def worker(*caps: str):
    return local_ctx(*(caps or ("curation.approve",)), actor_kind=ActorKind.WORKER)


def answer(**kw: Any) -> str:
    base = {
        "disposition": "resolved",
        "canonical_label": "grazing-incidence wide-angle X-ray scattering",
        "category": "technique",
        "aliases": [],
        "evidence_quote": "GIWAXS",
        "confidence": 0.93,
        "reason": "stated",
    }
    base.update(kw)
    return json.dumps(base)


class FakePort:
    """Scriptable model port. ``script`` entries are ModelReply, ModelCallError or Exception."""

    def __init__(self, script: Optional[List[Any]] = None, default: Optional[ModelReply] = None) -> None:
        self.script = list(script or [])
        self.default = default
        self.calls: List[Dict[str, Any]] = []

    def complete(self, prompt: str, *, ref: Any, timeout: float) -> ModelReply:
        self.calls.append({"prompt": prompt, "ref": ref})
        item = self.script.pop(0) if self.script else self.default
        if isinstance(item, BaseException):
            raise item
        return item


class RecordingAdmission:
    """Admission double that records slots and the peak concurrency."""

    def __init__(self) -> None:
        self.slots: List[str] = []
        self.active = 0

    @contextmanager
    def slot(self, provider: str):
        self.slots.append(provider)
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


__all__ = ("FakePort", "ModelCallError", "ModelReply", "RecordingAdmission", "answer", "case", "human", "worker")
