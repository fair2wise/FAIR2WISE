"""library/probe.py edge paths (T27f): encrypted PDFs, child-process failure statuses, resource limits, the child entry."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

from app.modules.library import probe


class FakeDoc:
    def __init__(self, *, needs_pass=False, metadata=None, texts=("p1", "p2", "p3")):
        self.needs_pass, self.metadata, self.texts = needs_pass, metadata, list(texts)
        self.page_count = len(self.texts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def load_page(self, i):
        return types.SimpleNamespace(get_text=lambda: self.texts[i])


def _open_with(monkeypatch, doc):
    import app.modules.literature.pdf_reader as reader

    monkeypatch.setattr(reader, "open_pdf", lambda path: doc)


def test_encrypted_pdf_is_reported_without_reading_it(monkeypatch):
    _open_with(monkeypatch, FakeDoc(needs_pass=True))
    result = probe.probe_pdf("x.pdf")
    assert result.status == "encrypted" and not result.ok and result.text == ""


def test_only_the_leading_pages_and_non_empty_metadata_are_kept(monkeypatch):
    _open_with(monkeypatch, FakeDoc(metadata={"title": "A Real Title", "subject": "doi 10.1/x", "author": ""}))
    result = probe.probe_pdf(Path("x.pdf"))
    assert result.ok and result.title == "A Real Title" and result.pages == 3
    assert result.page_text == "p1\np2" and result.meta_text == "A Real Title\ndoi 10.1/x"
    assert result.text == "A Real Title\ndoi 10.1/x\np1\np2"


def test_missing_metadata_and_oversized_text_are_bounded(monkeypatch):
    monkeypatch.setattr(probe, "MAX_TEXT_CHARS", 5)
    _open_with(monkeypatch, FakeDoc(metadata=None, texts=("abcdefghij",)))
    result = probe.probe_pdf("x.pdf")
    assert result.ok and result.title == "" and result.meta_text == "" and result.page_text == "abcde"


def test_a_reader_exception_is_the_unreadable_status(monkeypatch):
    import app.modules.literature.pdf_reader as reader

    def boom(path):
        raise RuntimeError("pdfium crashed")

    monkeypatch.setattr(reader, "open_pdf", boom)
    assert probe.probe_pdf("x.pdf") == probe.ProbeResult(status="unreadable")


@pytest.mark.parametrize(("raw", "kept"), [
    ("  Spaced   out   title ", "Spaced out title"), ("abc", ""), ("Untitled", ""), ("Microsoft Word - draft", ""),
    ("12345 - 6", ""), ("paper.pdf", ""), ("x" * 400, "x" * probe.MAX_TITLE_CHARS), (None, "")])
def test_clean_title(raw, kept):
    assert probe.clean_title(raw) == kept


def test_child_environment_carries_no_credentials(monkeypatch):
    monkeypatch.setenv("CBORG_API_KEY", "sk-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    env = probe._child_env()
    assert set(env) == {"PATH", "LANG", "HF_HUB_OFFLINE"} and "sk-secret" not in json.dumps(env)


def _fake_run(monkeypatch, *, stdout=b"", returncode=0, raises=None):
    seen = {}

    def run(cmd, **kw):
        seen.update(cmd=cmd, kw=kw)
        if raises:
            raise raises
        return types.SimpleNamespace(stdout=stdout, returncode=returncode)

    monkeypatch.setattr(probe.subprocess, "run", run)
    return seen


def test_isolated_probe_runs_an_isolated_child_with_a_minimal_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CBORG_API_KEY", "sk-secret")
    out = json.dumps({"status": "ok", "title": "T", "pages": 2, "meta_text": "m", "page_text": "p"}).encode()
    seen = _fake_run(monkeypatch, stdout=out)
    result = probe.isolated_probe(tmp_path / "a.pdf", timeout=7.9)
    assert result == probe.ProbeResult(status="ok", title="T", pages=2, meta_text="m", page_text="p")
    cmd, kw = seen["cmd"], seen["kw"]
    assert cmd[0] == sys.executable and cmd[1] == "-I" and cmd[-3:] == [cmd[-3], str(tmp_path / "a.pdf"), "7"]
    assert kw["timeout"] == 7.9 and kw["stdin"] == subprocess.DEVNULL and kw["env"] == probe._child_env()
    assert "shell" not in kw and "sk-secret" not in " ".join(cmd) + json.dumps(kw["env"])


@pytest.mark.parametrize(("kwargs", "status"), [
    ({"raises": subprocess.TimeoutExpired("c", 1)}, "timeout"),
    ({"raises": FileNotFoundError("no interpreter")}, "error"),
    ({"returncode": -9, "stdout": b"{}"}, "error"),
    ({"stdout": b"not json"}, "error"),
    ({"stdout": b'{"title": "no status"}'}, "error"),
    ({"stdout": b'{"status": "ok", "pages": "many"}'}, "error"),
    ({"stdout": b"[1, 2]"}, "error"),
])
def test_isolated_probe_turns_every_child_failure_into_a_status(monkeypatch, kwargs, status):
    _fake_run(monkeypatch, **kwargs)
    assert probe.isolated_probe("a.pdf").status == status


def test_resource_limits_are_set_from_the_timeout(monkeypatch):
    import resource

    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda which, limits: calls.append((which, limits)))
    monkeypatch.setattr(probe.sys, "platform", "linux")
    probe._limit_resources(10)
    assert (resource.RLIMIT_CPU, (11, 15)) in calls and (resource.RLIMIT_AS, (4 << 30, 4 << 30)) in calls
    calls.clear()
    monkeypatch.setattr(probe.sys, "platform", "darwin")
    probe._limit_resources(3)
    assert calls == [(resource.RLIMIT_CPU, (4, 8))]


def test_a_platform_that_refuses_limits_is_tolerated(monkeypatch, caplog):
    import resource

    def refuse(which, limits):
        raise ValueError("not supported")

    monkeypatch.setattr(resource, "setrlimit", refuse)
    with caplog.at_level("DEBUG", logger=probe.logger.name):
        probe._limit_resources(1)
    assert "resource limits not applied" in caplog.text


def test_child_main_prints_one_json_object_for_the_pdf(monkeypatch, tmp_path, capsys):
    limits = []
    monkeypatch.setattr(probe, "_limit_resources", limits.append)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(probe, "probe_pdf", lambda path: probe.ProbeResult(status="ok", title="T", pages=1, page_text="x"))
    assert probe._child_main(["probe.py", str(tmp_path), "doc.pdf", "5"]) == 0
    assert limits == [5] and sys.path[0] == str(tmp_path)
    assert json.loads(capsys.readouterr().out) == {"status": "ok", "title": "T", "pages": 1, "meta_text": "", "page_text": "x"}
