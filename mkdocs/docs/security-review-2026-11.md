# Security review of the 2026-11 additions (owner backlog, Tier 2 F)

Scope: the routes added between 2026-11-20 and 2026-11-26 (V89 to V101: schema authoring, generation, preview and cancel;
V95 KG download; V99 supporting-text job; V100 relation repair), the manual PDF flow (V78, V87, V88 and the Harvester
routes), schema text as untrusted input that reaches a prompt, the `F2W_LIBRARY_PROXY_PREFIX` setting, and everything that
writes under the workdir or the library. Method: reading the code paths from the route to the file system, and the tests
that pin them; no new tooling. Status per item: **ok** (nothing to change), **fixed** (changed in the same commit as this page),
**open** (a question for the owner).

## Routes

| Item | Finding | Status |
|---|---|---|
| Capability and profile | Every route is in `API_ROUTE_POLICIES` with `on_als=False` and a capability the route re-checks (`context` dependency plus `require(ctx, ...)` where the job kind needs more); the pins count 85 routes, 35 local-only. `library.author_schema` is in `LOCAL_ONLY_CAPABILITIES`, so no ALS principal holds it. | ok |
| Body limits | Schema text routes refuse bodies over 256 KiB + 4 KiB before parsing (`Content-Length`, then a counted stream); generate 16 KiB; cancel 4 KiB; the description 2,000 characters; names 80. Pydantic bodies use `extra="forbid"`. | ok |
| Ids | Schema names `^[a-z][a-z0-9_-]{0,63}$`, versions 1 to 999, `request_id` `^[A-Za-z0-9_-]{1,64}$`, bundle names `validate_name`, harvest run ids `job-` plus hex, candidate ids bare hex, paper ids 16 hex. A bad id is a 404 or 422, never a path. | ok |
| Paths | A user schema file is built from the validated name and version only; `path_from_relpath` refuses any other shape; a stored file that is a symlink is "not found". The preview opens a library paper only when it resolves inside the library root and is not a symlink. The download route streams a file named from the validated bundle name inside the release directory. The bundle listing skips symlinked directories. | ok |
| Cancel route | A cancel for an unknown id is remembered until that id's request runs or ends; the set is per process and bounded only by the number of ids a caller invents. A local operator could grow it without bound. | fixed: the set keeps at most 1,000 ids (oldest dropped) |

## Uploads (manual PDF flow)

The multipart routes read exactly one `file` field, cap it at the library's `max_upload_bytes` before reading the rest,
require the `%PDF-` signature, sanitise the file name on storage and file under the run's category by year. The batch route
takes one file per request (the browser sends at most 20). The matcher reads the PDF through the bounded probe; nothing is
stored before a strong match or an explicit confirmation. Undo deletes only a file whose sha256 is still the one filed.
**ok**.

## Untrusted text that reaches a prompt

- A user or model-written schema is loaded with `sanitize_descriptions=True`: every class and slot description becomes one
  line, control characters and bidi marks stripped, capped; the extraction prompt is bounded (6,000 / 12,000 characters).
- The generate prompt fences the description as data, removes the fence markers from it and says the rules do not change.
- The preview sends page text as data; the model's reply is parsed as JSON and capped at 64 KiB.
- The chat's alternative-terms helper sends the question and a sample of the KG's own node words; its reply is accepted only
  as a JSON list of strings, each at most 60 characters, at most five.
- The model's `aliases` and the value of an attribute relation were stored without a bound. | **fixed**: aliases are at most 80
  characters and 20 per term; an attribute value at most 500 characters; a table-row passage at most 600 characters.

## `F2W_LIBRARY_PROXY_PREFIX`

Validated at startup (https, a host, no user-info, no fragment, no whitespace or control characters, ends with `=` or `/`,
at most the length cap); a bad value refuses startup with the rule, never the value. It is only ever prefixed to the
`open_url` string the candidates route carries; the server never requests it. **ok**.

## Writes under the workdir and the library

| Writer | Where | How |
|---|---|---|
| User schema store | `<workdir>/schemas/<name>/<name>@vN.yaml` | directories 0o700, files 0o600, temp file then replace, symlinked directories refused |
| Bundles | `<workdir>/kgs/<name>/` | atomic JSON writes; `previous/` (0o700) holds one generation of `terms.json` and `kg.json`; `recovery_state.json` atomic, a symlink is ignored on read |
| Harvest | `<workdir>/library/harvest.sqlite`; category manifests under the library root | per-category file lock, temp file then replace |
| Preview | `<workdir>/tmp/schema-preview-*/schema.yaml` | temporary directory 0o700, removed after the run |
| Export | the release directory | the validated bundle name only |

**ok**.

## Secrets, URLs and logs

- `OPENALEX_API_KEY` travels as the `Authorization` header of the hardened getter only; the routes report `openalex_keyed`
  as a boolean. `TILED_API_KEY` is not touched by the new code.
- The Harvester logs hosts, rule names and short codes, never a URL or a query string; `JSONFetchError` messages are
  `blocked URL (reason)`, `network error (Type)`, `HTTP n`; a proxy refusal is named with the host only. The backfill logs
  codes. The publication records module logs nothing.
- Error messages of the schema routes name the rule broken, never the text; the generate route's typed errors carry no key or
  URL (pinned by `test_generate_route_typed_errors_have_no_key_or_url`).
- Traces: the new label `kg-alternatives` is in the closed set; the trace payload holds the label and sizes.

**ok**, with the one open point below.

## Open for the owner

- The chat's alternative-terms helper and the pending-download interpreter record a failed call through `note_error`, whose
  text is the exception's message. For an `httpx` transport error that message can name the provider base URL (no query
  string, no key). That is the existing behaviour of every helper; if the base URL itself must stay out of traces, the fix is
  one place (`note_error`), not these callers.
