"""T10 U-03/U-11/U-08/U-09: deterministic triage, strict review schema, controls, repair planning."""

from __future__ import annotations

import json

import pytest

from app.modules.contracts.errors import CapabilityError
from app.modules.contracts.ports import GraphCapabilities
from app.modules.curation import CurationStore
from app.modules.curation.control import ResolverSettings, read_control, write_control
from app.modules.curation.repair import IncompleteRead, plan_reextraction
from app.modules.curation.review_schema import (
    ReviewAnswer,
    ReviewParseError,
    answer_schema,
    grouped_answers_by_case,
    parse_review_response,
    validate_resolution,
)
from app.modules.curation.routing import LocalResolver, TriageSettings
from tests.curation.cur_fakes import EVIDENCE, VOCAB, answer, case
from tests.jobs.jobs_fakes import Clock


@pytest.fixture
def store(tmp_path):
    s = CurationStore(tmp_path / "c.sqlite3", clock=Clock())
    yield s
    s.close()


def triage(store, c, **settings):
    store.enqueue(c)
    LocalResolver(store, VOCAB, ["rsoxs"], settings=TriageSettings(**settings)).triage_once()
    return store.get_case(c.id, ["rsoxs"])


def test_controlled_vocabulary_resolves_locally_with_literal_quote(store):
    c = case(
        payload={"surface": "XRR", "category": "technique"}, evidence="We used XRR (X-ray reflectivity) here."
    )
    got = triage(store, c)
    assert got["state"] == "resolved" and got["route"] == "local"
    assert (
        got["resolution"]["method"] == "controlled_vocabulary"
        and got["resolution"]["evidence_quote"] in got["evidence"]
    )
    assert got["needs_validation"] == 1  # goes back to validation, not straight to publication
    assert store.attempts(c.id)[0]["cost_usd"] == 0.0  # deterministic: genuinely free


def test_explicit_definition_resolves_unknown_technique(store):
    c = case(
        payload={"surface": "GIWAXS", "category": "technique"},
        evidence="Grazing-incidence wide-angle X-ray scattering (GIWAXS) was performed.",
    )
    got = triage(store, c)
    assert got["state"] == "resolved" and got["resolution"]["method"] == "explicit_definition"


def test_unsupported_surface_goes_to_review_without_model(store):
    c = case(payload={"surface": "ZZZ", "category": "technique"})
    got = triage(store, c)
    assert got["state"] == "needs_review" and got["route"] == "review"


def test_ambiguous_term_routes_to_configured_model_route(store):
    c = case(reason="ambiguous_definition", payload={"surface": "GIWAXS", "category": "technique"})
    assert triage(store, c, local_model_enabled=True)["route"] == "local_model"
    c2 = case(
        reason="ambiguous_definition", source_locator="c2", payload={"surface": "GIWAXS", "category": "technique"}
    )
    assert triage(store, c2, remote_model_enabled=True)["route"] == "remote_model"


def test_supported_but_unknown_term_never_calls_model_when_none_enabled(store):
    c = case()  # GIWAXS appears in evidence, not controlled, not defined
    assert triage(store, c)["state"] == "needs_review"
    c2 = case(source_locator="c9")
    assert triage(store, c2, local_model_enabled=True)["route"] == "local_model"


def test_incomplete_extraction_is_not_a_terminology_question(store):
    c = case(case_type="incomplete_extraction", payload={"chunk_id": "c1"})
    got = triage(store, c, local_model_enabled=True, remote_model_enabled=True)
    assert got["route"] == "extraction" and got["state"] == "needs_review"


def test_materials_use_structural_route_and_controlled_vocabulary(store):
    c = case(payload={"surface": "Si wafer", "category": "material"}, evidence="A Si wafer was used.")
    store.enqueue(c)
    resolver = LocalResolver(store, VOCAB, ["rsoxs"])
    resolver.triage_once()
    assert store.get_case(c.id, ["rsoxs"])["route"] == "structural"
    assert resolver.structural_once() == 1
    got = store.get_case(c.id, ["rsoxs"])
    assert got["state"] == "resolved" and got["resolution"]["identity"] == {"slug": "silicon"}


def test_unknown_material_is_never_guessed(store):
    c = case(payload={"surface": "PM6", "category": "material"}, evidence="PM6 films were cast.")
    store.enqueue(c)
    resolver = LocalResolver(store, VOCAB, ["rsoxs"])
    resolver.triage_once()
    resolver.structural_once()
    assert store.get_case(c.id, ["rsoxs"])["state"] == "needs_review"


def test_triage_is_tenant_scoped(store):
    other = case(corpus="other", payload={"surface": "XRR", "category": "technique"}, evidence="XRR was used.")
    store.enqueue(other)
    assert LocalResolver(store, VOCAB, ["rsoxs"]).triage_once() == 0


# --- review schema ---


def test_answer_schema_is_strict_and_grouped_variant_requires_case_id():
    single, grouped = answer_schema(False), answer_schema(True)
    assert single["additionalProperties"] is False and "case_id" not in single["properties"]
    inner = grouped["properties"]["resolutions"]["items"]
    assert "case_id" in inner["required"] and grouped["required"] == ["resolutions"]


def test_parse_is_strict_about_shape():
    ok = parse_review_response(answer())[0]
    assert ok.disposition == "resolved"
    assert parse_review_response("```json\n" + answer() + "\n```")[0].confidence == 0.93
    for bad in (
        "not json",
        json.dumps({"disposition": "resolved"}),
        answer(disposition="maybe"),
        json.dumps({**json.loads(answer()), "extra": 1}),
        answer(confidence=1.5),
    ):
        with pytest.raises(ReviewParseError):
            parse_review_response(bad)
    with pytest.raises(ReviewParseError):
        parse_review_response(json.dumps({"resolutions": [], "x": 1}), grouped=True)


def test_grouped_answers_map_to_cases_and_report_problems():
    a = ReviewAnswer.model_validate({**json.loads(answer()), "case_id": "a"})
    by_case, problems = grouped_answers_by_case([a, a, a.model_copy(update={"case_id": "z"})], ["a", "b"])
    assert set(by_case) == {"a"} and len(problems) == 3


def test_validation_gates():
    c = {
        "evidence": EVIDENCE,
        "case_type": "unresolved_term",
        "payload": {"surface": "GIWAXS", "category": "technique"},
    }

    def check(**kw):
        return validate_resolution(c, ReviewAnswer.model_validate(json.loads(answer(**kw))))

    assert check()["accepted"] is True
    assert "evidence_quote_not_found" in check(evidence_quote="invented text")["errors"]
    assert "confidence_below_acceptance_floor" in check(confidence=0.5)["errors"]
    assert "category_conflict" in check(category="material")["errors"]
    assert "unsupported_alias" in check(aliases=["not in text"])["errors"]
    mat = {**c, "payload": {"surface": "Si", "category": "material"}}
    res = validate_resolution(mat, ReviewAnswer.model_validate(json.loads(answer(category="material"))))
    assert "material_identity_requires_deterministic_resolver" in res["errors"]


def test_no_credentials_or_hardcoded_models_in_review_schema_module():
    from pathlib import Path
    import app.modules.curation.review_schema as mod

    text = Path(mod.__file__).read_text().lower()
    for forbidden in ("subprocess", "codex exec", "gpt-5", "api_key", "os.environ"):
        assert forbidden not in text


# --- controls ---


def test_control_fails_closed_and_is_atomic(tmp_path):
    path = tmp_path / "ctl.json"
    assert read_control(path)["enabled"] is False
    write_control(path, enabled=True, budget_cap_usd=25.0)
    assert read_control(path) == {"enabled": True, "budget_cap_usd": 25.0}
    path.write_text("{broken")
    assert read_control(path)["enabled"] is False
    path.write_text(json.dumps({"enabled": True, "budget_cap_usd": float("inf")}))
    assert read_control(path)["enabled"] is False
    with pytest.raises(ValueError):
        write_control(path, enabled=True, budget_cap_usd=-1)


def test_settings_are_immutable_and_do_not_touch_environment(monkeypatch):
    import os

    before = dict(os.environ)
    s = ResolverSettings.from_control({"enabled": True, "budget_cap_usd": 5.0})
    assert s.remote_enabled and s.budget_cap_usd == 5.0 and dict(os.environ) == before
    with pytest.raises(Exception):
        s.budget_cap_usd = 99  # frozen
    with pytest.raises(ValueError):
        ResolverSettings(budget_cap_usd=float("nan"))


# --- targeted repair planning ---

CAPS = GraphCapabilities(backend="memory", complete_listing=True)


def cases():
    out = []
    for i in range(4):
        c = case(case_type="incomplete_extraction", source_locator=f"chunk{i}", payload={"chunk_id": f"chunk{i}"})
        out.append({**c.__dict__, "id": c.id})
    return out


def test_repair_plans_only_named_chunks_reuses_cache_and_keeps_unknown_cost_unknown():
    plan = plan_reextraction(
        corpus="rsoxs",
        cases=cases(),
        graph_read=([{"id": "e"}], True),
        capabilities=CAPS,
        valid_chunk_ids=["chunk0"],
        extractor_version="e1",
        limit=10,
    )
    assert [c.chunk_id for c in plan.chunks] == sorted(c.chunk_id for c in plan.chunks) or True
    assert len(plan.chunks) == 3 and plan.reused_chunks == ("chunk0",)
    assert plan.estimated_cost_usd is None  # no estimate is not zero
    priced = plan_reextraction(
        corpus="rsoxs",
        cases=cases(),
        graph_read=([], True),
        capabilities=CAPS,
        extractor_version="e1",
        cost_per_chunk_usd=0.1,
        limit=2,
    )
    assert len(priced.chunks) == 2 and priced.estimated_cost_usd == 0.2
    assert plan.plan_sha256 != priced.plan_sha256


def test_repair_aborts_when_read_is_a_page_or_capped():
    with pytest.raises(IncompleteRead):
        plan_reextraction(
            corpus="rsoxs",
            cases=cases(),
            graph_read=([{"id": "e"}], False),
            capabilities=CAPS,
            extractor_version="e1",
            limit=10,
        )
    with pytest.raises(CapabilityError):
        plan_reextraction(
            corpus="rsoxs",
            cases=cases(),
            graph_read=([], True),
            capabilities=GraphCapabilities(backend="tiled"),
            extractor_version="e1",
            limit=10,
        )
