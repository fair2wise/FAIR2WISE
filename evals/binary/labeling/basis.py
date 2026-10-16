"""Which labels a binary case rests on: gold, provisional or missing (H3 B4).

The runner loads the private label directory (``--labels-dir``) once. For each pilot paper:

* ``gold``: ``gold.json`` is complete (every label reviewed, every labelling call succeeded)
  and was made from the reviewed pilot PDFs (source SHA-256 as registered);
* ``provisional``: model labels exist but are not complete gold (unreviewed, partly reviewed
  or with missing calls);
* ``missing``: nothing usable.

A case's basis combines its papers under its rule (``all`` or ``any``, see
:data:`~evals.binary.labeling.pilot.CASE_PAPERS`). A result resting on ``provisional``
labels is reported as PROVISIONAL and never passes the gate; ``missing`` keeps the case
blocked. Only identifiers, statuses and counts leave this module: no quote, answer or page
text reaches a report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from evals.binary.labeling.pilot import ALL_RULE, CASE_PAPERS, PILOT_PAPERS
from evals.binary.labeling.store import LabelStore, LabelStoreError

GOLD = "gold"
PROVISIONAL = "provisional"
MISSING = "missing"
_RANK = {GOLD: 0, PROVISIONAL: 1, MISSING: 2}


@dataclass(frozen=True)
class PaperLabels:
    """Label status of one pilot paper (public summary only)."""

    pilot_id: str
    status: str
    labeler_models: Tuple[str, ...] = ()
    guide_versions: Tuple[str, ...] = ()
    reviewer_recorded: bool = False
    item_count: int = 0
    calls_complete: Optional[bool] = None
    problems: Tuple[str, ...] = ()

    def summary(self) -> Dict[str, Any]:
        return {"status": self.status, "labeler_models": list(self.labeler_models),
                "guide_versions": list(self.guide_versions), "reviewer_recorded": self.reviewer_recorded,
                "items": self.item_count, "labeling_calls_complete": self.calls_complete,
                "problems": list(self.problems)}


@dataclass
class LabelSet:
    """Label status of every pilot paper."""

    papers: Dict[str, PaperLabels] = field(default_factory=dict)
    loaded: bool = False

    @property
    def labeler_models(self) -> List[str]:
        return sorted({m for p in self.papers.values() for m in p.labeler_models})

    def paper_status(self, pilot_id: str) -> str:
        found = self.papers.get(pilot_id)
        return found.status if found else MISSING

    def case_basis(self, case_id: str) -> Optional[Dict[str, Any]]:
        """``{"status", "rule", "papers": {id: status}}`` for a label case, else ``None``."""
        spec = CASE_PAPERS.get(case_id)
        if spec is None:
            return None
        statuses = {pid: self.paper_status(pid) for pid in spec["papers"]}  # type: ignore[union-attr]
        if spec["rule"] == ALL_RULE:
            status = max(statuses.values(), key=lambda s: _RANK[s])
        else:
            status = min(statuses.values(), key=lambda s: _RANK[s])
        return {"status": status, "rule": spec["rule"], "papers": statuses}

    def summary(self) -> Dict[str, Any]:
        return {"loaded": self.loaded,
                "papers": {pid: (self.papers[pid].summary() if pid in self.papers else {"status": MISSING})
                           for pid in sorted(PILOT_PAPERS)}}


def _registered_sha(pilot_id: str) -> set:
    return {v.sha256 for v in PILOT_PAPERS[pilot_id].versions}


def _paper(store: LabelStore, pilot_id: str) -> PaperLabels:
    problems: List[str] = []
    expected = _registered_sha(pilot_id)
    gold_path = store.gold_path(pilot_id)
    if gold_path.is_file():
        try:
            gold = store.read_json(gold_path)
        except LabelStoreError as exc:
            gold, problems = None, [str(exc)]
        if isinstance(gold, Mapping):
            prov = gold.get("provenance") or []
            shas = {p.get("source_sha256") for p in prov if isinstance(p, Mapping)}
            models = tuple(sorted({p.get("labeler_model") for p in prov if isinstance(p, Mapping)} - {None}))
            guides = tuple(sorted({p.get("guide_version") for p in prov if isinstance(p, Mapping)} - {None}))
            if shas != expected:
                problems.append("gold was made from a different PDF than the registered pilot file")
            ok = gold.get("status") == GOLD and gold.get("complete") is True and bool(gold.get("reviewer"))
            if ok and not problems:
                return PaperLabels(pilot_id, GOLD, models, guides, True, len(gold.get("items") or ()),
                                   bool(gold.get("labeling_calls_complete")))
            if not ok:
                problems.append("gold is incomplete (unreviewed or refused labels, or missing labelling calls)")
    try:
        docs = store.labels_for(pilot_id)
    except LabelStoreError as exc:
        return PaperLabels(pilot_id, MISSING, problems=tuple(problems + [str(exc)]))
    docs = [d for d in docs if isinstance(d, Mapping) and d.get("status") == "provisional_model_label"]
    if not docs:
        return PaperLabels(pilot_id, MISSING, problems=tuple(problems))
    shas = {d["provenance"].get("source_sha256") for d in docs}
    if not shas <= expected:
        problems.append("provisional labels were made from a different PDF than the registered pilot file")
        return PaperLabels(pilot_id, MISSING, problems=tuple(problems))
    return PaperLabels(
        pilot_id, PROVISIONAL,
        tuple(sorted({d["provenance"].get("labeler_model") for d in docs} - {None})),
        tuple(sorted({d["provenance"].get("guide_version") for d in docs} - {None})),
        False, sum(len(d.get("items") or ()) for d in docs), all(bool(d.get("complete")) for d in docs),
        tuple(problems),
    )


def load_label_set(root: Optional[Path]) -> LabelSet:
    """Load every pilot paper's labels under ``root`` (``None``: nothing loaded, all missing)."""
    if root is None:
        return LabelSet()
    store = LabelStore(Path(root))
    return LabelSet({pid: _paper(store, pid) for pid in sorted(PILOT_PAPERS)}, loaded=True)


__all__ = ("GOLD", "LabelSet", "MISSING", "PROVISIONAL", "PaperLabels", "load_label_set")
