"""T07 grouping, adjudication, growth, audit, cleanup and snapshots; vocabulary routes are gated too."""

from datetime import datetime, timezone

import numpy as np
import pytest

from app.modules.canonicalization import gates
from app.modules.canonicalization.adjudication import adjudicate_group
from app.modules.canonicalization.audit import activate_vocabulary, audit_vocabulary
from app.modules.canonicalization.cleanup import (
    classify_node,
    format_review_digest,
    plan_cleanup,
    propose_cleanup,
    quality_report,
    triage_proposal,
)
from app.modules.canonicalization.growth import (
    Observation,
    ObservationLog,
    definitions,
    promote,
    propose_promotion,
)
from app.modules.canonicalization.grouping import (
    GroupingTooLarge,
    TermGroup,
    estimate_grouping_memory,
    estimate_matrix_bytes,
    group_terms,
    summarize,
)
from app.modules.canonicalization.migration import apply_cleanup
from app.modules.canonicalization.quality_snapshots import snapshot_history, summarise, write_snapshot
from app.modules.canonicalization.vocabulary import VocabEntry, Vocabulary, VocabularyApproval
from tests.canonicalization.canon_helpers import NOW, TARGET, FakeTarget, local_approval, open_gates, rows, vocab


def terms_and_vectors(n=60, dim=16, seed=3):
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(8, dim))
    vecs = [centers[i % 8] + 0.05 * rng.normal(size=dim) for i in range(n)]
    terms = [
        {"surface": f"t{i}", "category": "technique" if i % 2 else "material", "mentions": i, "has_context": True}
        for i in range(n)
    ]
    return terms, [v.tolist() for v in vecs]


def test_rowwise_grouping_is_identical_to_matrix_grouping_and_capped():
    terms, vecs = terms_and_vectors()
    full = group_terms(terms, vecs, threshold=0.9)
    low_mem = group_terms(terms, vecs, threshold=0.9, max_matrix_bytes=1)
    assert [g.as_dict() for g in full] == [g.as_dict() for g in low_mem] and full
    with pytest.raises(GroupingTooLarge):
        group_terms(terms, vecs, max_terms_per_category=5)
    with pytest.raises(ValueError):
        group_terms(terms, vecs[:-1])
    est = estimate_grouping_memory(11199, 4096)
    assert est["matrix_bytes"] == estimate_matrix_bytes(11199) and est["mode"] == "rowwise"
    assert est["peak_bytes"] < est["matrix_bytes"]


def test_material_groups_are_proposal_only_and_context_free_members_block_auto():
    terms, vecs = terms_and_vectors()
    groups = group_terms(terms, vecs, threshold=0.9)
    assert all(not g.auto_applicable for g in groups if g.category == "material")
    terms[1]["has_context"] = False
    groups = group_terms(terms, vecs, threshold=0.9)
    assert summarize(groups)["groups"] == len(groups)
    assert all(not g.auto_applicable for g in groups if "t1" in g.members)


def reply(**payload):
    """A judge that always returns the given JSON payload."""
    import json

    return lambda prompt: json.dumps(payload)


def test_group_adjudication_is_proposal_with_mandatory_safety():
    group = TermGroup("technique", "SEM", ["SEM", "TEM", "S.E.M."], 0.9, True)
    judge = reply(same_thing=True, canonical="SEM", aliases=["TEM", "S.E.M."], confidence=0.99, reason="x")
    verdict = adjudicate_group(group, {}, judge=judge)
    assert verdict.aliases == ["S.E.M."] and verdict.held_for_review[0][0] == "TEM"
    assert adjudicate_group(group, {}, judge=None).method == "no_model"
    assert adjudicate_group(group, {}, judge=lambda p: "nope").method == "unparseable"
    bad = reply(same_thing=True, canonical="invented", confidence=1)
    assert adjudicate_group(group, {}, judge=bad).method == "canonical_not_in_group"
    mat = TermGroup("material", "Fe2O3", ["Fe2O3", "fe2o3"], 0.9, False)
    j = reply(same_thing=True, canonical="Fe2O3", aliases=["fe2o3"], confidence=1)
    assert not adjudicate_group(mat, {}, judge=j).auto_applicable  # the grouping layer's refusal wins


def obs(label, pub, category="technique", quote="q", aliases=()):
    return Observation(label, category, pub, tuple(aliases), quote, "h")


def test_growth_keeps_observations_proposals_and_withheld_apart():
    base = vocab("candidate")
    log = ObservationLog()
    for pub in ("p1", "p2", "p3"):
        log.observe(obs("Kelvin probe force microscopy", pub, aliases=("KPFM", "something unrelated entirely")))
    log.observe(obs("rare technique", "p1"))
    for pub in ("p1", "p2", "p3"):
        log.observe(obs("PbI2 film", pub, "material"))
    log.observe(obs("giwaxs", "p1"))
    proposal = propose_promotion(base, log)
    assert [e.slug for e in proposal.entries] == ["kelvin_probe_force_microscopy"]
    assert proposal.entries[0].aliases == ("KPFM",)  # the unsafe alias was dropped, not trusted
    reasons = {w.label: w.reason for w in proposal.withheld}
    assert "below 3" in reasons["rare technique"] and "structure identifier" in reasons["PbI2 film"]
    assert reasons["giwaxs"] == "already in vocabulary"
    assert base.revision == vocab("candidate").revision  # staging and proposing changed nothing
    assert ObservationLog.from_json(log.to_json()).observations == log.observations


def test_promotion_is_gated_and_yields_a_new_candidate_only(monkeypatch):
    base = vocab("candidate")
    log = ObservationLog([obs("Kelvin probe force microscopy", p) for p in ("p1", "p2", "p3")])
    proposal = propose_promotion(base, log)
    approval = VocabularyApproval("pa", proposal.revision, "owner", NOW)
    with pytest.raises(gates.ApplyRefused) as err:
        promote(base, proposal, approval)
    assert err.value.code == "gate_closed"
    open_gates(monkeypatch)
    with pytest.raises(gates.ApplyRefused) as err:
        promote(base, proposal, None)
    assert err.value.code == "approval_missing"
    with pytest.raises(gates.ApplyRefused) as err:
        promote(base, proposal, VocabularyApproval("pa", "0" * 64, "owner", NOW))
    assert err.value.code == "approval_mismatch"
    new = promote(base, proposal, approval)
    assert (
        new.status == "candidate" and new.version == base.version + 1 and new.get("kelvin_probe_force_microscopy")
    )
    with pytest.raises(gates.ApplyRefused) as err:
        promote(Vocabulary(1, [VocabEntry("x", "x", "technique")]), proposal, approval)
    assert err.value.code == "stale_vocabulary"


def test_definitions_helper():
    assert definitions("grazing incidence wide angle x-ray scattering (GIWAXS) was used") == [
        ("GIWAXS", "grazing incidence wide angle x-ray scattering")
    ]
    assert definitions("results (Table) shown") == []


def test_audit_flags_unsafe_and_cross_entry_aliases_and_activation_is_gated(monkeypatch):
    bad = Vocabulary(
        1,
        [
            VocabEntry(
                "sem",
                "scanning electron microscopy",
                "technique",
                aliases=("SEM", "transmission electron microscope"),
            ),
            VocabEntry("tem", "transmission electron microscopy", "technique", aliases=("TEM",)),
        ],
    )
    report = audit_vocabulary(bad)
    kinds = {(f.slug, f.alias, f.kind) for f in report.findings}
    assert ("sem", "transmission electron microscope", "unsafe_alias") in kinds
    assert ("sem", "transmission electron microscope", "cross_entry_rendering") in kinds
    assert not report.clean
    ok = audit_vocabulary(bad, reviewed_ok=[("sem", "transmission electron microscope")], cross_entry=False)
    assert ok.clean
    good = vocab()
    clean = audit_vocabulary(good)
    approval = VocabularyApproval("va", good.revision, "owner", NOW)
    with pytest.raises(gates.ApplyRefused) as err:
        activate_vocabulary(good, approval, clean)
    assert err.value.code == "gate_closed"
    open_gates(monkeypatch)
    with pytest.raises(gates.ApplyRefused) as err:
        activate_vocabulary(
            vocab("candidate"), VocabularyApproval("va", vocab("candidate").revision, "o", NOW), clean
        )
    assert err.value.code == "vocabulary_not_approved"
    assert activate_vocabulary(good, approval, clean).vocabulary_revision == good.revision


def test_cleanup_report_proposal_triage_and_scoped_plan(monkeypatch):
    v, r = vocab(), rows()
    report = quality_report(r, v)
    assert report["candidate_terms"] < report["candidate_nodes"] and report["canonical_vocabulary_entries"] == 3
    assert classify_node(r.nodes[-1], v).kind == "malformed"
    prop = propose_cleanup(r, v)
    actions = {i.surface: i.action for i in prop.items}
    assert actions["ImageJ"] == "reject_candidate" and actions["atomic force microscopy"] == "keep"
    triaged = triage_proposal(prop, min_reject_edges=0)
    assert "ImageJ" in format_review_digest(triaged)
    # no vocabulary alias matches "GI-WAXS" by slug? it does via entity_slug(gi-waxs)=gi_waxs: no -> kept visible
    assert all(i.action != "map_to" or i.target_slug for i in prop.items)
    exact = rows()
    exact.nodes.append(
        {
            "node_id": "n20",
            "surface": "TEM",
            "category": "technique",
            "publication_id": "p9",
            "section_id": "s",
            "text": "TEM images",
        }
    )
    prop2 = propose_cleanup(exact, v)
    draft = plan_cleanup(prop2, v, scope=["corpus-a"], target=TARGET, created_at=NOW, report_sha256="a" * 64)
    assert [o.target_slug for o in draft.plan.ops] == ["tem"]
    assert not any(o.method.endswith("reject_candidate") for o in draft.plan.ops)  # never a deletion plan
    with pytest.raises(ValueError):
        plan_cleanup(
            prop2, Vocabulary(2, v.entries), scope=["a"], target=TARGET, created_at=NOW, report_sha256="a" * 64
        )
    with pytest.raises(ValueError):
        plan_cleanup(prop2, v, scope=["*"], target=TARGET, created_at=NOW, report_sha256="a" * 64)
    target = FakeTarget(draft.plan.source_revision)
    with pytest.raises(gates.ApplyRefused):
        apply_cleanup(draft.plan, local_approval(draft.plan), target, v, dry_run=False, now=NOW)
    assert target.merged == []


def test_quality_snapshots_are_versioned_and_bounded(tmp_path):
    v = vocab()
    summary = summarise(quality_report(rows(), v), {"operations": 1, "held": 0}, {"clean": True, "findings": []})
    assert summary["candidate"]["terms"] and summary["canonical"]["vocabulary_entries"] == 3
    first = write_snapshot(tmp_path, summary, taken_at=datetime(2026, 10, 14, 1, tzinfo=timezone.utc))
    again = write_snapshot(tmp_path, summary, taken_at=datetime(2026, 10, 14, 1, tzinfo=timezone.utc))
    write_snapshot(tmp_path, {**summary, "x": 1}, taken_at=datetime(2026, 10, 15, 1, tzinfo=timezone.utc))
    assert first == again and len(snapshot_history(tmp_path)) == 2
    assert len(snapshot_history(tmp_path, limit=1)) == 1


def test_package_import_has_no_side_effects_and_is_listed_in_pyproject():
    import subprocess
    import sys
    import pathlib

    repo = str(pathlib.Path(__file__).resolve().parents[2])
    code = (
        f"import sys; sys.path.insert(0, {repo!r});"
        "import app.modules.canonicalization, app.modules.canonicalization.measurement;"
        "bad=[x for x in ('sentence_transformers','faiss','torch','rdkit') if x in sys.modules];"
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd="/", env={}
    ).stdout.strip()
    assert out == "[]", out
    text = (pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
    assert '"f2w.modules.canonicalization"' in text and '"f2w.modules.canonicalization.campaign"' in text
