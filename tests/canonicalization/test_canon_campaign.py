"""T07 offline campaign: quarantine, bounded rounds, retained votes, parking, plan, review, gated apply."""

import pytest

from app.modules.canonicalization import gates
from app.modules.canonicalization.campaign.evidence import IncompleteRead, load_rows
from app.modules.canonicalization.campaign.models import Decision
from app.modules.canonicalization.campaign.planning import effective_decisions, record_review_decision
from app.modules.canonicalization.campaign.resolver import ResolverConfig, StructuredTermResolver
from app.modules.canonicalization.campaign.runner import run_campaign
from app.modules.canonicalization.migration import (
    MAX_PLAN_OPS,
    ApplyFailed,
    MergeOp,
    ScopedMergePlan,
    apply_campaign,
    apply_cleanup,
    apply_migration,
)
from tests.canonicalization.canon_helpers import (
    NOW,
    TARGET,
    FakeTarget,
    ScriptedJudge,
    local_approval,
    open_gates,
    rows,
    vocab,
)


class Source:
    def __init__(self, r):
        self.r = r

    def read(self):
        return self.r


def outcome(judge=None, **kw):
    v = vocab()
    judge = judge or ScriptedJudge({"GI-WAXS": "giwaxs", "WTe2": "mote2", "atomic force microscopy": None})
    resolver = StructuredTermResolver(judge, v)
    return v, run_campaign(
        Source(rows()), v, resolver, campaign_id="c1", scope=["corpus-a"], target=TARGET, created_at=NOW, **kw
    )


def test_campaign_end_to_end_proposes_a_plan_and_keeps_everything_visible():
    v, out = outcome()
    run, plan = out.run, out.draft.plan
    codes = {q.reason_code for q in run.quarantine}
    assert "malformed:surface_not_text" in codes and "software" in codes and "procedure" in codes
    assert [o.surface for o in plan.ops] == ["GI-WAXS"] and plan.ops[0].target_slug == "giwaxs"
    # votes with reasons retained for every asked term, both rounds
    gi = next(k for k, e in run.evidence.items() if e.surface == "GI-WAXS")
    assert len(run.votes[gi]) == 2 and all(v.reason for v in run.votes[gi])
    # the forced WTe2 -> MoTe2 vote fails the safety check and the term stays parked, visible
    wte = next(k for k, e in run.evidence.items() if e.surface == "WTe2")
    assert all("safety_check" in vote.errors and not vote.accepted for vote in run.votes[wte])
    parked = {p.surface for p in run.parked}
    assert {"WTe2", "atomic force microscopy"} <= parked
    assert plan.source_revision == rows().revision() and plan.vocabulary_revision == v.revision
    assert out.report["parked"] == len(run.parked)


def test_run_is_bounded_and_saved(tmp_path):
    judge = ScriptedJudge()
    v = vocab()
    out = run_campaign(
        Source(rows()),
        v,
        StructuredTermResolver(judge, v, ResolverConfig(max_calls=2)),
        campaign_id="c2",
        scope=["a"],
        target=TARGET,
        created_at=NOW,
        max_terms_per_round=1,
    )
    assert judge.calls <= 2
    assert out.run.pending or out.run.parked  # unasked terms are parked as "not asked", not dropped
    assert any(p.reason == "not asked (round budget)" for p in out.run.parked)
    path = out.run.save(tmp_path)
    assert path.exists() and '"votes"' in path.read_text()


def test_incomplete_read_blocks_planning():
    bad = rows()
    bad.complete = False
    with pytest.raises(IncompleteRead):
        load_rows(Source(bad))
    v = vocab()
    with pytest.raises(IncompleteRead):
        run_campaign(
            Source(bad),
            v,
            StructuredTermResolver(ScriptedJudge(), v),
            campaign_id="x",
            scope=["a"],
            target=TARGET,
            created_at=NOW,
        )


def test_resolver_rejects_free_form_ids_unquoted_votes_and_budget():
    v = vocab()
    ev = next(
        iter(
            run_campaign(
                Source(rows()),
                v,
                StructuredTermResolver(ScriptedJudge(), v),
                campaign_id="z",
                scope=["a"],
                target=TARGET,
                created_at=NOW,
            ).run.evidence.values()
        )
    )
    import json

    r = StructuredTermResolver(
        lambda p: json.dumps(
            {"verdict": "map_to", "target_slug": "invented", "confidence": 1, "evidence_quote": "x"}
        ),
        v,
    )
    res = r.resolve(ev, round_key="k")
    assert not res.accepted and "slug_not_in_menu" in res.errors and "quote_not_in_evidence" in res.errors
    assert res.cost_usd is None
    r0 = StructuredTermResolver(ScriptedJudge(), v, ResolverConfig(max_calls=0))
    assert r0.resolve(ev, round_key="k").errors == ("budget_exhausted",)
    with pytest.raises(ValueError):
        StructuredTermResolver(None, v)


def test_prompt_is_bounded_by_token_cap():
    v = vocab()
    out = run_campaign(
        Source(rows()),
        v,
        StructuredTermResolver(ScriptedJudge(), v),
        campaign_id="z",
        scope=["a"],
        target=TARGET,
        created_at=NOW,
    )
    ev = next(iter(out.run.evidence.values()))
    small = StructuredTermResolver(ScriptedJudge(), v, ResolverConfig(max_prompt_tokens=60))
    assert len(small.build_prompt(ev, "menu_first")) / 4 < 200


def test_human_review_is_a_record_not_an_apply_and_cannot_bypass_safety():
    v, out = outcome()
    run = out.run
    wte = next(k for k, e in run.evidence.items() if e.surface == "WTe2")
    with pytest.raises(ValueError):
        record_review_decision(
            run, term_key=wte, action="approve", target_slug="nope", reviewer="r", decided_at=NOW, vocab=v
        )
    with pytest.raises(ValueError):
        record_review_decision(
            run, term_key=wte, action="approve", target_slug="mote2", reviewer=" ", decided_at=NOW, vocab=v
        )
    record_review_decision(
        run, term_key=wte, action="approve", target_slug="mote2", reviewer="alice", decided_at=NOW, vocab=v
    )
    assert any(d.method == "campaign_human_review" for d in effective_decisions(run))
    from app.modules.canonicalization.campaign.planning import run_plan

    draft = run_plan(run, v, scope=["corpus-a"], target=TARGET, created_at=NOW)
    # human approval does not bypass the identity check: WTe2 -> MoTe2 is held
    assert any(k == wte and "chemical" in why for k, why in draft.held)
    assert wte not in [o.term_key for o in draft.plan.ops]


def test_plan_rejects_empty_whole_graph_and_oversized_scope():
    op = MergeOp("k", "technique", "s", ("s",), ("n",), "tem", "m", 1.0)
    base = dict(
        target=TARGET,
        source_revision="a" * 64,
        vocabulary_revision="b" * 64,
        report_sha256="c" * 64,
        created_at="t",
    )
    for bad in ([], ["*"], ["all"]):
        with pytest.raises(ValueError):
            ScopedMergePlan.build(scope=bad, ops=[op], **base)
    with pytest.raises(ValueError):
        ScopedMergePlan.build(scope=["a"], ops=[], **base)
    many = [MergeOp(str(i), "technique", "s", ("s",), (str(i),), "tem", "m", 1.0) for i in range(MAX_PLAN_OPS + 1)]
    with pytest.raises(ValueError, match="cap"):
        ScopedMergePlan.build(scope=["a"], ops=many, **base)


ROUTES = [apply_migration, apply_cleanup, apply_campaign]


@pytest.mark.parametrize("route", ROUTES)
def test_default_flow_refuses_every_apply_route_even_with_a_perfect_approval(route):
    v, out = outcome()
    plan = out.draft.plan
    target = FakeTarget(plan.source_revision)
    with pytest.raises(gates.ApplyRefused) as err:
        route(plan, local_approval(plan), target, v, dry_run=False, now=NOW)
    assert err.value.code == "gate_closed" and target.merged == []
    with pytest.raises(gates.ApplyRefused):
        route(plan, None, target, v, dry_run=False, now=NOW)
    assert route(plan, None, target, v).dry_run is True  # default is a dry run, which never touches the target
    assert target.merged == []


@pytest.mark.parametrize("route", ROUTES)
def test_open_gate_still_requires_exact_approval_and_fresh_revisions(route, monkeypatch):
    open_gates(monkeypatch)
    v, out = outcome()
    plan = out.draft.plan
    target = FakeTarget(plan.source_revision)

    def code(**kw):
        args = dict(approval=local_approval(plan), target=target, vocabulary=v)
        args.update(kw)
        with pytest.raises(gates.ApplyRefused) as err:
            route(plan, args["approval"], args["target"], args["vocabulary"], dry_run=False, now=NOW)
        return err.value.code

    assert code(approval=None) == "approval_missing"
    other = ScopedMergePlan.build(
        scope=["corpus-a"],
        target=TARGET,
        source_revision=plan.source_revision,
        vocabulary_revision=plan.vocabulary_revision,
        ops=list(plan.ops)[:1],
        report_sha256="d" * 64,
        created_at="other",
    )
    assert code(approval=local_approval(other)) == "approval_mismatch"
    assert code(approval=local_approval(plan, expires=NOW)) == "approval_mismatch"
    assert code(approval=local_approval(plan, scope=["corpus-b"])) == "approval_mismatch"
    assert code(target=FakeTarget("e" * 64)) == "stale_source"
    changed = vocab("candidate")
    from app.modules.canonicalization.vocabulary import VocabEntry, Vocabulary

    moved = Vocabulary(1, [*changed.entries, VocabEntry("new", "new thing", "technique")])
    assert code(vocabulary=moved) == "stale_vocabulary"
    assert code(vocabulary=vocab("candidate")) == "vocabulary_not_approved"
    assert target.merged == []
    result = route(plan, local_approval(plan), target, v, dry_run=False, now=NOW)
    assert result.applied == tuple(o.term_key for o in plan.ops) and target.merged == list(result.applied)


def test_tampered_plan_is_refused(monkeypatch):
    open_gates(monkeypatch)
    v, out = outcome()
    plan = out.draft.plan
    forged = ScopedMergePlan(
        **{**plan.__dict__, "ops": (MergeOp("x", "technique", "TEM", ("TEM",), ("n9",), "tem", "m", 1.0),)}
    )
    with pytest.raises(gates.ApplyRefused) as err:
        apply_migration(forged, local_approval(plan), FakeTarget(plan.source_revision), v, dry_run=False, now=NOW)
    assert err.value.code == "plan_tampered"


def test_apply_failure_reports_partial_progress(monkeypatch):
    open_gates(monkeypatch)
    v, out = outcome()
    plan = out.draft.plan

    class Boom(FakeTarget):
        def merge(self, op):
            raise RuntimeError("store down")

    with pytest.raises(ApplyFailed) as err:
        apply_migration(plan, local_approval(plan), Boom(plan.source_revision), v, dry_run=False, now=NOW)
    assert err.value.applied == ()


def test_changed_source_rows_change_the_revision():
    a, b = rows(), rows()
    b.nodes[0] = {**b.nodes[0], "text": "different"}
    assert a.revision() != b.revision() and a.revision() == rows().revision()


def test_decision_from_dict_rejects_unknown_keys():
    with pytest.raises(TypeError):
        Decision.from_dict(
            {
                "term_key": "k",
                "category": "c",
                "node_ids": [],
                "target_slug": "t",
                "method": "m",
                "confidence": 1.0,
                "votes": [],
                "oops": 1,
            }
        )
