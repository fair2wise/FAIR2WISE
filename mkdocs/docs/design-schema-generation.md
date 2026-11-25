# Design: on-the-fly LinkML schema generation

Status: **backend implemented** on branch `schema_gen` (steps 1 to 7 below); the UI (schema panel, editor, error list) is not built yet. This page records the owner decisions, the numbers used, what the routes do and what is still open. Items that could not be verified are listed at the end.

## Goal

Let a user create a KG for a domain the repository does not ship a schema for (for example loricariid catfish). When creating a library category or starting an extraction, the user opens "Create schema", describes the domain in a text box and presses send. The model chosen in Settings (free CBORG or Ollama) drafts a LinkML schema, with the shipped schemas as context. The draft lands in an editable monospace box that validates continuously and shows errors with line numbers. A schema that does not validate cannot be saved or used. The user may also paste or hand-write a schema, and can try a schema on one paper before using it.

## Owner decisions (2026-11-24)

| Question | Decision |
|---|---|
| Self-contained or `imports: matkg_schema`? | Self-contained only: `imports` is exactly `linkml:types`. |
| Mandatory core? | `Thing` and `related_to` are mandatory; the required core is exactly what the validator checks: classes `Thing` and `CodeSnippet`, slots `name`, `description`, `related_to` (`domain: Thing`, `range: Thing`). |
| Schema change? | A change makes a **new KG**; no in-place migration. A bundle's manifest lock is unchanged: it stays on the version it started with. |
| Validator? | Dependency-free (PyYAML plus `linkml_runtime`, already installed). The UI must state what was checked (see "What the UI must state"). The full `linkml` package is not approved. |
| Who may create schemas? | A separate capability, `library.author_schema` (see "Capability"). No approval step on the local profile beyond that capability. |
| Naming? | Schemas are named independently of any KG: `<F2W_WORKDIR>/schemas/<name>/<name>@vN.yaml`, id `user/<name>@vN`, immutable versions, several KGs may use one schema; name pattern `^[a-z][a-z0-9_-]{0,63}$`. |
| Untrusted descriptions? | Descriptions become extraction prompt text, so a model-written description is untrusted there too: capped (400 characters), control characters stripped, opt-in `sanitize_descriptions` on `SchemaHelper` for user schemas only; shipped schemas render byte for byte as before. |
| First-class? | A KG built on a user schema is fully usable on the whole path (selection, bundle creation, extraction, continuation, re-extraction, chat, viewer, Operations) and is marked as built on a user schema wherever its schema is shown. Only **publication** (Tiled, adoption, release binding) is refused, with a typed error; see below. |

## What the code does today (after this work)

| Concern | Behaviour | Source |
|---|---|---|
| Store | `UserSchemaStore`: list, get (text, sha256, version, created_at), save (validates, writes a new immutable `vN` atomically, mode 0600 in 0700 directories), diff, delete (only when no `<workdir>/kgs/*/manifest.json` names it; an unreadable manifest fails closed) | `app/modules/library/user_schemas.py` |
| Validator | `validate_schema_text` returns a `Report` with line numbers, `checked` and `note`; `insert_required_core` adds what is missing | `app/modules/library/user_schema_validation.py` |
| Choices | `list_schemas` and `resolve_schema` merge the store after the registry sets: `origin: user`, `review_status: user_generated`, label `<title> (user schema, vN)`, no host path | `app/modules/library/schemas.py` |
| Extraction | `LibraryExtractor` resolves the id through `KgLibrary.resolve`; the manifest block is `{id, version: "vN", sha256, path: "<name>/<name>@vN.yaml"}` (path relative to the schema store); the extractor opens the stored file after proving its hash equals the locked one and passes `user_schema=True` to the factory, which sets `sanitize_schema_descriptions` on the `Orchestrator` | `extraction.py`, `term_extractor/orchestrator.py`, `term_extractor/schema.py` |
| Lock and unit key | `ensure_schema_locked` and the unit key (schema id plus file hash) are unchanged | `bundles.py`, `extraction.py` |
| Estimate and confirmation | accept a user id (`KgLibrary.resolve`) | `kg_service.py` |
| Harvester label | the `schema` parameter accepts `user/<name>@vN` | `harvest.py` |
| Marking | `GET /v1/library/kgs` summaries add `schema_origin`, `schema_name`, `schema_version`; export metadata `schema_info` carries the same; the graph source label of a bundle on a user schema is `<name> (user schema <schema>, vN)` | `bundles.py`, `f2w_agent/api.py` |

### Why `id`, `name` and `version` of the text are not rewritten

The stored text is byte for byte what was validated. The top-level `name` of the text must equal the name the schema is saved under; the save is refused with a line-numbered problem (`name_mismatch`) otherwise, so the editor can fix it. Rewriting YAML text safely needs a parser round trip, which loses comments and layout. The text's own `id` (an https URI that is never fetched) and `version` stay as written: the store's `vN` is the identity.

## Capability

`library.author_schema` is a new member of `LOCAL_ONLY_CAPABILITIES` (`app/modules/contracts/context.py`), next to `library.read`, `library.upload`, `library.index`, `library.extract` and `library.harvest`. The local operator holds every library capability, so it is granted on `:local`. No ALS principal holds it, admin or not (`capabilities_for` never grants a local-only capability, and `RequestContext` refuses it in `tiled_authenticated` mode), so the ALS profiles do not have it by default. The routes are also `on_als=False`: ALS answers 404. Pins: `tests/library/test_library_jobs.py` (the closed set), `tests/api_v1/test_v1_local.py` (82 routes), `tests/api_v1/test_v1_als.py` (32 local-only routes), `tests/api_v1/test_v1_library_schemas.py`.

## Routes (policies V89 to V94 and V96 to V98, `:local` only)

| Id | Route | Capability |
|---|---|---|
| V89 | `POST /v1/library/schemas/validate` `{text}` returns the report | `library.author_schema` |
| V90 | `POST /v1/library/schemas/insert-core` `{text}` returns `{text}` | `library.author_schema` |
| V91 | `POST /v1/library/schemas` `{name, text}` saves; invalid text is 422 with the report | `library.author_schema` |
| V92 | `GET /v1/library/schemas/user` | `library.read` |
| V93 | `GET /v1/library/schemas/user/{name}@vN` | `library.read` |
| V94 | `GET /v1/library/schemas/user/{name}/diff?from=&to=` | `library.read` |
| V98 | `DELETE /v1/library/schemas/user/{name}@vN` (409 `schema_in_use` while a KG uses it) | `library.author_schema` |
| V96 | `POST /v1/library/schemas/generate` `{description, backend?, model?}` | `library.author_schema` |
| V97 | `POST /v1/library/schemas/preview` `{schema, paper_id, model?, backend?, confirm?, confirmation?}` | `library.author_schema` |

Request bodies are capped before they are parsed (256 KiB plus overhead for schema text, 16 KiB for the generate body). Schema text is returned verbatim, not clipped by the 600-character body projector. Details per route are in `docs/migration/api/README.md`.

## Caps proposed and used

| Cap | Value |
|---|---|
| Schema text | 256 KiB |
| Classes, slots | 150, 400 |
| Description length (also the prompt cap) | 400 characters |
| Other text values, prefixes, nesting | 200 characters, 20 prefixes, 8 levels |
| Generate description | 2,000 characters |
| Prompt view budget | 6,000 characters (Ollama), 12,000 (CBORG) |
| Model reply | 64 KiB, 6,000 tokens; one call 180 s |
| Repair rounds | 2 after the first draft |
| Preview | 3 pages, 8,000 characters per page, one call per page (60 s), 120 s overall |
| Versions of one schema | 999 |

## Generating a draft

`app/modules/library/schema_prompt.py` builds the prompt: instructions, the required core verbatim (`required_core_text()`), a deterministic condensed view of the shipped schemas (class names with the first sentence of at most 70 characters, slots with `domain>range` for domain-bearing slots, sorted by name, whole lines only, closed by a fixed `... (N more classes omitted)` line when cut) and the user's description fenced as data (fence markers and control characters removed). Measured: the condensed view of all offered shipped schemas is about 5,200 characters, so it fits both budgets, while today's full rendering of `matkg_schema` is about 13,000 characters.

`app/modules/library/schema_generate.py` makes the call through `make_chat_client` (the same clients as extraction), under the provider admission and `call_with_deadline`, with the label `schema-generate` (added to the closed set `LLM_LABELS` in `app/modules/tracing/runtime.py`, now 22 labels, and to `observability.md`). The backend resolves like extraction (`require_backend`); a CBORG model must pass `require_free_cborg_model` before any client is built (the policy default is used when the request names none; Ollama needs a model). The chat clients gained three optional, additive keyword arguments: `system`, `max_tokens` and `num_ctx` (Ollama only); left unset the request is byte for byte the old one. The reply is capped at 64 KiB, fences are stripped and the text is validated; if invalid, the problems go back to the model at most twice (the repair prompt omits the shipped view). Every attempt (text and problems) is returned and nothing is silently replaced. Failures are typed with plain words (501 backend not configured, 403 model not free, 503 `ollama_unreachable`, `model_timeout`, `response_too_large`, `model_call_failed`, `cborg_ip_not_authorized`); no message holds a URL, key or provider body.

## Preview on one paper

`app/modules/library/schema_preview.py`. Without `confirm` the route answers only an estimate (pages at most 3, tokens, optional cost, confirmation token from `estimate.py`); nothing is read or called. With the matching token it reads the first up to 3 pages with text, makes one plain-JSON model call per page and coerces the answers onto the schema with `SchemaHelper` (the extractor's own matching). It returns the terms (category, truncated definition, relations with `verified`) and a summary: how many categories fell back to `Thing`, how many relations became `related_to` or stayed unverified. Nothing is stored: no bundle, no KG, no term store; a text schema is written under `<workdir>/tmp/schema-preview-*` and removed. The difference from real extraction (agent with tool calls, merging, snippet passes) is stated in the answer's `note`.

## What the UI must state

The validator checks: the text parses as YAML (no tags, anchors or duplicate keys), the structural rules (closed set of keys, names, caps, imports exactly `linkml:types`), the required core, and that it loads in `SchemaView` and `SchemaHelper` with every reference resolving. It does **not** check full LinkML conformance (the `linkml` linter and validator are not used). The report carries `checked` and `note` for exactly this wording. The generate and preview results must be shown with their limits (a draft is a draft; a preview is three pages).

## Cost of the `linkml` package (measured, not approved)

Adding the full `linkml` package would add about 38 new PyPI distributions (Sphinx, SQLAlchemy, docutils and others), about 66 MB installed and about 10 MB download when every current pin is frozen. A free solve adds 43 and bumps 27 shared pins (pydantic, pyyaml, curies and others). Not approved; the dependency-free validator stays.

## Publication of a KG built on a user schema: refused today (open owner decision)

A KG on a user schema works everywhere except publication. Three places refuse it with a typed error whose message says the KG itself is unaffected and only publication is not enabled for user schemas yet:

| Place | Refusal |
|---|---|
| `app/modules/library/publish.py` (`LibraryPublisher.preview`, `_adopt`) | problem and error `reason: user_schema_not_publishable`, HTTP 501, before any export or job; a queued job fails closed in `_adopt` |
| `app/modules/graph/adoption.py` (`adopt_legacy_flat_file`) | `AdoptionRefused` rule `user-schema` when the flat file's `metadata.schema` is a `user/...` id (without it the file would silently bind to the default `matkg_schema` by file name) |
| `app/modules/graph/releases.py` (`check_schema_binding`) | `ReleaseRefused` code `schema_user_unsupported` |

### What it would take to enable publication (not implemented)

1. **Registry.** `SchemaRegistry.discover()` reads only `storage/schema` and the versioned sets, and the selectable purposes come from a hard-coded per-stem table (`_SELECTABLE`). User schemas would need registration and classification (`CLASS_BASE` with a new origin, `selectable_for`), keyed by id and hash, discovered from the store, with a review status. Roughly 1 day plus tests.
2. **Closure fingerprint.** `closure_files` assumes one base directory and resolves imports from it; a user schema has no imports besides `linkml:types`, so the closure is the single stored file. The fingerprint (`closure_fingerprint`) would be computed over the user store path, and `default_supported_schema_fingerprints` (the importer's allow-list) would have to include approved user schemas. About 1 day.
3. **Release binding.** `check_schema_binding` compares path and hash with the repository's `storage/schema` and the closure fingerprint, and the manifest `SchemaBlock` records a repository-relative path. A user schema lives outside the repository checkout and the release is a Git commit of `storage/kg`, so the schema bytes would have to be copied into the release (or the manifest must carry the text hash and the importer accept a schema outside the repo). This is the largest design change. About 2 to 3 days.
4. **Validation.** `graph/snapshot.py` `validate_graph(graph, entry, registry=..., helper=...)` needs a `SchemaEntry` and a helper for the user schema, and its classification of domain and range checks against a self-contained schema must be run against real KGs. About 1 day.
5. **Owner approval.** Registry sets pending owner review bind only on explicit selection. A user schema needs an equivalent review status and an approval route (who approves, where it is recorded). A policy decision plus about 1 day.

Estimated effort: 6 to 8 working days including tests and a rehearsal on the in-memory Tiled double. Risks: publishing makes a schema that nobody reviewed part of a Git release and of the shared Tiled catalog; model-written descriptions would then sit in published records; two corpora could carry classes of the same name with different meanings; a schema edit after publication needs a rule (new corpus version only). The owner decides whether publication is wanted at all and under what review.

## Not verified

- No live model call was made (tests use fakes); the 6,000 and 12,000 character budgets and the reply caps were not tried against a real small Ollama model, and `num_ctx` 8192 was not tried against a real server.
- The UI does not exist yet; the route shapes are tested through the real `create_api_app` on `:local` and `:als-dev`.
- The preview and the extraction share the schema prompt text, but the preview does not run the agent loop, so the number of model calls and the terms differ from a real extraction by design.
- A user schema with a very large number of classes makes a large extraction prompt (the 150-class and 400-slot caps allow about 200 KB of description text); the preview caps the schema text at 30,000 characters, extraction does not.
- Whether every KG node builder works without the base `Thing` slots was not exercised beyond the required core.
- Nothing was run against the cluster profile or built into an image.

## Remaining owner questions

1. Publication of user-schema KGs: wanted, and under what review (see above)?
2. Are the caps (256 KiB, 150 classes, 400 slots, 2 repairs, 3 preview pages) acceptable, or should any change?
3. Should extraction cap the schema prompt text the way the preview does?
