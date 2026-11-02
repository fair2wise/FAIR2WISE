"""T29 B3 / R-08: the Langfuse exporter is opt-in and only ever sees disclosure-projected data."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from app.modules.provenance.langfuse import (
    LangfuseDisabled, LangfuseExporter, LangfuseSettings, exporter_from_settings,
)
from app.modules.security.disclosure import DisclosureProjector
from tests.literature.lit_fakes import local_ctx

SECRET_SOURCE = "The lamellar period of the PS-b-PMMA film was 28.4 nm by RSoXS at 285.2 eV in our own sample."
KEYS = {"langfuse_export_enabled": True, "langfuse_public_key": "pk-test", "langfuse_secret_key": "sk-test"}


class FakeSpan:
    def end(self) -> None: ...


class FakeClient:
    def __init__(self) -> None:
        self.observations: list[dict[str, Any]] = []
        self.flushed = 0

    def create_trace_id(self, *, seed: str) -> str:
        return "t" * 8 + seed

    def start_observation(self, **kw: Any) -> FakeSpan:
        self.observations.append(kw)
        return FakeSpan()

    def flush(self) -> None:
        self.flushed += 1


def record() -> dict[str, Any]:
    return {
        "schema_version": 1, "turn_id": "turn-1", "started_at": "2026-11-02T00:00:00Z",
        "request": {"message": "what is the period?"},
        "retrieval": {"merged_context": SECRET_SOURCE, "raw_judge_output": SECRET_SOURCE, "sufficient": True},
        "llm_calls": [{"label": "answer", "model": "m", "latency_ms": 10, "messages": [
            {"role": "system", "content": SECRET_SOURCE}], "response": SECRET_SOURCE}],
        "response": {"status": "ok", "answer": "The period is reported."},
        "errors": [{"type": "ValueError", "where": "x", "message": SECRET_SOURCE}],
    }


def make(settings: LangfuseSettings | None = None, client: FakeClient | None = None):
    client = client or FakeClient()
    exporter = LangfuseExporter(settings or LangfuseSettings.from_mapping(KEYS), projector=DisclosureProjector(),
                                client_factory=lambda s: client)
    return exporter, client


def test_off_by_default_and_without_the_explicit_setting():
    assert LangfuseSettings().enabled is False
    projector = DisclosureProjector()
    called: list[Any] = []

    def factory(s: Any) -> Any:
        called.append(s)
        return FakeClient()

    for values in ({}, {"langfuse_public_key": "pk", "langfuse_secret_key": "sk"},
                   {**KEYS, "langfuse_export_enabled": "no"}, {**KEYS, "langfuse_export_enabled": 0}):
        settings = LangfuseSettings.from_mapping(values)
        assert exporter_from_settings(settings, projector=projector, client_factory=factory) is None
        with pytest.raises(LangfuseDisabled):
            LangfuseExporter(settings, projector=projector, client_factory=factory)
    assert exporter_from_settings(None, projector=projector, client_factory=factory) is None
    assert called == []


def test_enabled_without_keys_does_not_export_or_crash():
    settings = LangfuseSettings.from_mapping({"langfuse_export_enabled": True})
    assert exporter_from_settings(settings, projector=DisclosureProjector()) is None
    with pytest.raises(LangfuseDisabled):
        LangfuseExporter(settings, projector=DisclosureProjector())


def test_secret_key_is_not_in_repr():
    assert "sk-test" not in repr(LangfuseSettings.from_mapping(KEYS))


def test_projector_is_required():
    with pytest.raises(TypeError):
        LangfuseExporter(LangfuseSettings.from_mapping(KEYS), projector=None, client_factory=lambda s: FakeClient())  # type: ignore[arg-type]


def test_source_text_never_leaves_the_process():
    exporter, client = make()
    trace_id = exporter.export_trace(record(), local_ctx())
    assert trace_id == "ttttttttturn-1" and client.observations
    sent = json.dumps(client.observations, default=str)
    assert SECRET_SOURCE not in sent and "lamellar period" not in sent and "285.2" not in sent
    assert "sha256" in sent                       # digests and lengths stand in for the text
    assert {o["as_type"] for o in client.observations} >= {"agent", "generation", "retriever"}
    assert all("user_id" not in o and "session_id" not in o for o in client.observations)


def test_export_goes_through_the_projector_only(monkeypatch: pytest.MonkeyPatch):
    projector = DisclosureProjector()
    seen: list[Any] = []

    def project(rec: Any, viewer: Any, **kw: Any) -> dict[str, Any]:
        seen.append(rec)
        return {"turn_id": "only-this", "response": {"status": "ok"}}

    monkeypatch.setattr(projector, "project_trace", project)
    client = FakeClient()
    exporter = LangfuseExporter(LangfuseSettings.from_mapping(KEYS), projector=projector,
                                client_factory=lambda s: client)
    exporter.export_trace(record(), local_ctx())
    assert len(seen) == 1
    sent = json.dumps(client.observations, default=str)
    assert "only-this" in sent and "turn-1" not in sent and "what is the period" not in sent


def test_admin_context_still_gets_projection():
    """``project_trace_for`` would hand an admin the raw record; the exporter must not."""
    exporter, client = make()
    ctx = local_ctx(capabilities=frozenset({"chat.ask", "trace.read_any"}))
    assert ctx.has("trace.read_any")
    raw = DisclosureProjector().project_trace_for(record(), ctx)
    assert SECRET_SOURCE in json.dumps(raw)  # the admin route view is raw: that is what must not be exported
    exporter.export_trace(record(), ctx)
    assert SECRET_SOURCE not in json.dumps(client.observations, default=str)


def test_export_failure_is_swallowed_and_flush_works():
    class Boom(FakeClient):
        def start_observation(self, **kw: Any) -> FakeSpan:
            raise RuntimeError(SECRET_SOURCE)

    exporter, client = make(client=Boom())
    assert exporter.export_trace(record(), local_ctx()) is None
    exporter.flush()
    assert client.flushed == 1


def test_langfuse_is_not_imported_and_default_factory_is_lazy():
    assert "langfuse" not in sys.modules or sys.modules["langfuse"] is not None
    import app.modules.provenance.langfuse as mod
    if "langfuse" not in sys.modules:
        with pytest.raises(ImportError, match="optional 'langfuse' package"):
            mod.default_client_factory(LangfuseSettings.from_mapping(KEYS))
