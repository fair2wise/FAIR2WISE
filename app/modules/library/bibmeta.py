"""Authors and abstract of a paper, cleaned and capped for storage in the library manifest (P3b).

Every writer of a manifest row (the Harvester, an upload that carries what the registry said, the verify step, the
backfill command) goes through these helpers, so what is stored has one shape: ``authors`` is a list of display
names (at most :data:`MAX_AUTHORS_STORED`, no e-mail addresses) and ``abstract`` is whitespace-collapsed text of at
most :data:`MAX_ABSTRACT_STORED` characters, the cap the legacy ``harvest_rsoxs.py`` rows use.

Disclosure: these values live in the local manifest only. Nothing here logs, traces or raises with a name or an
abstract in its message.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

#: Longest abstract stored (the same cut as the legacy harvest rows).
MAX_ABSTRACT_STORED = 4000
#: Most author names stored per paper.
MAX_AUTHORS_STORED = 100
#: Longest single author name stored.
MAX_AUTHOR_NAME_CHARS = 200

_EMAIL = re.compile(r"\S+@\S+")


def clean_author_list(names: Iterable[object] | None) -> list[str]:
    """Author display names: whitespace collapsed, e-mail addresses removed, deduplicated, capped."""
    out: list[str] = []
    for raw in names or ():
        if not isinstance(raw, str):
            continue
        name = " ".join(_EMAIL.sub(" ", raw).split())[:MAX_AUTHOR_NAME_CHARS]
        if name and name not in out:
            out.append(name)
        if len(out) >= MAX_AUTHORS_STORED:
            break
    return out


def cap_abstract(text: object) -> str | None:
    """The abstract with whitespace collapsed and cut at :data:`MAX_ABSTRACT_STORED`, or ``None`` when empty."""
    if not isinstance(text, str):
        return None
    collapsed = " ".join(text.split())
    return collapsed[:MAX_ABSTRACT_STORED] or None


def openalex_authors(work: Mapping[str, Any]) -> list[str]:
    """Display names from an OpenAlex work's ``authorships`` (empty when the record has none)."""
    names: list[object] = []
    for authorship in work.get("authorships") or []:
        if isinstance(authorship, Mapping):
            author = authorship.get("author")
            name = author.get("display_name") if isinstance(author, Mapping) else None
            names.append(name or authorship.get("raw_author_name"))
    return clean_author_list(names)


def openalex_abstract(work: Mapping[str, Any]) -> str | None:
    """The abstract rebuilt from an OpenAlex work's ``abstract_inverted_index`` (``None`` when it has none)."""
    from ..literature.openalex import abstract_from_inverted_index

    index = work.get("abstract_inverted_index")
    return cap_abstract(abstract_from_inverted_index(index if isinstance(index, dict) else None))


__all__ = ("MAX_ABSTRACT_STORED", "MAX_AUTHORS_STORED", "MAX_AUTHOR_NAME_CHARS", "cap_abstract", "clean_author_list",
           "openalex_abstract", "openalex_authors")
