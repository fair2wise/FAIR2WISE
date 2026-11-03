"""Cost estimate and local confirmation token for an extraction (A8, T38).

The estimate is deterministic: pages times fixed per-page token heuristics times the optional per-model
price table. No price is built in (a model list is live and prices change), so without a configured price
for the model ``estimated_cost_usd`` is ``None`` and only tokens are reported.

Confirmation. ``/v1/curation/cost-approvals`` (policy V14, ``create_cost_approval``) does not fit: it needs
``curation.approve``, a corpus scope, a USD cap and an expiry, and it is bound to review cases and the
remote-cost switch. Here the confirmation is a local token: the SHA-256 of the canonical estimate
(papers, pages, model, schema, heuristics, prices). The client echoes it in the start request, and
:func:`verify_confirmation` recomputes it, so a changed paper set, model, schema or page count invalidates it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import json
from typing import Any, Protocol

from ..contracts.errors import ContractError, ErrorCode

#: Heuristic prompt tokens per PDF page (text of the page plus instructions); a documented estimate, not a measurement.
INPUT_TOKENS_PER_PAGE = 1200
#: Heuristic completion tokens per PDF page.
OUTPUT_TOKENS_PER_PAGE = 400
HEURISTICS_VERSION = "1"
MAX_PAPERS = 5000


@dataclass(frozen=True)
class PaperFacts:
    """What the estimate needs of one library paper (T35 supplies it)."""

    id: str
    name: str = ""
    sha256: str | None = None
    pages: int | None = None


class PaperLookup(Protocol):
    """The slice of the T35 library service the T38 routes use (``app.state.f2w_library_papers``)."""

    def paper_facts(self, paper_id: str) -> PaperFacts | None:
        """Facts of one paper, or ``None`` when the id is unknown."""
        ...


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def estimate_extraction(papers: Sequence[PaperFacts], model: str, schema: str,
                        prices: Mapping[str, ModelPrice] | None = None) -> dict[str, Any]:
    """The ``EstimateResponse`` of ``extractionApi.ts`` plus ``unknown_pages`` and ``paper_count``.

    Args:
        papers: The chosen papers (duplicates by id are counted once).
        model: Model id (any non-empty string; it is not checked against the live list here).
        schema: Schema id.
        prices: Optional price table by model id.

    Raises:
        ContractError: ``INVALID_INPUT`` for no papers, too many papers or an empty model.
    """
    if not model or not model.strip():
        raise ContractError(ErrorCode.INVALID_INPUT, "a model is required")
    unique = {p.id: p for p in papers}
    if not unique:
        raise ContractError(ErrorCode.INVALID_INPUT, "choose at least one paper")
    if len(unique) > MAX_PAPERS:
        raise ContractError(ErrorCode.INVALID_INPUT, "too many papers")
    ordered = [unique[k] for k in sorted(unique)]
    pages = sum(p.pages or 0 for p in ordered)
    unknown = [p.id for p in ordered if not p.pages]
    in_tok, out_tok = pages * INPUT_TOKENS_PER_PAGE, pages * OUTPUT_TOKENS_PER_PAGE
    price = (prices or {}).get(model)
    cost = None if price is None else round(
        in_tok / 1e6 * price.input_per_mtok + out_tok / 1e6 * price.output_per_mtok, 4)
    token = _token(ordered, model, schema, price)
    return {"pages": pages, "model": model, "estimated_tokens": in_tok + out_tok, "estimated_cost_usd": cost,
            "confirmation": token, "paper_count": len(ordered), "unknown_pages": unknown}


def _token(papers: Iterable[PaperFacts], model: str, schema: str, price: ModelPrice | None) -> str:
    body = {"v": HEURISTICS_VERSION, "model": model, "schema": schema,
            "tokens_per_page": [INPUT_TOKENS_PER_PAGE, OUTPUT_TOKENS_PER_PAGE],
            "price": None if price is None else [price.input_per_mtok, price.output_per_mtok],
            "papers": [[p.id, p.sha256, p.pages or 0] for p in papers]}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def verify_confirmation(confirmation: str, papers: Sequence[PaperFacts], model: str, schema: str,
                        prices: Mapping[str, ModelPrice] | None = None) -> None:
    """Raise ``STALE_APPROVAL`` unless ``confirmation`` is the token of this exact paper set, model and schema."""
    expected = estimate_extraction(papers, model, schema, prices)["confirmation"]
    if not isinstance(confirmation, str) or not hmac.compare_digest(confirmation.encode(), expected.encode()):
        raise ContractError(ErrorCode.STALE_APPROVAL, "the cost confirmation does not match; estimate again")


__all__ = ("INPUT_TOKENS_PER_PAGE", "ModelPrice", "OUTPUT_TOKENS_PER_PAGE", "PaperFacts", "PaperLookup",
           "estimate_extraction", "verify_confirmation")
