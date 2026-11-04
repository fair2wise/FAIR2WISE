"""``f2w-run`` (app.modules.commands.run): argument parsing, validation exits and what reaches the extractor (fakes)."""
from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Any

import pytest

from app.modules import dotenv_entry
from app.modules.commands import run as run_cmd
import app.modules.term_extractor as term_extractor

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


@pytest.fixture(autouse=True)
def isolated_logging(monkeypatch):
    """main() installs root handlers and a level; put both back."""
    root = logging.getLogger()
    before = list(root.handlers), root.level
    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_OPENAI_TCP_KEEPALIVE", raising=False)
    yield
    for handler in list(root.handlers):
        if handler not in before[0]:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(before[1])
    logging.getLogger("httpx").setLevel(logging.NOTSET)


def _argv(monkeypatch, tmp_path: Path, *extra: str, pdfs: bool = True) -> Path:
    pdf_dir = tmp_path / "pdfs"
    (pdf_dir / "sub").mkdir(parents=True)
    if pdfs:
        (pdf_dir / "sub" / "a.pdf").write_bytes(b"%PDF-1.4")
        (pdf_dir / "b.pdf").write_bytes(b"%PDF-1.4")
        (pdf_dir / "note.txt").write_text("not a pdf")
    monkeypatch.setattr(sys, "argv", ["f2w-run", "--pdf-dir", str(pdf_dir), "--log-file", str(tmp_path / "logs" / "r.log"),
                                      "--output", str(tmp_path / "out.json"), *extra])
    return pdf_dir


def test_defaults_and_overrides_parse(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["f2w-run"])
    args = run_cmd.parse_args()
    assert (args.backend, args.workers, args.temperature, args.dry_run) == ("cborg", 4, 0.0, False)
    assert args.pdf_dir == Path("papers_test") and args.log_level == "INFO" and args.merge_top_k == 12
    monkeypatch.setattr(sys, "argv", ["f2w-run", "--backend", "ollama", "--workers", "2", "--dry-run", "--merge-top-k", "5",
                                      "--model", "llama3", "--log-level", "DEBUG"])
    args = run_cmd.parse_args()
    assert (args.backend, args.workers, args.dry_run, args.merge_top_k, args.model, args.log_level) == (
        "ollama", 2, True, 5, "llama3", "DEBUG")


def test_there_is_no_flag_that_would_put_the_api_key_in_argv(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["f2w-run", "--help"])
    with pytest.raises(SystemExit) as stop:
        run_cmd.parse_args()
    assert stop.value.code == 0
    help_text = capsys.readouterr().out
    assert "--pdf-dir" in help_text and "api-key" not in help_text.lower() and "--token" not in help_text


def test_bad_backend_and_unknown_flag_exit_2(monkeypatch, capsys):
    for bad in (["--backend", "openai"], ["--no-such-flag"], ["--workers", "many"], ["--log-level", "TRACE"]):
        monkeypatch.setattr(sys, "argv", ["f2w-run", *bad])
        with pytest.raises(SystemExit) as stop:
            run_cmd.parse_args()
        assert stop.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_missing_pdf_directory_exits_1_after_creating_the_log_file(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(sys, "argv", ["f2w-run", "--pdf-dir", str(tmp_path / "nope"), "--log-file", str(tmp_path / "l" / "r.log")])
    with pytest.raises(SystemExit) as stop:
        run_cmd.main()
    assert stop.value.code == 1
    assert (tmp_path / "l" / "r.log").is_file()
    assert "PDF directory not found" in (tmp_path / "l" / "r.log").read_text()


def test_directory_without_pdfs_exits_1(monkeypatch, tmp_path):
    _argv(monkeypatch, tmp_path, pdfs=False)
    with pytest.raises(SystemExit) as stop:
        run_cmd.main()
    assert stop.value.code == 1
    assert "No PDFs found" in (tmp_path / "logs" / "r.log").read_text()


def test_cborg_backend_without_a_key_exits_1(monkeypatch, tmp_path):
    _argv(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as stop:
        run_cmd.main()
    assert stop.value.code == 1
    assert "CBORG_API_KEY" in (tmp_path / "logs" / "r.log").read_text()


def test_dry_run_reports_the_settings_and_never_calls_the_extractor(monkeypatch, tmp_path):
    _argv(monkeypatch, tmp_path, "--dry-run", "--chebi", str(tmp_path / "missing.obo"))
    monkeypatch.setenv("CBORG_API_KEY", "sk-never-logged")
    monkeypatch.setenv("CBORG_IP_FAMILY", "ipv6")
    monkeypatch.setattr(term_extractor, "run_extraction", lambda **kw: pytest.fail("dry run must not extract"))
    for name in PROXY_VARS:
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    run_cmd.main()
    log = (tmp_path / "logs" / "r.log").read_text()
    assert "2 files in" in log and "Dry-run complete" in log and "family=ipv6" in log
    assert "ChEBI file not found" in log and "ChEBI    : disabled" in log
    assert "sk-never-logged" not in log
    import os

    assert all(name not in os.environ for name in PROXY_VARS)  # the dead agent-shell proxy is dropped
    assert os.environ["LANGCHAIN_OPENAI_TCP_KEEPALIVE"] == "0"
    monkeypatch.delenv("LANGCHAIN_OPENAI_TCP_KEEPALIVE")


def _extraction(captured: dict[str, Any], result: dict[str, Any]):
    def fake(**kw: Any) -> dict[str, Any]:
        captured.update(kw)
        return result

    return fake


def test_a_successful_run_passes_arguments_and_the_key_from_the_environment(monkeypatch, tmp_path):
    pdf_dir = _argv(monkeypatch, tmp_path, "--model", "m1", "--workers", "3", "--merge-top-k", "7", "--temperature", "0.2",
                    "--ollama-url", "http://127.0.0.1:9", "--schema", "s.yaml", "--chebi", str(tmp_path / "chebi.obo"))
    (tmp_path / "chebi.obo").write_text("format-version: 1.2")
    monkeypatch.setenv("CBORG_API_KEY", "sk-from-env")
    monkeypatch.setenv("CBORG_BASE_URL", "https://cborg.example")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(term_extractor, "run_extraction", _extraction(captured, {
        "status": "success", "unique_terms": 5, "processed_pages_with_terms": 3, "processed_pages_total": 4,
        "processed_files": 2, "output_file": "out.json"}))
    run_cmd.main()
    assert captured == {
        "pdf_dir": pdf_dir, "output_json": tmp_path / "out.json", "model": "m1", "backend": "cborg",
        "cborg_api_key": "sk-from-env", "cborg_base": "https://cborg.example", "ollama_url": "http://127.0.0.1:9",
        "schema_path": "s.yaml", "temperature": 0.2, "max_workers": 3, "chebi_obo_path": str(tmp_path / "chebi.obo"),
        "merge_top_k": 7, "merge_embed_model": captured["merge_embed_model"]}
    log = (tmp_path / "logs" / "r.log").read_text()
    assert "5 terms extracted from 3/4 pages across 2 files" in log and "sk-from-env" not in log
    assert "sk-from-env" not in " ".join(sys.argv)


def test_ollama_backend_needs_no_key_and_passes_none(monkeypatch, tmp_path):
    _argv(monkeypatch, tmp_path, "--backend", "ollama")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(term_extractor, "run_extraction", _extraction(captured, {"status": "success", "unique_terms": 0,
                        "processed_pages_with_terms": 0, "processed_pages_total": 0, "processed_files": 2,
                        "output_file": "o"}))
    run_cmd.main()
    assert captured["backend"] == "ollama" and captured["cborg_api_key"] is None


def test_a_failed_pipeline_exits_1_with_its_message(monkeypatch, tmp_path):
    _argv(monkeypatch, tmp_path, "--backend", "ollama")
    monkeypatch.setattr(term_extractor, "run_extraction", lambda **kw: {"status": "error", "message": "model unreachable"})
    with pytest.raises(SystemExit) as stop:
        run_cmd.main()
    assert stop.value.code == 1
    assert "Pipeline failed: model unreachable" in (tmp_path / "logs" / "r.log").read_text()


def test_cli_loads_the_dotenv_for_this_entry_point_before_main(monkeypatch):
    order: list[object] = []
    monkeypatch.setattr(dotenv_entry, "load_entrypoint_dotenv", lambda anchor: order.append(("dotenv", anchor)))
    monkeypatch.setattr(run_cmd, "main", lambda: order.append("main"))
    run_cmd.cli()
    assert order == [("dotenv", run_cmd.__file__), "main"]
