"""T05 slice 4: guarded downloads, local-path policy, upload limits, OA acquisition, DownloadAgent parity."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.literature.acquire import PaperAcquirer, arxiv_url_for
from app.modules.literature.legacy import LegacyIndex
from app.modules.literature.openalex import OpenAlexSettings
from app.modules.literature.safe_fetch import (
    BlockedURL, LocalIntakePolicy, NetworkPolicy, SafeFetcher, UploadTooLarge, assert_public_http_url,
    check_declared_length, is_public_address, limited_stream,
)
from app.modules.literature.store import FileDocumentStore
from tests.literature.lit_fakes import Router, als_user_ctx, local_ctx, minimal_pdf_bytes, openalex_work

PDF = minimal_pdf_bytes("downloaded paper")

DNS = {
    "arxiv.org": ["151.101.3.42"], "export.arxiv.org": ["128.84.21.199"], "europepmc.org": ["193.62.193.80"],
    "api.unpaywall.org": ["104.26.0.1"], "api.semanticscholar.org": ["52.84.0.1"],
    "evil.example": ["169.254.169.254"], "rebind.example": ["93.184.216.34", "10.0.0.5"],
    "mapped.example": ["::ffff:127.0.0.1"], "cgnat.example": ["100.64.1.1"],
    "publisher.example": ["93.184.216.34"],
}


def resolver(host: str):
    if host not in DNS:
        raise OSError("NXDOMAIN")
    return DNS[host]


POLICY = NetworkPolicy(resolver=resolver, max_bytes=50_000)


@pytest.mark.parametrize("url,reason", [
    ("file:///etc/passwd", "scheme"), ("ftp://arxiv.org/x.pdf", "scheme"), ("gopher://x", "scheme"),
    ("http://127.0.0.1/x.pdf", "private_address"), ("http://[::1]/x.pdf", "private_address"),
    ("http://169.254.169.254/latest/meta-data", "private_address"), ("http://10.1.2.3/x", "private_address"),
    ("https://evil.example/x.pdf", "private_address"), ("https://rebind.example/x", "private_address"),
    ("https://mapped.example/x", "private_address"), ("https://cgnat.example/x", "private_address"),
    ("https://user:pw@arxiv.org/x.pdf", "credentials_in_url"), ("https://arxiv.org:8443/x.pdf", "port"),
    ("https://nowhere.example/x.pdf", "unresolvable"), ("https:///x.pdf", "no_host"),
])
def test_policy_blocks(url, reason):
    with pytest.raises(BlockedURL) as err:
        POLICY.check_url(url)
    assert err.value.reason == reason
    with pytest.raises(ValueError):
        assert_public_http_url(url, POLICY)


def test_public_addresses():
    assert is_public_address("151.101.3.42") and is_public_address("2606:4700::6810:84e5")
    assert not is_public_address("192.168.1.1") and not is_public_address("fe80::1") and not is_public_address("x")
    assert POLICY.check_url("https://arxiv.org/pdf/2101.00001") == "https://arxiv.org/pdf/2101.00001"


def fetcher_for(router: Router, **policy) -> SafeFetcher:
    return SafeFetcher(router.client(), NetworkPolicy(resolver=resolver, **{"max_bytes": 50_000, **policy}))


@pytest.fixture
def store(tmp_path):
    return FileDocumentStore(tmp_path / "candidate-store")


def test_fetch_stores_pdf_after_validated_redirect(store):
    router = Router()
    router.add("GET", "europepmc.org", "/articles/", lambda r: httpx.Response(
        302, headers={"location": "https://arxiv.org/pdf/2101.00001"}))
    router.add("GET", "arxiv.org", "/pdf/", (200, PDF))
    result = fetcher_for(router).fetch_pdf(local_ctx(), "https://europepmc.org/articles/PMC1?pdf=render", store)
    assert result.ok and result.redirects == ["https://arxiv.org/pdf/2101.00001"]
    assert store.get(local_ctx(), result.ref) == PDF
    assert store.describe(local_ctx(), result.ref).kind == "source_pdf"


@pytest.mark.parametrize("location", [
    "http://169.254.169.254/latest/meta-data/iam", "file:///etc/passwd", "http://localhost/admin",
    "https://evil.example/x.pdf", "//10.0.0.7/x.pdf",
])
def test_blocked_redirect_target_fetches_nothing(store, location):
    router = Router().add("GET", "arxiv.org", "/abs/",
                          lambda r: httpx.Response(302, headers={"location": location}))
    result = fetcher_for(router).fetch_pdf(local_ctx(), "https://arxiv.org/abs/2101.00001", store)
    assert result.status == "blocked:redirect_target"
    assert len(router.calls) == 1 and list(store.list_refs(local_ctx())) == []


def test_redirect_loop_is_bounded(store):
    router = Router().add("GET", "arxiv.org", "/",
                          lambda r: httpx.Response(302, headers={"location": r.url.path + "x"}))
    result = fetcher_for(router, max_redirects=3).fetch_pdf(local_ctx(), "https://arxiv.org/a", store)
    assert result.status == "blocked:too_many_redirects" and len(router.calls) == 4


def test_html_landing_page_and_oversize_bodies_are_refused(store):
    router = Router().add("GET", "arxiv.org", "/landing", (200, b"<!doctype html><html>" + b"x" * 5000))
    router.add("GET", "arxiv.org", "/declared", lambda r: httpx.Response(
        200, headers={"content-length": "999999999"}, content=PDF))
    router.add("GET", "arxiv.org", "/streamed", (200, PDF + b"%" + b"y" * 60_000))
    router.add("GET", "arxiv.org", "/missing", (404, b"nope"))
    f = fetcher_for(router)
    assert f.fetch_pdf(local_ctx(), "https://arxiv.org/landing", store).status == "refused:not_pdf"
    assert f.fetch_pdf(local_ctx(), "https://arxiv.org/declared", store).status == "refused:too_large"
    assert f.fetch_pdf(local_ctx(), "https://arxiv.org/streamed", store).status == "refused:too_large"
    assert f.fetch_pdf(local_ctx(), "https://arxiv.org/missing", store).status == "failed:http_404"
    assert list(store.list_refs(local_ctx())) == []
    assert list((store.root / "tmp").iterdir()) == []


def test_timeouts_and_network_errors(store):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def broken(request):
        raise httpx.ConnectError("refused", request=request)

    router = Router().add("GET", "arxiv.org", "/slow", slow).add("GET", "arxiv.org", "/broken", broken)
    f = fetcher_for(router)
    assert f.fetch_pdf(local_ctx(), "https://arxiv.org/slow", store).status == "failed:timeout"
    assert f.fetch_pdf(local_ctx(), "https://arxiv.org/broken", store).status == "failed:network"
    ticks = iter([0.0, 0.0, 500.0, 500.0, 500.0])
    router2 = Router().add("GET", "arxiv.org", "/pdf", (200, PDF))
    deadline = SafeFetcher(router2.client(), NetworkPolicy(resolver=resolver, total_timeout=10),
                           clock=lambda: next(ticks))
    assert deadline.fetch_pdf(local_ctx(), "https://arxiv.org/pdf", store).status == "failed:timeout"


# -- local intake and uploads --------------------------------------------------------------------

def test_local_policy_roots_and_symlinks(tmp_path):
    root, outside = tmp_path / "intake", tmp_path / "outside"
    (root / "sub").mkdir(parents=True)
    outside.mkdir()
    (root / "sub" / "a.pdf").write_bytes(PDF)
    (outside / "secret.pdf").write_bytes(PDF)
    (root / "escape.pdf").symlink_to(outside / "secret.pdf")
    (root / "inner.pdf").symlink_to(root / "sub" / "a.pdf")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    policy = LocalIntakePolicy.from_paths([root], max_bytes=len(PDF) + 10)
    assert policy.require_allowed(root / "sub" / "a.pdf") == (root / "sub" / "a.pdf").resolve()
    assert policy.require_allowed(root / "sub" / ".." / "sub" / "a.pdf")
    for bad in (outside / "secret.pdf", root / "escape.pdf", root / "linked" / "secret.pdf",
                root / ".." / "outside" / "secret.pdf", root / "inner.pdf", root / "missing.pdf"):
        with pytest.raises(ValueError):
            policy.require_allowed(bad)
    relaxed = LocalIntakePolicy.from_paths([root], allow_symlinks=True)
    assert relaxed.require_allowed(root / "inner.pdf") == (root / "sub" / "a.pdf").resolve()
    with pytest.raises(ValueError):
        relaxed.require_allowed(root / "escape.pdf")  # resolves outside: still refused
    (root / "sub" / "big.pdf").write_bytes(PDF * 3)
    with pytest.raises(ValueError):
        policy.require_allowed(root / "sub" / "big.pdf")
    assert policy.require_allowed_folder(root / "sub").name == "sub"
    with pytest.raises(ValueError):
        policy.require_allowed_folder(tmp_path)


def test_giant_upload_refused_before_and_during_streaming():
    with pytest.raises(UploadTooLarge):
        check_declared_length({"Content-Length": "10485761"}, 10 * 1024 * 1024)
    check_declared_length({"content-type": "multipart/form-data"}, 10)  # unknown length: enforced later
    stream = limited_stream(iter([b"a" * 6, b"b" * 6]), 10)
    assert next(stream) == b"a" * 6
    with pytest.raises(UploadTooLarge):
        next(stream)


# -- acquisition ---------------------------------------------------------------------------------

def _acquirer(store, router, *, mailto="real.person@lbl.gov"):
    index = LegacyIndex(store)
    return PaperAcquirer(store, index, SafeFetcher(router.client(), POLICY), router.client(),
                         OpenAlexSettings(mailto=mailto)), index


def test_acquire_uses_cache_before_network(store, tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "10.1021_acs.macromol.0c01782.pdf").write_bytes(PDF)
    router = Router()
    acquirer, index = _acquirer(store, router)
    index.adopt_tree(local_ctx(), "papers", papers)
    result = acquirer.acquire(als_user_ctx(), "https://doi.org/10.1021/acs.macromol.0c01782")
    assert result.status == "cached" and router.calls == []  # an ordinary user may read what is stored


def test_acquire_requires_capability_and_falls_back_through_routes(store):
    router = Router()
    router.add("GET", "api.unpaywall.org", "/v2/", (200, {"best_oa_location": {
        "url_for_pdf": "https://publisher.example/paper.pdf", "host_type": "publisher", "is_oa": True}}))
    router.add("GET", "publisher.example", "/", (200, PDF))
    router.add("GET", "api.semanticscholar.org", "/graph/v1/paper/", (200, {"openAccessPdf": None}))
    router.add("GET", "arxiv.org", "/pdf/", (503, b"busy"))
    acquirer, index = _acquirer(store, router)
    doi = "10.48550/arXiv.2101.00001"
    with pytest.raises(ContractError) as err:
        acquirer.acquire(als_user_ctx(), doi)
    assert err.value.code is ErrorCode.DENIED
    result = acquirer.acquire(local_ctx(), doi)
    # Repository copies are tried first (F2W rule, B22); the listed publisher PDF is the fallback.
    assert result.status == "acquired" and result.source_url == "https://publisher.example/paper.pdf"
    assert [(a["url"], a["status"]) for a in result.attempts] == [
        ("https://arxiv.org/pdf/2101.00001", "failed:http_503"), ("https://publisher.example/paper.pdf", "stored")]
    assert result.lookups == {"unpaywall": "ok", "semantic_scholar": "ok"}
    assert index.find_by_doi(doi) == result.ref
    assert arxiv_url_for("10.1/x") == ""


def test_acquire_reports_no_oa_pdf_and_skips_unpaywall_without_email(store):
    router = Router().add("GET", "api.semanticscholar.org", "/graph/v1/paper/", (404, {}))
    work = openalex_work("10.1/closed", "Closed paper")
    acquirer, _ = _acquirer(store, router, mailto="your-email@example.com")
    result = acquirer.acquire(local_ctx(), "10.1/closed", work=work)
    assert result.status == "no_oa_pdf" and result.lookups["unpaywall"] == "skipped:no_contact_email"
    assert acquirer.acquire(local_ctx(), "nonsense").status == "invalid_doi"


def test_corrupt_cached_source_is_not_served(store, tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    target = papers / "10.1_x.pdf"
    target.write_bytes(PDF)
    router = Router().add("GET", "api.semanticscholar.org", "/graph/v1/paper/", (404, {}))
    acquirer, index = _acquirer(store, router, mailto="")
    index.adopt_tree(local_ctx(), "papers", papers)  # reference mode
    target.write_bytes(PDF.replace(b"downloaded", b"tampered!!"))
    assert acquirer.cached(local_ctx(), "10.1/x") is None
    assert acquirer.acquire(local_ctx(), "10.1/x").status == "no_oa_pdf"


# -- DownloadAgent parity (B22) ------------------------------------------------------------------

def _run_agent(tmp_path, monkeypatch, **store_kw):
    from app.modules.f2w_agent.download_agent import DownloadAgent
    from app.modules.literature import download_pdfs

    def fake_download(url, dest):
        Path(dest).write_bytes(PDF)
        return True

    monkeypatch.setattr(download_pdfs, "download_pdf", fake_download)
    agent = DownloadAgent(backend="ollama", model="test", download_delay_seconds=0, validate_downloads=False,
                          **store_kw)
    candidate = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1234/example", "title": "T",
                 "pdf_urls": ["https://arxiv.org/pdf/2401.00001"], "_score": 0.9}
    target = tmp_path / "pdfs"
    first = agent._download_candidates("q", [], str(target), [candidate], 1)
    second = agent._download_candidates("q", [], str(target), [candidate], 1)  # skipped_existing
    lines = [json.loads(x) for x in (tmp_path / "downloads.jsonl").read_text().splitlines()]
    for line in lines:
        line.pop("ts")
    return first, second, lines


def test_download_agent_parity_with_and_without_shared_store(tmp_path, monkeypatch):
    plain = _run_agent(tmp_path / "plain", monkeypatch)
    store = FileDocumentStore(tmp_path / "store")
    index = LegacyIndex(store)
    shared = _run_agent(tmp_path / "shared", monkeypatch, document_store=store, legacy_index=index,
                        store_context=local_ctx())

    def scrub(obj):
        return json.loads(json.dumps(obj).replace(str(tmp_path / "plain"), "<T>").replace(
            str(tmp_path / "shared"), "<T>"))

    assert scrub(plain) == scrub(shared)  # same results and same downloads.jsonl records
    assert index.find_by_doi("10.1234/example") is not None
    names = index.names_for(index.find_by_doi("10.1234/example"))
    assert len(names) == 1 and names[0].endswith("/10.1234_example.pdf")
    assert (tmp_path / "shared" / "pdfs" / "10.1234_example.pdf").exists()  # legacy file stays in place


def test_download_agent_store_failure_does_not_change_result(tmp_path, monkeypatch):
    class Broken:
        def adopt_download(self, *a, **k):
            raise RuntimeError("disk full")

    first, _, _ = _run_agent(tmp_path, monkeypatch, legacy_index=Broken(), store_context=local_ctx())
    assert first["count"] == 1 and first["failed"] == 0
