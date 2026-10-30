"""Pilot labelling tool (H3 B4) with a synthetic PDF and fake clients: no network, no key, no real paper."""

import csv
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import label_fakes as F  # noqa: E402
from evals.binary.labeling import cli, guide  # noqa: E402
from evals.binary.labeling import pilot as P  # noqa: E402
from evals.binary.labeling.labeler import calls_for, page_chunks, parse_response  # noqa: E402
from evals.binary.labeling.parse import parse_pdf  # noqa: E402
from evals.binary.labeling.review import PromotionError, promote, read_sheet, sheet_csv  # noqa: E402
from evals.binary.labeling.roles import RoleSeparationError, check_roles, resolve_judge_model  # noqa: E402
from evals.binary.labeling.store import LabelStoreError, resolve_root  # noqa: E402
from evals.binary.model import REPO_ROOT  # noqa: E402


@pytest.fixture
def synth(monkeypatch, tmp_path):
    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    monkeypatch.delenv("F2W_DOTENV", raising=False)
    paper = F.install_synthetic(monkeypatch, tmp_path)
    return SimpleNamespace(paper=paper, root=tmp_path, papers=tmp_path / "papers", out=tmp_path / "labels")


def _label(synth, *extra, factory=None, lister=None):
    argv = ["--model", F.LABELER, "--paper", "A", "--papers-root", str(synth.papers), "--out", str(synth.out),
            *extra]
    return cli.main(argv, client_factory=factory or F.fake_factory(), lister=lister or F.lister())


def _doc(synth):
    return json.loads((synth.out / "A" / "published.label.json").read_text())


# ------------------------------------------------------------------ registry and guide


def test_registry_holds_the_three_owner_chosen_papers_and_the_13_cases():
    assert sorted(P.PILOT_PAPERS) == ["A", "B", "C"]
    assert len(P.LABEL_CASES) == 13
    assert set(P.LABEL_CASES) == {"EXT-01", "EXT-02", "EXT-03", "EXT-04", "FIG-01", "FIG-02", "FIG-03", "QA-01",
                                  "QA-02", "QA-03", "QA-04", "OPS-04", "LOCAL-02"}
    assert P.CASE_PAPERS["LOCAL-02"]["rule"] == "any"
    assert [v.key for v in P.PILOT_PAPERS["C"].versions] == ["published", "preprint"]
    for paper in P.PILOT_PAPERS.values():
        for version in paper.versions:
            assert len(version.sha256) == 64 and not Path(version.path).is_absolute()


def test_guide_is_versioned_and_its_template_hash_is_pinned():
    # Editing the guide, schemas or task text must bump GUIDE_VERSION and this pin together.
    assert guide.GUIDE_VERSION == "f2w-pilot-label-guide/2"  # /2: multimodal figure instruction (D1.7)
    assert guide.template_sha256() == guide.GUIDE_TEMPLATE_SHA256
    for tag in P.VALUE_TAGS:
        assert f"`{tag}`" in guide.GUIDE


def test_planned_calls_per_paper():
    pages = {"repository_copy": 9}
    assert calls_for(P.PILOT_PAPERS["A"], pages, 5) == 2 + 1 + 1
    assert calls_for(P.PILOT_PAPERS["C"], {"published": 16, "preprint": 42}, 5) == (4 + 1 + 1) + (9 + 1)
    assert page_chunks(9, 5) == [(1, 5), (6, 9)]


def test_parse_response_tolerates_fences_and_rejects_prose():
    assert parse_response('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_response("sorry") is None and parse_response("[1, 2]") is None


# ------------------------------------------------------------------ labelling


def test_end_to_end_labels_are_provisional_with_provenance(synth):
    assert _label(synth) == 0
    doc = _doc(synth)
    assert doc["status"] == "provisional_model_label" and doc["complete"] is True
    prov = doc["provenance"]
    assert prov["labeler_model"] == F.LABELER and prov["guide_version"] == guide.GUIDE_VERSION
    assert prov["prompt_template_sha256"] == guide.template_sha256()
    assert prov["source_sha256"] == synth.paper.versions[0].sha256
    assert prov["parser_revision"].startswith("pymupdf-") and prov["created_at"].endswith("Z")
    assert all(len(c["prompt_sha256"]) == 64 for c in doc["calls"])
    kinds = {i["kind"] for i in doc["items"]}
    assert {"value", "absent", "question", "identity"} <= kinds
    assert (synth.out / "A" / "review_sheet.csv").is_file() and (synth.out / "A" / "review_sheet.md").is_file()


def test_raw_prompts_and_page_text_are_not_stored(synth):
    _label(synth)
    text = (synth.out / "A" / "published.label.json").read_text()
    assert guide.GUIDE[:200] not in text
    assert "The films were annealed at 150 C for 2 h." not in text  # page text never quoted by any label


def test_unlocated_and_wrong_page_quotes_are_flagged_not_dropped(synth):
    _label(synth)
    values = {i["data"]["quantity"]: i for i in _doc(synth)["items"] if i["kind"] == "value"}
    assert values["lamellar spacing"]["quote_status"] in ("exact", "normalized")
    assert values["peak position"]["quote_status"] == "wrong_page"
    assert values["peak position"]["quotes"][0]["found_pages"] == [2]
    assert values["film thickness"]["quote_status"] == "not_found"
    assert "quote_not_located" in values["film thickness"]["flags"]
    assert values["spacing"]["data"]["tag"] == "cited"
    assert _doc(synth)["counts"]["quote_not_located"] == 2


def test_malformed_items_are_counted_not_invented_and_the_call_counts_as_missing(synth):
    assert _label(synth, factory=F.fake_factory(malformed=True)) == 1
    doc = _doc(synth)
    value_calls = [c for c in doc["calls"] if c["task"] == "values"]
    assert value_calls[0]["status"] == "partial" and value_calls[0]["items_invalid"] == 1
    assert value_calls[0]["items_returned"] == 4 and doc["complete"] is False
    expected = {"task": "values", "scope": "1-2", "call_id": value_calls[0]["call_id"], "reason": "partial"}
    assert expected in doc["missing"]


def test_a_paper_without_parsed_figures_records_the_figure_task_missing(synth):
    # a text-only PDF: no figure call is made and the gap is explicit
    F.make_pdf(synth.papers / "synthetic.pdf", figure=False)
    paper = F.synthetic_paper("synthetic.pdf", F.file_sha256(synth.papers / "synthetic.pdf"))
    P.PILOT_PAPERS["A"] = paper  # restored by the synth fixture's monkeypatch.setitem
    assert _label(synth) == 1
    doc = _doc(synth)
    assert {"task": "figures", "scope": "figures", "call_id": None, "reason": "no_figures_parsed"} in doc["missing"]
    assert [c["task"] for c in doc["calls"]] == ["values", "context"]


def test_figure_readings_are_labelled_from_the_caption_and_flagged_for_visual_check(synth):
    _label(synth)
    figs = [i for i in _doc(synth)["items"] if i["kind"] == "figure_reading"]
    assert figs and figs[0]["quote_status"] in ("exact", "normalized")
    assert "needs_visual_check" in figs[0]["flags"]


def test_figures_are_text_only_by_default_and_multimodal_sends_the_crops(synth):
    """D1.7 (O-S3): the figure call is text-only unless --figures multimodal is given; then PNG crops of the
    parsed figure areas are attached to the same call, the prompt names the visible figures, and the provenance
    records the mode and the number of images."""
    factory = F.fake_factory()
    assert _label(synth, factory=factory) in (0, 1)
    client = factory.clients[-1]
    doc = _doc(synth)
    assert doc["provenance"]["figures_mode"] == "text" and doc["provenance"]["figure_images_sent"] == 0
    assert client.image_calls == []
    fig_call = next(c for c in doc["calls"] if c["task"] == "figures")
    assert "images_sent" not in fig_call

    factory = F.fake_factory()
    assert _label(synth, "--figures", "multimodal", "--force", factory=factory) in (0, 1)
    client = factory.clients[-1]
    doc = _doc(synth)
    assert doc["provenance"]["figures_mode"] == "multimodal"
    assert doc["provenance"]["figure_images_sent"] >= 1 and client.image_calls == [doc["provenance"]["figure_images_sent"]]
    fig_call = next(c for c in doc["calls"] if c["task"] == "figures")
    assert fig_call["images_sent"] == doc["provenance"]["figure_images_sent"] and fig_call["status"] == "ok"
    multimodal_prompt = next(p for p in client.prompts if "Task: for every figure" in p)
    assert "attached image of the figure area" in multimodal_prompt and "fig-p002-00" in multimodal_prompt


def test_render_figure_crops_returns_png_per_figure_and_none_without_a_bbox(synth):
    from evals.binary.labeling.parse import parse_pdf, render_figure_crops

    pdf = synth.papers / "synthetic.pdf"
    parsed = parse_pdf(pdf)
    assert parsed.figures and parsed.figures[0]["bbox"] is not None
    crops = render_figure_crops(pdf, parsed.figures)
    assert len(crops) == len(parsed.figures) and crops[0][:8] == b"\x89PNG\r\n\x1a\n"
    assert render_figure_crops(pdf, [dict(parsed.figures[0], bbox=None), dict(parsed.figures[0], page=99)]) == [None, None]
    small = render_figure_crops(pdf, parsed.figures, max_side=64)
    assert len(small[0]) < len(crops[0])


def test_multimodal_mode_needs_one_image_slot_per_figure_and_rejects_unknown_modes(synth):
    from evals.binary.labeling.labeler import label_version
    from evals.binary.labeling.parse import parse_pdf

    pdf = synth.papers / "synthetic.pdf"
    parsed = parse_pdf(pdf)
    paper = P.PILOT_PAPERS["A"]
    with pytest.raises(ValueError, match="figures_mode"):
        label_version(paper, paper.versions[0], parsed, F.FakeClient(), figures_mode="pictures")
    with pytest.raises(ValueError, match="one entry per parsed figure"):
        label_version(paper, paper.versions[0], parsed, F.FakeClient(), figures_mode="multimodal", figure_images=[])
    client = F.FakeClient()
    doc = label_version(paper, paper.versions[0], parsed, client, figures_mode="multimodal",
                        figure_images=[None] * len(parsed.figures))
    assert client.image_calls == [] and doc["provenance"]["figure_images_sent"] == 0  # nothing to see: text call
    assert "listed as visible: none" in next(p for p in client.prompts if "Task: for every figure" in p)


def test_failed_and_unparsable_calls_are_recorded_missing(synth):
    assert _label(synth, factory=F.fake_factory(fail=("figures",), garbage=("context",))) == 1
    doc = _doc(synth)
    assert doc["complete"] is False
    assert {(m["task"], m["reason"]) for m in doc["missing"]} >= {("figures", "failed"), ("context", "unparsable")}
    assert not [i for i in doc["items"] if i["kind"] in ("question", "identity", "absent")]
    assert "sk-shouldneverleak123" not in json.dumps(doc)


def test_refuses_without_a_key_and_never_prints_it(synth, capsys):
    rc = cli.main(["--model", F.LABELER, "--paper", "A", "--papers-root", str(synth.papers), "--out",
                   str(synth.out)], lister=F.lister())
    assert rc == 2 and "CBORG_API_KEY is not set" in capsys.readouterr().err
    assert not (synth.out / "A").exists()
    assert cli.main(["--list-models"], lister=F.lister()) == 2


def test_list_models_prints_the_live_list_and_scrubs_errors(synth, capsys, monkeypatch):
    monkeypatch.setenv("CBORG_API_KEY", "sk-testkeyvalue-0001")
    assert cli.main(["--list-models"], lister=F.lister("m-b", "m-a")) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[1:] == ["m-a", "m-b"]
    assert cli.main(["--list-models"], lister=F.unavailable_lister("401 for sk-testkeyvalue-0001")) == 2
    captured = capsys.readouterr()
    assert "sk-testkeyvalue-0001" not in captured.out + captured.err


def test_model_must_be_in_the_live_list(synth):
    assert _label(synth, lister=F.lister(F.JUDGE)) == 2
    assert _label(synth, lister=F.unavailable_lister()) == 2


def test_labeller_must_differ_from_judge_and_model_under_test(synth, tmp_path):
    assert _label(synth, "--judge-model", F.LABELER) == 2
    assert _label(synth, "--model-under-test", F.LABELER) == 2
    from app.modules.validation_settings import JudgeModelStore, judge_settings_path

    JudgeModelStore(judge_settings_path(tmp_path / "wd")).write(F.LABELER, updated_by="local-operator")
    assert _label(synth, "--settings-workdir", str(tmp_path / "wd")) == 2
    assert not (synth.out / "A").exists()


def test_a_different_pdf_under_the_registered_name_is_refused(synth):
    F.make_pdf(synth.papers / "synthetic.pdf", [["changed content"]])
    assert _label(synth) == 2


def test_label_dir_inside_the_repo_is_refused_except_the_ignored_one(tmp_path):
    with pytest.raises(LabelStoreError):
        resolve_root(REPO_ROOT / "docs" / "labels")
    assert resolve_root(REPO_ROOT / ".pilot_labels") == (REPO_ROOT / ".pilot_labels").resolve()
    assert resolve_root(None, environ={"F2W_PILOT_LABELS_DIR": str(tmp_path)}) == tmp_path.resolve()
    assert ".pilot_labels/" in (REPO_ROOT / ".gitignore").read_text()


def test_relabel_refuses_to_overwrite_review_decisions(synth):
    _label(synth)
    sheet = synth.out / "A" / "review_sheet.csv"
    rows = list(csv.DictReader(sheet.open()))
    rows[0]["decision"] = "accept"
    _write_sheet(sheet, rows)
    assert _label(synth) == 2
    assert _label(synth, "--force") == 0
    assert list((synth.out / "A").glob("review_sheet.*.csv"))


def test_explicit_dotenv_only(monkeypatch, tmp_path):
    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("CBORG_API_KEY=from-a-searched-dotenv\n")
    monkeypatch.delenv("F2W_DOTENV", raising=False)
    cli._load_explicit_dotenv()
    import os

    assert "CBORG_API_KEY" not in os.environ
    named = tmp_path / "owner.env"
    named.write_text("CBORG_API_KEY=from-the-named-file\n")
    monkeypatch.setenv("F2W_DOTENV", str(named))
    try:
        cli._load_explicit_dotenv()
        assert os.environ["CBORG_API_KEY"] == "from-the-named-file"
    finally:
        # load_dotenv wrote os.environ directly; monkeypatch did not record it, so remove it here
        os.environ.pop("CBORG_API_KEY", None)


def test_real_client_goes_through_the_shared_admission_slot(monkeypatch):
    from app.modules.term_extractor import clients
    from evals.binary.labeling.labeler import CborgLabelClient

    held = []

    class Slot:
        def __enter__(self):
            held.append("in")

        def __exit__(self, *a):
            held.append("out")

    class FakeCompletions:
        def create(self, **kw):
            assert kw["temperature"] == 0.0 and kw["stream"] is True
            return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="{}"))])]

    class FakeOpenAI:
        def __init__(self, **kw):
            assert kw["api_key"] == "test-only-key"
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("CBORG_API_KEY", "test-only-key")
    monkeypatch.setattr(clients.openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(clients, "sync_slot", lambda: Slot())
    assert CborgLabelClient("some-model").chat("prompt") == "{}"
    assert held == ["in", "out"]


# ------------------------------------------------------------------ review and promotion


def _write_sheet(path, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buf.getvalue())


def _decide(synth, decide):
    sheet = synth.out / "A" / "review_sheet.csv"
    rows = list(csv.DictReader(sheet.open()))
    for row in rows:
        decide(row)
    _write_sheet(sheet, rows)
    return rows


def _promote(synth, reviewer="Dana Reviewer"):
    return cli.main(["promote", "--paper", "A", "--reviewer", reviewer, "--out", str(synth.out)])


def test_promote_refuses_when_nothing_is_reviewed(synth):
    _label(synth)
    assert _promote(synth) == 2
    assert not (synth.out / "A" / "gold.json").exists()


def test_promote_refuses_the_labelling_model_as_reviewer(synth):
    _label(synth)
    _decide(synth, lambda r: r.update(decision="reject"))
    assert _promote(synth, reviewer=F.LABELER) == 2


def test_unreviewed_and_unlocated_accepts_are_refused_and_gold_stays_incomplete(synth):
    _label(synth)

    def decide(row):
        if row["kind"] == "value":
            row["decision"] = "accept"  # includes the wrong-page and not-found quotes

    _decide(synth, decide)
    assert _promote(synth) == 1
    gold = json.loads((synth.out / "A" / "gold.json").read_text())
    assert gold["status"] == "gold_incomplete" and gold["complete"] is False
    reasons = {r["label_id"]: r["reason"] for r in gold["refused"]}
    assert any("quote not located" in v for v in reasons.values())
    assert any(v == "unreviewed" for v in reasons.values())
    assert gold["reviewer"] == "Dana Reviewer"


def test_full_review_with_corrections_promotes_complete_gold(synth):
    _label(synth)

    def decide(row):
        if row["quote_status"] == "wrong_page":
            row["decision"] = "correct"
            row["corrected_json"] = json.dumps({"locator": {"page": 2}})
        elif row["quote_status"] == "not_found":
            row["decision"] = "reject"
            row["reviewer_note"] = "not in the paper"
        else:
            row["decision"] = "accept"

    _decide(synth, decide)
    assert _promote(synth) == 0
    gold = json.loads((synth.out / "A" / "gold.json").read_text())
    assert gold["status"] == "gold" and gold["complete"] is True and gold["refused"] == []
    corrected = [i for i in gold["items"] if i["review"]["decision"] == "corrected"]
    assert corrected and corrected[0]["quote_status"] in ("exact", "normalized")
    assert len(gold["rejected"]) == 1


def test_a_corrected_quote_that_still_does_not_locate_is_refused(synth):
    _label(synth)

    def decide(row):
        row["decision"] = "accept"
        if row["quote_status"] == "not_found":
            row["decision"] = "correct"
            row["corrected_json"] = json.dumps({"locator": {"quote": "still not in the text"}})

    _decide(synth, decide)
    assert _promote(synth) == 1


def test_sheet_lists_every_label_once(synth):
    _label(synth)
    doc = _doc(synth)
    rows = list(csv.DictReader(io.StringIO(sheet_csv([doc]))))
    assert [r["label_id"] for r in rows] == [i["label_id"] for i in doc["items"]]
    assert all(r["decision"] == "" for r in rows)
    assert read_sheet(synth.out / "A" / "review_sheet.csv").keys() == {r["label_id"] for r in rows}


def test_promote_direct_api_requires_a_reviewer(synth):
    _label(synth)
    with pytest.raises(PromotionError):
        promote([_doc(synth)], {}, reviewer=" ", parsed={})


# ------------------------------------------------------------------ roles


def test_role_separation_rules(tmp_path):
    check_roles(labeler="a", judge="b", model_under_test="c")
    with pytest.raises(RoleSeparationError):
        check_roles(labeler="a", judge="A ")
    with pytest.raises(RoleSeparationError):
        check_roles(judge="b", model_under_test="b")
    with pytest.raises(RoleSeparationError):
        check_roles(labelers=["x", "c"], model_under_test="c")
    assert resolve_judge_model(" j ", None) == ("j", "cli")
    assert resolve_judge_model(None, tmp_path) == (None, "none")


def test_parse_pdf_reads_page_text_with_the_f2w_parser(tmp_path):
    parsed = parse_pdf(F.make_pdf(tmp_path / "x.pdf"))
    assert parsed.num_pages == 2 and "21.0 nm" in parsed.page_text(1) and parsed.page_text(3) == ""
    assert parsed.parser_revision.startswith("pymupdf-")
