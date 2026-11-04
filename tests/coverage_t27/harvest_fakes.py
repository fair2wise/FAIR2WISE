"""Offline doubles for ``app.modules.commands.harvest_rsoxs`` tests: fake ``arxiv``, ``pyalex`` and PDF downloads."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import types
from typing import Any

GOOD_TITLE = "Resonant soft X-ray scattering of polymer blends"
GOOD_ABSTRACT = "We apply resonant soft x-ray scattering to polymer thin films."


class ArxivResult:
    def __init__(self, short_id: str, title: str = GOOD_TITLE, *, pdf_url: str | None = "", doi: str = "",
                 summary: str = GOOD_ABSTRACT, published: datetime | None = datetime(2021, 5, 4)) -> None:
        self.short_id, self.title, self.summary, self.doi, self.published = short_id, title, summary, doi, published
        self.pdf_url = f"https://arxiv.org/pdf/{short_id}" if pdf_url == "" else pdf_url

    def get_short_id(self) -> str:
        return self.short_id


class ArxivWorld:
    """``queries[query]`` is a list of results, an exception, or a list of (exception | results) tried in turn."""

    def __init__(self, table: dict[str, Any] | None = None) -> None:
        self.table = table or {}
        self.calls: list[str] = []
        self.clients: list[dict[str, Any]] = []

    def module(self) -> types.ModuleType:
        world = self
        mod = types.ModuleType("arxiv")

        class Search:
            def __init__(self, query: str, max_results: int, sort_by: Any = None, sort_order: Any = None) -> None:
                self.query, self.max_results, self.sort_by, self.sort_order = query, max_results, sort_by, sort_order

        class Client:
            def __init__(self, **kw: Any) -> None:
                world.clients.append(kw)

            def results(self, search: Search):
                world.calls.append(search.query)
                entry = world.table.get(search.query, [])
                if isinstance(entry, list) and entry and isinstance(entry[0], (Exception, list)):
                    entry = entry.pop(0)
                if isinstance(entry, Exception):
                    raise entry
                yield from entry

        mod.Search, mod.Client = Search, Client  # type: ignore[attr-defined]
        mod.SortCriterion = types.SimpleNamespace(SubmittedDate="submitted", Relevance="relevance")  # type: ignore[attr-defined]
        mod.SortOrder = types.SimpleNamespace(Descending="desc")  # type: ignore[attr-defined]
        return mod

    def install(self, monkeypatch: Any) -> ArxivWorld:
        monkeypatch.setitem(sys.modules, "arxiv", self.module())
        return self


class OpenAlexWorld:
    """Fake ``pyalex``: ``works[query]`` pages (lists of work dicts) or an exception; ``authors[name]`` records."""

    def __init__(self) -> None:
        self.works: dict[str, Any] = {}
        self.authors: dict[str, Any] = {}
        self.by_doi: dict[str, Any] = {}
        self.fail_paginate = False
        self.fail_get = False
        self.no_paginate = False
        self.search_failures: dict[str, int] = {}
        self.queries: list[str] = []
        self.filters: list[dict[str, Any]] = []
        self.config = types.SimpleNamespace(email=None)

    def module(self) -> types.ModuleType:
        world = self
        mod = types.ModuleType("pyalex")

        class BaseQuery:
            def __init__(self) -> None:
                self.q = ""

            def search(self, q: str) -> BaseQuery:
                if world.search_failures.get(q, 0) > 0:
                    world.search_failures[q] -= 1
                    raise RuntimeError("search broke")
                self.q = q
                world.queries.append(q)
                return self

            def filter(self, **kw: Any) -> BaseQuery:
                world.filters.append(kw)
                return self

            def _entry(self) -> Any:
                entry = world.works.get(self.q, [])
                if isinstance(entry, Exception):
                    raise entry
                return entry

            def get(self, per_page: int = 25) -> Any:
                if world.fail_get:
                    raise RuntimeError("get broke")
                return self._entry()

            def __getitem__(self, doi: str) -> Any:
                return world._by_doi(doi)

        class PagingQuery(BaseQuery):
            def paginate(self, per_page: int = 25):
                if world.fail_paginate:
                    raise RuntimeError("paginate broke")
                entry = self._entry()
                for i in range(0, len(entry), per_page):
                    yield entry[i : i + per_page]

        def works_factory() -> BaseQuery:
            return BaseQuery() if world.no_paginate else PagingQuery()

        class Authors:
            def search(self, name: str) -> Authors:
                self.name = name
                return self

            def get(self, per_page: int = 25) -> Any:
                entry = world.authors.get(self.name, [])
                if isinstance(entry, Exception):
                    raise entry
                return entry

        mod.Works = works_factory  # type: ignore[attr-defined]
        mod.Authors = Authors  # type: ignore[attr-defined]
        mod.config = world.config  # type: ignore[attr-defined]
        return mod

    def _by_doi(self, doi: str) -> Any:
        entry = self.by_doi.get(doi)
        if isinstance(entry, Exception):
            raise entry
        return entry

    def install(self, monkeypatch: Any) -> OpenAlexWorld:
        monkeypatch.setitem(sys.modules, "pyalex", self.module())
        return self


class Downloads:
    """Fake ``download_pdf(url, dest)``: ``table[url]`` is bytes to write (success) or falsy (failure)."""

    def __init__(self, table: dict[str, bytes | None] | None = None, default: bytes | None = None) -> None:
        self.table, self.default, self.calls = table or {}, default, []

    def __call__(self, url: str, dest: str) -> bool:
        self.calls.append(url)
        data = self.table.get(url, self.default)
        if not data:
            return False
        Path(dest).write_bytes(data)
        return True
