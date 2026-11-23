"""P3b: the authors/abstract backfill over the category manifests (fake sources only, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.modules.commands import backfill_library_metadata as cli
from app.modules.library.backfill import BackfillAbort, arxiv_fetcher, backfill, openalex_fetcher
from app.modules.library.probe import ProbeResult
from app.modules.library.service import LibraryService
from app.modules.literature import legacy_download
from app.modules.literature.legacy_download import JSONFetchError, RateLimiter, arxiv_lookup
from tests.library.test_library_authors_abstract import ATOM, WORK
from tests.library.test_library_service import pdf
from tests.security.security_fakes import local_settings


class Source:
    """A fake source: ``key -> {"authors", "abstract"}`` (``None``: not found) or an exception to raise."""

    def __init__(self, table: dict[str, Any]) -> None:
        self.table, self.calls = table, []

    def __call__(self, key: str) -> Any:
        self.calls.append(key)
        value = self.table.get(key)
        if isinstance(value, Exception):
            raise value
        return value


def row(n: str, **kw: Any) -> dict[str, Any]:
    base = {"doi": f"https://doi.org/10.1/{n}", "arxiv_id": None, "title": f"Paper {n}", "abstract": None,
            "pdf_path": f"papers/rsoxs/2020/{n}.pdf", "pdf_sha256": n}
    return {**base, **kw}


def write(lib: Path, rows: list[dict[str, Any]], category: str = "rsoxs") -> Path:
    (lib / category).mkdir(parents=True, exist_ok=True)
    path = lib / category / "manifest.json"
    path.write_text(json.dumps({"corpus": category, "papers": rows}), encoding="utf-8")
    return path


def read(lib: Path, category: str = "rsoxs") -> list[dict[str, Any]]:
    return json.loads((lib / category / "manifest.json").read_text())["papers"]


GOT = {"authors": ["A. One", "B. Two"], "abstract": "Found abstract."}


def run(lib: Path, oa: Source, ax: Source | None = None, **kw: Any):
    return backfill(lib, openalex=oa, arxiv=ax or Source({}), sleep=lambda s: None, **kw)


def test_dry_run_is_offline_and_writes_nothing(tmp_path):
    path = write(tmp_path, [row("a"), row("b", doi=None), row("c", abstract="x", authors=["Y"])])
    before = path.read_bytes()
    oa, ax = Source({}), Source({})
    report = run(tmp_path, oa, ax)
    assert oa.calls == [] and ax.calls == [] and path.read_bytes() == before
    assert not (tmp_path / "rsoxs" / ".manifest.lock").exists()
    assert report.counts()["would_fetch"] == 1 and report.counts()["unfillable"] == 1
    assert report.counts()["already_filled"] == 1
    assert report.to_dict()["mode"] == "dry-run"


def test_apply_fills_only_missing_fields_and_never_overwrites(tmp_path):
    write(tmp_path, [row("a"), row("b", abstract="Kept abstract"), row("c", authors=["Kept Author"])])
    oa = Source({"10.1/a": GOT, "10.1/b": GOT, "10.1/c": GOT})
    report = run(tmp_path, oa, apply=True)
    a, b, c = read(tmp_path)
    assert a["authors"] == ["A. One", "B. Two"] and a["abstract"] == "Found abstract."
    assert b["abstract"] == "Kept abstract" and b["authors"] == ["A. One", "B. Two"]
    assert c["authors"] == ["Kept Author"] and c["abstract"] == "Found abstract."
    assert report.counts()["updated"] == 3
    assert next(r for r in report.rows if r.pdf_path == "rsoxs/2020/b.pdf").filled == ["authors"]


def test_a_rerun_skips_filled_rows_and_asks_nothing(tmp_path):
    write(tmp_path, [row("a"), row("b")])
    run(tmp_path, Source({"10.1/a": GOT, "10.1/b": GOT}), apply=True)
    oa = Source({})
    report = run(tmp_path, oa, apply=True)
    assert oa.calls == [] and report.counts()["already_filled"] == 2 and report.counts()["updated"] == 0


def test_rows_without_doi_or_arxiv_id_are_unfillable_not_guessed(tmp_path):
    write(tmp_path, [row("a", doi=None), row("b", doi="", arxiv_id="not an id")])
    oa, ax = Source({}), Source({})
    report = run(tmp_path, oa, ax, apply=True)
    assert oa.calls == [] and ax.calls == [] and report.counts()["unfillable"] == 2
    assert read(tmp_path)[0]["abstract"] is None
    assert [u["pdf_path"] for u in report.to_dict()["unfillable"]] == ["rsoxs/2020/a.pdf", "rsoxs/2020/b.pdf"]


def test_arxiv_id_rows_use_arxiv_and_doi_rows_fall_back_to_it(tmp_path):
    write(tmp_path, [row("a", doi=None, arxiv_id="2101.00001v2"), row("b", arxiv_id="2102.00002")])
    oa = Source({"10.1/b": {"authors": [], "abstract": "From OpenAlex."}})
    ax = Source({"2101.00001v2": {"authors": ["Ada"], "abstract": "From arXiv."},
                 "2102.00002": {"authors": ["Grace"], "abstract": "ignored: already have one"}})
    run(tmp_path, oa, ax, apply=True)
    a, b = read(tmp_path)
    assert a["authors"] == ["Ada"] and a["abstract"] == "From arXiv."
    assert b["authors"] == ["Grace"] and b["abstract"] == "From OpenAlex."
    assert oa.calls == ["10.1/b"]  # the arXiv-only row never reaches OpenAlex


def test_not_found_no_data_and_failures_are_counted_with_short_codes(tmp_path):
    write(tmp_path, [row("a"), row("b"), row("c"), row("d"), row("e")])
    oa = Source({"10.1/b": {"authors": [], "abstract": None},
                 "10.1/c": JSONFetchError("HTTP 500", status=500),
                 "10.1/d": JSONFetchError("network error (ConnectError)"),
                 "10.1/e": ValueError("Unknown response format")})
    report = run(tmp_path, oa, apply=True)
    assert [r.outcome for r in report.rows] == ["not_found", "no_data", "failed", "failed", "failed"]
    assert report.failed_codes() == {"openalex_http_500": 1, "openalex_unreachable": 1, "openalex_bad_response": 1}
    assert all(r["abstract"] is None for r in read(tmp_path))


def test_quota_or_a_missing_key_stops_the_run_and_leaves_the_rest_deferred(tmp_path):
    write(tmp_path, [row("a"), row("b"), row("c")])
    oa = Source({"10.1/a": GOT, "10.1/b": BackfillAbort("openalex_quota")})
    report = run(tmp_path, oa, apply=True)
    assert report.stopped == "openalex_quota" and oa.calls == ["10.1/a", "10.1/b"]
    assert [r.outcome for r in report.rows] == ["updated", "deferred", "deferred"]
    assert read(tmp_path)[0]["authors"] == ["A. One", "B. Two"]


def test_limit_and_delay(tmp_path):
    write(tmp_path, [row("a"), row("b"), row("c")])
    pauses: list[float] = []
    oa = Source({"10.1/a": GOT, "10.1/b": GOT, "10.1/c": GOT})
    report = backfill(tmp_path, apply=True, limit=2, delay=2.5, openalex=oa, arxiv=Source({}), sleep=pauses.append)
    assert oa.calls == ["10.1/a", "10.1/b"] and pauses == [2.5]
    assert report.counts()["deferred"] == 1 and read(tmp_path)[2]["abstract"] is None
    assert run(tmp_path, Source({"10.1/c": GOT}), apply=True, limit=2).counts()["updated"] == 1  # resumed


def test_category_filter_unreadable_manifests_and_non_category_folders(tmp_path):
    write(tmp_path, [row("a")], "rsoxs")
    write(tmp_path, [row("b", pdf_path="papers/tomo/b.pdf")], "tomo")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "manifest.json").write_text("{not json")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "manifest.json").write_text(json.dumps({"papers": [row("z")]}))
    oa = Source({"10.1/a": GOT, "10.1/b": GOT, "10.1/z": GOT})
    report = run(tmp_path, oa, apply=True, category="tomo")
    assert oa.calls == ["10.1/b"] and read(tmp_path, "rsoxs")[0]["abstract"] is None
    report = run(tmp_path, oa, apply=True)
    assert report.unreadable_manifests == ["bad/manifest.json"] and "10.1/z" not in oa.calls
    assert read(tmp_path, "rsoxs")[0]["authors"] == ["A. One", "B. Two"]


def test_the_report_never_holds_bibliographic_text(tmp_path):
    write(tmp_path, [row("a", title="Secret Title")])
    report = run(tmp_path, Source({"10.1/a": {"authors": ["Secretname Person"], "abstract": "Secret abstract text."}}),
                 apply=True)
    text = json.dumps(report.to_dict())
    assert "Secret" not in text and "rsoxs/2020/a.pdf" in text


# -- the hardened fetchers ---------------------------------------------------------------------------------


def test_openalex_fetcher_asks_by_doi_with_the_key_in_the_header_only(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "k-test-123")
    seen: list[tuple[str, dict[str, str]]] = []

    def get_json(url: str, *, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        seen.append((url, dict(headers or {})))
        return {"results": [WORK], "meta": {}}

    got = openalex_fetcher(get_json=get_json, sleep=lambda s: None)("10.1021/x")
    url, headers = seen[0]
    assert url.startswith("https://api.openalex.org/works?") and "doi" in url and "select=" in url
    assert "authorships" in url and "k-test-123" not in url and headers["Authorization"] == "Bearer k-test-123"
    assert got == {"authors": ["A. One", "B. Two", "C. Three"], "abstract": "Lamellar domains form."}
    empty = openalex_fetcher(get_json=lambda url, **kw: {"results": [], "meta": {}}, sleep=lambda s: None)
    assert empty("10.1/none") is None


def test_openalex_fetcher_without_a_key_or_on_quota_aborts(monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    with pytest.raises(BackfillAbort, match="openalex_no_key"):
        openalex_fetcher(get_json=lambda *a, **k: {})("10.1/x")
    monkeypatch.setenv("OPENALEX_API_KEY", "k")

    def quota(url: str, **kw: Any) -> Any:
        raise JSONFetchError("HTTP 429", status=429)

    with pytest.raises(BackfillAbort, match="openalex_quota"):
        openalex_fetcher(get_json=quota, sleep=lambda s: None)("10.1/x")


def test_arxiv_lookup_goes_through_the_getter_by_id_and_is_rate_limited(monkeypatch):
    urls: list[str] = []
    pauses: list[float] = []
    clock = iter(range(0, 1000))
    limiter = RateLimiter(3.0, clock=lambda: float(next(clock)) / 100, sleep=pauses.append)

    def get_bytes(url: str, **kw: Any) -> bytes:
        urls.append(url)
        return ATOM

    entry = arxiv_lookup("2101.00001v2", get_bytes=get_bytes, limiter=limiter)
    assert entry is not None and entry["authors"] == ["Ada Lovelace", "Grace Hopper"]
    assert urls[0].startswith("https://export.arxiv.org/api/query?") and "id_list=2101.00001v2" in urls[0]
    assert arxiv_lookup("2101.00001v2", get_bytes=get_bytes, limiter=limiter) is not None
    assert pauses and pauses[0] > 2.0  # the second request waited out the 3 s spacing
    assert arxiv_lookup("x", get_bytes=lambda url, **kw: b"<feed xmlns='http://www.w3.org/2005/Atom'/>",
                        limiter=limiter) is None
    fetched = arxiv_fetcher(lambda i: arxiv_lookup(i, get_bytes=get_bytes, limiter=limiter))("2101.00001v2")
    assert fetched == {"authors": ["Ada Lovelace", "Grace Hopper"], "abstract": "We measure domains."}


def test_arxiv_lookup_retries_a_failed_request_but_not_a_blocked_one():
    limiter = RateLimiter(3.0, clock=lambda: 0.0, sleep=lambda s: None)
    attempts: list[int] = []

    def flaky(url: str, **kw: Any) -> bytes:
        attempts.append(1)
        if len(attempts) < 2:
            raise JSONFetchError("network error (ReadTimeout)")
        return ATOM

    assert arxiv_lookup("2101.00001", get_bytes=flaky, limiter=limiter) is not None and len(attempts) == 2

    def blocked(url: str, **kw: Any) -> bytes:
        attempts.append(1)
        raise JSONFetchError("blocked URL (host)")

    attempts.clear()
    with pytest.raises(JSONFetchError):
        arxiv_lookup("2101.00001", get_bytes=blocked, limiter=limiter)
    assert len(attempts) == 1


def test_no_new_hosts_beyond_openalex_and_arxiv():
    assert legacy_download.OPENALEX_URL == "https://api.openalex.org"
    assert legacy_download.ARXIV_QUERY_URL == "https://export.arxiv.org/api/query"
    import app.modules.library.backfill as module
    source = Path(module.__file__).read_text()
    import re
    hosts = set(re.findall(r"https?://([^/\"' ]+)", source))
    # Literal URLs are only prefixes stripped from stored ids (DOI resolvers, arxiv.org/abs); requests go through legacy_download.
    assert hosts <= {"doi.org", "dx.doi.org", "arxiv.org"}


# -- the list after a backfill, and the CLI ----------------------------------------------------------------


def test_the_library_list_shows_backfilled_values_and_its_stat_cache_sees_the_change(tmp_path):
    lib = tmp_path / "papers"
    target = lib / "rsoxs" / "2020" / "a.pdf"
    target.parent.mkdir(parents=True)
    data = pdf("a")
    target.write_bytes(data)
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    write(lib, [row("a", pdf_sha256=sha)])
    service = LibraryService(lib, tmp_path / "state" / "catalog.sqlite", settings=local_settings(),
                             probe=lambda p: ProbeResult(status="ok", pages=2, page_text="x"))
    before = service.list_papers()["papers"][0]
    assert before["authors"] is None and before["abstract"] is None
    assert set(before["metadata_missing"]) == {"authors", "abstract"}
    run(lib, Source({"10.1/a": GOT}), apply=True)
    after = service.list_papers()["papers"][0]
    assert after["authors"] == ["A. One", "B. Two"] and after["abstract"] == "Found abstract."
    assert after["metadata_missing"] == []


def test_cli_dry_run_by_default_and_apply_is_explicit(tmp_path, monkeypatch, capsys):
    write(tmp_path, [row("a")])
    monkeypatch.setenv("F2W_LIBRARY_DIR", str(tmp_path))
    assert cli.parse_args([]).apply is False
    assert cli.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run" and out["counts"]["would_fetch"] == 1 and read(tmp_path)[0]["abstract"] is None
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    assert cli.main(["--apply", "--limit", "5"]) == 1  # no key: stops with a code, writes nothing
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "apply" and out["stopped"] == "openalex_no_key" and read(tmp_path)[0]["abstract"] is None


def test_cli_refuses_a_missing_directory_and_bad_numbers(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("F2W_LIBRARY_DIR", raising=False)
    assert cli.main([]) == 1 and "library directory" in capsys.readouterr().out
    assert cli.main(["--library-dir", str(tmp_path / "missing")]) == 1
    for bad in (["--limit", "0"], ["--delay", "-1"], ["--apply", "--dry-run"]):
        with pytest.raises(SystemExit):
            cli.parse_args(bad)
