"""Fakes for the paper job handlers: no catalog, OpenAlex, PDF, parser or model is ever contacted."""
from typing import Any

from app.modules.jobs.paper_handlers import Proposal
from app.modules.jobs.pipeline import JobStopped
from app.modules.literature.acquire import AcquireResult
from app.modules.literature.openalex import OpenAlexQuotaPaused
from app.modules.literature.parse_service import ParseRecord
from app.modules.literature.parsed_document import ParsedDocument, ParsedFigure, ParsedSection


class FakeStage:
    """What :class:`JobControl` needs of a stage: counters, a stop flag, settled-unit checkpoints."""

    def __init__(self, stop_after_guards: int | None = None):
        self.units: dict[str, tuple[str, dict[str, Any]]] = {}
        self.failed: dict[str, str] = {}
        self.progress_calls: list[dict[str, int]] = []
        self.guards = 0
        self.stop_after_guards = stop_after_guards

    def guard(self):
        self.guards += 1
        if self.stop_after_guards is not None and self.guards > self.stop_after_guards:
            raise JobStopped("stop requested")

    def progress(self, **counters):
        self.guard()
        self.progress_calls.append(counters)

    def unit(self, unit_id, unit_key):
        got = self.units.get(unit_id)
        return got[1] if got and got[0] == unit_key else None

    def save_unit(self, unit_id, unit_key, result):
        self.failed.pop(unit_id, None)
        self.units[unit_id] = (unit_key, dict(result))

    def fail_unit(self, unit_id, unit_key, error):
        self.failed[unit_id] = error


class RecordingSink:
    """Collects proposals; an identical proposal is not new the second time."""

    def __init__(self):
        self.proposals: list[Proposal] = []
        self._seen: set[str] = set()

    def propose(self, ctx, corpus, proposal):
        self.proposals.append(proposal)
        ident = f"{corpus}|{proposal.kind}|{proposal.key}|{sorted(proposal.content.items())!r}"
        new = ident not in self._seen
        self._seen.add(ident)
        return new

    def kinds(self):
        return [p.kind for p in self.proposals]


class FakeCatalog:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def datasets(self, ctx, corpus, *, entity_ids, limit):
        self.calls.append((corpus, entity_ids, limit))
        return list(self.rows)


def work(oa, title, *, doi="", authors=(("A1", "Ann"),), refs=(), abstract_words=(), concepts=()):
    index = {}
    for position, word in enumerate(abstract_words):
        index.setdefault(word, []).append(position)
    return {
        "id": f"https://openalex.org/{oa}", "doi": f"https://doi.org/{doi}" if doi else None, "title": title,
        "publication_year": 2020, "cited_by_count": 7,
        "authorships": [{"author": {"id": f"https://openalex.org/{a}", "display_name": n},
                         "institutions": [{"display_name": "LBL"}]} for a, n in authors],
        "primary_location": {"source": {"display_name": "J"}},
        "referenced_works": [f"https://openalex.org/{r}" for r in refs],
        "abstract_inverted_index": index or None,
        "concepts": [{"display_name": c, "score": 0.5} for c in concepts],
        "open_access": {"is_oa": True},
    }


class FakeWorks:
    """Search results by search text; ``paused_for`` makes those searches raise the quota breaker."""

    def __init__(self, results=None):
        self.results = results or {}
        self.calls: list[tuple[str, int, int]] = []
        self.paused_for: set[str] = set()

    def works(self, ctx, *, search, per_page, page=1, year_from=None):
        self.calls.append((search, per_page, page))
        if search in self.paused_for:
            raise OpenAlexQuotaPaused(1_900_000_000.0, "spent")
        return self.results.get(search, []) if page == 1 else []


class FakeCorpus:
    def __init__(self, rows):
        self.rows = rows

    def publications(self, ctx, corpus):
        return list(self.rows)


class FakeSource:
    def __init__(self, missing=()):
        self.missing = set(missing)
        self.calls: list[str] = []

    def acquire(self, ctx, doi, *, work=None):
        self.calls.append(doi)
        if doi in self.missing:
            return AcquireResult(doi=doi, status="no_oa_pdf")
        return AcquireResult(doi=doi, status="cached", ref=f"sha256:{doi}")


class FakeParser:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls: list[tuple[str, bool]] = []

    def parse(self, ctx, source_ref, *, force=False):
        self.calls.append((source_ref, force))
        if source_ref.removeprefix("sha256:") in self.fail:
            raise RuntimeError("docling crashed")
        return ParseRecord(source_ref=source_ref, parser_key="docling-1-abc", parser="docling",
                           parser_version="1", document_ref=f"doc:{source_ref}")

    def load(self, ctx, record):
        text = "See 10.1000/known.1 and 10.1000/other.9 for details."
        return ParsedDocument(
            source_ref=record.source_ref, markdown="# Intro", num_pages=3, parser="docling", parser_version="1",
            sections=(ParsedSection("Intro", text, page=1, level=1, structural_id="s1"),),
            figures=(ParsedFigure("Fig 1", page=2, index=0, figure_id="fig-p002-00"),))
