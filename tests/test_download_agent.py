import asyncio
import sys
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import download_pdfs
import pyalex

from app.modules.f2w_agent import download_agent as mod
from app.modules.f2w_agent.download_agent import DownloadAgent, _search_queries


def test_find_and_download_action_delegates_to_download(tmp_path):
    agent = DownloadAgent(backend="ollama", model="test", download_delay_seconds=0)
    calls = []

    def fake_download(query, missing_topics, target_dir, max_papers, candidate_pool):
        calls.append((query, missing_topics, target_dir, max_papers, candidate_pool))
        return {"status": "success", "count": 2, "downloaded": ["a.pdf", "b.pdf"]}

    agent._download = fake_download

    result = asyncio.run(
        agent.find_and_download(
            "question",
            ["missing"],
            str(tmp_path / "pdfs"),
            max_papers=2,
            candidate_pool=7,
        )
    )

    assert result == {"status": "success", "count": 2, "downloaded": ["a.pdf", "b.pdf"]}
    assert calls == [("question", ["missing"], str(tmp_path / "pdfs"), 2, 7)]


def test_search_candidates_action_does_not_write_pdfs(tmp_path, monkeypatch):
    agent = DownloadAgent(backend="ollama", model="test", download_delay_seconds=0)
    candidate = {
        "id": "W123",
        "doi": "https://doi.org/10.1234/example",
        "title": "Relevant abstract paper",
        "abstract_inverted_index": {"Relevant": [0], "abstract": [1]},
        "best_oa_location": {"pdf_url": "https://example.test/paper.pdf"},
        "_score": 0.82,
    }

    monkeypatch.setattr(agent, "_search_candidates", lambda query, missing, pool: [candidate])
    monkeypatch.setattr(agent, "_rank", lambda query, candidates: candidates)

    result = asyncio.run(
        agent.search_candidates(
            "question",
            ["missing"],
            candidate_pool=5,
        )
    )

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["candidates"][0]["title"] == "Relevant abstract paper"
    assert result["candidates"][0]["abstract"] == "Relevant abstract"
    assert result["candidates"][0]["pdf_urls"] == ["https://example.test/paper.pdf"]
    assert not list(tmp_path.rglob("*.pdf"))
    assert not (tmp_path / "downloads.jsonl").exists()


class FakeWorks:
    calls = []
    fail_all = False
    fail_oa = False
    data = {}

    def __init__(self):
        self.search_text = ""
        self.oa_filter = False

    def search(self, text):
        self.search_text = text
        return self

    def filter(self, **kwargs):
        self.oa_filter = True
        return self

    def get(self, per_page):
        self.calls.append((self.search_text, self.oa_filter, per_page))
        if self.fail_all or (self.oa_filter and self.fail_oa):
            raise RuntimeError("openalex down")
        return self.data.get(self.search_text, [])[:per_page]


def test_download_agent_does_not_count_invalid_download(tmp_path, monkeypatch):
    agent = DownloadAgent(backend="ollama", model="test", download_delay_seconds=0)
    target = tmp_path / "pdfs"
    candidate = {
        "id": "https://openalex.org/W123",
        "doi": "https://doi.org/10.1234/example",
        "title": "Relevant paper",
        "best_oa_location": {"pdf_url": "https://example.test/not-a-pdf"},
        "open_access": {"oa_url": "https://example.test/landing"},
    }

    monkeypatch.setattr(agent, "_search_candidates", lambda query, missing, pool: [candidate])
    monkeypatch.setattr(agent, "_rank", lambda query, candidates: candidates)
    monkeypatch.setattr(download_pdfs, "download_pdf", lambda url, dest: False)

    result = agent._download(
        "question",
        ["missing topic"],
        str(target),
        max_papers=1,
        candidate_pool=5,
    )

    assert result["count"] == 0
    assert result["downloaded"] == []
    assert result["failed"] == 1
    assert result["url_attempts"] == 2
    assert result["oa_url_attempts"] == 1
    assert Path(result["manifest"]).exists()
    records = [json.loads(line) for line in Path(result["manifest"]).read_text().splitlines()]
    assert records[-1]["status"] == "failed"
    assert records[-1]["attempted_urls"] == [
        "https://example.test/not-a-pdf",
        "https://example.test/landing",
    ]
    assert not list(target.glob("*.pdf"))


def test_search_candidates_fallback_failure_returns_empty(monkeypatch):
    agent = DownloadAgent(backend="ollama", model="test", download_delay_seconds=0)
    FakeWorks.calls = []
    FakeWorks.fail_all = True
    FakeWorks.fail_oa = False
    FakeWorks.data = {}

    candidates = agent._search_one(FakeWorks, "graphene oxide", 5)

    assert candidates == []
    assert FakeWorks.calls == [
        ("graphene oxide", True, 5),
        ("graphene oxide", False, 5),
    ]


def test_search_candidates_merges_missing_topic_queries(monkeypatch):
    agent = DownloadAgent(backend="ollama", model="test", download_delay_seconds=0)
    FakeWorks.calls = []
    FakeWorks.fail_all = False
    FakeWorks.fail_oa = False
    FakeWorks.data = {
        "How measure scattering peak fitting": [
            {"id": "W1", "title": "broad", "best_oa_location": {"pdf_url": "u1"}},
        ],
        "peak fitting": [
            {"id": "W1", "title": "duplicate", "best_oa_location": {"pdf_url": "u1"}},
            {"id": "W2", "title": "focused", "best_oa_location": {"pdf_url": "u2"}},
        ],
    }
    monkeypatch.setattr(pyalex, "Works", FakeWorks)

    candidates = agent._search_candidates("How measure scattering", ["peak fitting"], 10)

    assert [c["id"] for c in candidates] == ["W1", "W2"]
    assert ("How measure scattering peak fitting", True, 10) in FakeWorks.calls
    assert ("peak fitting", True, 10) in FakeWorks.calls


def test_search_queries_strip_openalex_wildcards():
    queries = _search_queries(
        "What are practical applications of graphene?",
        ["graphene applications", "defect* engineering"],
    )

    assert queries[0] == "What are practical applications of graphene graphene applications defect engineering"
    assert all("?" not in q and "*" not in q for q in queries)


def test_download_agent_rate_limits_between_failed_urls(tmp_path, monkeypatch):
    agent = DownloadAgent(
        backend="ollama",
        model="test",
        download_delay_seconds=0.25,
        validate_downloads=False,
    )
    target = tmp_path / "pdfs"
    candidate = {
        "id": "W123",
        "title": "Relevant paper",
        "best_oa_location": {"pdf_url": "https://example.test/not-a-pdf"},
        "open_access": {"oa_url": "https://example.test/landing"},
    }
    sleeps = []

    monkeypatch.setattr(agent, "_search_candidates", lambda query, missing, pool: [candidate])
    monkeypatch.setattr(agent, "_rank", lambda query, candidates: candidates)
    monkeypatch.setattr(download_pdfs, "download_pdf", lambda url, dest: False)
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = agent._download("question", [], str(target), max_papers=1, candidate_pool=5)

    assert result["failed"] == 1
    assert result["url_attempts"] == 2
    assert result["oa_url_attempts"] == 1
    assert sleeps == [0.25, 0.25]


def test_download_agent_rejects_semantically_invalid_pdf(tmp_path, monkeypatch):
    agent = DownloadAgent(
        backend="ollama",
        model="test",
        download_delay_seconds=0,
        validate_downloads=True,
    )
    target = tmp_path / "pdfs"
    candidate = {
        "id": "W123",
        "title": "Unrelated paper",
        "best_oa_location": {"pdf_url": "https://example.test/paper.pdf"},
    }

    def fake_download(url, dest):
        Path(dest).write_bytes(b"%PDF-1.4\nbody")
        return True

    monkeypatch.setattr(agent, "_search_candidates", lambda query, missing, pool: [candidate])
    monkeypatch.setattr(agent, "_rank", lambda query, candidates: candidates)
    monkeypatch.setattr(download_pdfs, "download_pdf", fake_download)
    monkeypatch.setattr(agent, "_validate_downloaded_pdf", lambda query, work, path: False)

    result = agent._download("question", [], str(target), max_papers=1, candidate_pool=5)

    assert result["count"] == 0
    assert result["failed"] == 1
    assert result["semantic_rejected"] == 1
    records = [json.loads(line) for line in Path(result["manifest"]).read_text().splitlines()]
    assert records[-1]["status"] == "semantic_rejected"
    assert not list(target.glob("*.pdf"))
