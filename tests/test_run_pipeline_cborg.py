import importlib
import sys
from pathlib import Path

import pytest


def import_pipeline(monkeypatch):
    app_dir = Path(__file__).resolve().parents[1] / "app"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    monkeypatch.setenv("CBORG_BASE_URL", "https://example.invalid")
    import app.run_pipeline_cborg as pipeline

    return importlib.reload(pipeline)


def make_pdf(path):
    path.write_bytes(b"%PDF-1.4\n% test pdf\n")


class FixedDateTime:
    calls = 0

    @classmethod
    def now(cls, tz=None):
        cls.calls += 1

        class Stamp:
            def strftime(self, fmt):
                return f"20260101_00000{FixedDateTime.calls}"

        return Stamp()


def test_organize_papers_into_folders_copies_sorted_pdfs(tmp_path, monkeypatch):
    pipeline = import_pipeline(monkeypatch)
    source = tmp_path / "source"
    output = tmp_path / "organized"
    source.mkdir()
    for name in ["b.pdf", "a.pdf", "notes.txt", "c.pdf"]:
        path = source / name
        if name.endswith(".pdf"):
            make_pdf(path)
        else:
            path.write_text("ignore", encoding="utf-8")

    pipeline.organize_papers_into_folders(source, output, papers_per_folder=2)

    assert sorted(p.name for p in (output / "paper_2").glob("*.pdf")) == ["a.pdf", "b.pdf"]
    assert sorted(p.name for p in (output / "paper_4").glob("*.pdf")) == ["c.pdf"]
    assert not (output / "paper_6").exists()


def test_organize_papers_handles_empty_source(tmp_path, monkeypatch):
    pipeline = import_pipeline(monkeypatch)
    source = tmp_path / "source"
    output = tmp_path / "organized"
    source.mkdir()

    pipeline.organize_papers_into_folders(source, output)

    assert not output.exists()


def test_organize_papers_caps_at_first_100_pdfs(tmp_path, monkeypatch):
    pipeline = import_pipeline(monkeypatch)
    source = tmp_path / "source"
    output = tmp_path / "organized"
    source.mkdir()
    for idx in range(105):
        make_pdf(source / f"{idx:03d}.pdf")

    pipeline.organize_papers_into_folders(source, output, papers_per_folder=25)

    assert len(list(output.glob("paper_*/*.pdf"))) == 100
    assert not (output / "paper_125").exists()


def test_run_checkpoint_pipeline_requires_cborg_api_key(tmp_path, monkeypatch):
    pipeline = import_pipeline(monkeypatch)
    monkeypatch.delenv("CBORG_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="CBORG_API_KEY is not set"):
        pipeline.run_checkpoint_pipeline(tmp_path, ["model"], dry_run=True)


def test_run_checkpoint_pipeline_dry_run_prints_plan_without_side_effects(tmp_path, monkeypatch, capsys):
    pipeline = import_pipeline(monkeypatch)
    monkeypatch.setenv("CBORG_API_KEY", "test-key")
    monkeypatch.setattr(pipeline, "PAPER_FOLDERS", {25: ["paper_25"]})
    paper_dir = tmp_path / "paper_25"
    paper_dir.mkdir()
    make_pdf(paper_dir / "one.pdf")

    pipeline.run_checkpoint_pipeline(tmp_path, ["vendor/model:tag"], dry_run=True)

    out = capsys.readouterr().out
    assert "[DRY-RUN] Checkpoint=25, model=vendor/model:tag" in out
    assert "New papers: 1" in out
    assert "vendor_model_tag_25_" in out
    assert "test-key" not in out
    assert not (tmp_path / "storage").exists()


def test_run_checkpoint_pipeline_skips_missing_folder_in_dry_run(tmp_path, monkeypatch, capsys):
    pipeline = import_pipeline(monkeypatch)
    monkeypatch.setenv("CBORG_API_KEY", "test-key")
    monkeypatch.setattr(pipeline, "PAPER_FOLDERS", {25: ["missing"]})

    pipeline.run_checkpoint_pipeline(tmp_path, ["model"], dry_run=True)

    out = capsys.readouterr().out
    assert "New folders: ['missing']" in out
    assert "New papers: 0" in out


def test_run_checkpoint_pipeline_logs_missing_folder(tmp_path, monkeypatch, caplog):
    pipeline = import_pipeline(monkeypatch)
    monkeypatch.setenv("CBORG_API_KEY", "test-key")
    monkeypatch.setattr(pipeline, "PAPER_FOLDERS", {25: ["missing"]})

    pipeline.run_checkpoint_pipeline(tmp_path, ["model"], dry_run=True)

    assert "Missing folder missing for checkpoint 25" in caplog.text


def test_run_checkpoint_pipeline_extracts_and_converts_incrementally(tmp_path, monkeypatch):
    pipeline = import_pipeline(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CBORG_API_KEY", "test-key")
    monkeypatch.setattr(pipeline, "PAPER_FOLDERS", {25: ["paper_25"], 50: ["paper_50"]})
    monkeypatch.setattr(pipeline, "datetime", FixedDateTime)
    FixedDateTime.calls = 0
    for folder, pdf_name in [("paper_25", "first.pdf"), ("paper_50", "second.pdf")]:
        path = tmp_path / folder
        path.mkdir()
        make_pdf(path / pdf_name)

    extraction_calls = []
    conversion_calls = []

    def fake_run_extraction(data_dir, output_file, **kwargs):
        copied_pdfs = sorted(p.name for p in Path(data_dir).glob("*.pdf"))
        prior = Path(output_file).read_text(encoding="utf-8") if Path(output_file).exists() else ""
        extraction_calls.append((copied_pdfs, Path(output_file).name, prior, kwargs))
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(output_file).write_text(f"terms after {copied_pdfs}", encoding="utf-8")

    def fake_convert_terms_to_graph(terms_json, graph_json):
        conversion_calls.append((Path(terms_json).name, Path(graph_json).name))
        Path(graph_json).parent.mkdir(parents=True, exist_ok=True)
        Path(graph_json).write_text("graph", encoding="utf-8")
        return {"things": [{"id": "n"}], "associations": [{"subject": "s"}]}

    monkeypatch.setattr(pipeline, "run_extraction", fake_run_extraction)
    monkeypatch.setattr(pipeline, "convert_terms_to_graph", fake_convert_terms_to_graph)

    pipeline.run_checkpoint_pipeline(tmp_path, ["vendor/model"], dry_run=False)

    assert [call[0] for call in extraction_calls] == [["first.pdf"], ["second.pdf"]]
    assert extraction_calls[0][1] == "extracted_terms_vendor_model_25_20260101_000001.json"
    assert extraction_calls[1][1] == "extracted_terms_vendor_model_50_20260101_000002.json"
    assert extraction_calls[1][2] == "terms after ['first.pdf']"
    assert extraction_calls[0][3]["backend"] == "cborg"
    assert extraction_calls[0][3]["cborg_api_key"] == "test-key"
    assert extraction_calls[0][3]["max_workers"] == 1
    assert conversion_calls == [
        ("extracted_terms_vendor_model_25_20260101_000001.json", "matkg_vendor_model_25_20260101_000001.json"),
        ("extracted_terms_vendor_model_50_20260101_000002.json", "matkg_vendor_model_50_20260101_000002.json"),
    ]


def test_run_checkpoint_pipeline_handles_no_pdfs_with_mocked_extraction(tmp_path, monkeypatch):
    pipeline = import_pipeline(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CBORG_API_KEY", "test-key")
    monkeypatch.setattr(pipeline, "PAPER_FOLDERS", {25: ["paper_25"]})
    monkeypatch.setattr(pipeline, "datetime", FixedDateTime)
    FixedDateTime.calls = 0
    (tmp_path / "paper_25").mkdir()

    seen = {}

    def fake_run_extraction(data_dir, output_file, **kwargs):
        seen["pdfs"] = list(Path(data_dir).glob("*.pdf"))
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(output_file).write_text('{"terms": []}', encoding="utf-8")

    monkeypatch.setattr(pipeline, "run_extraction", fake_run_extraction)
    monkeypatch.setattr(
        pipeline,
        "convert_terms_to_graph",
        lambda terms_json, graph_json: {"things": [], "associations": []},
    )

    pipeline.run_checkpoint_pipeline(tmp_path, ["model"], dry_run=False)

    assert seen["pdfs"] == []
