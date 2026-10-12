# ADR: Durable jobs, profile-aware approvals and replay safety

Status: Proposed (T01, gate H1)
Date: 2026-10-12

## Context

Plan sections 4.2 and 11 define the ingestion lifecycle and recovery cases.
The H0 baseline found that today:

- an approved extraction automatically rebuilds the KG and, in splash mode,
  wipes and re-imports it; graph publication has no gate of its own;
- pending actions persist in `workflow_state.json` and never expire;
- one global `asyncio.Lock` serializes all sessions, and the KG rebuild runs
  synchronously inside the async request handler.

## Decision

1. **Contracts.** `JobHandle`, `JobEvent` (versioned), `JobState` and
   `StageOutcome` in `app/modules/contracts/jobs.py`. Stage outcome and job
   lifecycle stay separate: a paused or failed stage says nothing about a
   paper's science.
2. **Approvals.** `ApprovalRecord` binds to one `plan_sha256`, target and
   scope, with optional expiry. Local approvals record `local-operator` with no
   synthetic Tiled identity; ALS approvals record the verified issuer and role
   check time. `executed_as` (the D27 writer) is always separate from
   `approved_by`. Approvals are created only by a trusted application
   operation, never by a tool or model.
3. **Publication gets its own gate.** Building a JSON snapshot and importing it
   are separate jobs. Import requires an `ApprovalRecord` that `authorizes()`
   the exact plan. This changes baseline behavior (rebuild-after-extraction
   stays; publication to Tiled no longer follows automatically) and needs owner
   approval at H1.
4. **Replay safety.** Each stage records intent, takes an owned lease, performs
   effects with stable IDs, verifies, then writes a completion record. Retries
   reconcile completed work. Exactly-once is not claimed.
5. **Workers.** Heavy work runs in an owned worker with durable state, not in
   the request handler or FastAPI `BackgroundTasks`. No Celery/Redis unless a
   need is demonstrated (plan T10).
6. **Pending-action expiry** (baseline: never) is a proposed improvement for
   T10, behind an explicit owner decision, because the current UI relies on
   pending state surviving reloads.

## Consequences

- The global lock and synchronous rebuild remain until T10 replaces the paths
  they protect. Removing them is a behavior change with its own parity tests.
