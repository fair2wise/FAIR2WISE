import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import download_pdfs


class FakeResponse:
    def __init__(self, chunks, *, content_type="application/pdf"):
        self._chunks = chunks
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield from self._chunks


def test_download_pdf_saves_valid_pdf_response(tmp_path, monkeypatch):
    dest = tmp_path / "paper.pdf"
    resp = FakeResponse([b"%P", b"DF-1.4\nbody"])

    monkeypatch.setattr(download_pdfs.requests, "get", lambda *args, **kwargs: resp)
    monkeypatch.setattr(download_pdfs.time, "sleep", lambda seconds: None)

    assert download_pdfs.download_pdf("https://example.test/paper.pdf", str(dest))
    assert dest.read_bytes() == b"%PDF-1.4\nbody"
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_pdf_rejects_html_response(tmp_path, monkeypatch):
    dest = tmp_path / "landing-page.pdf"
    resp = FakeResponse(
        [b"<html><title>Article landing page</title></html>"],
        content_type="text/html",
    )

    monkeypatch.setattr(download_pdfs.requests, "get", lambda *args, **kwargs: resp)
    monkeypatch.setattr(download_pdfs.time, "sleep", lambda seconds: None)

    assert not download_pdfs.download_pdf("https://example.test/article", str(dest))
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()
