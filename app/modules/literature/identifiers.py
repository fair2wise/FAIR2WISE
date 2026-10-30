"""Resolve a publication identifier (DOI, OpenAlex id/URL, arXiv id/URL) to a bibliographic record.

The record is a *bibliographic candidate* from OpenAlex. It is not a new F2W
Work: an arXiv preprint and its journal version resolve to two records, and
deciding they are one Work is :func:`.intake.version_pair_candidate`'s job,
followed by review (L-04/L-06, plan T05 identity boundary).

The donor raised one ``ResolverError`` for both "no such record" and "could not
ask". Here they are distinct: :class:`WorkNotFound` (permanent for that
identifier) and :class:`ResolverUnavailable` (retryable; quota pauses carry
their resume time).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_resolver.py (``resolve``,
``_normalise_abstract``, ``_normalise_bibliographic``, ``_ensure_oa_fields``) (BSD-3-Clause).
"""

from __future__ import annotations

import re
from typing import Any

from ..contracts.errors import ContractError, ErrorCode
from .openalex import (
    OpenAlexClient,
    abstract_from_inverted_index,
    is_not_found,
    is_quota_paused,
)

_RE_OPENALEX_WID = re.compile(r"W(\d{5,})")
_RE_DOI = re.compile(r"10\.\d{4,}/\S+")
_RE_ARXIV_ID = re.compile(r"(?:arXiv:|arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)")
_SNIPPET_CHARS = 300


class ResolverError(ContractError):
    """Base: an identifier could not be resolved."""


class UnrecognisedIdentifier(ResolverError):
    """The string is not a DOI, OpenAlex id or arXiv id."""

    def __init__(self, identifier: str) -> None:
        super().__init__(ErrorCode.INVALID_INPUT, f"unrecognised identifier format: {identifier!r}",
                         retryable=False)


class WorkNotFound(ResolverError):
    """OpenAlex answered and has no record (permanent for this identifier)."""

    def __init__(self, identifier: str) -> None:
        super().__init__(ErrorCode.NOT_FOUND, f"no OpenAlex record for {identifier!r}", retryable=False)


class ResolverUnavailable(ResolverError):
    """OpenAlex did not answer (transport/5xx/rejected) or its quota is paused."""

    def __init__(self, identifier: str, reason: str, resume_at: float | None = None) -> None:
        super().__init__(ErrorCode.UNAVAILABLE, f"could not resolve {identifier!r} ({reason})", retryable=True)
        self.reason = reason
        self.resume_at = resume_at


def classify_identifier(identifier: str) -> dict[str, str]:
    """``{"kind": "openalex"|"doi"|"arxiv", "value": ...}`` or raise :class:`UnrecognisedIdentifier`."""
    text = identifier.strip()
    if "openalex.org" in text:
        match = _RE_OPENALEX_WID.search(text)
        if match:
            return {"kind": "openalex", "value": match.group(0)}
        raise UnrecognisedIdentifier(identifier)
    if _RE_OPENALEX_WID.fullmatch(text):
        return {"kind": "openalex", "value": text}
    if "arxiv.org/abs/" in text:
        return {"kind": "arxiv", "value": text.split("arxiv.org/abs/")[-1].strip().rstrip("/")}
    if text.lower().startswith("arxiv:"):
        return {"kind": "arxiv", "value": text[len("arxiv:"):]}
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if text.lower().startswith(prefix):
            return {"kind": "doi", "value": text[len(prefix):]}
    if _RE_DOI.match(text):
        return {"kind": "doi", "value": text}
    if _RE_ARXIV_ID.fullmatch(text):
        return {"kind": "arxiv", "value": _RE_ARXIV_ID.fullmatch(text).group(1)}  # type: ignore[union-attr]
    raise UnrecognisedIdentifier(identifier)


def resolve(client: OpenAlexClient, identifier: str) -> dict[str, Any]:
    """Resolve ``identifier`` to a normalized OpenAlex works record.

    Raises:
        UnrecognisedIdentifier: Bad format.
        WorkNotFound: OpenAlex has no such record.
        ResolverUnavailable: OpenAlex could not be asked (retry later).
    """
    parsed = classify_identifier(identifier)
    if parsed["kind"] == "openalex":
        data = client.fetch(f"works/{parsed['value']}")
        record = data if data and data.get("id") else None
    else:
        doi = parsed["value"] if parsed["kind"] == "doi" else f"10.48550/arXiv.{parsed['value']}"
        data = client.fetch("works", {"filter": f"doi:{doi}"})
        results = (data.get("results") or []) if data else []
        record = results[0] if results else None
    if record is None:
        if is_quota_paused(data):
            raise ResolverUnavailable(identifier, "quota_paused", getattr(data, "resume_at", None))
        if not data and not is_not_found(data):
            raise ResolverUnavailable(identifier, getattr(data, "reason", "unreachable"))
        raise WorkNotFound(identifier)
    record = dict(record)
    normalise_abstract(record)
    ensure_oa_fields(record)
    normalise_bibliographic(record)
    return record


def normalise_abstract(record: dict[str, Any]) -> None:
    """Reconstruct ``abstract`` from the inverted index when missing (in place)."""
    if not record.get("abstract") and record.get("abstract_inverted_index"):
        record["abstract"] = abstract_from_inverted_index(record.get("abstract_inverted_index"))


def normalise_bibliographic(record: dict[str, Any]) -> None:
    """Lift ``authors``, ``journal`` and ``abstract_snippet`` to the top level, never overwriting.

    Author ids are bare (``A123``), never URLs, so a graph writer cannot mint a
    second node for an author already present (donor note).
    """
    if not record.get("authors"):
        authors = []
        for entry in record.get("authorships") or []:
            author = entry.get("author") or {}
            institutions = entry.get("institutions") or []
            authors.append({
                "name": author.get("display_name", ""),
                "openalex_id": (author.get("id") or "").replace("https://openalex.org/", ""),
                "affiliation": institutions[0].get("display_name", "") if institutions else "",
            })
        record["authors"] = authors
    if not record.get("journal"):
        source = (record.get("primary_location") or {}).get("source") or {}
        legacy = record.get("host_venue") or {}
        record["journal"] = source.get("display_name") or legacy.get("display_name") or ""
    if not record.get("abstract_snippet"):
        record["abstract_snippet"] = str(record.get("abstract") or "")[:_SNIPPET_CHARS]


def ensure_oa_fields(record: dict[str, Any]) -> None:
    """Ensure ``is_oa``, ``oa_url`` and ``pdf_url`` exist at the top level (in place)."""
    oa_block = record.get("open_access") or {}
    record.setdefault("is_oa", bool(oa_block.get("is_oa", False)))
    record.setdefault("oa_url", oa_block.get("oa_url"))
    record.setdefault("pdf_url", (record.get("primary_location") or {}).get("pdf_url"))


__all__ = (
    "ResolverError", "ResolverUnavailable", "UnrecognisedIdentifier", "WorkNotFound", "classify_identifier",
    "ensure_oa_fields", "normalise_abstract", "normalise_bibliographic", "resolve",
)
