# Pending Approvals page (H6 review packet)

Status: implemented on branch `forge_merge-T-approvals-ui` (not merged). **Evidence for review, not owner
approval.** Owner-gated items are in section 7.

Owner instruction (2026-11-09, sixteenth set, verbatim): "I think we need another page in the UI left side bar for
\"Pending Approvals\" that show up in :local mode and in the :als-dev and :als-prod images when an admin is logged
in".

## 1. Problem

Every human decision the deployment already asks for lives behind a different route: a publication, rollback,
retire or purge plan waits in `awaiting_review` for `POST /v1/publications/{job_id}/approval` (V18) with the exact
`plan_sha256` shown by `GET /v1/publications/{job_id}/plan`; a curation case routed to a person waits for
`POST /v1/curation/cases/{id}/review` (V13) at its revision; a job paused on its budget waits for resume or cancel
(V09, V08); the remote-cost cap can be exceeded (V32 reports it, V33/V14 fix it); accepted canonicalization
proposals wait for the gated V81 apply. The UI had no place that showed any of this.

## 2. What was added

### Backend: one aggregating read, no new write route

| Method and path | Policy row | Entry capability | Notes |
|---|---|---|---|
| `GET /v1/approvals/pending` | V82 (new) | `graph.publish`; on ALS additionally `Principal.is_admin` (`security.authorization.can_review_approvals`) | lists every item awaiting a human; each item names the existing route and body that decides it |
| `GET /v1/capabilities` | V60 (unchanged row) | `chat.read_support` | additive field `can_approve` = `can_review_approvals(ctx)` |

`F2WOperations.pending_approvals(ctx)` (`app/api/operations.py`) builds the list; `app/api/routes_approvals.py`
is the thin route. The body is `{items, counts: {kind: n}, total, includes, generated_at}`; `includes` says which
sources were consulted for this caller (`plans`, `review_cases`, `cost_control`, `merge_apply`).

Item kinds and what the UI calls (every `approve`/`reject` is `{method, path, body}`; `path` is **relative to the
API base**, `v1/...`, because the `/v1` response projector (AUD-06) turns absolute paths into opaque `f2w://` ids):

| kind | source | fields | approve | reject |
|---|---|---|---|---|
| `publication`, `rollback`, `retire`, `purge` | publish / `corpus_retire` / `corpus_purge` jobs in `awaiting_review` whose current plan has no valid approval (a publish job with `params.rollback.reason` is `rollback`) | `job_id, job_kind, corpus_scope, target, snapshot_ref, reason, plan: {plan_id, plan_sha256, snapshot_id, base_revision, creates, links, supersessions, affected}, submitted_at, requested_by` | `POST v1/publications/{job_id}/approval {plan_sha256}` (V18) | `POST v1/jobs/{job_id}/cancel` (V08) |
| `paused_job` | jobs in `paused_budget` visible to the caller | `job_id, job_kind, corpus_scope, pause_reason, submitted_at, requested_by` | `POST v1/jobs/{job_id}/resume {expected_revision}` (V09) | `POST v1/jobs/{job_id}/cancel` (V08) |
| `review_case` | `needs_review` cases in the caller's case scope; **only when the caller holds `curation.approve`**; ids and metadata only, no evidence, payload or attempts | `case_id, corpus, case_type, reason, route, priority, revision, updated` | `POST v1/curation/cases/{id}/review {bound_revision, accept: true}` (V13) | same with `accept: false` |
| `cost_control` | `jobs.admin` callers, when `spend.cap_exceeded` | `enabled, budget_cap_usd, spend, routes` | `null` | `null` |
| `merge_apply` | corpora of the case scope with accepted cases awaiting validation, when the T07B `MergeApplyService` is wired | `corpus, accepted_cases, case_ids, plan: null, plan_route` | `POST v1/curation/merges/apply {corpus, route, dry_run: false, plan_sha256: null}` (V81; the UI fills the hash of the dry run `plan_route`) | `null` |

`cost_control` carries no one-click body on purpose: raising the cap (V33) or recording a cost approval (V14) needs
an amount a person types; the item shows the two routes with placeholder bodies. `merge_apply` has no reject route
(rejecting an accepted proposal is a case decision, not an apply decision).

The read approves nothing, writes nothing and creates no approval record. A caller who approves still goes
through the route the item names, with the server's own checks: `create_approval` (human, `curation.approve`,
fresh ALS role check with the approver's own session credential, profile and target binding), the plan hash
comparison (`409 stale_approval`) and the H5 gate.

### Authorization

- Entry capability `graph.publish`: it is the capability that submits the plans the page lists (V16, V20, V53-V55)
  and applies merges (V81), and it is an admin capability, so the route dependency re-runs the ALS role check on
  entry (`RoutePolicy.needs_fresh_role_check`). An ALS non-admin never holds it (`capabilities_for`).
- `can_review_approvals(ctx)` (`app/modules/security/authorization.py`) states the owner's condition explicitly:
  `graph.publish` **and** (`:local`: the local operator; ALS: `Principal.is_admin is True`, the admin status the
  Tiled identity port derived from the roles at the last verified `whoami`). The service refuses with `DENIED` when
  it is false even if a context somehow carried `graph.publish` without the admin flag; the same function is
  `can_approve` in V60, so the sidebar entry and the route cannot disagree.
- Review items need `curation.approve` (the entry capability of V10-V13) and are filtered by the caller's corpus
  scope exactly as V11 (`_case_scope`). The cost item needs `jobs.admin` (the V32 read's capability). Jobs are the
  ones `JobService.list_visible` shows the caller (an admin sees every job; the local operator sees all).
- On ALS the listing never carries a token, cookie or credential (tested); strings go through the projector.

### UI

`ui/src/app/components/approvalsApi.ts` (typed client: parse, group, `performApprovalAction` sends exactly the
item's method, path and body, `approvalErrorMessage` names the typed bodies), `ApprovalsTab.tsx` (groups by kind,
plan details with creates/links/supersessions, target and hash; Approve/Reject buttons; a confirmation dialog that
shows the hash the decision binds to and the route and body about to be sent; result and error display per item;
"no reject route" when an item has none; the dry-run-then-approve flow of a merge item; empty state; re-read every
15 s and after every decision), `capabilitiesApi.ts` (`can_approve`, `canApprove()`), `App.tsx` (sidebar entry
"Pending Approvals" at `/approvals`, shown only on an explicit `can_approve: true`; absent field, 401, 403 and 404
hide it), `apiHttp.ts`/`agentApiErrors.ts` (the typed `code` and `reason` of a `/v1` error body on `ApiError`).
No browser storage is used by the page (the frozen persistence inventory is unchanged).

## 3. Exposure per profile

`:local`: the local operator holds every capability, the entry is always shown. `:als-dev`/`:als-prod`: the route
is registered; an anonymous caller gets `401 sign_in_required`, a signed-in user `403 denied` (no `graph.publish`),
a verified admin `200`; an admin whose role was dropped at Tiled gets `403` on the next read (fresh role check) and
`can_approve: false`. The legacy UI routes and their frozen contract fixtures are untouched.

## 4. Tests

Python: `tests/api_v1/test_v1_approvals.py` (20: policy row, kind mapping pinned to the publication module,
`can_review_approvals` and `can_approve` on `:local`/ALS/forged contexts, empty queue, staged plan listed with its
hash and approved through the route it names with the stale 409 first, reject = cancel, rollback item, review cases
ids-only and decided through V13, budget-paused job, exceeded cap, wired merge service with the dry run and the
gate-closed 501, grouping, no publication/curation, forged headers, ALS anonymous/user/admin/dropped role, SDK
parity). `tests/api_v1/test_v1_capabilities.py` and `test_v1_local.py` updated (66 policy rows, `can_approve` in
the DTO). Vitest: `approvalsApi.test.ts`, `ApprovalsTab.test.tsx`, `capabilitiesApi.test.ts`, `App.tabs.test.tsx`.
Playwright: `ui/e2e/approvals.spec.ts` (hidden when `can_approve` is false or absent, approve with the hash
confirmation, stale-approval refusal, reject through cancel) over the mock agent fixture (`approvals` option).

## 5. Trying it on the running `:local` container

The container has two publications live and nothing pending, so the page shows the empty state. Stage a plan
without approving it (docs: `mkdocs/docs/local-image.md` section 4), then open **Pending Approvals**:

```bash
API=http://127.0.0.1:8080/fair2wise/api
curl -fsS -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1:8080' -X POST $API/v1/publications \
  -d '{"snapshot_ref":"e2e_demo/v1","target":"http://127.0.0.1:8765"}'        # -> job_id; the worker plans it
curl -fsS $API/v1/approvals/pending                                             # the plan is listed with its hash
```

A re-publication of the same snapshot that is already live is idempotent on the job identity (the same job id as
the live one, so nothing new waits); use a snapshot or version that is not the live one (for example the other
published version, which yields a rollback-style replacement plan), or `POST $API/v1/publications/rollback-replacement`
`{"target": "http://127.0.0.1:8765", "reason": "demo"}`. In the page, **Approve** shows the hash and calls V18;
**Reject** cancels the job. Nothing is written until the approval.

## 6. Limits

- The queue is a point-in-time read; a plan can be re-planned between the read and the approval, and the server
  then answers `409 stale_approval` (the page shows it and re-reads).
- Case acceptance through the page sends `accept: true` without a resolution; the server refuses a blind accept
  (`422`) for cases that need a validated proposal, as V13 does today. The page is a queue view, not a case editor.
- `merge_apply` items are planned live by a dry run per click, not stored; the real apply stays gate-closed (T07B).
- The listing is bounded by `list_visible(limit=1000)` and `list_cases(limit=1000)` per source.

## 7. Owner-gated and open items (not self-approved)

1. **Entry capability.** `graph.publish` was chosen because it is the capability that already submits and applies
   every plan the page lists. The alternative is `curation.approve` (the capability that *approves* a plan, V18) or
   `jobs.admin`. On ALS all three are admin capabilities, so the exposure is the same there; on `:local` the local
   operator holds all of them. The choice matters only if the capability model is ever split.
2. **ALS admin identity.** "An admin is logged in" was read as `Principal.is_admin`, i.e. the admin status the Tiled
   identity port derived from the roles at the last verified `whoami` (the same notion `create_approval` and
   `approver_is_admin` use). No other admin notion (an allowlist, a FORGE role) exists in the codebase.
3. **Review cases in the queue** were included as ids and counts only; whether the page should also open a case
   (the 600-character evidence excerpt of V12) is a disclosure decision.
4. **Cost cap item** carries no one-click action by design; a form for V33 on the page would be a new UI write path
   over an existing route and needs the owner's word.
5. **Paused jobs** are listed for `paused_budget` only; `paused_provider` pauses resolve on their own and were left
   out.
