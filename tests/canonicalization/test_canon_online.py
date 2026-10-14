"""T07 online resolution: approved aliases, negative controls, bounds, retained mentions, no-index."""
from datetime import datetime, timezone

import numpy as np
import pytest

from app.modules.canonicalization import gates
from app.modules.canonicalization.adjudication import ChatJudge, adjudicate_mention, parse_json_object
from app.modules.canonicalization.candidate_search import (
    HARD_MAX_CANDIDATES, CandidateSearch, IndexedEntity, estimate_tokens)
from app.modules.canonicalization.chemical_identity import chemical_verdict, formula_counts
from app.modules.canonicalization.embeddings import (
    DeterministicEncoder, EmbeddingCache, EmbeddingUnavailable, embed_texts_cached)
from app.modules.canonicalization.measurement_fixture import (
    MENTIONS, NAMED_NEGATIVE_CONTROLS, approved_alias_vocabulary, entities)
from app.modules.canonicalization.merge_safety import check_merge, may_merge
from app.modules.canonicalization.online import ACCEPTED, REVIEW, UNRESOLVED, MutableBudget, SourceMention, resolve_mention
from app.modules.canonicalization.vocabulary import VocabularyApproval, entity_slug, load_vocabulary_asset
from app.modules.extraction.contract import ExtractedMention


def approved():
    cand = approved_alias_vocabulary()
    return cand.approve(VocabularyApproval("a1", cand.revision, "owner", datetime.now(timezone.utc)))


@pytest.mark.parametrize("canonical,alias", NAMED_NEGATIVE_CONTROLS)
def test_named_negative_controls_never_merge_either_direction(canonical, alias):
    assert not check_merge(canonical, alias).allowed
    assert not check_merge(alias, canonical).allowed


@pytest.mark.parametrize("canonical,alias", [("GIWAXS", "GI-WAXS"), ("Soxhlet", "Sohxlet"), ("MoTe2", "mote2")])
def test_spelling_variants_pass(canonical, alias):
    assert check_merge(canonical, alias).allowed


def test_different_classes_never_merge_even_when_one_inherits_the_other():
    verdict = check_merge("RSoXS", "RSoXS", canonical_class="ExperimentalTechnique", alias_class="RSoXSMeasurement")
    assert not verdict.allowed and "different schema classes" in verdict.reason


def test_formula_helpers():
    assert formula_counts("(NH4)2SO4") == {"N": 2, "H": 8, "S": 1, "O": 4}
    assert chemical_verdict("chloroform", "solvent") is None
    assert may_merge("SEM", "TEM").allowed is False


def test_candidate_vocabulary_is_not_authoritative_and_approved_alias_resolves():
    candidate = approved_alias_vocabulary()
    assert candidate.status == "candidate"
    m = SourceMention("wide-angle scattering at grazing incidence", "technique", work_id="w1")
    res = resolve_mention(m, vocabulary=candidate)
    assert res.status == REVIEW and res.method == "candidate_alias"
    res = resolve_mention(m, vocabulary=approved())
    assert (res.status, res.entity_id, res.method) == (ACCEPTED, "t:giwaxs", "approved_alias")
    assert res.mention is m and res.is_proposal_only


def test_approval_must_name_the_exact_revision():
    cand = approved_alias_vocabulary()
    with pytest.raises(ValueError):
        cand.approve(VocabularyApproval("a", "0" * 64, "owner", datetime.now(timezone.utc)))


def test_donor_yaml_loads_only_as_candidate(tmp_path):
    f = tmp_path / "vocabulary.yaml"
    f.write_text("version: 3\nentries:\n  - {slug: rsoxs, display_name: RSoXS, category: technique, aliases: [r-soxs]}\n")
    vocab = load_vocabulary_asset(f)
    assert vocab.status == "candidate" and "sha256:" in vocab.source
    assert vocab.match("R-SoXS").slug == "rsoxs" and entity_slug("R-SoXS") == "r_soxs"


def test_alias_collision_is_an_error(tmp_path):
    f = tmp_path / "v.yaml"
    f.write_text("entries:\n  - {slug: a, category: technique, aliases: [x y]}\n  - {slug: b, category: technique, aliases: [x-y]}\n")
    with pytest.raises(ValueError, match="claimed by both"):
        load_vocabulary_asset(f)


def test_identifier_resolution_and_source_mention_retained():
    ext = ExtractedMention("benzene", "ChemicalEntity", attributes={"chebi": "CHEBI:16716"})
    m = SourceMention.from_extracted(ext, "work-9")
    assert ("chebi", "CHEBI:16716") in m.identifiers and m.work_id == "work-9"
    res = resolve_mention(m, identifier_index={("chebi", "CHEBI:16716"): "m:benzene"})
    assert res.entity_id == "m:benzene" and res.method == "identifier" and res.mention.surface == "benzene"


def search(**kw):
    return CandidateSearch(entities(), encoder=DeterministicEncoder(), model="det", **kw)


def judge_picking(entity_id):
    import json
    return lambda prompt: json.dumps({"entity_id": entity_id, "confidence": 0.99, "reason": "x"})


@pytest.mark.parametrize("m", [m for m in MENTIONS if m.kind == "negative_control"], ids=lambda m: m.surface)
def test_negative_controls_never_accepted_even_if_the_judge_says_yes(m):
    """The judge is forced to pick the most similar shortlist entry; the safety check still refuses."""
    s = search()
    short = s.search(m.surface, m.category)
    for cand in short.candidates:
        res = resolve_mention(SourceMention(m.surface, m.category), search=s, judge=judge_picking(cand.entity_id))
        assert res.status != ACCEPTED, (m.surface, cand.label)


def test_unresolved_terms_stay_visible_with_reason_and_shortlist():
    res = resolve_mention(SourceMention("spin coating", "technique"), search=search(), judge=None)
    assert res.status == UNRESOLVED and res.mention.surface == "spin coating"
    res = resolve_mention(SourceMention("GIWAXS", "technique"), search=search(), judge=None)
    assert res.status == UNRESOLVED and res.method == "needs_adjudication" and res.shortlist.candidates


def test_adjudication_budget_and_judge_fault_do_not_merge():
    s = search()
    budget = MutableBudget(0)
    res = resolve_mention(SourceMention("GIWAXS", "technique"), search=s, judge=judge_picking("t:giwaxs"), budget=budget)
    assert res.status == UNRESOLVED and "budget" in res.reason

    def boom(prompt):
        raise RuntimeError("down")

    res = resolve_mention(SourceMention("GIWAXS", "technique"), search=s, judge=boom)
    assert res.status == UNRESOLVED and "judge_failed" in res.reason


def test_judge_may_only_choose_offered_ids():
    short = search().search("GIWAXS", "technique")
    v = adjudicate_mention("GIWAXS", "technique", short, judge=judge_picking("t:not_offered"))
    assert v.status == "not_offered"
    assert adjudicate_mention("GIWAXS", "technique", short, judge=lambda p: "garbage").status == "unparseable"
    assert parse_json_object('prose {"a": 1} tail') == {"a": 1}


def test_accepted_adjudicated_mapping_for_true_merge():
    s = search()
    res = resolve_mention(SourceMention("GIWAXS", "technique"), search=s, judge=judge_picking("t:giwaxs"))
    assert res.status == ACCEPTED and res.entity_id == "t:giwaxs" and res.method == "adjudicated"


def test_candidates_and_prompts_are_bounded():
    many = [IndexedEntity(f"e{i}", f"wide angle scattering study {i}", "technique") for i in range(500)]
    s = CandidateSearch(many, encoder=DeterministicEncoder(), model="det", max_candidates=10_000, token_budget=60)
    assert s.max_candidates == HARD_MAX_CANDIDATES
    short = s.search("wide angle scattering study", "technique")
    assert short.truncated and short.dropped > 0
    assert short.token_estimate <= 60 and len(short.candidates) <= HARD_MAX_CANDIDATES
    s2 = CandidateSearch(many, encoder=DeterministicEncoder(), model="det", max_candidates=5)
    assert len(s2.search("wide angle scattering study 7", "technique").candidates) == 5


def test_shortlist_is_typed_by_category():
    short = search().search("TEM", "material")
    assert all(c.category == "material" for c in short.candidates)


def test_unavailable_encoder_is_reported_not_empty_success():
    def broken(texts):
        raise RuntimeError("weights missing")

    s = CandidateSearch(entities(), encoder=broken, model="x")
    short = s.search("spin coating", "technique")
    assert short.status == "index_unavailable" and "embedding_unavailable" in short.degraded
    # lexical channels still work without the encoder
    assert s.search("GI-WAXS", "technique").candidates[0].entity_id == "t:giwaxs"
    res = resolve_mention(SourceMention("spin coating", "technique"), search=s)
    assert res.status == UNRESOLVED and "unavailable" in res.reason


def test_embedding_cache_and_failure_modes():
    calls = []

    def enc(texts):
        calls.append(list(texts))
        return np.ones((len(texts), 4), dtype=np.float32)

    cache = EmbeddingCache()
    a = embed_texts_cached(["a", "b"], enc, model="m", revision="1", cache=cache)
    b = embed_texts_cached(["a", "b", "c"], enc, model="m", revision="1", cache=cache)
    assert (a.computed, b.cache_hits, b.computed) == (2, 2, 1)
    embed_texts_cached(["a"], enc, model="m", revision="2", cache=cache)  # new revision is a new key
    assert len(calls) == 3
    with pytest.raises(ValueError):
        embed_texts_cached(["a", "b"], enc, model="m", max_texts=1)
    with pytest.raises(EmbeddingUnavailable):
        embed_texts_cached(["a"], lambda t: np.ones((2, 2)), model="m")


def test_chat_judge_uses_existing_client_signature_and_has_no_default():
    class Client:
        def chat(self, prompt, *, temperature=0.0, timeout=240):
            return '{"entity_id": null}'

    assert ChatJudge(Client())("p") == '{"entity_id": null}'
    with pytest.raises(ValueError):
        ChatJudge(None)


def test_estimate_tokens():
    assert estimate_tokens("x" * 8) == 2


def test_importing_package_has_no_side_effects_and_gates_closed():
    assert not any(gates.CANONICALIZATION_GATES.values())
