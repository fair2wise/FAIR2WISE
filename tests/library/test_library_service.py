"""T35: LibraryService: local metadata (no network), upload hardening, sha256 dedupe, verify and its cache."""

from __future__ import annotations

import os
from pathlib import Path
import socket
from typing import Any

from fastapi import HTTPException
import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.library.catalog import paper_id
from app.modules.library.handlers import library_handlers
from app.modules.library.probe import ProbeResult, clean_title, isolated_probe, probe_pdf
from app.modules.library.service import NOT_MOUNTED, LibraryService, VerifyUnavailable, clean_upload_name
from app.modules.literature.openalex import OpenAlexNoData, QuotaBreaker
from tests.literature.lit_fakes import have_pdf_stack, local_ctx, minimal_pdf_bytes, openalex_work
from tests.security.security_fakes import local_settings

TITLE = "Resonant soft X-ray scattering of polymer blend morphology"
DOI = "10.1021/acs.macromol.0c01782"
TEXT = f"{TITLE}\nA. Author\nhttps://doi.org/{DOI}\nAbstract: lamellar domains"


class FakeProbe:
    """A probe that never opens a PDF: ``name -> ProbeResult`` (default: readable, no title or DOI)."""

    def __init__(self, results: dict[str, ProbeResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[str] = []

    def __call__(self, path: Path) -> ProbeResult:
        self.calls.append(path.name)
        return self.results.get(path.name, ProbeResult(status="ok", pages=1, page_text="no identifiers here"))


class Registry:
    """Offline OpenAlex: ``fetch("works", {"filter": "doi:X"})``; counts calls."""

    def __init__(self, works: dict[str, Any] | None = None) -> None:
        self.works = {k.lower(): v for k, v in (works or {}).items()}
        self.calls = 0

    def __call__(self, path: str, params: dict[str, Any] | None = None, **_kw: Any) -> Any:
        self.calls += 1
        doi = (params or {}).get("filter", "").split("doi:", 1)[-1].lower()
        value = self.works.get(doi)
        if isinstance(value, OpenAlexNoData):
            return value
        return {"results": [value] if value else []}


def make(tmp_path: Path, *, probe: Any = None, registry: Any = None, mounted: bool = True,
         breaker: QuotaBreaker | None = None, **kw: Any) -> tuple[LibraryService, Path]:
    lib = tmp_path / "papers"
    if mounted:
        lib.mkdir()
    factory = (lambda: registry) if registry is not None else None
    service = LibraryService(lib, tmp_path / "state" / "library" / "catalog.sqlite", settings=local_settings(),
                             probe=probe or FakeProbe(), fetch_factory=factory, breaker=breaker or QuotaBreaker(), **kw)
    return service, lib


def pdf(text: str = "x") -> bytes:
    return minimal_pdf_bytes(text)


def test_not_mounted_reports_and_refuses_writes(tmp_path):
    service, _ = make(tmp_path, mounted=False)
    assert service.status() == {"mounted": False, "paper_count": 0, "indexed_count": 0, "quota_open": False}
    assert service.list_papers()["papers"] == []
    with pytest.raises(ContractError) as err:
        service.upload("a.pdf", pdf())
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY and str(err.value) == NOT_MOUNTED


def test_listing_reads_local_metadata_without_a_network(tmp_path, monkeypatch):
    def blocked(*a: Any, **k: Any) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    probe = FakeProbe({"a.pdf": ProbeResult(status="ok", title="A real title", pages=3, page_text=TEXT)})
    service, lib = make(tmp_path, probe=probe, registry=Registry())  # a registry exists but must stay unused
    (lib / "a.pdf").write_bytes(pdf("a"))
    (lib / "10.1021_acs.macromol.0c01782.pdf").write_bytes(pdf("b"))
    (lib / "plain.pdf").write_bytes(pdf("c"))
    out = service.list_papers()
    by = {p["file_name"]: p for p in out["papers"]}
    assert by["a.pdf"]["title"] == "A real title" and by["a.pdf"]["doi"] == DOI
    assert by["10.1021_acs.macromol.0c01782.pdf"]["doi"] == DOI  # from the file name
    assert by["plain.pdf"]["title"] is None and by["plain.pdf"]["doi"] is None
    assert all(p["verify_status"] == "unverified" and p["index_status"] is None for p in out["papers"])
    assert out["total"] == 3 and service._fetch is None  # the registry was never even built
    assert service.status()["paper_count"] == 3


def test_real_probe_reads_a_pdf_in_process_without_a_network(tmp_path, monkeypatch):
    if not have_pdf_stack():
        pytest.skip("pypdfium2 not installed")
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("net")))
    path = tmp_path / "p.pdf"
    path.write_bytes(minimal_pdf_bytes(f"see doi.org/{DOI} Resonant soft X-ray scattering"))
    result = probe_pdf(path)
    assert result.ok and result.pages == 1 and DOI in result.text
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really")
    assert probe_pdf(bad).status == "unreadable"


def test_isolated_probe_runs_a_child_and_survives_hostile_input(tmp_path):
    if not have_pdf_stack():
        pytest.skip("pypdfium2 not installed")
    path = tmp_path / "p.pdf"
    path.write_bytes(minimal_pdf_bytes(f"doi.org/{DOI}"))
    result = isolated_probe(path, timeout=60)
    assert result.ok and DOI in result.text
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"%PDF-1.7\n" + os.urandom(4096))
    assert isolated_probe(junk, timeout=60).status in ("unreadable", "error")
    assert isolated_probe(tmp_path / "missing.pdf", timeout=60).status in ("unreadable", "error")


def test_isolated_probe_times_out(tmp_path, monkeypatch):
    import subprocess

    def hang(cmd: Any, **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(subprocess, "run", hang)
    assert isolated_probe(tmp_path / "x.pdf", timeout=1).status == "timeout"


def test_clean_title_drops_generator_noise():
    assert clean_title("  A  Good\nTitle ") == "A Good Title"
    for noise in ("", "Untitled", "Microsoft Word - draft.docx", "paper.pdf", "12345", "ab"):
        assert clean_title(noise) == ""


def test_probe_budget_leaves_the_rest_pending_then_fills_them(tmp_path):
    probe = FakeProbe()
    service, lib = make(tmp_path, probe=probe, max_probe=2)
    for i in range(5):
        (lib / f"p{i}.pdf").write_bytes(pdf(str(i)))
    first = service.list_papers()
    assert first["pending_metadata"] == 3 and len(probe.calls) == 2
    assert sorted(p["metadata_status"] for p in first["papers"]).count("pending") == 3
    service.list_papers()
    service.list_papers()
    assert service.list_papers()["pending_metadata"] == 0 and len(probe.calls) == 5  # each file probed once


def test_removed_files_leave_the_catalog_and_changed_bytes_lose_the_verification(tmp_path):
    registry = Registry({DOI: openalex_work(DOI, TITLE)})
    probe = FakeProbe({"a.pdf": ProbeResult(status="ok", title="local", page_text=TEXT)})
    service, lib = make(tmp_path, probe=probe, registry=registry)
    (lib / "a.pdf").write_bytes(pdf("one"))
    pid = paper_id("a.pdf")
    service.list_papers()
    assert service.verify_paper(pid, local_ctx())["paper"]["verify_status"] == "verified"
    (lib / "a.pdf").write_bytes(pdf("two, different bytes"))
    os.utime(lib / "a.pdf", (1, 1))  # a different mtime so the change is seen on any filesystem
    assert service.list_papers()["papers"][0]["verify_status"] == "unverified"
    (lib / "a.pdf").unlink()
    assert service.list_papers()["papers"] == []


# ---- upload ---------------------------------------------------------------------------------------


def test_upload_stores_atomically_and_dedupes_by_sha256(tmp_path):
    service, lib = make(tmp_path)
    data = pdf("hello")
    first = service.upload("Paper One.pdf", data)
    assert first["duplicate"] is False and first["paper"]["relpath"] == "Paper One.pdf"
    stored = lib / "Paper One.pdf"
    assert stored.read_bytes() == data and stored.stat().st_uid == os.getuid() and not stored.is_symlink()
    assert [p.name for p in lib.iterdir()] == ["Paper One.pdf"]  # no temporary file left behind
    again = service.upload("another name.pdf", data)
    assert again["duplicate"] is True and again["paper"]["id"] == first["paper"]["id"]
    assert [p.name for p in lib.iterdir()] == ["Paper One.pdf"]  # nothing written for a duplicate
    assert service.status()["paper_count"] == 1


def test_upload_dedupes_against_files_already_in_the_folder(tmp_path):
    service, lib = make(tmp_path)
    data = pdf("preexisting")
    (lib / "old.pdf").write_bytes(data)
    out = service.upload("new.pdf", data)
    assert out["duplicate"] is True and out["paper"]["relpath"] == "old.pdf"


def test_same_name_different_bytes_never_overwrites(tmp_path):
    service, lib = make(tmp_path)
    service.upload("a.pdf", pdf("one"))
    second = service.upload("a.pdf", pdf("two"))
    assert second["paper"]["relpath"] == "a (1).pdf"
    assert (lib / "a.pdf").read_bytes() == pdf("one")


def test_upload_name_is_reduced_to_a_plain_pdf_name(tmp_path):
    service, lib = make(tmp_path)
    assert service.upload("../../etc/evil.pdf", pdf("1"))["paper"]["relpath"] == "evil.pdf"
    assert service.upload("C:\\Users\\x\\win.PDF", pdf("2"))["paper"]["relpath"] == "win.PDF"
    assert service.upload("noext", pdf("3"))["paper"]["relpath"] == "noext.pdf"
    assert not (tmp_path / "etc").exists() and sorted(p.name for p in lib.iterdir()) == ["evil.pdf", "noext.pdf", "win.PDF"]


@pytest.mark.parametrize("name", ["", None, "..", ".hidden.pdf", "a\x00b.pdf", "x" * 300 + ".pdf", "bell\x07.pdf"])
def test_unsafe_names_are_422(tmp_path, name):
    service, lib = make(tmp_path)
    with pytest.raises(HTTPException) as err:
        service.upload(name, pdf())
    assert err.value.status_code == 422 and list(lib.iterdir()) == []


def test_non_pdf_and_oversize_are_refused(tmp_path):
    service, lib = make(tmp_path, max_upload_bytes=1000)
    with pytest.raises(HTTPException) as err:
        service.upload("a.pdf", b"MZ\x90\x00 not a pdf at all")
    assert err.value.status_code == 415
    with pytest.raises(HTTPException) as err:
        service.upload("big.pdf", b"%PDF-1.4\n" + b"x" * 2000)
    assert err.value.status_code == 413
    assert list(lib.iterdir()) == []


def test_upload_never_follows_or_replaces_a_link(tmp_path):
    service, lib = make(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    (lib / "trap.pdf").symlink_to(outside)  # a link already holding the name
    (lib / "dangling.pdf").symlink_to(tmp_path / "nowhere.pdf")
    out = service.upload("trap.pdf", pdf("t"))
    assert out["paper"]["relpath"] == "trap (1).pdf" and outside.read_bytes() == b"%PDF-1.4 secret"
    out = service.upload("dangling.pdf", pdf("d"))
    assert out["paper"]["relpath"] == "dangling (1).pdf" and not (tmp_path / "nowhere.pdf").exists()
    assert "trap.pdf" not in [p["relpath"] for p in service.list_papers()["papers"]]  # links are not papers


def test_a_symlinked_upload_name_to_a_directory_is_not_written_through(tmp_path):
    service, lib = make(tmp_path)
    (tmp_path / "outdir").mkdir()
    (lib / "d.pdf").symlink_to(tmp_path / "outdir", target_is_directory=True)
    out = service.upload("d.pdf", pdf("z"))
    assert out["paper"]["relpath"] == "d (1).pdf" and list((tmp_path / "outdir").iterdir()) == []


def test_clean_upload_name_helper():
    assert clean_upload_name("dir/sub/a b.pdf") == "a b.pdf"


# ---- verify ---------------------------------------------------------------------------------------


def _verified_service(tmp_path, **kw):
    registry = Registry({DOI: openalex_work(DOI, TITLE)})
    probe = FakeProbe({"a.pdf": ProbeResult(status="ok", title="local title", page_text=TEXT),
                       "b.pdf": ProbeResult(status="ok", page_text="Unrelated words only, long enough to count as text. 10.9999/none.such " * 2)})
    service, lib = make(tmp_path, probe=probe, registry=registry, **kw)
    (lib / "a.pdf").write_bytes(pdf("a"))
    (lib / "b.pdf").write_bytes(pdf("b"))
    return service, lib, registry


def test_verify_caches_the_registry_title_and_doi_by_sha256(tmp_path):
    service, lib, registry = _verified_service(tmp_path)
    service.list_papers()
    a = service.verify_paper(paper_id("a.pdf"), local_ctx())["paper"]
    assert a["verify_status"] == "verified" and a["title"] == TITLE and a["doi"] == DOI and a["verified_at"]
    calls = registry.calls
    again = service.verify_paper(paper_id("a.pdf"), local_ctx())["paper"]
    assert again == a and registry.calls == calls  # cached: no lookup
    b = service.verify_paper(paper_id("b.pdf"), local_ctx())["paper"]
    assert b["verify_status"] == "not_found"
    (lib / "copy.pdf").write_bytes((lib / "a.pdf").read_bytes())  # same bytes under another name
    listed = {p["file_name"]: p for p in service.list_papers()["papers"]}
    assert listed["copy.pdf"]["verify_status"] == "verified" and listed["copy.pdf"]["title"] == TITLE
    assert service.verify_paper(paper_id("a.pdf"), local_ctx(), force=True)["paper"]["verify_status"] == "verified"
    assert registry.calls > calls


def test_verify_reports_an_open_quota_breaker_and_does_not_cache_it(tmp_path):
    breaker = QuotaBreaker()
    service, lib, registry = _verified_service(tmp_path, breaker=breaker)
    service.list_papers()
    breaker.pause(3600, "credits exhausted")

    class Paused(Registry):
        def __call__(self, *a: Any, **k: Any) -> Any:  # what OpenAlexClient.fetch answers while the breaker is open
            return OpenAlexNoData("quota_paused", resume_at=breaker.state()["resume_at"], detail="credits exhausted")

    service._fetch = Paused()
    assert service.status()["quota_open"] is True
    paper = service.verify_paper(paper_id("a.pdf"), local_ctx())["paper"]
    assert paper["verify_status"] == "quota_open" and paper["verify_detail"]
    assert service.list_papers()["papers"][0]["verify_status"] == "unverified"  # not cached
    breaker.reset()
    service._fetch = registry
    assert service.verify_paper(paper_id("a.pdf"), local_ctx())["paper"]["verify_status"] == "verified"


def test_verify_without_a_registry_key_is_an_error_status_not_a_crash(tmp_path):
    def no_key() -> Any:
        raise VerifyUnavailable("OPENALEX_API_KEY is not configured")

    service, lib = make(tmp_path, probe=FakeProbe({"a.pdf": ProbeResult(status="ok", page_text=TEXT)}))
    service._fetch_factory = no_key
    (lib / "a.pdf").write_bytes(pdf("a"))
    service.list_papers()
    paper = service.verify_paper(paper_id("a.pdf"), local_ctx())["paper"]
    assert paper["verify_status"] == "error" and "OPENALEX_API_KEY" in paper["verify_detail"]


def test_verify_of_an_unreadable_pdf_is_an_error_and_unknown_id_is_404(tmp_path):
    service, lib = make(tmp_path, probe=FakeProbe({"a.pdf": ProbeResult(status="timeout")}), registry=Registry())
    (lib / "a.pdf").write_bytes(pdf("a"))
    service.list_papers()
    paper = service.verify_paper(paper_id("a.pdf"), local_ctx())["paper"]
    assert paper["verify_status"] == "error" and "timeout" in paper["verify_detail"]
    with pytest.raises(ContractError) as err:
        service.verify_paper("0" * 16, local_ctx())
    assert err.value.code is ErrorCode.NOT_FOUND


def test_verify_all_job_handler_runs_and_stops_on_an_open_breaker(tmp_path):
    from app.modules.jobs import JobService, JobStore
    from app.modules.contracts.jobs import JobState
    from app.modules.library import LIBRARY_VERIFY_KIND, library_inputs, register_library_jobs

    breaker = QuotaBreaker()
    service, lib, registry = _verified_service(tmp_path, breaker=breaker)
    settings = local_settings()
    jobs = JobService(JobStore(tmp_path / "jobs.sqlite3"), settings=settings)
    register_library_jobs(jobs, settings, handlers=library_handlers(service))
    handle = jobs.submit(service.worker_context("submit"), LIBRARY_VERIFY_KIND, library_inputs())
    assert jobs.work_one("w") == (handle.job_id, JobState.SUCCEEDED)
    rows = {p["file_name"]: p["verify_status"] for p in service.list_papers()["papers"]}
    assert rows == {"a.pdf": "verified", "b.pdf": "not_found"}

    (lib / "c.pdf").write_bytes(pdf("c"))
    service._probe.results["c.pdf"] = ProbeResult(status="ok", page_text=TEXT)
    breaker.pause(3600, "credits")
    service._fetch = lambda *a, **k: OpenAlexNoData("quota_paused", resume_at=breaker.state()["resume_at"])
    handle2 = jobs.submit(service.worker_context("submit"), LIBRARY_VERIFY_KIND, library_inputs(nonce="2"))
    assert jobs.work_one("w") == (handle2.job_id, JobState.FAILED)  # incomplete coverage: retryable
    assert any("quota" in log.message for log in jobs.store.logs(handle2.job_id))


def test_paper_facts_feed_the_t38_estimate_lookup(tmp_path):
    service, _ = make(tmp_path)
    paper = service.upload("Facts.pdf", pdf("facts"))["paper"]
    facts = service.paper_facts(paper["id"])
    assert (facts.id, facts.name, facts.sha256, facts.pages) == (paper["id"], "Facts.pdf", paper["sha256"], None)
    assert service.paper_facts("0" * 16) is None
