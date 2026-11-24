# Design: on-the-fly LinkML schema generation

Status: proposal for owner review. Nothing here is built. Written from the code at branch `linkml_design` (from `forge_merge`); items I could not check are listed under "Not verified".

## Goal

Let a user create a KG for a domain the repository does not ship a schema for (for example loricariid catfish). When creating a library category or starting an extraction, the user opens "Create schema", describes the domain in a text box, and presses send. The model chosen in Settings (CBORG or Ollama) drafts a LinkML schema, with the shipped schemas as context. The draft lands in an editable monospace box. The box validates continuously and shows errors with line numbers. A schema that does not validate cannot be saved or used. The user may also paste or hand-write a schema.

## What the code does today

| Concern | Today | Source |
|---|---|---|
| Shared base | `matkg_schema.yaml` (1062 lines): `Graph` (tree_root), abstract `Thing`, about 30 classes, `Association` classes, slots incl. `related_to`; imports only `linkml:types` | `storage/schema/matkg_schema.yaml` |
| Overlays | `rsoxs_schema.yaml`, `bl1101_schema.yaml`: `imports: [matkg_schema]` by sibling file name, add classes/slots, `is_a` shipped classes (`ExperimentalTechnique`, `Thing`) | `storage/schema/` |
| Registry | `SchemaRegistry.discover()` globs `*.yaml` in `storage/schema` plus versioned set directories; selectable purposes come from a hard-coded per-stem table | `app/modules/schema/registry.py` |
| Extraction choices | `list_schemas`/`resolve_schema` offer only registry sets whose version starts `0.3.`; `manifest_block` stores `id, version, sha256, path` | `app/modules/library/schemas.py` |
| Extractor use | `SchemaHelper` opens the file with `SchemaView`, reads `all_classes()`/`all_slots()`, renders them as prompt text, and coerces model terms onto classes and relations (fuzzy match, fallback `Thing`, `related_to`) | `app/modules/term_extractor/schema.py` |
| Lock | A bundle's `manifest.json` records the schema; `ensure_schema_locked` refuses any other schema on continuation or re-extraction; the unit key includes the schema id and file hash | `app/modules/library/bundles.py`, `extraction.py` |
| Routes | `GET /v1/library/schemas`, `GET /v1/library/models`, `GET /v1/ollama/models` | `app/api/routes_library_kgs.py`, `routes_ollama.py` |

## What a new schema must contain

The extractor only needs what `SchemaHelper` reads, so the contract is small. Required for the registry and manifest: top-level `id`, `name`, `version`, plus `classes` (the registry classifier requires `id`, `name` and one of `classes`/`imports`/`slots`). Required for good extraction: a `description` on every class and slot (they become the prompt text), a class named `Thing` (the fallback category), a slot `related_to` (fallback relation; without it unknown relations are kept with `verified=False`), and `domain`/`range` on relation slots (used for domain/range checks). Slots named `description` or `category` are dropped as relations.

Blocking constraint: overlays import `matkg_schema` as a sibling file, resolved from the importing file's directory. A generated schema stored elsewhere cannot do that. Proposal: generated schemas are self-contained, `imports` limited to `linkml:types`. The model is shown `Thing`, `related_to` and one or two overlays as a pattern to copy, not as something to import. Allowing import of a shipped schema would need an import map and a closure fingerprint over files in two directories (`closure_files` assumes one base directory); defer.

## Storage and naming

Generated schemas live under the state directory, never under `storage/schema`: `<F2W_WORKDIR>/schemas/<kg-name>/<kg-name>@vN.yaml`. `F2W_WORKDIR` resolves in `app/modules/workdir.py`, and bundles already sit in `<workdir>/kgs` (`bundles.py`, `BUNDLES_DIRNAME`). The id is `user/<kg-name>@vN`; names follow the bundle `NAME_RE`; the file name is derived server-side, never taken from the client. Each save writes a new immutable `vN`; no file is ever overwritten. The manifest block gets `{id, version, sha256, path}` as today, with `path` relative to the schema store (the client never supplies a path).

## Extraction selection and edits

A new `UserSchemaStore` is merged into the choices returned by `list_schemas`, marked `origin: user`, `review_status: user_generated`, and selectable for `extraction`. The shipped registry stays untouched. `resolve_schema` consults both. Once a bundle exists its manifest wins (as in `extraction.py`), so editing a schema never changes an existing KG. Because the unit key contains the file hash, a different version re-runs papers instead of mixing.

| Situation | Behaviour |
|---|---|
| Save an edit | New `vN+1`; `vN` stays on disk while any bundle references it |
| Existing KG, schema edited | KG stays locked to `vN`; UI offers "new KG with vN+1" |
| Continue a KG with a newer version | Refused by `ensure_schema_locked` (unchanged); a migration is out of scope |
| Delete a schema | Allowed only when no manifest references it |

## Treating model output as untrusted

Model text is data. Pipeline: size cap (proposal 256 KiB and a class/slot count cap) before parsing; `yaml.safe_load` only (no `!!python` tags, no custom loaders); top-level mapping check; allow-list of top-level keys; `imports` must be exactly `linkml:types` (no URLs, no paths, no `..`); no `source_file`/`schema_map` style fields; then `SchemaView` load, `SchemaHelper` construction and `class_induced_slots` for every class (the call that fails for the broken `rsoxs_schema_extract`, see `KNOWN_ISSUES`). Nothing is executed or fetched. Prompts built from user text carry no secrets and no file contents.

## Validation

Installed LinkML tooling is `linkml-runtime` only (`pyproject.toml` line 34, `pixi.toml` line 110). Proposal for v1, no new dependency: three levels run server-side on every change (debounced, `POST /v1/library/schemas/validate`): YAML parse errors with line and column from the YAML mark; our structural rules above with line numbers resolved from a position-tracking parse; then the `SchemaView`/`SchemaHelper` load check, whose errors carry no line, so they are reported against the nearest class or slot name. Full meta-model validation (`linkml` package linter/validator) would add a heavy dependency; flagged, not proposed.

## Model failure and repair

If the draft fails validation, the editor still receives the text with its errors. One optional bounded repair: send the errors back at most 2 times, then stop; the user can edit or retry. Every attempt is shown, never silently replaced. Model errors (CBORG refusal, Ollama unreachable) use the existing `/v1/library/models` status and the free-model policy.

## App-side versus deployment

| Part | Change | Image/deploy |
|---|---|---|
| API | `POST /v1/library/schemas/generate`, `.../validate`, `POST /v1/library/schemas` (save), `GET .../schemas/{id}`; capability `library.extract` as publish uses | none |
| Service | `app/modules/library/user_schemas.py`: store, validator, prompt builder, merged choices | none |
| UI | schema panel in the library category and extraction forms: prompt box, monospace editor (`white-space: pre`, tab as spaces), error list | none |
| Storage | `<workdir>/schemas/` | none if `F2W_WORKDIR` is a writable volume (compose `agent-runs:/app/runs`; cluster state PVC per `docs/cluster-profile.md`); a read-only root filesystem is fine because nothing is written outside it |
| Dependencies | none for the proposed validator | none |

## Effort and commit order

About 6 to 8 working days including tests. Order: (1) validator and size/safety rules with tests; (2) user schema store with immutable versions and manifest block; (3) merge into `list_schemas`/`resolve_schema` and extraction selection; (4) generate route with prompt builder and bounded repair; (5) UI editor and panel; (6) docs and the lock/edit behaviour tests.

## Not verified

I did not read the model client code that routes Settings choices to CBORG/Ollama, so the generate route's backend reuse is assumed. I did not measure whether `matkg_schema.yaml` fits a small Ollama model's context; the prompt builder will need a condensed form. I did not check that every KG node builder works without the base `Thing` slots, nor that `linkml_model` is present in the image (it is absent from the dev venv listing). I ran nothing against the cluster profile.

## Open questions for the owner

1. Self-contained schemas only (proposed), or allow `imports: matkg_schema` with an import map?
2. May `Thing` and `related_to` be mandatory in every generated schema?
3. New KG required on schema change (proposed), or is an in-place migration wanted?
4. Is the dependency-free validator enough, or should the `linkml` package be added for full meta-model checks?
5. Who may create schemas: any `library.extract` user, or an approval step like pending-review sets?
6. Size and repair limits (256 KiB, 2 repairs): acceptable?
