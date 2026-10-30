# ADR: Durable jobs, profile-aware approvals and replay safety

Status: Accepted at H1 (2026-10-12) (T01; `docs/migration/reviews/H1/decision.json` H1-8: the ADR set is the H1 baseline)
Date: 2026-10-12
Superseded/amended by: item 3 approved as H1-5 (separate publication gate; Tiled import needs an `ApprovalRecord` for the exact plan) and implemented by T10 (`16e4a37`) and T14 (`/v1/publications`, H5-gated). Item 6 (expiry) is amended below. T02B (H2 Q8, option b): a pending approval stays open while new chat messages are answered. T28A (`12156d1`, AUD-01): job stages re-authorize through `JobAuthority`; an unwired required capability is a startup error.

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
   *Amended 2026-10-29 (T28D, AUD-10).* Chat pending approvals still never
   expire (T02B). Publication approvals made through the `/v1` interface do
   expire: the code applies `DEFAULT_APPROVAL_TTL = timedelta(hours=1)` on
   every profile, `:local` included (`app/api/operations.py:58`). The owner
   recommendation in OPEN_DECISIONS B14/D3.5 is no expiry on `:local` and
   24 h on ALS. The TTL is pending owner decision (OPEN_DECISIONS D3.5, D3.8);
   this ADR does not choose a value.

## Consequences

- The global lock and synchronous rebuild remain until T10 replaces the paths
  they protect. Removing them is a behavior change with its own parity tests.
