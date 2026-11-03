"""Handlers of the paper job kinds (B5/J-11): proposals out, units settled, stop and resume honoured, no network."""
import pytest
from tests.jobs.paper_handler_fakes import (
    FakeCatalog, FakeCorpus, FakeParser, FakeSource, FakeStage, FakeWorks, RecordingSink, work,
)

from app.modules.contracts.context import LOCAL_OPERATOR
from app.modules.curation.models import CaseState
from app.modules.curation.store import CurationStore
from app.modules.jobs.paper_handlers import (
    PROPOSAL_CASE_TYPE, CurationProposalSink, EnrichmentConfig, EnrichmentHandler, PdfParseHandler, Proposal,
    TopicSweepHandler, build_paper_handlers, build_record, condition_bin, link_confidence,
    material_names_from_metadata,
)
from app.modules.jobs.paper_jobs import ENRICHMENT_KIND, PDF_PARSE_KIND, TOPIC_SWEEP_KIND, JobControl
from app.modules.jobs.pipeline import JobStopped

CTX = LOCAL_OPERATOR
VOCAB = {"pm6": ("pm6", "PM6"), "y6": ("y6", "Y6")}


def match(name, category):
    return VOCAB.get(name.strip().lower())


def meta(**kw):
    base = {"sample_name": "S1", "beamline": "bl1", "technique": "GIWAXS", "material": "PM6",
            "Acceptor": "Y6/Mystery", "incident_angle_deg": 0.12, "AnnealingTemp": "150C", "energy_keV": 12}
    base.update(kw)
    return base


def rows():
    return [("/a/one", meta()), ("/a/two", meta(beamline="bl2", incident_angle_deg=0.14, AnnealingTemp="153")),
            ("/a/three", meta(sample_name="other", beamline="bl1", energy_keV=14, material="", Acceptor="")),
            ("/a/bare", {"note": "nothing to link on"})]


def control(stage=None):
    stage = stage or FakeStage()
    return JobControl(stage), stage  # type: ignore[arg-type]


def enrichment(sink, *, works=None, corpus=None, tanimoto=None, catalog=None):
    return EnrichmentHandler(catalog or FakeCatalog(rows()), sink, vocabulary_match=match, works=works, corpus=corpus,
                             tanimoto=tanimoto, config=EnrichmentConfig(max_pubs_per_material=2))


def test_record_helpers_port_the_donor_rules():
    assert material_names_from_metadata(meta()) == ["PM6", "Y6", "Mystery"]          # stack split, primary first
    assert build_record("/x", {"note": "n"}) is None
    assert build_record("/x", meta())["energy_keV"] == 12
    assert condition_bin("150 C", 10.0) == condition_bin(150, 10.0) == "150.00"
    assert condition_bin("n/a", 10.0) is None
    conf, signals = link_confidence({"title": "PM6 films", "abstract": "pm6", "concepts": [{"name": "giwaxs"}]},
                                    "PM6", "GIWAXS")
    assert conf == 1.0 and set(signals) == {"baseline", "title_match", "abstract_match", "concept_technique_match"}


def test_enrichment_proposes_links_and_never_names_a_graph_write():
    sink = RecordingSink()
    works = FakeWorks({"PM6 GIWAXS": [work("W1", "PM6 films", doi="10.1/a", refs=("W2",), concepts=("GIWAXS",)),
                                      work("W2", "Other", doi="10.1/b")]})
    corpus = FakeCorpus([{"id": "publication:W9", "doi": "10.1/known", "openalex_id": "W9", "referenced_works": []}])
    ctl, stage = control()
    result = dict(enrichment(sink, works=works, corpus=corpus)(CTX, {"corpus": "rsoxs"}, ctl))
    assert result["datasets_scanned"] == 3 and result["incomplete"] == []
    assert result["material_names_unmatched"] == 2                   # "Mystery" twice: vocabulary only, never minted
    assert set(sink.kinds()) >= {"dataset_material_links", "cross_beamline", "similar_conditions",
                                 "publication_links", "citations"}
    material = next(p for p in sink.proposals if p.kind == "dataset_material_links" and p.key == "pm6")
    roles = {e["properties"]["role"] for e in material.content["edges"]}
    assert roles == {"named"} and len(material.content["edges"]) == 2
    publications = next(p for p in sink.proposals if p.kind == "publication_links")
    edges = {(e["relation_type"], e["target"]) for e in publications.content["edges"]}
    assert ("published_in", "publication:W1") in edges and ("authored_by", "author:A1") in edges
    cites = next(p for p in sink.proposals if p.kind == "citations")
    assert [(s["subject"], s["object"]) for s in cites.content["statements"]] == [("publication:W1", "publication:W2")]
    assert result["proposals"] == len(sink.proposals) and "chemical_similarity" in result  # skipped, said so


def test_enrichment_skips_papers_already_in_the_corpus_and_reports_quota_pause():
    sink = RecordingSink()
    works = FakeWorks({"PM6 GIWAXS": [work("W1", "PM6", doi="10.1/Known")]})
    works.paused_for = {"Y6 GIWAXS"}
    corpus = FakeCorpus([{"id": "publication:W5", "doi": "10.1/known", "referenced_works": []}])
    ctl, stage = control()
    result = dict(enrichment(sink, works=works, corpus=corpus)(CTX, {"corpus": "rsoxs"}, ctl))
    assert result["publications_skipped_existing"] == 1 and "publication_links" not in sink.kinds()
    assert result["openalex_quota_paused_until"].startswith("2030")
    assert all(u.startswith("publications:") for u in result["incomplete"]) and len(result["incomplete"]) >= 1


def test_enrichment_chemical_similarity_uses_the_injected_function():
    sink = RecordingSink()
    catalog = FakeCatalog([("/a", meta(thinfilm_OrganicSalt_SMILES="CCO")),
                           ("/b", meta(thinfilm_OrganicSalt_SMILES="CCC")),
                           ("/c", meta(thinfilm_OrganicSalt_SMILES="c1ccccc1"))])
    scores = {frozenset(("CCC", "CCO")): 0.8}
    ctl, _ = control()
    result = dict(enrichment(sink, catalog=catalog, tanimoto=lambda a, b: scores.get(frozenset((a, b)), 0.1))(
        CTX, {"corpus": "rsoxs", "limit": 10, "entity_ids": ["x"]}, ctl))
    assert result["chem_edges"] == 1 and catalog.calls == [("rsoxs", ["x"], 10)]


def test_stop_between_units_keeps_settled_units_and_resume_skips_them():
    works = FakeWorks({"PM6 GIWAXS": [work("W1", "PM6 films", doi="10.1/a")]})
    stage = FakeStage(stop_after_guards=6)
    with pytest.raises(JobStopped):
        enrichment(RecordingSink(), works=works)(CTX, {"corpus": "rsoxs"}, JobControl(stage))  # type: ignore[arg-type]
    assert stage.units and not stage.failed                    # a stop is not a failed unit
    settled = set(stage.units)
    stage.stop_after_guards = None
    sink2 = RecordingSink()
    result = dict(enrichment(sink2, works=works)(CTX, {"corpus": "rsoxs"}, JobControl(stage)))  # type: ignore[arg-type]
    assert result["units_skipped_settled"] == len(settled)
    already = sum(stage.units[u][1].get("proposals", 0) for u in settled)
    assert result["proposals"] == len(sink2.proposals) + already   # settled units proposed nothing again


def test_topic_sweep_proposes_papers_authors_and_citations_per_topic():
    sink = RecordingSink()
    works = FakeWorks({"gyroid": [work("W1", "G1", doi="10.1/g1", authors=(("A1", "Ann"), ("A2", "Bo")), refs=("W2",)),
                                  work("W2", "G2", doi="10.1/g2", authors=(("A1", "Ann"),))],
                       "empty": []})
    ctl, _ = control()
    result = dict(TopicSweepHandler(works, sink)(
        CTX, {"corpus": "rsoxs", "topics": ["gyroid", "empty", "  "], "limit": 500, "year_from": 2015}, ctl))
    assert result["papers_added"] == 2 and result["authors_added"] == 2 and result["cites_added"] == 1
    assert result["incomplete"] == []
    paper = next(p for p in sink.proposals if p.kind == "publication")
    nodes = {n["id"]: n for n in paper.content["nodes"]}
    assert set(nodes) == {"publication:W1", "publication:W2", "author:A1", "author:A2"}
    assert nodes["publication:W1"]["properties"]["ingested_via"] == "topic_sweep:gyroid"
    assert works.calls[0] == ("gyroid", 50, 1)                       # page size capped at the OpenAlex maximum


def test_topic_sweep_caps_papers_and_stops_on_quota_pause():
    many = [work(f"W{i}", f"T{i}", doi=f"10.1/{i}") for i in range(300)]
    works = FakeWorks({"big": many, "later": [work("W999", "L", doi="10.1/l")]})
    works.paused_for = {"later"}
    ctl, _ = control()
    result = dict(TopicSweepHandler(works, RecordingSink())(
        CTX, {"corpus": "rsoxs", "topics": ["big", "later"], "limit": 100}, ctl))
    assert result["papers_added"] == 100                                # per-topic cap
    assert result["openalex_quota_paused_until"] and len(result["incomplete"]) == 1


def pdf_handler(source=None, parser=None, sink=None, **kw):
    corpus = FakeCorpus([
        {"id": "publication:A", "doi": "10.1000/a", "citations": 5},
        {"id": "publication:B", "doi": "10.1000/b", "citations": 50},
        {"id": "publication:K", "doi": "10.1000/known.1", "citations": 1},
        {"id": "publication:N", "doi": "", "citations": 99},
    ])
    return PdfParseHandler(corpus, source or FakeSource(), parser or FakeParser(), sink or RecordingSink(), **kw)


def test_pdf_parse_orders_by_citations_proposes_sections_figures_and_fails_closed_on_licence():
    sink, source, parser = RecordingSink(), FakeSource(), FakeParser()
    ctl, _ = control()
    result = dict(pdf_handler(source, parser, sink)(CTX, {"corpus": "rsoxs", "limit": 2}, ctl))
    assert source.calls == ["10.1000/b", "10.1000/a"]                  # most cited first, DOI-less excluded, capped
    assert result["sections_created"] == 2 and result["figures_created"] == 2 and result["parse_failures"] == 0
    parse = next(p for p in sink.proposals if p.kind == "paper_parse" and p.subject == "publication:B")
    figure = next(n for n in parse.content["nodes"] if n["entity_type"] == "paper_figure")
    assert figure["licence_status"] == "unknown" and figure["licence_source"] == "unavailable"
    assert {e["relation_type"] for e in parse.content["edges"]} == {"has_section", "has_figure"}
    section = next(n for n in parse.content["nodes"] if n["entity_type"] == "paper_section")
    assert section["parser_version"] == "1" and "text" not in section and len(section["text_sha256"]) == 64


def test_pdf_parse_turns_reference_list_dois_into_resolved_citation_proposals_only():
    sink = RecordingSink()
    ctl, _ = control()
    pdf_handler(sink=sink)(CTX, {"corpus": "rsoxs", "paper_ids": ["publication:A"]}, ctl)
    cites = next(p for p in sink.proposals if p.kind == "citations")
    assert [s["object"] for s in cites.content["statements"]] == ["publication:K"]   # the unknown DOI is not minted


def test_pdf_parse_reports_why_each_paper_failed_and_resume_retries_only_failures():
    source, parser, sink = FakeSource(missing={"10.1000/a"}), FakeParser(fail={"10.1000/known.1"}), RecordingSink()
    stage = FakeStage()
    params = {"corpus": "rsoxs", "limit": 10}
    result = dict(pdf_handler(source, parser, sink)(CTX, params, JobControl(stage)))  # type: ignore[arg-type]
    assert result["parse_failures"] == 2 and len(result["incomplete"]) == 2
    reasons = {r["doi"]: r["reason"] for r in result["failure_reasons"]}
    assert reasons == {"10.1000/a": "no_oa_pdf", "10.1000/known.1": "parse_error"}
    source2, parser2 = FakeSource(), FakeParser()
    result2 = dict(pdf_handler(source2, parser2, RecordingSink())(CTX, params, JobControl(stage)))  # type: ignore[arg-type]
    assert sorted(source2.calls) == ["10.1000/a", "10.1000/known.1"]    # the settled paper (b) is not re-fetched
    assert result2["incomplete"] == [] and result2["units_skipped_settled"] == 1


def test_pdf_parse_force_reparses_and_paper_ids_restrict():
    parser = FakeParser()
    ctl, _ = control()
    pdf_handler(parser=parser)(CTX, {"corpus": "rsoxs", "paper_ids": ["publication:A"], "force": True}, ctl)
    assert parser.calls == [("sha256:10.1000/a", True)]


def test_build_paper_handlers_registers_only_kinds_with_all_their_ports():
    sink = RecordingSink()
    assert build_paper_handlers(sink=sink) == {}
    assert set(build_paper_handlers(sink=sink, works=FakeWorks())) == {TOPIC_SWEEP_KIND}
    full = build_paper_handlers(sink=sink, works=FakeWorks(), catalog=FakeCatalog([]), vocabulary_match=match,
                                corpus=FakeCorpus([]), source=FakeSource(), parser=FakeParser())
    assert set(full) == {ENRICHMENT_KIND, TOPIC_SWEEP_KIND, PDF_PARSE_KIND}


def test_curation_sink_queues_for_review_dedupes_and_resets_on_change():
    store = CurationStore(":memory:")
    sink = CurationProposalSink(store)
    proposal = Proposal("publication", "k1", "topic:x", "2 papers", {"nodes": [{"id": "publication:W1"}]})
    assert sink.propose(CTX, "rsoxs", proposal) is True
    assert sink.propose(CTX, "rsoxs", proposal) is False
    (case,) = store.list_cases(["rsoxs"], state=CaseState.NEEDS_REVIEW.value)
    assert case["case_type"] == PROPOSAL_CASE_TYPE and case["route"] == "review"
    assert case["payload"] == {"proposal_kind": "publication"} and "publication:W1" in case["evidence"]
    assert store.list_cases(["other"]) == []                              # corpus scoped
    decided = store.apply_decision(case["id"], ["rsoxs"], bound_revision=case["revision"], accept=True,
                                   resolution={"ok": True}, reviewer="curator")
    assert decided["state"] == "resolved"
    changed = Proposal("publication", "k1", "topic:x", "3 papers", {"nodes": [{"id": "publication:W1"}, {"id": "x"}]})
    assert sink.propose(CTX, "rsoxs", changed) is True                    # new evidence: decision invalidated
    assert store.get_case(case["id"], ["rsoxs"])["state"] == "needs_review"
