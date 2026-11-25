"""What a rejected model sentence was: classified and sampled for the run log (plecos run 2026-11-25: 42% were rejected)."""
from __future__ import annotations

from types import SimpleNamespace

from app.modules.term_extractor import passages as P
from app.modules.term_extractor.store import TermStore
from app.modules.term_extractor.schema import SchemaHelper
from app.modules.term_extractor.tools import ToolState, build_tools

PAGE = ("Hypostomus ancistroides has a diploid number of 2n = 74 chromosomes. The karyotype of Ancistrus sp. includes "
        "metacentric and submetacentric pairs, with a fundamental number of 108.")


def test_a_rejected_sentence_is_classified_by_how_much_of_it_is_on_the_page():
    pages = [PAGE]
    near = "Hypostomus ancistroides has a diploid number of 2n equal 74 chromosome."          # words present, wording differs
    table = "Ancistrus 2n 74 FN 108 m sm st a 12 8 4 20"                                       # a table row written out
    partial = "Ancistrus sp. has metacentric and submetacentric pairs with large heterochromatic blocks and a fundamental number"
    absent = "Catfish are widely kept as aquarium pets because they eat algae."
    assert P.classify_rejection(near, pages)[0] == P.REJECT_NEAR_MATCH
    assert P.classify_rejection(table, pages)[0] == P.REJECT_TABLE
    assert P.classify_rejection(partial, pages)[0] == P.REJECT_PARTIAL
    assert P.classify_rejection(absent, pages) == (P.REJECT_ABSENT, 1) or P.classify_rejection(absent, pages)[0] == P.REJECT_ABSENT
    assert P.classify_rejection("", pages) == (P.REJECT_ABSENT, None)
    assert P.classify_rejection(near, ["x " * 50, PAGE])[1] == 2                              # the page of the best overlap


def test_the_store_counts_by_class_and_keeps_a_bounded_spread_sample(tmp_path):
    store = TermStore(str(tmp_path / "terms.json"))
    for i in range(30):
        store.note_rejection("a.pdf", P.REJECT_NEAR_MATCH, f"sentence number {i} " + "x" * 400, 3)
    store.note_rejection("b.pdf", P.REJECT_TABLE, "Ancistrus 2n 74", 5)
    report = store.rejection_report()
    assert report["by_reason"] == {P.REJECT_NEAR_MATCH: 30, P.REJECT_TABLE: 1}
    assert report["by_paper"] == {"a.pdf": {P.REJECT_NEAR_MATCH: 30}, "b.pdf": {P.REJECT_TABLE: 1}}
    assert len(report["samples"]) == store.REJECT_SAMPLE_PER_CLASS + 1   # six of the common class, and the rare one
    assert all(len(item["text"]) <= 300 for item in report["samples"])
    assert TermStore(str(tmp_path / "t2.json")).rejection_report() == {}


def test_register_term_records_the_class_of_a_rejected_sentence(tmp_path):
    store = TermStore(str(tmp_path / "terms.json"))
    state = ToolState(store=store, schema=SchemaHelper("storage/schema/v0.3.1/matkg_schema.yaml"),
                      services=SimpleNamespace(chebi_lookup=None))
    tools = {t.name: t for t in build_tools(state)}
    state.paper_pages = ("p.pdf", [PAGE])
    state.set_page_context("p.pdf", 1, PAGE)
    tools["register_term"].invoke({"term": "karyotype", "definition": "The chromosome set.", "category": "Thing",
                                   "context": "Hypostomus ancistroides has a diploid number of 2n equal 74 chromosome."})
    report = store.rejection_report()
    assert report["by_reason"] == {P.REJECT_NEAR_MATCH: 1}
    assert report["samples"][0]["paper"] == "p.pdf" and report["samples"][0]["page"] == 1
