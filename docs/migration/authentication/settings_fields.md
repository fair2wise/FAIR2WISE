# Settings fields: personal or deployment-wide (T25D)

- **Status:** Implemented (T25D). Owner decisions: H2 Q4 (independent per-user settings on ALS,
  admin or not), H2 Q5 (`workflow_mode` admin-only), H1 O-A5/O-A6 (graph reading open to all
  readers; `extraction_mode` admin-gated), H1 "Default graph selection" (per-user graph selection),
  H1 "Tiled URL in settings" (editable on `:local` only).
- **Code:** `SETTINGS_USER_FIELDS`, `SETTINGS_DEPLOYMENT_FIELDS`, `settings_update_plan` in
  `app/modules/security/authorization.py`; `PersonalSettingsStore` and `LegacyUiGuard.settings_key` /
  `settings_update` in `app/api/legacy_ui.py`; `AgentPipelineService.apply_settings_for`,
  `settings_response_for`, `_use_runtime`, `runtime_view` in `app/modules/f2w_agent/api.py`.

## Behavior

- **`:local`:** unchanged. There is one principal (the local operator) and its settings are the
  server's runtime settings. Every field is writable, `tiled_uri` included. No personal store exists.
- **ALS (`:als-dev`, `:als-prod`):**
  - Personal fields are stored per principal, keyed by the verified Tiled issuer and subject, in
    `<workdir>/access/personal_settings.json`. They survive restarts.
  - `GET /settings` returns the caller's effective settings: deployment defaults with the caller's
    saved personal fields on top. A principal who never saved sees the deployment defaults.
  - `PUT /settings` stores the personal fields the body sets (only those; fields never saved keep
    following the deployment defaults). Deployment-wide fields are applied to the server runtime for
    a verified admin (`settings.global`, role check re-run) and ignored for anyone else, with
    `X-F2W-Warning: setting_not_writable=<fields>` instead of 403. The frozen UI sends every field,
    so the warning lists only ignored values that differ from the server's current value.
  - Every chat turn (`/chat`, `/chat/stream`, `/chat/action`, `/chat/action/stream`) switches the
    service to the caller's effective settings under the service lock before any retrieval or model
    call; agents are rebuilt only when the settings differ from the ones they were built for.
    `GET /graph` and `GET /graph/node/{id}` resolve the caller's selected graph.

## Field classification

Fields are those of `AgentSettingsUpdate` (the PUT body) plus `provider` (LLM-01 list).

| Field | Class | Reason |
|---|---|---|
| `backend` | personal | Chooses the model provider for the caller's own turns only (H2 Q4, LLM-01). |
| `model` | personal | Same as `backend`; affects only the caller's answers. |
| `provider` | personal | Listed by LLM-01 with backend/model; not a field of the current body (pydantic drops it). |
| `graph_source` | personal | Which graph the caller's turns read (splash or JSON). Chat requests already choose it per turn. |
| `json_graph_path` | personal | The caller's graph view / primary graph (per-user graph selection, H1). Paths are hidden from non-admins, so for them the selection is by `selected_graph_ids`. |
| `json_graph_paths` | personal | The caller's selected graphs (H1: "no user changes the selection for others"). |
| `selected_graph_ids` | personal | Graph-id form of the selection; how non-admins select graphs. |
| `kg_query_hops` | personal | Retrieval depth for the caller's turns only. |
| `kg_query_max_nodes` | personal | Retrieval node cap for the caller's turns only. |
| `source_rag` | personal | Whether the caller's turns add source-passage retrieval; reads only. |
| `use_live_tiled` | personal | Whether the caller's turns also read the live Tiled graph; reads only, against the deployment's fixed Tiled. |
| `tiled_uri` | deployment-wide, never writable on ALS | Changes which Tiled the shared service talks to. Editable on `:local` only (H1); ignored on ALS for admins too. |
| `extraction_mode` | deployment-wide (admin) | Controls how the shared extraction pipeline builds the KG for everyone (O-A6). |
| `targeted_max_pages` | deployment-wide (admin) | Parameter of the same shared extraction pipeline. |
| `workflow_mode` | deployment-wide (admin) | Owner Q5: admin-only. A legacy no-op in the UI API; download, extraction, KG rebuild and Tiled publication are separately admin-only (`ingest.acquire`, `ingest.extract`, `graph.publish`). |
| server default graph list | deployment-wide (startup) | The deployment default selection comes from startup configuration (`cfg.graphs` / `cfg.graph`); on ALS no PUT field changes it, since every selection field is personal. |
| `judge_model` (validation, `/v1/validation/judge-model`, not a `PUT /settings` field) | deployment-wide (admin) | Owner O-U2. The judge decides validation verdicts that are gate evidence for the whole deployment, and on ALS validation runs are admin actions. A per-user judge would let two users report the same revision under different judges, or let a non-admin pick the judge of an admin's run. Stored once at `<workdir>/validation/judge_model.json`; must be in the provider's live model list when set (T11B). |
| any unknown field | deployment-wide (fail closed) | Dropped by the body model today; `settings_update_plan` treats unknown names as deployment-wide. |

## Notes

- `settings_update_capabilities` (the server-wide rule) is unchanged and still used for `:local`.
- The response projection is unchanged: paths, `tiled_uri` and `tiled_error` need `settings.global`.
