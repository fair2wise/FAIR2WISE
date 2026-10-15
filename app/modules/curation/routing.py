"""Deterministic local triage and structural resolution before any model call (U-03).

Cheap, free, explainable steps come first: controlled-vocabulary matches and explicit in-text
definitions resolve a case with a literal source quote; chemical identity needs a controlled
entry or an explicit structure; everything else is routed (model, review, extraction). Every
original mention stays immutable and every outcome is an explicit state.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/resolution_routing.py (``LocalResolver``,
``terminology_group``, ``source_quote``) (BSD-3-Clause). Not ported: the PubChem lookup and its
cache (network); a ``StructuralAuthority`` port may be supplied instead.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Protocol, Tuple

from ..canonicalization.chemical_identity import canonical_smiles
from ..canonicalization.growth import definitions
from ..canonicalization.vocabulary import Vocabulary
from .models import CaseState, ResolutionAttempt, Route
from .review_schema import source_quote
from .store import CurationStore

POLICY = "local-resolution-v1"


class StructuralAuthority(Protocol):
    """Optional authority for molecular identity (for example a cached PubChem client)."""

    def lookup(self, label: str) -> Optional[Dict[str, Any]]:
        """Return ``{"smiles": ..., "source": ...}`` or ``None``."""
        ...


@dataclass(frozen=True)
class TriageSettings:
    """Which model routes exist; with neither, ambiguous cases go to human review."""

    local_model_enabled: bool = False
    remote_model_enabled: bool = False

    @property
    def model_route(self) -> Route:
        if self.local_model_enabled:
            return Route.LOCAL_MODEL
        if self.remote_model_enabled:
            return Route.REMOTE_MODEL
        return Route.REVIEW


def _normal(value: str) -> str:
    return " ".join(value.split()).casefold()


def terminology_group(case: Dict[str, Any], vocabulary: Vocabulary) -> str:
    """Group controlled aliases across papers for review; never merges verdicts."""
    payload = case["payload"]
    if case["case_type"] != "unresolved_term" or payload.get("category") == "material":
        return case["id"]
    surface = str(payload.get("surface") or "")
    entry = vocabulary.match(surface)
    normal = re.sub(r"\s*[-‐-―−]\s*", "-", _normal(unicodedata.normalize("NFKC", surface)))
    key = ["canonical", entry.slug] if entry and entry.category != "material" else ["literal", normal]
    return "v2:" + hashlib.sha256(json.dumps(key).encode()).hexdigest()


class LocalResolver:
    """Triage and structural resolution without model calls."""

    def __init__(
        self,
        store: CurationStore,
        vocabulary: Vocabulary,
        corpora: Iterable[str],
        *,
        settings: TriageSettings = TriageSettings(),
        authority: Optional[StructuralAuthority] = None,
    ) -> None:
        self.store = store
        self.vocabulary = vocabulary
        self.corpora = tuple(corpora)
        self.settings = settings
        self.authority = authority
        self.owner = f"local-{uuid.uuid4().hex}"

    def _interpret(self, case: Dict[str, Any]) -> Tuple[str, str, bool]:
        """``(label, literal quote, defined_in_text)``; an empty quote means no literal support."""
        surface = str(case["payload"].get("surface") or "")
        evidence = case["evidence"]
        matching = [
            (acr, long) for acr, long in definitions(evidence) if _normal(surface) in {_normal(acr), _normal(long)}
        ]
        meanings = {_normal(long) for _acr, long in matching}
        if len(meanings) == 1:
            acr, long = matching[0]
            quote = source_quote(evidence, f"{long} ({acr})") or source_quote(evidence, long)
            return long, quote, True
        if len(meanings) > 1 or case["reason"] == "ambiguous_definition":
            return "", "", False
        return surface, source_quote(evidence, surface), False

    def _accept(
        self,
        case: Dict[str, Any],
        token: str,
        label: str,
        quote: str,
        *,
        route: Route,
        method: str,
        scope: str,
        identity: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not quote or quote not in case["evidence"]:
            raise ValueError("local resolution requires a literal source quote")
        response = {
            "disposition": "resolved",
            "canonical_label": label,
            "category": case["payload"].get("category"),
            "evidence_quote": quote,
            "confidence": 1.0,
            "scope": scope,
            "method": method,
            "identity": identity or {},
            "aliases": [],
        }
        self.store.record_attempt(
            ResolutionAttempt(
                case_id=case["id"],
                provider="local",
                requested_model="deterministic-v1",
                executed_model=method,
                request_fingerprint=case["id"],
                outcome="resolved",
                response=response,
                validation={"accepted": True, "errors": [], "policy": POLICY},
                cost_usd=0.0,
            ),
            token,
            route=route.value,
        )

    def triage_once(self, limit: int = 100) -> int:
        """Resolve or route a bounded batch of unclassified cases."""
        cases = self.store.claim(self.owner, self.corpora, limit=limit, route=Route.TRIAGE.value)
        for case in cases:
            self._triage(case)
        return len(cases)

    def _triage(self, case: Dict[str, Any]) -> None:
        token = case["lease_token"]
        group = terminology_group(case, self.vocabulary)
        model_route = self.settings.model_route
        if case["case_type"] != "unresolved_term":
            # A terminology answer cannot repair an incomplete extraction.
            self.store.route_case(case["id"], token, Route.EXTRACTION.value, group, CaseState.NEEDS_REVIEW)
            return
        category = case["payload"].get("category")
        label, quote, defined = self._interpret(case)
        if category == "material":
            self.store.route_case(case["id"], token, Route.STRUCTURAL.value, group)
            return
        if not quote:
            if case["reason"] == "ambiguous_definition" and model_route is not Route.REVIEW:
                self.store.route_case(case["id"], token, model_route.value, group)
            else:
                self.store.route_case(case["id"], token, Route.REVIEW.value, group, CaseState.NEEDS_REVIEW)
            return
        entry = self.vocabulary.match(label)
        if entry is not None and entry.category == category:
            self._accept(
                case,
                token,
                entry.display_name,
                quote,
                route=Route.LOCAL,
                method="controlled_vocabulary",
                scope="vocabulary",
                identity={"slug": entry.slug},
            )
        elif defined and entry is None and category in {"technique", "analysis", "instrument"}:
            self._accept(
                case, token, label, quote, route=Route.LOCAL, method="explicit_definition", scope="publication"
            )
        elif model_route is Route.REVIEW:
            self.store.route_case(case["id"], token, Route.REVIEW.value, group, CaseState.NEEDS_REVIEW)
        else:
            self.store.route_case(case["id"], token, model_route.value, group)

    def structural_once(self) -> int:
        """Resolve one chemical mention from the controlled material vocabulary or an explicit structure."""
        cases = self.store.claim(self.owner, self.corpora, limit=1, route=Route.STRUCTURAL.value)
        if not cases:
            return 0
        case, token = cases[0], cases[0]["lease_token"]
        label, quote, _defined = self._interpret(case)
        if not quote:
            self.store.release(case["id"], token, state=CaseState.NEEDS_REVIEW, reason="no literal source support")
            return 1
        entries = [
            e
            for e in self.vocabulary.entries
            if e.category == "material" and any(_normal(a) == _normal(label) for a in (*e.aliases, e.display_name))
        ]
        if len(entries) == 1:
            self._accept(
                case,
                token,
                entries[0].display_name,
                quote,
                route=Route.STRUCTURAL,
                method="controlled_material_vocabulary",
                scope="vocabulary",
                identity={"slug": entries[0].slug},
            )
            return 1
        explicit = re.fullmatch(r"smiles\s*:\s*(\S+)", label, re.I)
        smiles = canonical_smiles(explicit[1]) if explicit else None
        result: Dict[str, Any] = {"smiles": smiles, "source": "explicit SMILES"} if smiles else {}
        if not result and self.authority is not None and not explicit:
            result = dict(self.authority.lookup(label) or {})
        if result and canonical_smiles(str(result.get("smiles") or "")):
            self._accept(
                case,
                token,
                label,
                quote,
                route=Route.STRUCTURAL,
                method="molecular_structure",
                scope="structure",
                identity=result,
            )
        else:
            self.store.release(case["id"], token, state=CaseState.NEEDS_REVIEW, reason="identity not established")
        return 1


__all__ = ("LocalResolver", "POLICY", "StructuralAuthority", "TriageSettings", "terminology_group")
