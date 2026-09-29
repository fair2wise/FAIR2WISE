"""Durable per-turn interaction traces for FAIR2WISE chat.

One chat request writes one summary line and one unclipped detail file when
``F2W_TRACE_ENABLED`` is on. Writes are best-effort: a disk failure logs a
warning and never fails the turn. API keys are redacted before anything hits
disk.

The active collector lives in a :class:`contextvars.ContextVar`.
``loop.run_in_executor`` does not copy context, so call sites that do model or
retrieval work in a worker must wrap the callable with :func:`bind_context`.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_TURN_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "f2w_turn_id", default=None
)
_COLLECTOR: contextvars.ContextVar[Optional["TurnTrace"]] = contextvars.ContextVar(
    "f2w_trace_collector", default=None
)

_WRITE_LOCK = threading.Lock()
_LAST_TURN_BY_SESSION: Dict[str, str] = {}
_GIT_SHA: Optional[str] = None
_GIT_SHA_READY = False

_SECRET_NAME = re.compile(
    r"(API_KEY|SECRET|TOKEN|PASSWORD|AUTHORIZATION|CREDENTIAL)",
    re.IGNORECASE,
)
_TURN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_CITATION_KG = re.compile(r"\[KG:\s*([^\]]+)\]")
_CITATION_PDF = re.compile(r"\[PDF:\s*([^\]]+)\]")
_CITATION_OPS = re.compile(r"\[OPS:\s*([^\]]+)\]")

DETAIL_KEYS = (
    "schema_version",
    "turn_id",
    "session_id",
    "parent_turn_id",
    "started_at",
    "finished_at",
    "env",
    "request",
    "orchestration",
    "progress",
    "retrieval",
    "llm_calls",
    "response",
    "errors",
)

ENV_KEYS = ("git_sha", "backend", "models", "settings", "graphs")
REQUEST_KEYS = (
    "message",
    "effective_question",
    "history",
    "graph_source",
    "json_graph_paths",
    "source_rag",
    "use_live_tiled",
    "ui_settings",
)
ORCHESTRATION_KEYS = (
    "raw_classifier_output",
    "classification",
    "decision",
    "prechecks",
)
PRECHECK_KEYS = (
    "_direct_download_request",
    "_paper_reference_followup",
    "_extracted_terms_followup",
)
RETRIEVAL_KEYS = (
    "selected_hits",
    "sufficient",
    "no_evidence",
    "direct_evidence_count",
    "missing_topics",
    "live_tiled",
    "hybrid_rag",
    "route_flags",
    "layout_ops_only_filter",
    "merged_context",
    "raw_judge_output",
    "parsed_verdict",
    "leeway_used",
    "leeway_system_prompt",
)
ROUTE_FLAG_KEYS = (
    "_is_ops_layout_question",
    "_is_numeric_claim",
    "_is_conceptual_question",
    "is_experiment_identity_question",
)
RESPONSE_KEYS = (
    "answer",
    "citations",
    "publications",
    "pending",
    "status",
    "sufficient",
    "confidence",
    "elapsed_seconds",
)
LLM_CALL_KEYS = (
    "label",
    "model",
    "messages",
    "system_prompt_sha256",
    "response",
    "latency_ms",
    "timeout",
    "error",
)


def tracing_enabled() -> bool:
    """Return whether trace files should be written. Unset means off."""
    raw = os.environ.get("F2W_TRACE_ENABLED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def trace_dir() -> Path:
    """Directory that holds dated turn logs and the annotation log."""
    return Path(os.environ.get("F2W_TRACE_DIR") or "runs/traces")


def current_turn_id() -> Optional[str]:
    """Turn id for the active context, if a turn is open."""
    try:
        return _TURN_ID.get()
    except Exception:
        return None


def current_trace() -> Optional["TurnTrace"]:
    """Active collector, if a turn is open in this context."""
    try:
        return _COLLECTOR.get()
    except Exception:
        return None


def bind_context(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Return ``fn`` so a worker thread sees the caller's context vars.

    Copy the context on the event-loop thread, then run ``fn`` inside that
    copy. ``loop.run_in_executor(None, bind_context(fn), *args)`` is the
    supported shape.
    """
    ctx = contextvars.copy_context()

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return ctx.run(lambda: fn(*args, **kwargs))

    return wrapped


def last_turn_id_for_session(session_id: Optional[str]) -> Optional[str]:
    """Most recently finished turn for a browser session, if any."""
    if not session_id:
        return None
    return _LAST_TURN_BY_SESSION.get(str(session_id))


def git_sha() -> Optional[str]:
    """HEAD sha for this checkout, or None when git is unavailable."""
    global _GIT_SHA, _GIT_SHA_READY
    if _GIT_SHA_READY:
        return _GIT_SHA
    _GIT_SHA_READY = True
    root = Path(__file__).resolve().parents[3]
    try:
        _GIT_SHA = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        ).strip() or None
    except Exception:
        _GIT_SHA = None
    return _GIT_SHA


def _secret_values() -> List[str]:
    found: List[str] = []
    for name, value in os.environ.items():
        if not value or len(value) < 6:
            continue
        if _SECRET_NAME.search(name):
            found.append(value)
    found.sort(key=len, reverse=True)
    return found


def redact(value: Any) -> Any:
    """Remove API keys, tokens, and known secret values from ``value``."""
    secrets = _secret_values()
    return _redact(value, secrets)


def _redact(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, Mapping):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _SECRET_NAME.search(name):
                cleaned[name] = "[REDACTED]"
            else:
                cleaned[name] = _redact(item, secrets)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        text = value
        for secret in secrets:
            if secret and secret in text:
                text = text.replace(secret, "[REDACTED]")
        return text
    if isinstance(value, Path):
        return str(value)
    return value


def settings_snapshot() -> Dict[str, str]:
    """``F2W_*`` and ``KG_RAG_*`` environment values, with secrets redacted."""
    raw: Dict[str, str] = {}
    for name, value in os.environ.items():
        if name.startswith("F2W_") or name.startswith("KG_RAG_"):
            raw[name] = value
    redacted = redact(raw)
    return redacted if isinstance(redacted, dict) else {}


def graph_fingerprints(paths: Sequence[str]) -> List[Dict[str, Optional[str]]]:
    """Path plus sha256 for each loaded graph file. Missing files stay listed."""
    rows: List[Dict[str, Optional[str]]] = []
    for path in paths:
        text = str(path or "").strip()
        if not text:
            continue
        digest: Optional[str] = None
        file_path = Path(text)
        try:
            if file_path.is_file():
                hasher = hashlib.sha256()
                with file_path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
        except Exception as exc:
            logger.warning("Trace graph fingerprint failed for %s: %s", text, exc)
        rows.append({"path": text, "sha256": digest})
    return rows


def _empty_retrieval() -> Dict[str, Any]:
    return {
        "selected_hits": [],
        "sufficient": None,
        "no_evidence": None,
        "direct_evidence_count": None,
        "missing_topics": [],
        "live_tiled": None,
        "hybrid_rag": None,
        "route_flags": {key: None for key in ROUTE_FLAG_KEYS},
        "layout_ops_only_filter": None,
        "merged_context": None,
        "raw_judge_output": None,
        "parsed_verdict": None,
        "leeway_used": False,
        "leeway_system_prompt": None,
    }


def _empty_record(turn_id: str, session_id: Optional[str], parent_turn_id: Optional[str]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "turn_id": turn_id,
        "session_id": session_id,
        "parent_turn_id": parent_turn_id,
        "started_at": now,
        "finished_at": None,
        "env": {
            "git_sha": None,
            "backend": None,
            "models": {},
            "settings": {},
            "graphs": [],
        },
        "request": {
            "message": None,
            "effective_question": None,
            "history": [],
            "graph_source": None,
            "json_graph_paths": [],
            "source_rag": None,
            "use_live_tiled": None,
            "ui_settings": {},
        },
        "orchestration": {
            "raw_classifier_output": None,
            "classification": None,
            "decision": None,
            "prechecks": {key: False for key in PRECHECK_KEYS},
        },
        "progress": [],
        "retrieval": _empty_retrieval(),
        "llm_calls": [],
        "response": {
            "answer": None,
            "citations": {"kg": [], "pdf": [], "ops": []},
            "publications": [],
            "pending": None,
            "status": None,
            "sufficient": None,
            "confidence": None,
            "elapsed_seconds": None,
        },
        "errors": [],
    }


def _normalize_turn_id(client_turn_id: Optional[str]) -> str:
    text = str(client_turn_id or "").strip()
    if text and _TURN_ID_RE.fullmatch(text):
        return text
    return str(uuid.uuid4())


class TurnTrace:
    """In-memory collector for a single chat or action request."""

    def __init__(
        self,
        turn_id: str,
        *,
        session_id: Optional[str],
        parent_turn_id: Optional[str],
        enabled: bool,
    ) -> None:
        self.turn_id = turn_id
        self.enabled = enabled
        self.finished = False
        self.started_monotonic = time.monotonic()
        self._lock = threading.Lock()
        self.data = _empty_record(turn_id, session_id, parent_turn_id)
        self._tokens: List[contextvars.Token[Any]] = []

    def bind(self) -> None:
        """Install this collector in the current context."""
        self._tokens.append(_TURN_ID.set(self.turn_id))
        self._tokens.append(_COLLECTOR.set(self))

    def merge_env(self, env: Optional[Mapping[str, Any]]) -> None:
        if not env:
            return
        with self._lock:
            current = self.data["env"]
            for key in ENV_KEYS:
                if key in env and env[key] is not None:
                    current[key] = env[key]

    def merge_request(self, request: Optional[Mapping[str, Any]]) -> None:
        if not request:
            return
        with self._lock:
            current = self.data["request"]
            for key, value in request.items():
                if key not in REQUEST_KEYS or value is None:
                    continue
                if key == "history" and value == [] and current.get("history"):
                    continue
                current[key] = value

    def note_request(self, **fields: Any) -> None:
        self.merge_request(fields)

    def note_precheck(self, name: str, fired: bool) -> None:
        if name not in PRECHECK_KEYS:
            return
        with self._lock:
            flags = self.data["orchestration"]["prechecks"]
            flags[name] = bool(flags.get(name)) or bool(fired)

    def note_classifier_raw(self, raw: Optional[str]) -> None:
        with self._lock:
            slot = self.data["orchestration"]
            if slot.get("raw_classifier_output") is None and raw is not None:
                slot["raw_classifier_output"] = raw

    def note_decision(self, decision: Mapping[str, Any]) -> None:
        action = decision.get("action")
        agent = decision.get("agent")
        reason = decision.get("reason")
        classification = decision.get("classification")
        with self._lock:
            slot = self.data["orchestration"]
            if slot.get("decision") is None and action:
                slot["decision"] = {
                    "action": action,
                    "agent": agent,
                    "reason": reason,
                }
            if classification and not slot.get("classification"):
                slot["classification"] = str(classification)

    def note_progress(self, name: str, message: str, data: Optional[Mapping[str, Any]] = None) -> None:
        offset_ms = int((time.monotonic() - self.started_monotonic) * 1000)
        event = {
            "name": str(name),
            "message": str(message),
            "data": dict(data or {}),
            "offset_ms": offset_ms,
        }
        with self._lock:
            self.data["progress"].append(event)

    def record_retrieval(self, fields: Mapping[str, Any]) -> None:
        with self._lock:
            slot = self.data["retrieval"]
            for key, value in fields.items():
                if key not in RETRIEVAL_KEYS or value is None:
                    continue
                if key == "route_flags" and isinstance(value, Mapping):
                    flags = slot["route_flags"]
                    for flag, flag_value in value.items():
                        if flag in ROUTE_FLAG_KEYS and flag_value is not None:
                            flags[flag] = flag_value
                    continue
                if key == "leeway_used":
                    slot["leeway_used"] = bool(slot.get("leeway_used")) or bool(value)
                    continue
                if key == "selected_hits" and value == [] and slot.get("selected_hits"):
                    continue
                slot[key] = value

    def record_llm(
        self,
        *,
        label: str,
        model: Optional[str],
        messages: Sequence[Mapping[str, Any]],
        response: Optional[str],
        latency_ms: float,
        timeout: Optional[float],
        error: Optional[BaseException],
    ) -> None:
        system_parts = [
            str(item.get("content") or "")
            for item in messages
            if str(item.get("role") or "") == "system"
        ]
        system_hash = (
            hashlib.sha256("\n".join(system_parts).encode("utf-8")).hexdigest()
            if system_parts
            else None
        )
        entry = {
            "label": label,
            "model": model,
            "messages": [dict(item) for item in messages],
            "system_prompt_sha256": system_hash,
            "response": response,
            "latency_ms": latency_ms,
            "timeout": timeout,
            "error": _error_dict(error),
        }
        with self._lock:
            self.data["llm_calls"].append(entry)
        if label == "orchestrator-classify" and response is not None:
            self.note_classifier_raw(response)

    def note_error(self, where: str, exc: BaseException) -> None:
        entry = {
            "type": type(exc).__name__,
            "message": str(exc),
            "where": where,
        }
        with self._lock:
            self.data["errors"].append(entry)

    def attach_response(self, response: Any) -> None:
        answer = getattr(response, "answer", None)
        text = answer if isinstance(answer, str) else None
        elapsed = time.monotonic() - self.started_monotonic
        publications = getattr(response, "publications", None)
        pending = getattr(response, "pending", None)
        with self._lock:
            slot = self.data["response"]
            slot["answer"] = text
            slot["citations"] = parse_citations(text or "")
            slot["publications"] = list(publications or [])
            slot["pending"] = pending
            slot["status"] = getattr(response, "status", None)
            slot["sufficient"] = getattr(response, "sufficient", None)
            slot["confidence"] = getattr(response, "confidence", None)
            slot["elapsed_seconds"] = elapsed
            if getattr(response, "turn_id", None) is None and hasattr(response, "turn_id"):
                try:
                    response.turn_id = self.turn_id
                except Exception:
                    pass

    def finish(self) -> None:
        """Write the summary line and detail file once. Never raises."""
        if self.finished:
            return
        self.finished = True
        try:
            elapsed = time.monotonic() - self.started_monotonic
            with self._lock:
                self.data["finished_at"] = datetime.now(timezone.utc).isoformat()
                response = self.data["response"]
                if response.get("elapsed_seconds") is None:
                    response["elapsed_seconds"] = elapsed
                request = self.data["request"]
                if not request.get("effective_question"):
                    request["effective_question"] = request.get("message")
                payload = redact(self.data)
                summary = _summary_line(payload, elapsed)
            session_id = self.data.get("session_id")
            if session_id:
                _LAST_TURN_BY_SESSION[str(session_id)] = self.turn_id
            if not self.enabled:
                return
            _write_turn(payload, summary)
        except Exception as exc:
            logger.warning("Trace finish failed for %s: %s", self.turn_id, exc)
        finally:
            _reset_tokens(self._tokens)
            self._tokens = []


def _error_dict(exc: Optional[BaseException]) -> Optional[Dict[str, str]]:
    if exc is None:
        return None
    return {"type": type(exc).__name__, "message": str(exc)}


def parse_citations(answer: str) -> Dict[str, List[str]]:
    """Pull ``[KG:]``, ``[PDF:]``, and ``[OPS:]`` tokens out of an answer."""
    return {
        "kg": [match.strip() for match in _CITATION_KG.findall(answer)],
        "pdf": [match.strip() for match in _CITATION_PDF.findall(answer)],
        "ops": [match.strip() for match in _CITATION_OPS.findall(answer)],
    }


def _summary_line(payload: Mapping[str, Any], elapsed_s: float) -> Dict[str, Any]:
    request = payload.get("request") or {}
    orchestration = payload.get("orchestration") or {}
    retrieval = payload.get("retrieval") or {}
    response = payload.get("response") or {}
    decision = orchestration.get("decision") or {}
    question = str(request.get("message") or "")
    hits = retrieval.get("selected_hits") or []
    graph_ids: List[str] = []
    for hit in hits:
        if not isinstance(hit, Mapping):
            continue
        graph_id = str(hit.get("graph_id") or "").strip()
        if graph_id and graph_id not in graph_ids:
            graph_ids.append(graph_id)
        if len(graph_ids) == 3:
            break
    return {
        "turn_id": payload.get("turn_id"),
        "session_id": payload.get("session_id"),
        "ts": payload.get("started_at"),
        "question": question[:300],
        "classification": orchestration.get("classification"),
        "action": decision.get("action") if isinstance(decision, Mapping) else None,
        "top_graph_ids": graph_ids,
        "sufficient": _final_sufficient(response, retrieval),
        "leeway_used": bool(retrieval.get("leeway_used")),
        "status": response.get("status"),
        "n_llm_calls": len(payload.get("llm_calls") or []),
        "latency_ms": int(elapsed_s * 1000),
        "error_count": len(payload.get("errors") or []),
    }


def _final_sufficient(response: Mapping[str, Any], retrieval: Mapping[str, Any]) -> Any:
    if "sufficient" in response and response.get("sufficient") is not None:
        return response.get("sufficient")
    return retrieval.get("sufficient")


def _write_turn(payload: Mapping[str, Any], summary: Mapping[str, Any]) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    root = trace_dir() / day
    detail_dir = root / "turns"
    detail_path = detail_dir / f"{payload.get('turn_id')}.json"
    summary_path = root / "turns.jsonl"
    try:
        detail_dir.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, default=_json_default, indent=2)
        temporary = detail_path.with_suffix(".json.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, detail_path)
        _append_jsonl(summary_path, summary)
    except Exception as exc:
        logger.warning("Trace write failed for %s: %s", payload.get("turn_id"), exc)


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=_json_default) + "\n"
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def _json_default(value: Any) -> str:
    return str(value)


def _reset_tokens(tokens: Sequence[contextvars.Token[Any]]) -> None:
    """Pop context vars set by this collector. Ignore tokens from another context."""
    for token in reversed(list(tokens)):
        var = getattr(token, "var", None)
        if var is None:
            continue
        try:
            var.reset(token)
        except ValueError:
            continue


def open_turn(
    *,
    client_turn_id: Optional[str] = None,
    session_id: Optional[str] = None,
    parent_turn_id: Optional[str] = None,
    request: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, Any]] = None,
) -> TurnTrace:
    """Start a turn, or return the collector already bound in this context."""
    try:
        existing = _COLLECTOR.get()
        if existing is not None and not existing.finished:
            existing.merge_request(request)
            existing.merge_env(env)
            if parent_turn_id and not existing.data.get("parent_turn_id"):
                existing.data["parent_turn_id"] = parent_turn_id
            return existing
        turn_id = _normalize_turn_id(client_turn_id)
        parent = parent_turn_id or last_turn_id_for_session(session_id)
        collector = TurnTrace(
            turn_id,
            session_id=session_id,
            parent_turn_id=parent,
            enabled=tracing_enabled(),
        )
        if env is None:
            env = {
                "git_sha": git_sha(),
                "backend": os.environ.get("F2W_BACKEND") or os.environ.get("KG_RAG_BACKEND"),
                "models": {},
                "settings": settings_snapshot(),
                "graphs": [],
            }
        collector.merge_env(env)
        collector.merge_request(request)
        collector.bind()
        return collector
    except Exception as exc:
        logger.warning("Trace open failed: %s", exc)
        fallback = TurnTrace(str(uuid.uuid4()), session_id=session_id, parent_turn_id=parent_turn_id, enabled=False)
        try:
            fallback.bind()
        except Exception:
            pass
        return fallback


def finish_turn() -> None:
    """Finish the active turn. Safe to call when no turn is open."""
    collector = current_trace()
    if collector is None:
        return
    collector.finish()


def note_request(**fields: Any) -> None:
    _call(lambda collector: collector.note_request(**fields))


def note_precheck(name: str, fired: bool) -> None:
    _call(lambda collector: collector.note_precheck(name, fired))


def note_classifier_raw(raw: Optional[str]) -> None:
    _call(lambda collector: collector.note_classifier_raw(raw))


def note_decision(decision: Mapping[str, Any]) -> None:
    _call(lambda collector: collector.note_decision(decision))


def note_progress(name: str, message: str, data: Optional[Mapping[str, Any]] = None) -> None:
    _call(lambda collector: collector.note_progress(name, message, data))


def record_retrieval(fields: Mapping[str, Any]) -> None:
    _call(lambda collector: collector.record_retrieval(fields))


def note_error(where: str, exc: BaseException) -> None:
    _call(lambda collector: collector.note_error(where, exc))


def record_llm_call(
    *,
    label: str,
    model: Optional[str],
    messages: Sequence[Mapping[str, Any]],
    response: Optional[str],
    latency_ms: float,
    timeout: Optional[float] = None,
    error: Optional[BaseException] = None,
) -> None:
    """Record one model call on the active turn. No-op when no turn is open."""
    collector = current_trace()
    if collector is None or collector.finished:
        return
    try:
        collector.record_llm(
            label=label,
            model=model,
            messages=messages,
            response=response,
            latency_ms=latency_ms,
            timeout=timeout,
            error=error,
        )
    except Exception as exc:
        logger.warning("Trace LLM record failed: %s", exc)


def attach_response(response: Any) -> None:
    _call(lambda collector: collector.attach_response(response))


def _call(fn: Callable[["TurnTrace"], None]) -> None:
    collector = current_trace()
    if collector is None or collector.finished:
        return
    try:
        fn(collector)
    except Exception as exc:
        logger.warning("Trace update failed: %s", exc)


def find_detail(turn_id: str) -> Optional[Path]:
    """Locate ``turns/<turn_id>.json`` under the trace directory."""
    if not _TURN_ID_RE.fullmatch(turn_id or ""):
        return None
    root = trace_dir()
    if not root.is_dir():
        return None
    matches = sorted(root.glob(f"*/turns/{turn_id}.json"))
    return matches[-1] if matches else None


def append_ui_events(events: Sequence[Mapping[str, Any]]) -> None:
    """Append a batch of UI events to today's ``ui_events.jsonl``."""
    if not tracing_enabled():
        return
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = trace_dir() / day / "ui_events.jsonl"
    try:
        for event in events:
            _append_jsonl(path, redact(dict(event)))
    except Exception as exc:
        logger.warning("UI event trace write failed: %s", exc)


def append_annotation(record: Mapping[str, Any]) -> None:
    """Append one pass/fail label. The file is never rotated."""
    if not tracing_enabled():
        return
    path = trace_dir() / "annotations.jsonl"
    try:
        _append_jsonl(path, redact(dict(record)))
    except Exception as exc:
        logger.warning("Annotation trace write failed: %s", exc)


def load_detail(turn_id: str) -> Optional[Dict[str, Any]]:
    """Read a detail record, or None when it is missing."""
    path = find_detail(turn_id)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Trace read failed for %s: %s", turn_id, exc)
        return None
    return data if isinstance(data, dict) else None
