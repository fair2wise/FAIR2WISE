"""T09 orientation (source vs inference), classification adapters, and paper tables with cell geometry."""

import pytest
from vision_fakes import SHA, bl_bbox, parsed, table_pdf, tl_bbox

from app.modules.contracts.evidence import SourceLocator
from app.modules.literature.parsed_document import ParsedTable
from app.modules.vision.classification import ChatAdapter, classify_text_context, panel_context, parse_class_answer
from app.modules.vision.classification_units import panel_order_map, resolve_units, verdict_classes
from app.modules.vision.orientation import find_statements, parse_orientation_answer, read_orientation
from app.modules.vision.tables import extract_tables

LOC = SourceLocator(page=3, section="Results")


class FakeChat:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def chat(self, prompt, *, temperature=0.0, timeout=240, trace_label=None):
        self.calls.append((prompt, temperature, trace_label))
        return self.reply


def test_parse_orientation_answer_inverts_once_and_refuses_ambiguity():
    assert parse_orientation_answer("OUT_OF_PLANE") == "parallel"
    assert parse_orientation_answer("answer: in-plane") == "perpendicular"
    assert parse_orientation_answer("UNCLEAR, though could be IN_PLANE") == ""
    assert parse_orientation_answer("IN_PLANE or OUT_OF_PLANE") == ""
    assert parse_orientation_answer("") == ""


def test_source_statement_is_reported_with_locator_and_verbatim_term():
    text = "GIWAXS shows the crystallites are face-on relative to the substrate. Films were annealed at 150 C."
    stmts = find_statements(text, LOC, "section")
    assert [s.term for s in stmts] == ["face-on"] and stmts[0].locator.page == 3
    got = read_orientation("fig-p001-00", stmts)
    assert got.reported == "face-on" and got.inference_candidates == ()


def test_hedged_negated_or_conflicting_statements_are_withheld():
    hedged = find_statements("The lamellae may be edge-on.", LOC, "caption")
    assert read_orientation("f", hedged).reported is None
    assert read_orientation("f", hedged).withheld_reason == "only_hedged_or_negated_statements"
    both = find_statements("Lamellae are parallel to the substrate. Domains are perpendicular to the substrate.",
                           LOC, "section")
    got = read_orientation("f", both)
    assert got.reported is None and got.withheld_reason == "conflicting_source_statements"
    assert read_orientation("f", []).withheld_reason == "no_source_statement"


def test_inference_candidate_never_becomes_a_reported_observation():
    chat = ChatAdapter(FakeChat("OUT_OF_PLANE"))
    got = read_orientation("f", [], caption="2D GIWAXS pattern", model=chat,
                           verdict={"class": "experimental_scattering"}, locator=LOC)
    assert got.reported is None and got.withheld_reason == "no_source_statement"
    cand = got.inference_candidates[0]
    assert cand["orientation"] == "parallel" and cand["reportable"] is False
    assert cand["kind"] == "inference_candidate"
    assert cand["needs_review"] is True and got.as_dict()["reported"] is None
    # a contradicting guess is flagged, source still wins
    stmts = find_statements("Lamellae are perpendicular to the substrate.", LOC, "section")
    got = read_orientation("f", stmts, model=chat, verdict={"class": "experimental_scattering"})
    assert got.reported == "perpendicular"
    assert "inference_disagrees_with_source_statement" in got.uncertainties


def test_model_not_asked_for_non_scattering_class():
    fake = FakeChat("IN_PLANE")
    got = read_orientation("f", [], model=ChatAdapter(fake), verdict={"class": "sem_image"})
    assert got.inference_candidates == () and fake.calls == []


def test_classification_is_caption_based_and_flags_model_inference():
    v = classify_text_context("(a) SEM image of the film. (b) AFM height image.", "a")
    assert v["class"] == "sem_image" and v["source_supported"] and not v["needs_review"]
    assert classify_text_context("A plot.", "")["needs_review"] and classify_text_context("A plot.")["class"] == ""
    fake = FakeChat("tem_image")
    v = classify_text_context("A plot of something.", model=ChatAdapter(fake))
    assert v["class"] == "tem_image" and v["basis"] == "model_text" and v["source_supported"] is False
    assert "model_inference_only" in v["review_reasons"] and fake.calls[0][1] == 0.0
    v = classify_text_context("SEM image.", model=ChatAdapter(FakeChat("afm_image")))
    assert v["class"] == "sem_image" and "model_disagrees_with_caption" in v["review_reasons"]
    assert parse_class_answer("sem_image or afm_image") == "" and parse_class_answer("It is other") == "other"
    assert "WHOLE figure" in panel_context("z", "(a) one. (b) two.")


def test_figure_vs_panel_units_and_order():
    verdicts = {"f1": {"class": "other"}, "f1-b": {"class": "sem_image", "also": ["afm_image"]},
                "f1-a": {"class": "sem_image"}, "f2": {"class": "spectrum"}}
    parents = {"f1-a": "f1", "f1-b": "f1"}
    units = resolve_units(verdicts, parents, panel_order=panel_order_map(
        [{"id": "f1-a", "index": "0"}, {"id": "f1-b", "index": 1}]))
    assert [(u.subject_id, u.is_panel) for u in units] == [("f1-a", True), ("f1-b", True), ("f2", False)]
    assert len(resolve_units(verdicts, parents, include_superseded=True)) == 4
    assert verdict_classes(verdicts["f1-b"]) == {"sem_image", "afm_image"}


ROWS = [["Peak", "q (nm^-1)", "d (nm)"], ["1", "0.31", "20.3"], ["2", "0.62", "10.1"]]


@pytest.mark.parametrize("make", [bl_bbox, tl_bbox], ids=["bottomleft", "topleft"])
def test_table_cells_have_geometry_and_locators(make):
    region = (60.0, 420.0, 330.0, 486.0)
    doc = parsed("", tables=(ParsedTable(1, 3, 3, caption="Table 1. Peak positions", bbox=make(region)),))
    (table,) = extract_tables(doc, table_pdf(ROWS), work_id="10.1000/test")
    assert (table.n_rows, table.n_cols) == (3, 3) and not table.uncertainties
    texts = {(c.row, c.col): c.text for c in table.cells}
    assert texts[(0, 1)] == "q (nm^-1)" and texts[(2, 2)] == "10.1"
    cell = next(c for c in table.cells if (c.row, c.col) == (1, 1))
    assert cell.bbox == pytest.approx((150.0, 442.0, 240.0, 464.0), abs=1.5)
    assert table.locator.page == 1 and table.locator.figure == table.table_id
    assert table.locator.bbox_origin == "top-left" and table.locator.bbox_units == "pt"
    assert table.evidence[0].source_sha256 == SHA


def test_table_shape_mismatch_and_missing_bbox_are_surfaced():
    region = (60.0, 420.0, 330.0, 486.0)
    doc = parsed("", tables=(ParsedTable(1, 5, 3, bbox=bl_bbox(region)), ParsedTable(1, 3, 3, bbox=None)))
    first, second = extract_tables(doc, table_pdf(ROWS))
    assert [u.code for u in first.uncertainties] == ["table_shape_mismatch"] and first.cells
    assert [u.code for u in second.uncertainties] == ["table_region_unlocated"] and second.cells == ()
    assert second.locator.page == 1 and second.locator.bbox is None
