"""Declared access policy for the versioned ``/v1`` routes of ``app/api/main.py`` (tasks T14, T19E, T31, T32).

The 34 inventoried routes and the ALS sign-in handlers keep their policies in
:mod:`app.modules.security.authorization`. This module adds the T14 routes with the same
:class:`~app.modules.security.authorization.RoutePolicy` type, so the one route dependency
(:func:`app.api.auth.request_context`) and the DEP-19 check
(:func:`app.api.auth.undeclared_routes`) cover them too.

Rules (docs/migration/api/README.md section 3):

* **Entry capability.** A route whose effect needs one fixed capability declares it, so the
  dependency refuses the caller before the handler runs and, for an admin capability,
  re-runs the ALS role check (access contract section 5.3).
* **Object policy in the service.** Routes over a job whose capability depends on the job
  kind (submit, resume) or on job ownership (read, cancel) declare no entry capability and
  rely on :class:`~app.modules.jobs.service.JobService`, which the SDK, the CLI and the
  worker call with the same context. Their handlers still re-run the ALS role check before
  any effect (:func:`app.api.auth.fresh_request_context`).
* **Signed in.** On ALS every ``/v1`` route needs a verified caller (none is public). On
  ``:local`` the caller is the local operator with every capability (D34).
* **``:local`` only.** A policy with ``on_als=False`` (the T32 folder routes) is registered by
  the factory on the local profile only; ALS profiles answer 404 for it
  (:func:`local_only_policies`).
"""

from __future__ import annotations

from collections.abc import Mapping

from ..modules.contracts.context import CAPABILITIES
from ..modules.security.authorization import RoutePolicy

#: Surface name of the T14 app factory (``RoutePolicy.surface``).
API_SURFACE = "app/api/main.py"
#: Prefix of every T14 route.
V1_PREFIX = "/v1"

_OBJ = "object policy in JobService (creator or jobs.admin, corpus scope)"
_FRESH = "; handler re-runs the ALS role check before the effect"


def _v(route_id: str, method: str, path: str, *caps: str, note: str = "", on_als: bool = True) -> RoutePolicy:
    """Build one ``/v1`` policy, validating capability names.

    ``on_als=False`` marks a ``:local``-only route (T32): the factory does not register it on an
    ALS profile, so it answers a plain 404 there, and the policy refuses it should it ever be mounted.
    """
    unknown = set(caps) - CAPABILITIES
    if unknown:
        raise ValueError(f"unknown capabilities: {sorted(unknown)}")
    return RoutePolicy(route_id, API_SURFACE, method, V1_PREFIX + path, frozenset(caps), note=note, on_als=on_als)


#: Every ``/v1`` route (route_id, method, path, entry capabilities, note).
API_ROUTE_POLICIES: tuple[RoutePolicy, ...] = (
    # jobs (JobService)
    _v("V01", "POST", "/jobs", note="kind capability (DEFAULT_KIND_CAPABILITIES) in JobService.submit" + _FRESH),
    _v("V02", "GET", "/jobs", note="lists only jobs visible under " + _OBJ),
    _v("V03", "GET", "/jobs/{job_id}", note=_OBJ),
    _v("V04", "GET", "/jobs/{job_id}/events", note=_OBJ),
    _v("V05", "GET", "/jobs/{job_id}/stream", note=_OBJ + "; re-checked at every poll"),
    _v("V06", "GET", "/jobs/{job_id}/progress", note=_OBJ),
    _v("V07", "GET", "/jobs/{job_id}/view", note=_OBJ + "; staged/approved/published UI mapping"),
    _v("V08", "POST", "/jobs/{job_id}/cancel", note=_OBJ + _FRESH),
    _v("V09", "POST", "/jobs/{job_id}/resume", note=_OBJ + " plus the kind capability or jobs.admin" + _FRESH),
    # curation review (review_case, cost approvals)
    _v("V10", "GET", "/curation/summary", "curation.approve"),
    _v("V11", "GET", "/curation/cases", "curation.approve", note="cases within the caller's corpus scope"),
    _v("V12", "GET", "/curation/cases/{case_id}", "curation.approve"),
    _v("V13", "POST", "/curation/cases/{case_id}/review", "curation.approve",
       note="review_case: human only, bound to the case revision the reviewer saw; write-ahead audited"),
    _v("V14", "POST", "/curation/cost-approvals", "curation.approve", note="create_cost_approval: human only"),
    _v("V15", "POST", "/curation/cost-approvals/{approval_id}/revoke", "curation.approve"),
    # publication (Publication); real targets stay H5-gated in F2WOperations
    _v("V16", "POST", "/publications", "graph.publish",
       note="frozen snapshot only; target must be an approved rehearsal target (H5 gate)"),
    _v("V17", "GET", "/publications/{job_id}/plan", note=_OBJ),
    _v("V18", "POST", "/publications/{job_id}/approval", "curation.approve",
       note="security.authorization.create_approval bound to the exact plan_sha256 shown; H5 gate"),
    _v("V19", "GET", "/publications/pointer", "chat.read_support", note="active pointer of one target"),
    _v("V20", "POST", "/publications/rollback", "graph.publish", note="human only; audited; H5 gate"),
    # audit and operations status
    _v("V21", "GET", "/audit/status", "jobs.admin", note="AuditRecorder.status with the degraded flag"),
    _v("V22", "GET", "/ops/status", "jobs.admin", note="ingestion monitor DTO (O-07): jobs, review queue, audit"),
    # validation judge model (owner O-U2): deployment-wide, admin-only on ALS (T25D)
    _v("V23", "GET", "/validation/judge-model", "settings.global",
       note="deployment judge model plus the provider's live model list; no binary verdict is read"),
    _v("V24", "PUT", "/validation/judge-model", "settings.global",
       note="set or clear the judge; must be in the live model list" + _FRESH),
    # knowledge operations (T19E over the T19C suites): reads for any signed-in user, jobs for ops.run
    _v("V25", "GET", "/operations/statistics", "chat.ask",
       note="kg_compute_statistics; T19C scope rule (graph outside a non-empty corpus scope is denied)"),
    _v("V26", "GET", "/operations/compare", "chat.ask", note="kg_compare_snapshots; same scope rule"),
    _v("V27", "GET", "/operations/paper-subgraph", "chat.ask", note="kg_paper_subgraph; same scope rule"),
    _v("V28", "GET", "/operations/recent", "chat.ask", note="kg_recent_entities (Tiled only); same scope rule"),
    _v("V29", "GET", "/operations/corpus-audit", "chat.ask", note="curate_audit_corpus over a configured corpus"),
    _v("V30", "POST", "/operations/jobs", "ops.run",
       note="submit a knowledge_operation job (whole population, no inline bound)" + _FRESH),
    _v("V31", "GET", "/operations/jobs/{job_id}", note=_OBJ + "; bounded report of a knowledge_operation job"),
    # T31 (owner decision B5, O-P4): cost control, bulk review, vocabulary quality, figure-scale records
    _v("V32", "GET", "/curation/cost-control", "curation.approve",
       note="remote-cost switch and cap (donor cborg_control) with the ledger spend against the cap"),
    _v("V33", "PUT", "/curation/cost-control", "curation.approve",
       note="set the switch and cap: human only, write-ahead audited" + _FRESH),
    _v("V34", "POST", "/curation/cases/bulk-review", "curation.approve",
       note="review_case per item (human only, bound to each shown revision, audited); results per case"),
    _v("V35", "GET", "/vocabulary/quality", "chat.read_support",
       note="vocabulary-quality snapshot history: counts only, newest first, bounded"),
    _v("V36", "GET", "/figures/scales", "chat.read_support",
       note="human-read figure-scale records within the caller's corpus scope; no image, crop or path"),
    _v("V37", "POST", "/figures/scales", "curation.approve",
       note="record a human-read scale: human only, corpus in scope, write-ahead audited" + _FRESH),
    _v("V38", "GET", "/figures/scales/{record_id}", "chat.read_support", note="one record; outside scope is 404"),
    # T32 (owner decision B4, O-P4): folder browse/index over F2W_FOLDER_ROOTS, mounted on :local only
    _v("V39", "GET", "/folders/roots", "ingest.acquire", on_als=False,
       note="root labels only; not registered on ALS profiles (404)"),
    _v("V40", "GET", "/folders/list", "ingest.acquire", on_als=False,
       note="entries of one folder under a configured root; traversal and links refused as 404"),
    _v("V41", "GET", "/folders/index", "ingest.acquire", on_als=False,
       note="bounded document inventory under one folder (read only; ingest is a job)"),
    # per-job log channel (T33)
    _v("V42", "GET", "/jobs/{job_id}/logs", note=_OBJ + "; scrubbed, bounded free-text log lines"),
)

#: ``(METHOD, path) -> RoutePolicy`` for the ``/v1`` routes.
API_ROUTE_CAPABILITIES: Mapping[tuple[str, str], RoutePolicy] = {
    (p.method, p.path): p for p in API_ROUTE_POLICIES
}


def api_route_policy(method: str, path: str) -> RoutePolicy | None:
    """Return the policy of a ``/v1`` route, or ``None``."""
    return API_ROUTE_CAPABILITIES.get((method.upper(), path))


def local_only_policies() -> tuple[RoutePolicy, ...]:
    """The ``/v1`` policies served on the local profile only (not registered on ALS)."""
    return tuple(p for p in API_ROUTE_POLICIES if not p.on_als)


__all__ = ("API_ROUTE_CAPABILITIES", "API_ROUTE_POLICIES", "API_SURFACE", "V1_PREFIX", "api_route_policy",
           "local_only_policies")
