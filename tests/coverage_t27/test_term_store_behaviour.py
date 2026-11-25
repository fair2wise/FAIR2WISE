"""TermStore: JSON persistence round trips, upsert merge rules, snippets, provenance and importance tiers."""

from __future__ import annotations

import datetime
import json
import logging
import threading

import pytest

from app.modules.term_extractor.models import ContextSnippet, PropertyRecord, RelationRecord, TermRecord
from app.modules.term_extractor.store import TermStore


def rec(term="Alpha", **kw):
    kw.setdefault("definition", "d")
    kw.setdefault("category", "Thing")
    return TermRecord(term=term, **kw)


def snip(code="x = 1", paper="p1.pdf", page=1, **kw):
    return {"source_paper": paper, "page": page, "code_snippet": code, **kw}


@pytest.fixture
def path(tmp_path):
    return tmp_path / "nested" / "dir" / "terms.json"


def test_init_creates_parent_dir_and_starts_empty(path):
    store = TermStore(str(path))
    assert path.parent.is_dir() and not path.exists()
    assert len(store) == 0 and store.all_records() == [] and store.metadata["version"] == "2.1"


def test_save_then_reload_round_trips_terms_snippets_and_metadata(path):
    store = TermStore(str(path))
    store.upsert(rec("Alpha", pages=[1], source_papers=["p1.pdf"], formula="H2O"))
    store.add_code_snippets([snip()], {"paper_title": "T", "doi": "10.1/x"})
    store.increment("processed_files", 3)
    store.save()
    data = json.loads(path.read_text())
    assert [t["term"] for t in data["terms"]] == ["Alpha"] and data["metadata"]["processed_files"] == 3
    again = TermStore(str(path))
    assert again.get("alpha").formula == "H2O"
    assert again.all_display_names() == ["Alpha"]
    assert again.metadata["processed_files"] == 3
    assert again.code_snippets[0]["paper_title"] == "T"
    # reloaded snippet keys feed the dedup set
    assert again.add_code_snippets([snip()], None) is False


def test_load_skips_unreadable_records_and_keeps_good_ones(path, caplog):
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"terms": [{"term": "Good"}, {"definition": "no term"}, "not-a-dict"]}))
    with caplog.at_level(logging.WARNING):
        store = TermStore(str(path))
    assert [r.term for r in store.all_records()] == ["Good"]
    assert "Skipped 2 unreadable" in caplog.text


def test_load_collision_later_record_wins_and_null_metadata_keeps_default(path, caplog):
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "terms": [{"term": "Beta", "definition": "first"}, {"term": " beta ", "definition": "second"}],
        "metadata": {"version": None, "processed_files": 7},
    }))
    with caplog.at_level(logging.WARNING):
        store = TermStore(str(path))
    assert len(store) == 1 and store.get("beta").definition == "second"
    assert "collides" in caplog.text
    assert store.metadata["version"] == "2.1" and store.metadata["processed_files"] == 7


def test_corrupt_file_is_tolerated_and_logged(path, caplog):
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    with caplog.at_level(logging.WARNING):
        store = TermStore(str(path))
    assert len(store) == 0 and "Could not load previous terms" in caplog.text


def test_save_failure_is_logged_not_raised(tmp_path, caplog):
    store = TermStore(str(tmp_path / "terms.json"))
    store.output_file = str(tmp_path / "missing_dir" / "terms.json")
    with caplog.at_level(logging.ERROR):
        store.save()
    assert "Failed to save terms" in caplog.text


def test_save_derives_publications_from_source_metadata(path):
    store = TermStore(str(path))
    store.upsert(rec("A", source_metadata={"p.pdf": {"doi": "10.1/a", "journal": ""}}))
    store.add_code_snippets([snip(paper="p.pdf")], {"doi": "10.1/a"})
    store.save()
    data = json.loads(path.read_text())
    assert data["terms"][0]["publications"] == [{"source_paper": "p.pdf", "doi": "10.1/a"}]
    assert data["code_snippets"][0]["publications"] == [{"source_paper": "p.pdf", "doi": "10.1/a"}]


def test_normalize_and_lookup(path):
    store = TermStore(str(path))
    assert TermStore.normalize("  MiXed Case ") == "mixed case"
    store.upsert(rec("MiXed Case"))
    assert store.get("mixed case").term == "MiXed Case" and store.get("nope") is None


def test_upsert_new_record_flags_update_once(path):
    store = TermStore(str(path))
    key, modified = store.upsert(rec("Alpha"))
    assert (key, modified) == ("alpha", True)
    assert store.consume_updated() is True and store.consume_updated() is False
    assert store.upsert(rec("Alpha")) == ("alpha", False)
    assert store.consume_updated() is False


def test_upsert_merges_every_field_class(path):
    store = TermStore(str(path))
    store.upsert(rec(
        "Alpha", pages=[1], source_papers=["a.pdf"], definition="short",
        context_snippets=[ContextSnippet("t", "a.pdf", 1)],
        relations=[RelationRecord("is_a", "Beta")],
    ))
    store.consume_updated()
    incoming = rec(
        "alpha", pages=[1, 2], source_papers=["a.pdf", "b.pdf"], definition="a much longer definition",
        raw_category="Raw", formula="H2O", formula_validation={"status": "ok"}, chebi={"id": 1}, smiles="O",
        charge=0, inchi="I", inchikey="K", mass=18.0,
        context_snippets=[ContextSnippet("dup", "a.pdf", 1), ContextSnippet("new", "b.pdf", 2)],
        relations=[RelationRecord("is_a", "Beta"), RelationRecord("part_of", "Gamma")],
        source_metadata={"b.pdf": {"doi": "10.1/b"}},
        publications=[{"source_paper": "c.pdf", "doi": "10.1/c"}, {"source_paper": ""}, "junk"],
    )
    key, modified = store.upsert(incoming)
    got = store.get(key)
    assert modified is True and store.consume_updated() is True
    assert got.term == "Alpha"  # first display name kept
    assert got.pages == [1, 2] and got.source_papers == ["a.pdf", "b.pdf"]
    assert got.definition == "a much longer definition" and got.raw_category == "Raw"
    # a second passage of the same paper and page is kept (union by paper, page and text; pilot 2026-11-24)
    assert [(c.text, c.page) for c in got.context_snippets] == [("t", 1), ("dup", 1), ("new", 2)]
    assert [(r.relation, r.related_term) for r in got.relations] == [("is_a", "Beta"), ("part_of", "Gamma")]
    assert (got.formula, got.smiles, got.charge, got.inchi, got.inchikey, got.mass) == ("H2O", "O", 0, "I", "K", 18.0)
    assert got.chebi == {"id": 1} and got.formula_validation == {"status": "ok"}
    assert got.source_metadata == {"b.pdf": {"doi": "10.1/b"}}
    assert [p["source_paper"] for p in got.publications] == ["b.pdf", "c.pdf"]


def test_upsert_chemical_fields_fill_only_when_absent_and_shorter_definition_loses(path):
    store = TermStore(str(path))
    store.upsert(rec("Alpha", definition="the long definition", formula="CO2", raw_category="One"))
    key, modified = store.upsert(rec("Alpha", definition="x", formula="H2O", raw_category="Two"))
    got = store.get(key)
    assert modified is False
    assert got.formula == "CO2" and got.definition == "the long definition" and got.raw_category == "One"


def test_attach_property_dedupes_and_ignores_unknown_term(path):
    store = TermStore(str(path))
    store.upsert(rec("Alpha"))
    store.consume_updated()
    prop = PropertyRecord("gap", 1.1, "eV", "ctx")
    assert store.attach_property("missing", prop) is False
    assert store.attach_property("alpha", prop) is True and store.consume_updated() is True
    assert store.attach_property("alpha", PropertyRecord("gap", 1.1, "eV", "ctx")) is False
    assert store.attach_property("alpha", PropertyRecord("gap", 1.2, "eV", "ctx")) is True
    assert [p.value for p in store.get("alpha").properties] == [1.1, 1.2]


def test_add_code_snippets_stamps_pdf_metadata_and_dedupes(path):
    store = TermStore(str(path))
    assert store.add_code_snippets([], {"doi": "x"}) is False
    meta = {"paper_title": "Title", "doi": "10.1/x", "authors": ["A"], "publication_year": 2020}
    assert store.add_code_snippets([snip(paper="a.pdf")], meta) is True and store.consume_updated() is True
    stored = store.code_snippets[0]
    assert (stored["paper_title"], stored["doi"], stored["paper_authors"], stored["publication_year"]) == (
        "Title", "10.1/x", ["A"], 2020)
    assert stored["publications"][0]["source_paper"] == "a.pdf"
    # same snippet again with no new metadata: no change
    assert store.add_code_snippets([snip(paper="a.pdf")], meta) is False
    assert len(store.code_snippets) == 1
    # same snippet with extra metadata: merged in place and reported as updated
    assert store.add_code_snippets([snip(paper="a.pdf")], {"journal": "J"}) is True
    assert store.code_snippets[0]["source_metadata"]["a.pdf"]["journal"] == "J"
    assert store.code_snippets[0]["publications"][0]["journal"] == "J"


def test_add_code_snippets_keeps_existing_fields_and_defaults_without_meta(path):
    store = TermStore(str(path))
    s = snip(paper="a.pdf", paper_title="Own", doi="10.9/own", paper_authors=["Z"], publication_year=1999)
    store.add_code_snippets([s], {"paper_title": "Other", "doi": "10.1/x"})
    got = store.code_snippets[0]
    assert (got["paper_title"], got["doi"], got["paper_authors"], got["publication_year"]) == (
        "Own", "10.9/own", ["Z"], 1999)
    store.add_code_snippets([snip(code="y = 2", paper="a.pdf")], None)
    bare = store.code_snippets[1]
    assert bare["paper_title"] is None and bare["paper_authors"] == [] and bare["publications"] == []


def test_stamp_source_metadata_touches_only_citing_terms_and_snippets(path):
    store = TermStore(str(path))
    store.upsert(rec("Cites", source_papers=["a.pdf"]))
    store.upsert(rec("Other", source_papers=["b.pdf"]))
    store.add_code_snippets([snip(paper="a.pdf"), snip(code="z", paper="b.pdf")], None)
    store.consume_updated()
    assert store.stamp_source_metadata("", {"doi": "x"}) is False
    assert store.stamp_source_metadata("a.pdf", None) is False
    assert store.stamp_source_metadata("a.pdf", {"doi": "10.1/a"}) is True and store.consume_updated() is True
    assert store.get("cites").publications == [{"source_paper": "a.pdf", "doi": "10.1/a"}]
    assert store.get("other").source_metadata == {} and store.get("other").publications == []
    by_paper = {s["source_paper"]: s for s in store.code_snippets}
    assert by_paper["a.pdf"]["publications"][0]["doi"] == "10.1/a"
    assert by_paper["b.pdf"]["publications"] == []
    assert store.stamp_source_metadata("a.pdf", {"doi": "10.1/a"}) is False  # idempotent


def test_consume_updated_is_thread_local(path):
    store = TermStore(str(path))
    seen = {}

    def worker():
        store.upsert(rec("FromThread"))
        seen["worker"] = store.consume_updated()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen["worker"] is True and store.consume_updated() is False and len(store) == 1


def test_increment_initialises_missing_and_none_counters(path):
    store = TermStore(str(path))
    store.increment("fresh")
    store.increment("fresh", 4)
    store.metadata["nulled"] = None
    store.increment("nulled", 2)
    assert store.metadata["fresh"] == 5 and store.metadata["nulled"] == 2


@pytest.mark.parametrize(
    ("pages", "papers", "tier"),
    [([1], ["a"], "low"), ([1, 2], ["a"], "low"), ([1, 2, 3], ["a"], "medium"), (list(range(5)), ["a"], "medium"),
     (list(range(6)), ["a"], "high"), ([1], ["a", "b"], "high"), ([1], ["a", "a"], "low")],
)
def test_importance_tier_boundaries(pages, papers, tier):
    assert TermStore.importance_tier(pages, papers) == tier


def test_assign_importance_sets_every_record(path):
    store = TermStore(str(path))
    store.upsert(rec("Low", pages=[1], source_papers=["a"]))
    store.upsert(rec("High", pages=[1], source_papers=["a", "b"]))
    store.assign_importance()
    assert (store.get("low").importance, store.get("high").importance) == ("low", "high")


def test_extraction_date_is_naive_utc_iso_with_z_suffix_and_no_deprecation(path, recwarn):
    before = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    stamp = TermStore(str(path)).metadata["extraction_date"]
    assert stamp.endswith("Z") and "+" not in stamp
    parsed = datetime.datetime.fromisoformat(stamp[:-1])
    assert before <= parsed <= datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning) and "utcnow" in str(w.message)]
