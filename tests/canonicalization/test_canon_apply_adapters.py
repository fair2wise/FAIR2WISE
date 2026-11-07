"""T07B apply adapters: accepted curation cases -> scoped plan -> the gated apply routes -> the Save port.

Offline fixtures only: an in-memory curation store, a dict graph behind a fake Save port, the canon_helpers
vocabulary. No model client exists in these tests; the adjudication entry points are patched to fail if touched.
"""

from __future__ import annotations

import copy
import json

import pytest

from app.modules.canonicalization import adjudication, apply_adapters as aa, gates
from app.modules.canonicalization.apply_adapters import (
    APPLY_ROUTES,
    CANONICAL_FIELDS,
    MAX_ACCEPTED_CASES,
    MERGE_PROPOSAL_KIND,
    ROUTE_GATES,
    EditPortMergeTarget,
    HeldProposal,
    MergeApplyService,
    RefusedApply,
    accepted_merges,
    contract_error,
    merge_proposal_content,
    rows_from_graph,
)
from app.modules.canonicalization.campaign import resolver as campaign_resolver
from app.modules.canonicalization.vocabulary import VocabEntry, Vocabulary
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.curation.models import UncertaintyCase
from app.modules.curation.store import CurationStore
from app.modules.jobs.paper_handlers import CurationProposalSink, Proposal
from tests.canonicalization.canon_helpers import NOW, TARGET, local_approval, open_gates, vocab
from tests.jobs.jobs_fakes import local_ctx

CORPUS = "corpus-a"
PUB = "10.1/p1"


def node(node_id, name, category, pub=PUB, description=None):
    return {"id": node_id, "name": name, "category": category, "publications": [pub],
            "description": description or f"We measured the film by {name} at the beamline."}


def graph():
    return {
        "things": [
            node("n1", "GI-WAXS", "ExperimentalTechnique"),
            node("n2", "GI-WAXS", "ExperimentalTechnique", "10.1/p2"),
            node("n3", "GIWAXS", "ExperimentalTechnique", "10.1/p3"),
            node("n4", "WTe2", "Material"),
            node("n5", "WTe2", "Material", "10.1/p2"),
            node("n6", "atomic force microscopy", "ExperimentalTechnique", "10.1/p3"),
            node("n7", "TEM", "ExperimentalTechnique"),
            {"id": "pub:1", "name": "A paper", "category": "Publication"},  # not a candidate term
            {"id": "snip:1", "name": "code", "category": "CodeSnippet"},
        ],
        "associations": [
            {"subject": "pub:1", "predicate": "rel:mentions", "object": "n1"},
            {"source": "pub:1", "target": "n1", "predicate": "rel:mentions"},
            {"subject": "pub:1", "predicate": "rel:mentions", "object": "n4"},
        ],
    }


class FakePort:
    """The Save port as the adapter sees it: effective graph plus a before/after Save (node patches only)."""

    def __init__(self, data=None):
        self.data = data or graph()
        self.saves = []
        self.ctxs = []

    def effective_graph(self):
        return copy.deepcopy(self.data)

    def save(self, ctx, before, after):
        assert ctx.actor_kind.value in ("interactive", "cli")
        b = {n["id"]: n for n in before["things"]}
        a = {n["id"]: n for n in after["things"]}
        assert set(b) == set(a), "a merge never adds or removes nodes"
        assert before["associations"] == after["associations"], "a merge never re-links"
        changed = sorted(k for k in a if a[k] != b[k])
        self.saves.append(changed)
        self.ctxs.append(ctx)
        self.data["things"] = [a[n["id"]] for n in self.data["things"]]
        return {"edit_ids": changed}


def resolved(label, category="technique", quote="GI-WAXS", confidence=0.95):
    return {"disposition": "resolved", "canonical_label": label, "category": category, "aliases": [],
            "evidence_quote": quote, "confidence": confidence, "reason": "reviewed"}


def term_case(surface, category="technique", locator=None):
    return UncertaintyCase(
        corpus=CORPUS, publication_id=PUB, document_hash="d" * 64, source_kind="chunk",
        source_locator=locator or f"c:{surface}", case_type="unresolved_term", reason="unknown_term",
        evidence=f"We measured the film by {surface} at the beamline.",
        payload={"surface": surface, "category": category}, vocabulary_revision="v1", schema_revision="s1",
    )


def accept(store, case, resolution, reviewer="owner"):
    case_id, _ = store.enqueue_for_review(case)
    row = store.get_case(case_id, [CORPUS])
    return store.apply_decision(case_id, [CORPUS], bound_revision=row["revision"], accept=True,
                                resolution=resolution, reviewer=reviewer)


def service(store, port, v=None, **kw):
    v = v or vocab()
    return MergeApplyService(cases=store, vocabulary=lambda: v, port=lambda: port, target=TARGET,
                             clock=lambda: NOW, **kw), v


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    """The adapter never calls a model: every adjudication / resolver entry point fails loudly if reached."""

    def boom(*_a, **_k):
        raise AssertionError("the apply adapter called a model")

    monkeypatch.setattr(adjudication, "adjudicate_mention", boom)
    monkeypatch.setattr(campaign_resolver.StructuredTermResolver, "__call__", boom, raising=False)
    monkeypatch.setattr(campaign_resolver.StructuredTermResolver, "resolve", boom, raising=False)


def human(**kw):
    return local_ctx("graph.publish", "curation.approve", "graph.edit", **kw)


# ----------------------------------------------------------------------------------------------- rows


def test_rows_from_graph_maps_schema_classes_to_vocabulary_categories_and_counts_degree():
    rows = rows_from_graph(graph())
    assert [n["node_id"] for n in rows.nodes] == ["n1", "n2", "n3", "n4", "n5", "n6", "n7"]
    assert {n["category"] for n in rows.nodes} == {"technique", "material"}
    assert rows.nodes[0]["publication_id"] == PUB and rows.nodes[0]["graph_category"] == "ExperimentalTechnique"
    assert rows.edges == {"n1": 2, "n4": 1}  # publication endpoints are not candidate rows
    assert rows.revision() == rows_from_graph(graph()).revision()
    moved = graph()
    moved["things"][0]["name"] = "GI WAXS"
    assert rows_from_graph(moved).revision() != rows.revision()
    assert rows_from_graph({"nodes": [{"id": "x", "label": "TEM", "type": "technique"}], "edges": []}).nodes[0]["surface"] == "TEM"


# ----------------------------------------------------------------------------------------------- accepted cases


def test_accepted_alias_resolves_end_to_end_through_every_layer(monkeypatch):
    """Approved alias: accepted case -> merge -> plan (both revisions) -> gated route -> node patches via Save."""
    open_gates(monkeypatch)
    store, port = CurationStore(":memory:"), FakePort()
    accept(store, term_case("GI-WAXS"), resolved("GIWAXS"))
    svc, v = service(store, port)
    ctx = human()
    draft, report, rows, vocabulary = svc.plan(ctx, corpus=CORPUS)
    plan = draft.plan
    assert plan is not None and [o.target_slug for o in plan.ops] == ["giwaxs"]
    assert plan.ops[0].node_ids == ("n1", "n2", "n3") and set(plan.ops[0].variants) == {"GI-WAXS", "GIWAXS"}
    assert plan.source_revision == rows.revision() == rows_from_graph(port.effective_graph()).revision()
    assert plan.vocabulary_revision == v.revision and report.vocabulary_approved is True
    assert report.plan_sha256 == plan.plan_sha256 and report.gate == "apply_migration" and report.gate_open is True
    assert (report.false_merges, report.false_splits, report.model_calls) == (0, 0, 0)
    # dry run (default): nothing saved
    dry = svc.apply_plan(ctx, draft, report, approval=None, vocabulary=vocabulary)
    assert dry.dry_run is True and dry.applied == () and port.saves == []
    assert store.awaiting_validation([CORPUS])
    # real apply with an exact approval
    out = svc.apply_plan(ctx, draft, report, approval=local_approval(plan), vocabulary=vocabulary, dry_run=False, now=NOW)
    assert out.dry_run is False and out.applied == (plan.ops[0].term_key,) and out.refused == ()
    assert port.saves == [["n1", "n2", "n3"]]
    patched = {n["id"]: n for n in port.data["things"]}
    for nid in ("n1", "n2", "n3"):
        assert patched[nid]["canonical_slug"] == "giwaxs" and patched[nid]["canonical_plan_id"] == plan.plan_id
        assert patched[nid]["canonical_vocabulary_revision"] == v.revision
        assert set(CANONICAL_FIELDS) <= set(patched[nid]) and patched[nid]["name"] in ("GI-WAXS", "GIWAXS")
    assert "canonical_slug" not in patched["n4"] and "canonical_slug" not in patched["n7"]
    assert store.awaiting_validation([CORPUS]) == [] and len(out.validated_cases) == 1
    assert out.as_dict()["applied"] == [plan.ops[0].term_key] and json.dumps(out.as_dict())


@pytest.mark.parametrize("via", ["verdict", "proposal"])
def test_named_negative_control_never_merges_even_when_proposed_and_accepted(monkeypatch, via):
    """WTe2 -> MoTe2 is a named negative control: accepted or not, it is held, counted as a false merge, never applied."""
    open_gates(monkeypatch)
    store, port = CurationStore(":memory:"), FakePort()
    if via == "verdict":
        accept(store, term_case("WTe2", "material"), resolved("MoTe2", "material", quote="WTe2"))
    else:
        sink = CurationProposalSink(store)
        sink.propose(local_ctx(), CORPUS, Proposal(MERGE_PROPOSAL_KIND, "wte2", "m:wte2", "fold WTe2 into MoTe2",
                                                   merge_proposal_content(surface="WTe2", category="material",
                                                                          target_slug="mote2", node_ids=["n4", "n5"])))
        row = store.list_cases([CORPUS], state="needs_review")[0]
        store.apply_decision(row["id"], [CORPUS], bound_revision=row["revision"], accept=True,
                             resolution={"accepted": True}, reviewer="owner")
    svc, v = service(store, port)
    draft, report, _rows, vocabulary = svc.plan(human(), corpus=CORPUS)
    assert draft.plan is None and len(draft.held) == 1 and "mote2" in draft.held[0][1]
    assert report.false_merges == 1 and report.false_merge_items == ("WTe2 -> mote2",) and report.false_splits == 0
    assert report.unresolved_visible == ("WTe2",) and report.planned == ()
    out = svc.apply_plan(human(), draft, report, approval=None, vocabulary=vocabulary, dry_run=False)
    assert out.applied == () and port.saves == []  # nothing to apply; the case stays visible, unvalidated
    assert len(store.awaiting_validation([CORPUS])) == 1
    # even a forged plan that carries the pair is refused by the route's own per-operation safety check
    forged = aa.build_plan(
        [aa.AcceptedMerge("c", "r", "k", "material", "WTe2", ("WTe2",), ("n4", "n5"), "mote2", "m", 1.0)],
        rows_from_graph(port.effective_graph()), v, scope=[CORPUS], target=TARGET, created_at=NOW)
    assert forged.plan is None and forged.held[0][1].startswith("'WTe2' vs mote2")


def test_unresolved_terms_stay_visible_after_apply(monkeypatch):
    open_gates(monkeypatch)
    store, port = CurationStore(":memory:"), FakePort()
    accept(store, term_case("GI-WAXS"), resolved("GIWAXS"))
    accept(store, term_case("atomic force microscopy"), resolved("AFM", quote="atomic force microscopy"))  # not in vocab
    accept(store, term_case("WTe2", "material"), resolved("MoTe2", "material", quote="WTe2"))  # negative control
    svc, v = service(store, port)
    draft, report, _rows, vocabulary = svc.plan(human(), corpus=CORPUS)
    assert report.accepted_cases == 3 and len(report.merges) == 2 and len(report.held) == 1
    assert report.held[0].surface == "atomic force microscopy" and "not an approved vocabulary entry" in report.held[0].reason
    assert report.unresolved_visible == ("WTe2", "atomic force microscopy")
    out = svc.apply_plan(human(), draft, report, approval=local_approval(draft.plan), vocabulary=vocabulary,
                         dry_run=False, now=NOW)
    assert out.applied == (draft.plan.ops[0].term_key,) and out.unresolved_visible == ("WTe2", "atomic force microscopy")
    left = {c["payload"]["surface"] for c in store.awaiting_validation([CORPUS])}
    assert left == {"WTe2", "atomic force microscopy"}  # the two unresolved cases are still there to look at
    names = {n["id"]: n.get("canonical_slug") for n in port.data["things"]}
    assert names["n1"] == "giwaxs" and names["n4"] is None and names["n6"] is None


def test_candidate_reads_and_prompts_are_bounded_and_no_model_is_called():
    store, port = CurationStore(":memory:"), FakePort()
    accept(store, term_case("GI-WAXS"), resolved("GIWAXS"))
    svc, v = service(store, port)
    draft, report, rows, _ = svc.plan(human(), corpus=CORPUS)
    assert report.candidates_considered == len(rows.nodes) == 7 and report.model_calls == 0
    # the accepted-case read is bounded: cases past the cap are not even looked at
    many = [{"state": "resolved", "case_type": "incomplete_extraction", "id": str(i)} for i in range(MAX_ACCEPTED_CASES + 5)]
    merges, held, skipped = accepted_merges(many, v, rows)
    assert (merges, held, skipped) == ([], [], MAX_ACCEPTED_CASES)
    # the plan itself is capped by the migration module (MAX_PLAN_OPS), not by this adapter


def test_stale_source_and_stale_vocabulary_are_typed_refusals(monkeypatch):
    open_gates(monkeypatch)
    store, port = CurationStore(":memory:"), FakePort()
    accept(store, term_case("GI-WAXS"), resolved("GIWAXS"))
    svc, v = service(store, port)
    draft, report, _rows, vocabulary = svc.plan(human(), corpus=CORPUS)
    approval = local_approval(draft.plan)
    # the graph moved under the plan
    port.data["things"].append(node("n9", "GI-WAXS", "ExperimentalTechnique", "10.1/p9"))
    with pytest.raises(RefusedApply) as err:
        svc.apply_plan(human(), draft, report, approval=approval, vocabulary=vocabulary, dry_run=False, now=NOW)
    assert err.value.code == "stale_source" and err.value.report.refusal[0] == "stale_source" and port.saves == []
    assert isinstance(err.value, gates.ApplyRefused)
    port.data["things"].pop()
    # the vocabulary moved under the plan
    moved = Vocabulary(1, [*v.entries, VocabEntry("afm", "atomic force microscopy", "technique")], status="approved")
    with pytest.raises(RefusedApply) as err:
        svc.apply_plan(human(), draft, report, approval=approval, vocabulary=moved, dry_run=False, now=NOW)
    assert err.value.code == "stale_vocabulary" and port.saves == []
    # a candidate (unapproved) vocabulary of the same content is refused by the route itself
    with pytest.raises(RefusedApply) as err:
        svc.apply_plan(human(), draft, report, approval=approval, vocabulary=vocab("candidate"), dry_run=False, now=NOW)
    assert err.value.code == "vocabulary_not_approved"
    # the stale checks run before any gate, so a closed gate still reports staleness honestly
    monkeypatch.setattr(gates, "_GATES", {k: False for k in gates._GATES})
    port.data["things"].append(node("n9", "GI-WAXS", "ExperimentalTechnique", "10.1/p9"))
    with pytest.raises(RefusedApply, match="stale_source"):
        svc.apply_plan(human(), draft, report, approval=approval, vocabulary=vocabulary, dry_run=False, now=NOW)
    assert port.saves == [] and store.awaiting_validation([CORPUS])


@pytest.mark.parametrize("route", sorted(APPLY_ROUTES))
def test_every_apply_route_enforces_the_same_gate(route, monkeypatch):
    store, port = CurationStore(":memory:"), FakePort()
    accept(store, term_case("GI-WAXS"), resolved("GIWAXS"))
    svc, v = service(store, port)
    draft, report, _rows, vocabulary = svc.plan(human(), corpus=CORPUS, route=route)
    assert report.route == route and report.gate == ROUTE_GATES[route] and report.gate_open is False
    approval = local_approval(draft.plan)
    # closed gate (every build until the C4 rerun): typed refusal, nothing written, even with a perfect approval
    with pytest.raises(RefusedApply) as err:
        svc.apply_plan(human(), draft, report, approval=approval, vocabulary=vocabulary, dry_run=False, now=NOW)
    assert err.value.code == "gate_closed" and port.saves == [] and err.value.report.applied == ()
    assert svc.apply_plan(human(), draft, report, approval=None, vocabulary=vocabulary).dry_run is True
    # open gate: the approval is still required and must be exact
    open_gates(monkeypatch)
    with pytest.raises(RefusedApply) as err:
        svc.apply_plan(human(), draft, report, approval=None, vocabulary=vocabulary, dry_run=False, now=NOW)
    assert err.value.code == "approval_missing"
    with pytest.raises(RefusedApply) as err:
        svc.apply_plan(human(), draft, report, approval=local_approval(draft.plan, scope=["corpus-b"]),
                       vocabulary=vocabulary, dry_run=False, now=NOW)
    assert err.value.code == "approval_mismatch" and port.saves == []
    out = svc.apply_plan(human(), draft, report, approval=approval, vocabulary=vocabulary, dry_run=False, now=NOW)
    assert out.applied == (draft.plan.ops[0].term_key,) and port.saves == [["n1", "n2", "n3"]]


def test_a_worker_or_model_context_and_an_out_of_scope_corpus_are_denied():
    from app.modules.contracts.context import ActorKind

    store, port = CurationStore(":memory:"), FakePort()
    svc, _ = service(store, port)
    with pytest.raises(ContractError) as err:
        svc.plan(local_ctx("graph.publish", actor_kind=ActorKind.WORKER), corpus=CORPUS)
    assert err.value.code is ErrorCode.DENIED
    with pytest.raises(ContractError) as err:
        svc.plan(human(corpus_scope=frozenset({"other"})), corpus=CORPUS)
    assert err.value.code is ErrorCode.DENIED
    with pytest.raises(ContractError) as err:
        svc.plan(human(), corpus="")
    assert err.value.code is ErrorCode.INVALID_INPUT
    with pytest.raises(ContractError) as err:
        svc.plan(human(), corpus=CORPUS, route="apply_everything")
    assert err.value.code is ErrorCode.INVALID_INPUT
    none = MergeApplyService(cases=store, vocabulary=None, port=lambda: port, target=TARGET)
    with pytest.raises(ContractError) as err:
        none.plan(human(), corpus=CORPUS)
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY and port.saves == []


def test_false_merges_and_false_splits_are_reported_as_separate_counts():
    store, port = CurationStore(":memory:"), FakePort()
    # false merge: a negative-control fold that was accepted anyway
    accept(store, term_case("WTe2", "material"), resolved("MoTe2", "material", quote="WTe2"))
    # false split: the same approved alias accepted with two different targets -> both held, the alias one counts
    accept(store, term_case("GIWAXS"), resolved("GIWAXS", quote="GIWAXS"))
    accept(store, term_case("GIWAXS", locator="c:other"), resolved("TEM", quote="GIWAXS"))
    svc, _ = service(store, port)
    draft, report, _rows, _ = svc.plan(human(), corpus=CORPUS)
    assert draft.plan is None
    assert report.false_merges == 1 and report.false_merge_items == ("WTe2 -> mote2",)
    assert report.false_splits == 1 and report.false_split_items == ("GIWAXS -> giwaxs",)
    assert {h.reason.split(":")[0] for h in report.held} == {"two accepted targets for one term"}
    assert report.unresolved_visible == ("GIWAXS", "WTe2")
    d = report.as_dict()
    assert d["false_merges"] == 1 and d["false_splits"] == 1 and d["held"][0]["target_slug"] in ("giwaxs", "tem")


def test_merge_proposal_round_trips_through_the_curation_sink_and_two_cases_join():
    store, port = CurationStore(":memory:"), FakePort()
    sink = CurationProposalSink(store)
    content = merge_proposal_content(surface="GI-WAXS", category="technique", target_slug="giwaxs", node_ids=["n1", "n2"],
                                     variants=["GI-WAXS"], confidence=0.9, method="campaign_round1_agree")
    assert sink.propose(local_ctx(), CORPUS, Proposal(MERGE_PROPOSAL_KIND, "giwaxs", "t:giwaxs", "fold", content)) is True
    pending = store.list_cases([CORPUS], state="needs_review")
    assert len(pending) == 1 and pending[0]["payload"] == {"proposal_kind": MERGE_PROPOSAL_KIND}
    store.apply_decision(pending[0]["id"], [CORPUS], bound_revision=pending[0]["revision"], accept=True,
                         resolution={"accepted": True}, reviewer="owner")
    accept(store, term_case("GIWAXS"), resolved("GIWAXS", quote="GIWAXS"))  # same term from a reviewer verdict
    merges, held, skipped = accepted_merges(store.awaiting_validation([CORPUS]), vocab(), rows_from_graph(graph()))
    assert held == [] and skipped == 0 and len(merges) == 1
    m = merges[0]
    assert m.target_slug == "giwaxs" and m.node_ids == ("n1", "n2", "n3") and set(m.variants) == {"GI-WAXS", "GIWAXS"}
    assert any(v.startswith("also:") for v in m.votes) and m.method.startswith("reviewer_accepted:")
    # a proposal of another kind, and a resolved case of another type, are skipped (not held)
    sink.propose(local_ctx(), CORPUS, Proposal("publication_links", "x", "p", "r", {"nodes": []}))
    other = store.list_cases([CORPUS], state="needs_review")[0]
    store.apply_decision(other["id"], [CORPUS], bound_revision=other["revision"], accept=True, resolution={}, reviewer="o")
    _merges, held, skipped = accepted_merges(store.awaiting_validation([CORPUS]), vocab(), rows_from_graph(graph()))
    assert skipped == 1 and held == []


def test_merge_target_refuses_missing_nodes_and_unknown_slugs_without_saving():
    port = FakePort()
    v = vocab()
    target = EditPortMergeTarget(port, human(), plan_id="t07-x", vocabulary=v)
    assert target.source_revision() == rows_from_graph(graph()).revision()
    op = aa.MergeOp("k", "technique", "GI-WAXS", ("GI-WAXS",), ("n1", "n404"), "giwaxs", "m", 1.0)
    with pytest.raises(gates.ApplyRefused, match="node_missing"):
        target.merge(op)
    with pytest.raises(gates.ApplyRefused, match="unknown_slug"):
        target.merge(aa.MergeOp("k", "technique", "x", ("x",), ("n1",), "nope", "m", 1.0))
    assert port.saves == []


def test_held_proposals_and_contract_error_mapping():
    assert HeldProposal("c", "s", "why").as_dict() == {"case_id": "c", "surface": "s", "reason": "why", "category": "",
                                                       "target_slug": ""}
    for code, expected in (("gate_closed", ErrorCode.UNSUPPORTED_CAPABILITY), ("approval_missing", ErrorCode.STALE_APPROVAL),
                           ("stale_source", ErrorCode.CONFLICT), ("unsafe_operation", ErrorCode.INVALID_INPUT)):
        err = contract_error(gates.ApplyRefused(code, "m"))
        assert err.code is expected and err.reason == code
