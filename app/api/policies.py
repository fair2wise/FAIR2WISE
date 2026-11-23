"""Declared access policy for the versioned ``/v1`` routes of ``app/api/main.py`` (tasks T14, T19E, T31, T32, T35).

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
    _v("V19", "GET", "/publications/pointer", "chat.read_support", note="active pointer of one target (last activated) plus the per-corpus `corpora` live view"),
    _v("V20", "POST", "/publications/rollback", "graph.publish", note="human only; audited; H5 gate; optional corpus"),
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
    _v("V32", "GET", "/curation/cost-control", "jobs.admin",
       note="remote-cost switch and cap (donor cborg_control) with the ledger spend against the cap"
            " (owner 2026-11-03: jobs.admin, not curation.approve)"),
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
    # B5/J-11 job kinds (T10B): enrichment, topic sweep and PDF parse; status and stop are V03/V08.
    # Row ids V32-V42 belong to other branches; this block starts at V50.
    _v("V50", "POST", "/jobs/enrichment", "jobs.admin", note="enrichment job (RT-27..29); admin-only on ALS" + _FRESH),
    _v("V51", "POST", "/jobs/topic-sweep", "jobs.admin", note="topic_sweep job (RT-30..32); admin-only on ALS" + _FRESH),
    _v("V52", "POST", "/jobs/pdf-parse", "jobs.admin", note="pdf_parse job (J-11); admin-only on ALS" + _FRESH),
    # rollback and corpus operations as publication jobs (T10B); approval is V18 (curation.approve)
    _v("V53", "POST", "/publications/rollback-replacement", "graph.publish",
       note="rollback = replacement plan to the previous version (of `corpus` if named; revive); human only; H5 gate"),
    _v("V54", "POST", "/publications/corpus-retire", "graph.publish",
       note="corpus removal plan, corpus name typed in; approval via V18; H5 gate"),
    _v("V55", "POST", "/publications/corpus-purge", "graph.publish",
       note="purge plan of retired records, own approval via V18 (Q3); H5 gate"),
    # T35 (A8, supersedes T32): library catalog, upload and verify over F2W_LIBRARY_DIR, mounted on :local only
    _v("V56", "GET", "/library", "library.read", on_als=False,
       note="mounted flag, paper and indexed counts, OpenAlex quota state; not registered on ALS (404)"),
    _v("V57", "GET", "/library/papers", "library.read", on_als=False,
       note="papers of the mounted folder: file name, local or verified title and DOI, status; no bytes, no path"),
    _v("V58", "POST", "/library/papers", "library.upload", on_als=False,
       note="multipart PDF upload into the mounted folder: size cap, PDF check, safe name, sha256 dedupe, atomic write"),
    _v("V59", "POST", "/library/papers/{paper_id}/verify", "library.upload", on_als=False,
       note="OpenAlex verification of one paper under the quota breaker; settled result cached by sha256"),
    # T38 (A8): KG bundles, schemas, models and cost estimate under /v1/library; :local only (ALS answers 404).
    # V56-V59 belong to T35 and V60-V69 to another session; this block uses V70-V75.
    _v("V70", "GET", "/library/kgs", "library.read", on_als=False, note="KG bundle summaries and continuable KG files"),
    _v("V71", "GET", "/library/schemas", "library.read", on_als=False, note="extraction schema choices (registry v0.3.x)"),
    _v("V72", "GET", "/library/models", "library.read", on_als=False,
       note="live model list for extraction; read-only, never PUT /settings"),
    _v("V73", "POST", "/library/estimate", "library.read", on_als=False,
       note="deterministic cost estimate and local confirmation token; writes nothing"),
    _v("V74", "POST", "/library/kgs/{name}/export", "library.extract", on_als=False,
       note="export a bundle to the configured release directory; explicit confirm in the body"),
    _v("V75", "POST", "/library/kgs/continue", "library.extract", on_als=False,
       note="create a bundle by continuing a bundle or a KG file (kg_to_terms reverse build)"),
    # T45 (A8): the Harvester's runs and paywalled queue under /v1/library/harvest; :local only. V76-V79.
    _v("V76", "GET", "/library/harvest/runs", "library.read", on_als=False, note="harvest runs with status counts"),
    _v("V77", "GET", "/library/harvest/runs/{run_id}/candidates", "library.read", on_als=False,
       note="the candidates of a run: tier, status, landing URL (never fetched for a paywalled paper)"),
    _v("V78", "POST", "/library/harvest/candidates/{candidate_id}/pdf", "library.upload", on_als=False,
       note="file a PDF a person saved for a paywalled candidate into the run's category (multipart)"),
    _v("V79", "POST", "/library/harvest/candidates/{candidate_id}/dismiss", "library.upload", on_als=False,
       note="dismiss a paywalled candidate"),
    # Shared category list (owner bug list 2026-11-23, task 9): the Papers and Harvester tabs read and extend one list.
    _v("V84", "GET", "/library/categories", "library.read", on_als=False,
       note="categories = top-level folders of the library (empty ones too) with paper counts; no paths"),
    _v("V85", "POST", "/library/categories", "library.upload", on_als=False,
       note="create a category folder from a name (letters, digits, _ and -; 64 max); an existing name (any case) "
            "answers created=false; never a path"),
    # Ollama model dropdown (owner bug list 2026-11-23, task 4): the settings panel lists the models of the deployment's
    # Ollama server through the API (the browser never calls Ollama). Read-only, bounded, no URL in the answer; the
    # entry capability is the one the settings panel itself needs.
    _v("V86", "GET", "/ollama/models", "settings.personal",
       note="models of {OLLAMA_URL}/api/tags: state ok|empty|unset|unreachable|error, models, short fixed error; "
            "read-only, 3 s timeout, 1 MB cap, no redirect; never the URL or host"),
    # UI graph sources (owner instruction 2026-11-06): the selectable JSON/Tiled graphs of the graph view. Read-only;
    # same entry capability as GET /graph (R06). The `source` query parameter on R06-R09 needs no extra capability.
    _v("V80", "GET", "/graph/sources", "chat.read_support",
       note="selectable graph sources: id, kind json|tiled, label, counts, read_only; Tiled only on :local "
            "(own managed Tiled); ids are server-allowlisted, never a path or URL"),
    # T18D (Q13, O-U3): the capability read behind the frozen UI's hidden Edit/Save controls. V56-V59 belong to
    # another branch; this block is V60-V69.
    _v("V60", "GET", "/capabilities", "chat.read_support",
       note="caller's role, can_edit (graph.edit), can_save, can_upload; computed from the guards that answer 403"),
    # T07B (owner go 2026-11-06, thirteenth set): accepted canonicalization proposals -> scoped plan -> gated apply
    # route. graph.publish follows the other apply/publish rows (V16, V20, V53-V55); the choice is owner-gated
    # (docs/migration/reviews/H4/apply_adapters.md). V61-V69 and V81+ are free; this row is V81.
    _v("V81", "POST", "/curation/merges/apply", "graph.publish",
       note="human only; dry_run default plans and reports; a real apply names the shown plan_sha256, the approval "
            "is created by create_approval (curation.approve) and every route runs the one canonicalization gate "
            "(closed until the C4 rerun: 501 gate_closed)" + _FRESH),
    # Pending Approvals page (owner instruction 2026-11-09, sixteenth set): one aggregating read over the items that
    # already wait for a human; every item names the existing route that approves or rejects it (V18, V08, V09, V13,
    # V81). graph.publish is the capability that submits and applies those plans (owner-gated choice, see
    # docs/migration/reviews/H6/pending_approvals.md); on ALS it is admin-only, and the handler re-checks the admin
    # role explicitly (security.authorization.can_review_approvals). No write route is added.
    _v("V82", "GET", "/approvals/pending", "graph.publish",
       note="aggregated queue of items awaiting a human: publication/rollback/retire/purge plans (job id, kind, "
            "scope, target, plan hash and counts), budget-paused jobs, curation cases awaiting review (ids and "
            "counts only, when the caller holds curation.approve), an exceeded cost cap (jobs.admin) and corpora "
            "with accepted merges to apply; each item carries the existing approve/reject route and body; ALS: "
            "verified admin only"),
    # Publish to Tiled button (owner decision 2026-11-10, twenty-first set): export, adopt and stage the publication
    # plan of a finished KG bundle as one library_publish job; :local only. It approves nothing: the plan waits on
    # Pending Approvals (V82) for the existing V18 approval. Design: docs/migration/reviews/H6/publish_button.md.
    _v("V83", "POST", "/library/kgs/{name}/publish", "graph.publish", on_als=False,
       note="confirm=false previews (corpus, next free vN, problems; writes nothing); confirm=true submits the "
            "library_publish job (export, adopt in process with the image's commit label, stage via "
            "submit_publication to the managed local Tiled); needs library.extract too; never approves"),
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
