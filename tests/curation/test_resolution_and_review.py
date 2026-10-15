"""T10 U-04/U-05/U-07/U-11: model proposes, reviewer resolves; approvals; budget; admission; staleness."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from app.modules.contracts.context import ActorKind
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.curation import CaseState, CurationStore, Route, check_cost_approval, create_cost_approval
from app.modules.curation.control import ResolverSettings
from app.modules.curation.model_port import ChatClientAdapter, ModelCallError, ModelRef, ModelReply
from app.modules.curation.resolution import AllowListOutboundPolicy, ModelResolver
from app.modules.curation.reviewer import review_case
from app.modules.llm_admission import AdmissionController
from app.modules.provenance import AuditLog, AuditRecorder
from tests.curation.cur_fakes import LATER, NOW, FakePort, RecordingAdmission, answer, case, human, worker
from tests.jobs.jobs_fakes import Clock, local_ctx

LOCAL = ModelRef("ollama", "local-m", local=True)
REMOTE = ModelRef("cborg", "remote-m")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = CurationStore(tmp_path / "c.sqlite3", clock=clock)
    yield s
    s.close()


def queue(store, route, n=1, **kw):
    ids = []
    for i in range(n):
        c = case(source_locator=f"c{i}", **kw)
        store.enqueue(c)
        (row,) = store.claim("triage", ["rsoxs"], route="triage")
        store.route_case(c.id, row["lease_token"], route.value)
        ids.append(c.id)
    return ids


def approval(store, cap=1.0, models=("remote-m",), **kw):
    return create_cost_approval(
        human(), store, scope=["rsoxs"], models=models, cap_usd=cap, expires_at=LATER, now=NOW, **kw
    )


def resolver(store, port, route, ref, **kw):
    settings = kw.pop(
        "settings", ResolverSettings(remote_enabled=True, budget_cap_usd=1.0, reserve_usd_per_call=0.25)
    )
    base = dict(settings=settings, admission=RecordingAdmission(), now=lambda: NOW)
    if route is Route.REMOTE_MODEL:
        base.update(
            approval=kw.pop("approval", None), outbound_policy=AllowListOutboundPolicy(frozenset({"rsoxs"}))
        )
    base.update(kw)
    return ModelResolver(store, port, ["rsoxs"], route=route, ref=ref, **base)


def test_local_model_proposes_but_case_stays_unresolved(store):
    (cid,) = queue(store, Route.LOCAL_MODEL)
    port = FakePort(default=ModelReply(answer()))
    result = resolver(store, port, Route.LOCAL_MODEL, LOCAL).run_once()
    got = store.get_case(cid, ["rsoxs"])
    assert result.proposed == 1 and got["state"] == "needs_review" and got["resolution"] is None
    assert got["proposal_attempt_id"]
    attempt = store.attempts(cid)[0]
    assert attempt["outcome"] == "proposed" and attempt["cost_usd"] == 0.0  # local: known free
    assert store.ledger.summary()["charged_usd"] == 0.0  # no reservation for local inference


def test_invalid_or_unquoted_answers_never_become_proposals(store):
    ids = queue(store, Route.LOCAL_MODEL, n=3)
    port = FakePort(
        [
            ModelReply("not json"),
            ModelReply(answer(evidence_quote="made up")),
            ModelReply(answer(disposition="abstain", canonical_label=None, confidence=0.1)),
        ]
    )
    r = resolver(store, port, Route.LOCAL_MODEL, LOCAL).run_once()
    assert (r.invalid, r.abstained, r.proposed) == (2, 1, 0)
    assert all(store.get_case(i, ["rsoxs"])["proposal_attempt_id"] is None for i in ids)


def test_remote_without_approval_makes_no_call_and_no_reservation(store):
    queue(store, Route.REMOTE_MODEL)
    port = FakePort(default=ModelReply(answer()))
    r = resolver(store, port, Route.REMOTE_MODEL, REMOTE, approval=None).run_once()
    assert r.paused == "not_approved" and port.calls == []
    assert store.ledger.summary()["charged_usd"] == 0.0
    assert store.list_cases(["rsoxs"])[0]["state"] == "pending"


def test_remote_off_switch_blocks_even_with_approval(store):
    queue(store, Route.REMOTE_MODEL)
    port = FakePort(default=ModelReply(answer()))
    r = resolver(
        store,
        port,
        Route.REMOTE_MODEL,
        REMOTE,
        approval=approval(store),
        settings=ResolverSettings(remote_enabled=False),
    ).run_once()
    assert r.paused == "not_approved" and port.calls == []


def test_remote_fails_closed_without_outbound_policy(store):
    (cid,) = queue(store, Route.REMOTE_MODEL)
    port = FakePort(default=ModelReply(answer()))
    res = resolver(store, port, Route.REMOTE_MODEL, REMOTE, approval=approval(store), outbound_policy=None)
    r = res.run_once()
    assert r.refused == 1 and port.calls == []
    assert store.get_case(cid, ["rsoxs"])["state"] == "needs_review"  # evidence was not sent anywhere


def test_outbound_policy_blocks_oversized_evidence(store):
    (cid,) = queue(store, Route.REMOTE_MODEL, evidence="x" * 5000 + " GIWAXS")
    port = FakePort(default=ModelReply(answer()))
    r = resolver(store, port, Route.REMOTE_MODEL, REMOTE, approval=approval(store)).run_once()
    assert r.refused == 1 and port.calls == []


def test_remote_call_reserves_settles_reported_cost_and_proposes(store):
    (cid,) = queue(store, Route.REMOTE_MODEL)
    port = FakePort(default=ModelReply(answer(), cost_usd=0.07))
    r = resolver(store, port, Route.REMOTE_MODEL, REMOTE, approval=approval(store)).run_once()
    s = store.ledger.summary(cap_usd=1.0)
    assert r.proposed == 1 and s["known_usd"] == 0.07 and s["reserved_usd"] == 0.0
    assert store.attempts(cid)[0]["cost_usd"] == 0.07


def test_remote_missing_cost_is_unknown_stays_charged(store):
    (cid,) = queue(store, Route.REMOTE_MODEL)
    port = FakePort(default=ModelReply(answer(), cost_usd=None))
    resolver(store, port, Route.REMOTE_MODEL, REMOTE, approval=approval(store)).run_once()
    s = store.ledger.summary()
    assert s["unknown_calls"] == 1 and s["unknown_charge_usd"] == 0.25 and s["known_usd"] == 0.0
    attempt = store.attempts(cid)[0]
    assert attempt["cost_usd"] is None and attempt["cost_known"] == 0


def test_failed_remote_call_settles_even_for_partial_runs(store):
    queue(store, Route.REMOTE_MODEL, n=3)
    port = FakePort(
        [
            ModelCallError("boom", served=True, status=500, cost_header="0.04"),
            ModelCallError("timeout", served=True),
            ModelCallError("rejected", served=False, status=429),
        ]
    )
    r = resolver(store, port, Route.REMOTE_MODEL, REMOTE, approval=approval(store)).run_once()
    s = store.ledger.summary()
    assert s["known_usd"] == 0.04  # a failed response that billed is recorded
    assert s["unknown_calls"] == 1  # ambiguous failure: unknown, kept as charge
    assert s["reserved_usd"] == 0.0  # nothing left dangling
    assert r.failed == 3 and r.paused == "provider"
    assert store.ledger.summary()["charged_usd"] == pytest.approx(0.29)  # 0.04 known + 0.25 unknown; 429 released


def test_unexpected_exception_after_send_is_billing_unknown(store):
    queue(store, Route.REMOTE_MODEL)
    port = FakePort([RuntimeError("socket reset mid-stream")])
    resolver(store, port, Route.REMOTE_MODEL, REMOTE, approval=approval(store)).run_once()
    assert store.ledger.summary()["unknown_calls"] == 1


def test_configured_cap_pauses_run_without_exceeding_it(store):
    queue(store, Route.REMOTE_MODEL, n=6)
    port = FakePort(default=ModelReply(answer(), cost_usd=0.2))
    settings = ResolverSettings(remote_enabled=True, budget_cap_usd=0.5, reserve_usd_per_call=0.25, batch_size=6)
    r = resolver(store, port, Route.REMOTE_MODEL, REMOTE, approval=approval(store, cap=5.0), settings=settings)
    run = r.run_once()
    assert run.paused == "budget" and len(port.calls) == 2  # 0.25 + 0.25 reserved; third refused before calling
    assert store.ledger.summary(cap_usd=0.5)["cap_exceeded"] is False
    states = sorted(c["state"] for c in store.list_cases(["rsoxs"]))
    assert states.count("budget_paused") == 1 and states.count("pending") == 3


def test_approval_cap_limits_independently_of_global_cap(store):
    queue(store, Route.REMOTE_MODEL, n=3)
    port = FakePort(default=ModelReply(answer(), cost_usd=0.2))
    settings = ResolverSettings(remote_enabled=True, budget_cap_usd=None, reserve_usd_per_call=0.25, batch_size=3)
    run = resolver(
        store, port, Route.REMOTE_MODEL, REMOTE, approval=approval(store, cap=0.5), settings=settings
    ).run_once()
    assert len(port.calls) == 2 and run.paused in ("budget", "not_approved")


def test_stop_event_pauses_between_calls(store):
    queue(store, Route.LOCAL_MODEL, n=3)
    stop = threading.Event()

    class StoppingPort(FakePort):
        def complete(self, prompt, *, ref, timeout):
            stop.set()
            return super().complete(prompt, ref=ref, timeout=timeout)

    port = StoppingPort(default=ModelReply(answer()))
    r = resolver(
        store, port, Route.LOCAL_MODEL, LOCAL, stop=stop, settings=ResolverSettings(batch_size=3)
    ).run_once()
    assert len(port.calls) == 1 and r.paused == "stopped"
    assert sorted(c["state"] for c in store.list_cases(["rsoxs"])) == ["needs_review", "pending", "pending"]


def test_attempt_limit_sends_case_to_human_review(store):
    from app.modules.curation import ResolutionAttempt

    (cid,) = queue(store, Route.LOCAL_MODEL)
    (row,) = store.claim("x", ["rsoxs"])
    store.record_attempt(
        ResolutionAttempt(
            case_id=cid,
            provider="ollama",
            requested_model="m",
            executed_model="m",
            request_fingerprint="f",
            outcome="invalid",
            response={},
            validation={"accepted": False},
        ),
        row["lease_token"],
        next_state=CaseState.RETRY_WAIT,
    )
    port = FakePort(default=ModelReply(answer()))
    resolver(store, port, Route.LOCAL_MODEL, LOCAL, settings=ResolverSettings(max_attempts=1)).run_once()
    assert port.calls == [] and store.get_case(cid, ["rsoxs"])["state"] == "needs_review"


def test_calls_hold_a_shared_admission_slot_and_respect_its_cap(store):
    queue(store, Route.LOCAL_MODEL, n=4)
    controller = AdmissionController(1)

    class Adm:
        def slot(self, provider):
            return controller.slot()

    peak = []

    class SlowPort(FakePort):
        def complete(self, prompt, *, ref, timeout):
            peak.append(controller.in_flight)
            return ModelReply(answer())

    r1 = resolver(
        store, SlowPort(), Route.LOCAL_MODEL, LOCAL, admission=Adm(), settings=ResolverSettings(batch_size=2)
    )
    r2 = resolver(
        store, SlowPort(), Route.LOCAL_MODEL, LOCAL, admission=Adm(), settings=ResolverSettings(batch_size=2)
    )
    threads = [threading.Thread(target=r.run_once) for r in (r1, r2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert controller.peak == 1 and peak and set(peak) == {1}


def test_admission_recording_uses_provider_key(store):
    queue(store, Route.LOCAL_MODEL)
    adm = RecordingAdmission()
    resolver(store, FakePort(default=ModelReply(answer())), Route.LOCAL_MODEL, LOCAL, admission=adm).run_once()
    assert adm.slots == ["ollama"]


# --- approvals created only by humans ---


def test_workers_and_models_cannot_create_cost_approvals(store):
    for ctx in (
        worker(),
        local_ctx(
            "curation.approve",
            actor_kind=ActorKind.FORGE_DELEGATE,
        ),
    ):
        with pytest.raises(ContractError) as err:
            create_cost_approval(ctx, store, scope=["rsoxs"], models=["m"], cap_usd=1.0, expires_at=LATER, now=NOW)
        assert err.value.code is ErrorCode.DENIED
    with pytest.raises(ContractError):
        create_cost_approval(
            human("chat.ask"), store, scope=["rsoxs"], models=["m"], cap_usd=1, expires_at=LATER, now=NOW
        )
    for bad in (float("nan"), 0, -1):
        with pytest.raises(ContractError):
            create_cost_approval(
                human(), store, scope=["rsoxs"], models=["m"], cap_usd=bad, expires_at=LATER, now=NOW
            )
    with pytest.raises(ContractError):
        create_cost_approval(human(), store, scope=["rsoxs"], models=["m"], cap_usd=1, expires_at=NOW, now=NOW)


def test_cost_approval_binds_models_scope_expiry_and_can_be_revoked(store):
    a = approval(store, cap=1.0)
    check_cost_approval(a, store, corpus="rsoxs", model="remote-m", amount_usd=0.5, now=NOW)
    with pytest.raises(ContractError):
        check_cost_approval(a, store, corpus="other", model="remote-m", amount_usd=0.1, now=NOW)
    with pytest.raises(ContractError):
        check_cost_approval(a, store, corpus="rsoxs", model="another-model", amount_usd=0.1, now=NOW)
    with pytest.raises(ContractError):
        check_cost_approval(
            a, store, corpus="rsoxs", model="remote-m", amount_usd=0.1, now=LATER + timedelta(seconds=1)
        )
    with pytest.raises(ContractError) as err:
        check_cost_approval(a, store, corpus="rsoxs", model="remote-m", amount_usd=2.0, now=NOW)
    assert err.value.code is ErrorCode.BUDGET_EXHAUSTED
    store.revoke_cost_approval(a.approval_id)  # revocation invalidates the dependent approval
    with pytest.raises(ContractError) as denied:
        check_cost_approval(a, store, corpus="rsoxs", model="remote-m", amount_usd=0.1, now=NOW)
    assert denied.value.code is ErrorCode.DENIED


def test_forged_approval_object_is_rejected_against_the_stored_record(store):
    a = approval(store, cap=1.0)
    forged = a.model_copy(update={"cap_usd": 1000.0})
    with pytest.raises(ContractError):
        check_cost_approval(forged, store, corpus="rsoxs", model="remote-m", amount_usd=1.0, now=NOW)


# --- reviewer ---


def proposed_case(store):
    (cid,) = queue(store, Route.LOCAL_MODEL)
    resolver(store, FakePort(default=ModelReply(answer())), Route.LOCAL_MODEL, LOCAL).run_once()
    return store.get_case(cid, ["rsoxs"])


def test_reviewer_accepts_proposal_and_candidate_returns_to_validation(store, tmp_path, clock):
    c = proposed_case(store)
    rec = AuditRecorder(AuditLog(tmp_path / "a.sqlite3", clock=clock))
    done = review_case(human(), store, c["id"], bound_revision=c["revision"], accept=True, recorder=rec)
    assert (
        done["state"] == "resolved" and done["resolved_by"] == "local-operator" and done["needs_validation"] == 1
    )
    assert [x["id"] for x in store.awaiting_validation(["rsoxs"])] == [c["id"]]
    kinds = [(e.kind, e.phase) for _s, e in rec.log.events()]
    assert kinds == [("case_review", "intent"), ("case_review", "outcome")]


def test_approval_after_candidate_changed_is_stale_and_needs_new_review(store):
    c = proposed_case(store)
    shown = c["revision"]
    store.enqueue(case(source_locator="c0", schema_revision="s2"))  # schema changed under the reviewer
    with pytest.raises(ContractError) as err:
        review_case(human(), store, c["id"], bound_revision=shown, accept=True)
    assert err.value.code is ErrorCode.STALE_APPROVAL
    assert store.get_case(c["id"], ["rsoxs"])["state"] == "pending"  # back in the queue for fresh review


def test_vocabulary_or_evidence_change_also_stales_the_decision(store):
    c = proposed_case(store)
    store.enqueue(case(source_locator="c0", vocabulary_revision="v9"))
    with pytest.raises(ContractError) as err:
        review_case(human(), store, c["id"], bound_revision=c["revision"], accept=False)
    assert err.value.code is ErrorCode.STALE_APPROVAL


def test_workers_models_and_unprivileged_users_cannot_review(store):
    c = proposed_case(store)
    for ctx in (worker(), human("chat.ask")):
        with pytest.raises(ContractError) as err:
            review_case(ctx, store, c["id"], bound_revision=c["revision"], accept=True)
        assert err.value.code is ErrorCode.DENIED
    assert store.get_case(c["id"], ["rsoxs"])["state"] == "needs_review"


def test_reviewer_scope_is_enforced_on_als(store):
    from tests.literature.lit_fakes import als_user_ctx

    c = proposed_case(store)
    ctx = als_user_ctx(capabilities=frozenset({"curation.approve"}), corpus_scope=frozenset({"other"}))
    with pytest.raises(ContractError) as err:
        review_case(ctx, store, c["id"], bound_revision=c["revision"], accept=True)
    assert err.value.code is ErrorCode.NOT_FOUND


def test_cannot_accept_a_case_with_no_validated_proposal_or_failing_resolution(store):
    (cid,) = queue(store, Route.REVIEW)
    c = store.get_case(cid, ["rsoxs"])
    store.release(
        cid,
        store.claim("x", ["rsoxs"], route="review")[0]["lease_token"],
        state=CaseState.NEEDS_REVIEW,
    )
    with pytest.raises(ContractError):
        review_case(human(), store, cid, bound_revision=c["revision"], accept=True)
    import json

    bad = json.loads(answer(evidence_quote="not in evidence"))
    with pytest.raises(ContractError):
        review_case(human(), store, cid, bound_revision=c["revision"], accept=True, resolution=bad)
    good = json.loads(answer())
    assert (
        review_case(human(), store, cid, bound_revision=c["revision"], accept=True, resolution=good)["state"]
        == "resolved"
    )


def test_reviewer_rejection_is_terminal_without_validation_flag(store):
    c = proposed_case(store)
    done = review_case(human(), store, c["id"], bound_revision=c["revision"], accept=False, reason="wrong")
    assert done["state"] == "rejected" and done["needs_validation"] == 0 and done["resolution"] is None


def test_audit_unavailable_blocks_review_decision(store, tmp_path, clock):
    from app.modules.provenance import AuditUnavailable

    class Broken(AuditLog):
        def probe(self):
            raise OSError("disk full")

        def append(self, event):
            raise OSError("disk full")

    c = proposed_case(store)
    rec = AuditRecorder(Broken(tmp_path / "a.sqlite3", clock=clock))
    with pytest.raises(AuditUnavailable):
        review_case(human(), store, c["id"], bound_revision=c["revision"], accept=True, recorder=rec)
    assert store.get_case(c["id"], ["rsoxs"])["state"] == "needs_review"


# --- adapter over existing F2W chat clients ---


class FakeChatClient:
    """Shaped like extract_terms.CBORGChatClient: chat() admits, _chat_unadmitted() does not."""

    def __init__(self, text="{}", exc=None):
        self.text, self.exc, self.used = text, exc, []

    def chat(self, prompt, *, temperature=0.0, timeout=240):
        self.used.append("chat")
        return self._go()

    def _chat_unadmitted(self, prompt, *, temperature, timeout):
        self.used.append("unadmitted")
        return self._go()

    def _go(self):
        if self.exc:
            raise self.exc
        return self.text


def test_adapter_uses_unadmitted_entry_so_the_resolver_owns_the_slot():
    client = FakeChatClient(answer())
    reply = ChatClientAdapter(lambda ref: client).complete("p", ref=REMOTE, timeout=5)
    assert client.used == ["unadmitted"] and reply.cost_usd is None  # these clients expose no cost: unknown
    assert (
        ChatClientAdapter(lambda ref: client, cost_probe=lambda c: 0.5)
        .complete("p", ref=REMOTE, timeout=5)
        .cost_usd
        == 0.5
    )


def test_adapter_translates_provider_errors_conservatively():
    class RateLimit(Exception):
        status_code = 429

    class Boom(Exception):
        status_code = 502

    for exc, served in (
        (RateLimit(), False),
        (Boom(), True),
        (ConnectionRefusedError(), False),
        (ValueError("x"), True),
    ):
        with pytest.raises(ModelCallError) as err:
            ChatClientAdapter(lambda ref, e=exc: FakeChatClient(exc=e)).complete("p", ref=REMOTE, timeout=5)
        assert err.value.served is served


def test_adapter_has_no_default_model_or_credentials():
    import inspect
    import app.modules.curation.model_port as mod

    src = inspect.getsource(mod).lower()
    for forbidden in ("api_key", "os.environ", "subprocess", "gpt-", "glm-", "gemini"):
        assert forbidden not in src
