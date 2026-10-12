# ADR: Legacy `splash` / `splash_links` wire translation onto Tiled Graph

- **Status:** Proposed (T18A, gate H1). Not approved. No code, setting, endpoint or credential changed.
- **Date:** 2026-10-12
- **Baseline:** frozen `222b1e7b130567dbf4a3242697ac2b465794a38d` (UI tree `75a19b9823bc924a6391311191d3e41a5b4917ff`). Line references are to that export.
- **Decisions this ADR must satisfy:** D27 (F2W writes to Tiled through one admin/service account), D30 (ALS: Tiled sign-in, verified admins only for privileged work, others chat-only), D34 (`:local`: full access without sign-in), D22 (Tiled 0.2.19). Plan section 5.2; FRONTEND_COMPATIBILITY.md sections 3 and 5; TILED_DEPLOYMENT_SPEC.md section 3; JSON_KG_PUBLICATION_SPEC.md section 6; `docs/adr/tiled-deployment.md` sections 3.2-3.3 (T20A).
- **Evidence:** executed fixtures in `tests/contracts/legacy_ui/fixtures/http/graph_node_patch_*.json`, `settings_put_*.json`, `chat_action_stream_extraction_yes.json`. Labels: **[Observed]** read in baseline source or seen in an executed fixture. **[Inferred]** reasoned from several places. **[Proposed]** a design for H1 review.

## 1. Context: what the frozen UI does today

**[Observed]**

| Fact | Evidence |
|---|---|
| The browser stores `graphSource: 'splash_links' \| 'json'` in `fair2wise-agent-settings-v1`. | `ui/src/app/components/agentSettings.ts:2,58,190` |
| Any stored value other than `'json'` loads as `'splash_links'`. A missing key loads the default `'json'`. | `agentSettings.ts:131,190` |
| The browser sends `graph_source: 'splash' \| 'json'` on `PUT /settings` and maps any non-`json` response back to `splash_links`. | `agentSettings.ts:220-226,234` |
| The server accepts only `^(splash\|json)$`. `splash_links` on the wire is a 422. | `app/modules/f2w_agent/api.py:141,253,414`; fixture `settings_put_422_bad_graph_source` |
| On every page load the browser overwrites its stored settings from `GET /settings`, so the server's `graph_source` decides the stored token. | `ui/src/app/App.tsx:169` |
| Chat requests do not carry `graph_source`; the server's runtime setting selects the graph. `ui_settings.graphSource='splash_links'` reaches only the trace. | `ui/src/app/components/data/liveAgent.ts:295-311`; `api.py:3552` (trace request) |
| Graph editing is enabled in the viewer when `graphSource !== 'json'`. There is no server capability check in the UI. | `ui/src/app/components/GraphMockup.tsx:663` |
| The server refuses edits in JSON mode with HTTP 400 `Editing node properties requires splash graph mode`. | `api.py:2980-2981`; fixture `graph_node_patch_400_json_mode` |
| `kg_mode` / `graph_source` value `splash` is also what `/health` reports as `kg_mode`. | `api.py:6630`; fixture `health` |
| In splash mode the session graph `<workdir>/kg.json` is a mirror of Splash, loaded at startup and when switching back to splash. | `api.py:2533-2534,2578-2591,2854-2855` |
| An approved extraction rebuilds `<workdir>/kg.json`, then **wipes the whole Splash DB and re-imports** it (`splash_reimport`, `allow_wipe` from `--allow-splash-wipe`). Progress phases `splash_reimport_started/result`. | `api.py:5646-5683`; `kg_update.py:119-170`; `scripts/start_agent_backend.sh:261`; fixture `chat_action_stream_extraction_yes` |

### 1.1 The edit endpoint: `PATCH /graph/node/{node_id}`

**[Observed]** `AgentPipelineService.update_graph_node` (`api.py:2978-3046`) does, in order:

1. Load `<workdir>/kg.json`, find the thing by MatKG id (404 if absent) and its Splash entity by `uri` / `properties.matkg_id` (`api.py:2984-2991`; `kg_update.py:229-268` pages every entity to find it).
2. Validate relationship updates: the edited node must be source or target, no self-loops, both endpoints exist (`api.py:3049-3067`). Predicates are normalized: `Affects` becomes `rel:affects` (`api.py:1234-1246`).
3. **Node fields** (`label`, `type`, `description`, `code_snippet`, `publications`): edit the JSON thing and send a property patch through `splash_update_entity`, which sends `properties` in `UpdateEntityInput` (`api.py:1249-1300,3018-3022`; `kg_update.py:270-298`, properties at `:286`). `publications` **replaces** the list. A type change records `raw_category`.
4. **Linked snippets** (`linked_code_snippets[]` with alias `_action: upsert|unlink`, `api.py:300-309`): the list is a **desired state**. An `upsert` with an existing id updates the snippet entity's properties in place (`api.py:3256-3260`). An upsert without an id, or with a `temp:`/`new:` id, mints `matkg:snippet<slug><hash>` (`api.py:1176-1189`) and creates the entity and a `rel:has_code_snippet` link. `unlink` deletes links in both directions and keeps the snippet entity. **Any current snippet missing from the submitted list is also unlinked** (`api.py:3331-3332`). Fixture `graph_node_patch_unlink_and_remove` shows this: the snippet created by the previous PATCH disappeared because the request did not list it.
5. **Relationships** (`relationship_updates[]`, `action: add|remove`): `add` appends the association and creates a Splash link only if none exists. `remove` deletes the association and every matching link (`api.py:3069-3128`).
6. Save `kg.json`, reload retrieval, and return the re-read `GraphNode` from `kg.json` (`api.py:3040-3046`). The response is synchronous and is the readback the UI shows after Save.

Failure mapping: `FileNotFoundError` gives 404, `ValueError` gives 400, anything else gives 502 `Splash update failed: ...` (`api.py:6751-6760`). The Splash writes and the JSON save are **not atomic**. A failure after step 3 leaves Splash partly updated and `kg.json` unchanged **[Inferred]**.

### 1.2 Target backend facts

**[Observed in Tiled 0.2.19rc2 per `docs/adr/tiled-deployment.md` sections 3.2-3.3; final 0.2.19 still to be re-checked]**

- `createEntity` and `createLink` accept `properties`. `updateEntity(id, {name?, nodePathParts?, uri?, entityType?, accessTags?})` and `updateLink(id, {predicate?, accessTags?})` do **not**. **Entity and link properties are create-only.**
- `deleteEntity` (cascades links) and `deleteLink` exist. There is no upsert. Idempotency must come from lookups by `uri`/`name`.
- Writes need `write:metadata` and the record's access tags.
- The baseline already reads Tiled Graph over GraphQL for live lookup (`app/modules/tiled_graph.py`, `TILED_GRAPH_ID`, `/graph/neighborhood/{node_id:path}`). That path is read-only and separate from editing.

## 2. Decision (proposed)

### 2.1 Wire values that stay

| Surface | Value kept | Meaning after migration |
|---|---|---|
| Browser storage `graphSource` | `splash_links` | Logical "editable literature graph" mode. Unchanged key and token (`persistence_keys.json`). |
| `PUT /settings`, `ChatRequest`, `ChatActionRequest` `graph_source` | `splash` \| `json` (same pattern) | `splash` selects the configured, authorized Tiled literature scope. `json` keeps local read-only JSON graphs. |
| `GET /settings.graph_source`, `/health.kg_mode` | `splash` \| `json` | Same. Must stay a non-`json` literal in editable mode, or the browser disables editing (`GraphMockup.tsx:663`). |
| `ChatResponse.graph_source_requested/used` | existing values | No new literal is added on the old fields **[Proposed]**. Today they echo the retrieval verdict (`api.py:6502-6503`); the adapter keeps `splash` for the editable mode. |
| `GraphPayload.source_path` | string | Must not leak a Tiled token or admin URL. **[Proposed]** `tiled://<scope-alias>` (the baseline already uses `tiled://graphql` for neighbourhoods, `app/modules/tiled_graph.py:343`). Note: in splash mode today it is the workdir path of `kg.json`. Any change is a reviewed deviation (path disclosure). |
| SSE phases `splash_reimport_started/result` | names kept | Emitted for the Tiled publication/reload stage that replaces the reimport (FRONTEND_COMPATIBILITY.md section 4). Message text may say "Tiled graph". |
| Errors `splash_error` status, `Splash update failed: ...` 502 detail, `...requires splash graph mode` 400 detail | kept | Client shows `detail`/`answer` text only (`liveAgent.ts:512,606,724-726`). Wording may name Tiled; the status literal and HTTP code stay. |

`splash` stops meaning "a Splash server". No Splash URL, API key, process, schema or `pixi` dependency remains. Old `KG_RAG_SPLASH_URI` / `SPLASH_LINKS_URI` values are **not** reused as Tiled addresses (plan 5.2). The Tiled target comes only from the trusted server profile (T20A/T24A).

### 2.2 Capability / diagnostic field

**[Proposed]** Additive, optional fields that the frozen client ignores (verified: the client reads only named fields, `agentSettings.ts:251-302`; extra keys are harmless):

```json
"graph_backend": {
  "storage_backend": "tiled",            // "tiled" | "json"
  "legacy_mode_token": "splash",         // what the old enum still says
  "tiled_version": "0.2.19",
  "scope": "<configured literature scope alias>",
  "profile": "local | als-dev | als-prod",
  "edit_capability": "enabled | admin_required | sign_in_required | unavailable",
  "edit_strategy": "revision_projection",
  "capability_error": null
}
```

Placement: on `GET/PUT /settings` and `/health`. Not on `ChatResponse`: `ChatResponse` is frozen field-for-field (test `test_chat_response_fields_on_every_chat_payload`). The capability values come from the T20A capability snapshot (C3: does `UpdateEntityInput.properties` exist; C4: writer principal). The writer credential, tokens and admin URL never appear. Adding these fields changes the OpenAPI snapshot. That is an expected additive diff for H2, not a regression.

### 2.3 Edit projection on create-only Tiled

**[Proposed]** Each legacy public node id (`matkg:...`) is a **stable logical id**. The effective record for that id is resolved through a revision chain, so old citations, bookmarks, `fair2wise.citeFocusNodeId` and session histories keep resolving.

- **Logical identity:** one Tiled entity per revision with `uri = <matkg id>` and properties `{matkg_id, f2w_revision: n, f2w_supersedes: <entity id>|null, f2w_edit_id, f2w_base_snapshot}`. The current revision is the one whose id no live `rel:f2w_supersedes` link targets **or**, preferably, the one recorded in F2W's edit journal (`app/modules/graph/edit_journal.py`, JSON_KG_PUBLICATION_SPEC.md section 6). The journal is authoritative for readback. Tiled is the published projection.
- **Readback:** `PATCH` returns `GraphNode` built from the effective record after the journal write and the scoped Tiled effect both succeed. That matches today's synchronous Save (`api.py:3040-3046`). No async job envelope on the old endpoint.
- **Retirement:** superseded revisions are not deleted during a user edit. They get access tags that exclude them from default reads, or stay and are filtered by the projection. Deleting entities cascades links (`deleteEntity`), which could remove relationships owned by other records. Reject that as an edit mechanism.
- **Stale edits:** the request carries no base revision (frozen client). The server records the revision it read at request start and refuses the write with the existing 400/502 path if the effective revision changed meanwhile **[Proposed]**. Last-writer-wins without detection is not acceptable.
- **Durability:** every acknowledged edit is journaled before the Tiled effect. It is reconciled into the next immutable JSON version, so a later re-import cannot erase it (JSON_KG_PUBLICATION_SPEC.md section 6).

Per operation:

| Legacy operation (baseline) | Tiled projection (proposed) | Safe? |
|---|---|---|
| `label` change (`name`) | `updateEntity(name)` on the current revision is allowed by 0.2.19rc2. Prefer a new revision anyway when other fields change in the same request, so the request is one revision. | Yes |
| `type` change (`entityType`, plus `raw_category`/`type` properties) | `entityType` is updatable; `raw_category`/`category`/`type` are properties, so this needs a new revision. | Yes, by revision |
| `description`, `code_snippet` (CodeSnippet node) | Property change: new revision with merged properties. | Yes, by revision |
| `publications` replace | Property change: new revision. The full list is replaced, matching the baseline. | Yes, by revision |
| Linked snippet `upsert` with existing id | New revision of the snippet entity. Move the `rel:has_code_snippet` link to the new revision (create the new link, then delete the old one). | Yes, by revision and link move |
| Linked snippet `upsert` without id / `temp:` / `new:` id | `createEntity(CodeSnippet)` with the same minted `matkg:snippet...` id rule (`api.py:1184-1189`) plus `createLink`. Idempotent by `uri` lookup. | Yes |
| Linked snippet `unlink` | `deleteLink` for both directions. Keep the snippet entity. | Yes |
| Implicit unlink of snippets omitted from the list (`api.py:3331-3332`) | Same as unlink. Kept because the frozen client relies on it. | Yes, but see STOP-3 |
| Relationship `add` | `createLink(subject=current revision of source, predicate, object=current revision of target)` if no equivalent link exists. | Yes |
| Relationship `remove` | `deleteLink` for every matching link between the current revisions. | Yes, if the link is in the editable scope (STOP-2) |
| Re-pointing links when a node gets a new revision | Every incoming and outgoing link of the old revision must be recreated against the new revision (links have create-only properties too). Neighbours themselves are not touched. | Conditionally (STOP-1) |
| Post-extraction `splash_reimport` (wipe and re-import everything) | **Replaced** by the reviewed scoped publication of the new JSON version (JSON_KG_PUBLICATION_SPEC.md). No wipe. Progress phase names kept. | Wipe is not supported (STOP-4) |
| Startup `_sync_session_graph_from_splash` | Read the effective graph from the Tiled scope into the session view. No write. | Yes |

Effects stay scoped: only entities and links in the configured literature scope with F2W access tags. The adapter never edits FORGE-owned or beamline (`beamline:`/`tiled:` identity) records. `PATCH` on a `beamline:` or `tiled:` id returns the existing 404/400 path **[Proposed]**. Today such ids are not in `kg.json` and already give 404 (`api.py:2986-2987`).

### 2.4 Access policy per profile

**[Proposed]**, from D27/D30/D34 and AUTHORIZATION_SPEC.md:

| Profile | Who may `PATCH /graph/node`, approve download/extraction, upload | Identity check | Tiled write principal | Frozen UI effect |
|---|---|---|---|---|
| `:local` (`authorization_mode: local_trusted`) | Every local user, without sign-in (D34). | None. Actor recorded as `local-operator`. | D27 admin/service account, server-side only. | Unchanged: Save works, as today. |
| `:als-dev` / `:als-prod` (`tiled_authenticated`) | Verified Tiled admins only (D30). | Native Tiled `whoami` with the caller's token; `admin` in roles, fresh, right issuer. Never inferred from the writer credential, headers or browser fields. | D27 account, after the caller check. | Admin: unchanged. Non-admin: chat-only. |

Gaps the frozen UI cannot close by itself (ALS only):

- The frozen client sends no credentials on API calls: plain `fetch` with no `credentials`/`Authorization` (`liveAgent.ts:315-733`), and CORS has `allow_credentials=True` (`api.py:6616-6622`; fixture `cors_simple_get_allowed_origin`). Same-origin `/fair2wise/api` cookies would be sent by default for same-origin `fetch` **[Inferred: browser default `credentials: 'same-origin'`]**. So ALS identity must ride on a same-origin session cookie set by the minimal ALS login wrapper (T25A). It cannot be a bearer header.
- A non-admin ALS user still sees edit controls, because `canEdit` depends only on `graphSource` (`GraphMockup.tsx:663`). Save returns a denial through the existing error path (HTTP 403 with `detail`), which the client shows as an error (`liveAgent.ts:724-726`). 403 is a new status code on this route, so it is an owner-reviewed deviation. Hiding controls would need a UI change, which is out of scope for the frozen client.
- Denial happens before any journal or Tiled effect. A failed or expired sign-in never falls back to local mode.

## 3. Operations that cannot be supported safely: STOP for owner decision

| ID | Issue | Why it is unsafe | Options for the owner |
|---|---|---|---|
| **STOP-1** | Re-linking every incoming and outgoing link when a node gets a new revision. | Create-only link properties mean every evidence-bearing link must be recreated. That costs N writes per Save with no server-side transaction, so a crash can leave half-moved links. Links owned by other corpora or FORGE could be in the neighbourhood. | (a) Accept a journaled, resumable multi-step move with a recovery job. (b) Keep scientific property edits in the F2W journal or effective-record overlay only, and project them into Tiled at the next JSON publication. (c) Ask the Tiled team for property updates (`UpdateEntityInput.properties`). Then C3 picks in-place updates. TILED_DEPLOYMENT_SPEC.md section 3 says not to assume the rc2 limit holds for every deployment. |
| **STOP-2** | `remove` of a relationship or `unlink` of a snippet whose link Tiled holds under another owner's or corpus's access tags. | The baseline deletes every matching link (`api.py:3127-3129`). In shared Tiled that could delete data F2W does not own. | Restrict to links carrying F2W scope tags. Return the existing 400 for others. That narrows today's behaviour, so the owner must approve it. |
| **STOP-3** | Implicit unlink of snippets omitted from `linked_code_snippets`. | A stale browser tab with an old snippet list silently unlinks snippets added in another session. The baseline has the same hazard, but in Tiled it becomes a shared-server effect. | Keep it (bit-for-bit compatibility) with the stale-revision check in 2.3, or require explicit `unlink`. The second changes behaviour and needs approval. |
| **STOP-4** | `splash_reimport` wipe-and-reimport after each approved extraction (`kg_update.py:119-170`, `allow_wipe`). | A whole-graph reset on a shared or central Tiled server is forbidden (FRONTEND_COMPATIBILITY.md section 5; JSON_KG_PUBLICATION_SPEC.md). | Replace it with scoped publication of the new JSON version (no deletes outside the new entities). This changes timing: published data may lag the session answer. The owner must accept the projection and timing. |
| **STOP-5** | Non-atomic partial failure. | The baseline already leaves Splash and JSON inconsistent on mid-request failure. With the journal there is a third store. Acknowledging a Save that only partly reached Tiled would be a false success. | Acknowledge only when the journal plus the scoped effect complete. Otherwise return 502 with the journal entry marked failed and reconciled later. The owner confirms that this error UX (existing 502 text) is acceptable. |
| **STOP-6** | ALS non-admin users still see edit controls (frozen UI). | No security risk if the server denies. It is a UX deviation and adds a 403 code. | Accept 403 with `detail` on the old route, or approve an additive UI capability read later (not part of T18). |

None of these may be resolved by hiding controls, returning fake success, or keeping a Splash process running (FRONTEND_COMPATIBILITY.md section 5).

## 4. Consequences

- The old UI keeps working with no client change in local mode, and in ALS mode for admins.
- The OpenAPI snapshot changes additively (capability fields). The T18A suite will flag it and H2 records it as an approved diff.
- Required tests (T18B/T18C, UI04 and UI15-UI17): every row of the 2.3 table against a disposable Tiled 0.2.19. Readback through the same public id after Save. Old citation and bookmark ids still resolve after a revision. Stale-revision refusal. No write outside scope. ALS admin, non-admin, expired and forged identity cases. Local no-login Save. Crash between journal and effect.

## 5. Open questions for H1

1. Is `UpdateEntityInput.properties` present in the final 0.2.19 central build (T20A C3)? If yes, most of STOP-1 goes away.
2. Literature-scope access-tag layout on the central servers (T20A Q-T3).
3. Should `graph_source_used` on `ChatResponse` stay `splash` for Tiled-backed answers, or may it carry `tiled`? The browser stores it as `graphSourceUsed` in session history (`chatSessions.ts:19,113`) and only displays it **[Inferred]**. Keeping `splash` is the conservative default here.
