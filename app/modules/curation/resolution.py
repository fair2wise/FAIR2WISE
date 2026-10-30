"""Optional model resolution of triaged cases: local models and approved remote escalation (U-04, U-07).

The model **proposes**; it never resolves a case and never creates an approval. A validated
proposal moves the case to ``needs_review`` with the proposal attached; an authorized reviewer
resolves it through :mod:`.reviewer`.

Safety order for every call:

1. stop/cancel check (a paused or cancelled job makes no further calls);
2. remote only: valid explicit :class:`~.approval.CostApproval` and an outbound-content policy
   that allows this case (fail closed when no policy is supplied);
3. remote only: budget reservation at the upper-bound estimate; refused reservation pauses the
   run (``budget``) with the case left pending, no call made;
4. one shared-admission slot for the provider (the same quota chat, workers and evaluations use);
5. the call through the injected :class:`~.model_port.ModelPort` (existing F2W chat clients);
6. settlement exactly once, *including failed and partial calls* (see :mod:`.cost_ledger`):
   a reported cost is recorded, an unreported one is unknown and stays charged, only a
   never-served request is released;
7. strict parse and deterministic validation, then an immutable attempt record.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/local_llm_resolution.py (``local_llm_worker``)
and cborg_resolution.py (``CBorgResolver``) (BSD-3-Clause). Rewritten: no environment-derived
configuration or model defaults, no direct HTTP transport, no auto-acceptance of validated answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import threading
from typing import Any, Callable, Iterable, Protocol, cast
import uuid

from ..contracts.errors import ContractError, ErrorCode
from ..provenance.events import GraphWriteEvent
from ..provenance.recorder import AuditRecorder
from .approval import CostApproval, check_cost_approval
from .control import ResolverSettings
from .cost_ledger import CallOutcome, parse_cost_header, settle_outcome
from .model_port import ModelCallError, ModelPort, ModelRef, ModelReply
from .models import CaseState, ResolutionAttempt, Route
from .review_schema import ReviewParseError, parse_review_response, validate_resolution
from .store import CurationStore

PROMPT = (
    "You review one terminology case from a scientific paper. Use ONLY the evidence text. "
    "If the evidence is insufficient or contradictory, answer needs_review or abstain. "
    "Return exactly one JSON object with keys: disposition (resolved|needs_review|abstain), "
    "canonical_label (string|null), category (string|null), aliases (array of strings), "
    "evidence_quote (string copied verbatim from the evidence), confidence (number 0..1), reason (string)."
)


class OutboundPolicy(Protocol):
    """Decides whether a case's source text may leave the deployment for a remote model."""

    def refusal(self, case: dict[str, Any], ref: ModelRef) -> str | None:
        """Return a reason to refuse, or ``None`` to allow."""
        ...


@dataclass(frozen=True)
class AllowListOutboundPolicy:
    """Allow evidence only for listed corpora and up to ``max_chars`` of text."""

    corpora: frozenset[str]
    max_chars: int = 4000

    def refusal(self, case: dict[str, Any], ref: ModelRef) -> str | None:
        """Return a reason to refuse unless the corpus is listed and the evidence fits `max_chars`."""
        if case["corpus"] not in self.corpora:
            return "corpus not approved for outbound content"
        if len(case["evidence"]) > self.max_chars:
            return "evidence exceeds the outbound size limit"
        return None


@dataclass
class RunResult:
    """Outcome of one :meth:`ModelResolver.run_once`.

    ``paused`` is ``"budget"``, ``"provider"``, ``"not_approved"`` or ``"stopped"`` when the run ended early;
    none of these says anything about the papers.
    """

    processed: int = 0
    proposed: int = 0
    abstained: int = 0
    invalid: int = 0
    failed: int = 0
    refused: int = 0
    paused: str | None = None
    reservations: list[str] = field(default_factory=list)


def build_prompt(case: dict[str, Any]) -> str:
    """Prompt for one case (surface, category and evidence only; no identifiers beyond the case id)."""
    payload = case["payload"]
    body = {
        "case_id": case["id"],
        "surface": payload.get("surface", ""),
        "category": payload.get("category", ""),
        "reason": case["reason"],
        "evidence": case["evidence"],
    }
    return PROMPT + "\n\n" + json.dumps(body, ensure_ascii=False)


class ModelResolver:
    """Resolve leased cases through a local or an approved remote model.

    Args:
        store: Curation store (cases, attempts, ledger).
        port: Model access over the existing F2W chat clients.
        corpora: Corpora this run may touch (tenant scope).
        route: ``Route.LOCAL_MODEL`` or ``Route.REMOTE_MODEL``.
        ref: The model to call (chosen by configuration).
        settings: Immutable per-job settings.
        admission: Object with ``slot(provider)`` (``llm_admission.ProviderAdmission``).
        approval: Required for the remote route.
        outbound_policy: Required for the remote route (fail closed).
        recorder: Optional audit recorder for per-call notes.
        stop: Set to pause/cancel between calls.
    """

    def __init__(
        self,
        store: CurationStore,
        port: ModelPort,
        corpora: Iterable[str],
        *,
        route: Route,
        ref: ModelRef,
        settings: ResolverSettings,
        admission: Any,
        approval: CostApproval | None = None,
        outbound_policy: OutboundPolicy | None = None,
        recorder: AuditRecorder | None = None,
        stop: threading.Event | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if route not in (Route.LOCAL_MODEL, Route.REMOTE_MODEL):
            raise ValueError("route must be a model route")
        if route is Route.LOCAL_MODEL and not ref.local:
            raise ValueError("the local route needs a local ModelRef")
        if route is Route.REMOTE_MODEL and ref.local:
            raise ValueError("the remote route cannot use a local ModelRef")
        self.store = store
        self.port = port
        self.corpora = tuple(corpora)
        self.route = route
        self.ref = ref
        self.settings = settings
        self.admission = admission
        self.approval = approval
        self.policy = outbound_policy
        self.recorder = recorder
        self.stop = stop or threading.Event()
        self._now = now
        self.owner = f"resolver-{uuid.uuid4().hex[:12]}"

    @property
    def remote(self) -> bool:
        """Whether the route sends cases to a remote model."""
        return self.route is Route.REMOTE_MODEL

    def run_once(self, case_limit: int | None = None) -> RunResult:
        """Claim up to ``case_limit`` (default ``settings.batch_size``) cases and resolve them in order."""
        result = RunResult()
        if self.remote and not self.settings.remote_enabled:
            result.paused = "not_approved"  # the operator switch is off: no claim, no call
            return result
        cases = self.store.claim(
            self.owner, self.corpora, limit=case_limit or self.settings.batch_size, route=self.route.value
        )
        for position, case in enumerate(cases):
            if result.paused is not None or self.stop.is_set():
                if result.paused is None:
                    result.paused = "stopped"
                self._give_back(case)
                continue
            self._resolve(case, result)
        return result

    def _give_back(self, case: dict[str, Any], state: CaseState = CaseState.PENDING, reason: str = "") -> None:
        self.store.release(case["id"], case["lease_token"], state=state, reason=reason)

    def _resolve(self, case: dict[str, Any], result: RunResult) -> None:
        token = case["lease_token"]
        if self.store.attempt_count(case["id"]) >= self.settings.max_attempts:
            self._give_back(case, CaseState.NEEDS_REVIEW, "attempt limit reached")
            return
        reservation: str | None = None
        if self.remote:
            refusal = self.policy.refusal(case, self.ref) if self.policy is not None else "no outbound policy"
            if refusal:
                self._give_back(case, CaseState.NEEDS_REVIEW, f"outbound policy: {refusal}")
                result.refused += 1
                return
            try:
                check_cost_approval(
                    self.approval,
                    self.store,
                    corpus=case["corpus"],
                    model=self.ref.model,
                    amount_usd=self.settings.reserve_usd_per_call,
                    now=self._now(),
                )
            except ContractError as exc:
                self._give_back(case, CaseState.PENDING, exc.code.value)
                result.paused = "budget" if exc.code is ErrorCode.BUDGET_EXHAUSTED else "not_approved"
                return
            reservation = self.store.ledger.reserve(
                corpus=case["corpus"],
                case_id=case["id"],
                model=self.ref.model,
                amount_usd=self.settings.reserve_usd_per_call,
                cap_usd=self.settings.budget_cap_usd,
                scope=self.approval.budget_scope if self.approval else "default",
                scope_cap_usd=self.approval.cap_usd if self.approval else None,
                owner=self.owner,
            )
            if reservation is None:
                self._give_back(case, CaseState.BUDGET_PAUSED, "budget cap would be exceeded")
                result.paused = "budget"
                return
            result.reservations.append(reservation)
        prompt = build_prompt(case)
        fingerprint = hashlib.sha256(f"{self.ref.provider}|{self.ref.model}|{prompt}".encode()).hexdigest()
        reply = None
        error: ModelCallError | None = None
        try:
            with self.admission.slot(self.ref.provider):
                reply = self.port.complete(prompt, ref=self.ref, timeout=self.settings.timeout_seconds)
        except ModelCallError as exc:
            error = exc
        except Exception as exc:  # noqa: BLE001 - an unexpected failure after sending is billing-unknown
            error = ModelCallError(type(exc).__name__, served=True)
        # Settlement happens exactly once for every reservation, success or failure.
        if reservation is not None:
            if error is not None:
                outcome = CallOutcome(
                    cost_usd=parse_cost_header(error.cost_header), served=error.served, failed=True
                )
            else:
                outcome = CallOutcome(cost_usd=cast(ModelReply, reply).cost_usd, served=True, failed=False)
            settle_outcome(self.store.ledger, reservation, outcome)
        self._note(case, reply, error, reservation)
        if error is not None:
            result.failed += 1
            if error.status == 429 or (not error.served and error.status is None):
                self._give_back(case, CaseState.RETRY_WAIT, "provider unavailable")
                result.paused = "provider"
            else:
                self._give_back(case, CaseState.RETRY_WAIT, f"call failed: {error}")
            return
        self._record(case, token, reply, fingerprint, result)

    def _record(self, case: dict[str, Any], token: str, reply: Any, fingerprint: str, result: RunResult) -> None:
        cost = 0.0 if self.ref.local else reply.cost_usd  # only a local model is known to be free
        base: dict[str, Any] = dict(
            case_id=case["id"],
            provider=self.ref.provider,
            requested_model=self.ref.model,
            executed_model=reply.executed_model or self.ref.model,
            request_fingerprint=fingerprint,
            cost_usd=cost,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
        )
        result.processed += 1
        try:
            answer = parse_review_response(reply.text)[0]
        except ReviewParseError as exc:
            result.invalid += 1
            self.store.record_attempt(
                ResolutionAttempt(
                    outcome="invalid",
                    response={"raw_length": len(reply.text)},
                    validation={"accepted": False, "errors": [str(exc)], "policy": "review-schema-v1"},
                    **base,
                ),
                token,
                next_state=CaseState.NEEDS_REVIEW,
            )
            return
        verdict = validate_resolution(case, answer)
        response = answer.model_dump()
        if verdict["accepted"]:
            result.proposed += 1
            outcome = "proposed"
        elif answer.disposition == "resolved":
            result.invalid += 1
            outcome = "invalid"
        else:
            result.abstained += 1
            outcome = "abstained"
        self.store.record_attempt(
            ResolutionAttempt(outcome=outcome, response=response, validation=verdict, **base),
            token,
            next_state=CaseState.NEEDS_REVIEW,
        )

    def _note(
        self, case: dict[str, Any], reply: Any, error: ModelCallError | None, reservation: str | None
    ) -> None:
        if self.recorder is None:
            return
        cost = None if error is not None else (0.0 if self.ref.local else reply.cost_usd)
        self.recorder.record(
            GraphWriteEvent.build(
                kind="model_call",
                phase="note",
                operation_id=f"resolve:{case['id']}:{uuid.uuid4().hex[:8]}",
                actor=self.owner,
                target=self.route.value,
                status="failed" if error is not None else "ok",
                payload={
                    "case_id": case["id"],
                    "model": self.ref.model,
                    "provider": self.ref.provider,
                    "reservation": reservation,
                    "cost_usd": cost,
                    "cost_known": cost is not None,
                    "error": type(error).__name__ if error is not None else None,
                },
            )
        )


__all__ = ("AllowListOutboundPolicy", "ModelResolver", "OutboundPolicy", "PROMPT", "RunResult", "build_prompt")
