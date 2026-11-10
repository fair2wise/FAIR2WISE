# Pending Approvals

The **Pending Approvals** entry in the left sidebar lists everything on this
deployment that waits for a person to decide, and lets you decide it from one
place. It appears on `:local` always, and in the `:als-dev` and `:als-prod`
images when a verified Tiled admin is signed in (`GET /v1/capabilities` answers
`can_approve: true`). Anyone else, an older server, or a 401/403/404 hides it.

## What is listed

The page reads `GET /v1/approvals/pending` and groups the items by kind:

| Group | What waits | Approve | Reject |
|---|---|---|---|
| Publication, Rollback, Corpus removal, Purge plans | a planned change that is not approved yet: corpus, target, snapshot, plan id and **plan hash**, counts of creates, links and supersessions, who asked and when | `POST /v1/publications/{job_id}/approval` bound to the shown hash | cancel the job |
| Jobs paused on budget | a job stopped at its cost budget | resume | cancel the job |
| Curation cases awaiting review | cases routed to a person (ids and metadata only; the case text stays in the review routes) | accept at the shown revision | reject at the shown revision |
| Remote-cost cap exceeded | spend above the configured cap | no one-click action: the routes that take a new cap are shown | - |
| Accepted merges to apply | accepted canonicalization proposals of a corpus | **Plan (dry run)** first, then approve the planned hash | - (no reject route) |

Every button calls the route the server published for that item, with the body
the server published; the page never approves through a route of its own, and it
never approves without asking.

## Approving

1. Read the plan details on the card.
2. Click **Approve**. A confirmation shows the hash your decision is bound to and
   the exact route and body that will be sent.
3. Confirm. The result (or refusal) is shown on the card and the list is re-read.

Refusals keep their typed code: `409 stale_approval` means the plan changed since
it was shown (nothing was approved; review the new plan), `403` means the server
did not let this caller decide, `501` means the deployment does not support the
step (for example a merge apply whose gate is closed).

The list refreshes every 15 seconds and after every decision; **Refresh** reads
it now. An empty page says so.

## Trying it on `:local`

The `:local` container keeps nothing pending once its publications are live. To
see an item, plan a publication without approving it (see
[The :local image, end to end](local-image.md), section 4), then open the page:

```bash
API=http://127.0.0.1:8080/fair2wise/api
curl -fsS -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1:8080' \
  -X POST $API/v1/publications -d '{"snapshot_ref":"e2e_demo/v1","target":"http://127.0.0.1:8765"}'
curl -fsS $API/v1/approvals/pending
```

Use a snapshot that is not the live one (a re-publication of the live snapshot is
the same job and waits for nothing), or `POST $API/v1/publications/rollback-replacement`
with `{"target": "http://127.0.0.1:8765", "reason": "demo"}`.
