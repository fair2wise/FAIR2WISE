"""Canonicalization, conservative merging and corpus campaigns (task T07, pre-H3).

Two separate intents live here:

* online resolution of one new mention against approved vocabulary and existing entities
  (``vocabulary``, ``candidate_search``, ``adjudication``, ``merge_safety``, ``online``);
* offline campaigns over many unresolved mentions (``grouping``, ``campaign``, ``migration``,
  ``cleanup``, ``growth``, ``audit``).

Similarity is never identity: a deterministic safety check (``merge_safety.check_merge``) is
mandatory before any mapping is accepted. Nothing here applies a merge, activates a
vocabulary or changes a graph by default; every apply route calls
``gates.authorize_apply`` (gate + ApprovalRecord on the exact plan hash + revision checks).
Importing this package has no side effects (no environment, network, model or encoder load).
"""

from .gates import CANONICALIZATION_GATES, ApplyRefused, authorize_apply, require_gate

__all__ = ["CANONICALIZATION_GATES", "ApplyRefused", "authorize_apply", "require_gate"]
