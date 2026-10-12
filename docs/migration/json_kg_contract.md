# T23A: versioned JSON KG creation, import and edit contract

Gate H1 input. Baseline `222b1e7b130567dbf4a3242697ac2b465794a38d`. Written 2026-10-12.

This document freezes what `app/modules/json2kg.py` does today and proposes the snapshot, manifest, import and
edit-reconciliation contracts around it. It changes no application behavior.

Labels used throughout:

- **[OBSERVED]**: read in baseline source (file:line at `222b1e7`) or measured by running baseline code.
- **[PROPOSED]**: a contract for T23B/T08B/T26B to implement. Not yet code.
- **[INFERENCE]**: a conclusion drawn from observations, not directly verified.

Companion material:

- Compatibility fixtures and tests: `tests/contracts/json_kg/` (section 2.10).
- Inventory of the 47 tracked KG files: `docs/migration/data_releases/kg_inventory.json`.
- Git release and promotion contract, with JSON schemas: `docs/migration/data_releases/release_contract.md` (T26A).
- Requirements this answers: plan.md 4.11 and T23A; JSON_KG_PUBLICATION_SPEC.md 3-6; D21; crosswalk B24.

## 1. Artifact types

These are separate artifacts. None of them may stand in for another.

| Artifact | What it is | Where it lives today [OBSERVED] | Contract [PROPOSED] |
|---|---|---|---|
| Parsed paper package | Parser output for one source: text, sections, figures, crops | `workdir/pdfs`, parser caches, `storage/source_index/` (gitignored, kept out of the image by E4) | Private. Mounted. Never committed and never imported into Tiled. Not a KG snapshot (JSON_KG_PUBLICATION_SPEC 1). |
| Extracted-terms record (json2kg input) | `{"terms":[...], "code_snippets":[...], "metadata":{...}}` or a bare list | `storage/terminology/*.json` (58 tracked at baseline), `workdir/terms.json` (`coordinator.py:185`) | Private input. A snapshot manifest lists it by hash and reference only. |
| KG JSON (graph payload) | json2kg output: `things`, `associations`, optional `metadata` | `storage/kg/*.json`, `workdir/kg.json` (session copy), `workdir/uploads/*.json` | Frozen as `graph.json`. Bytes never change once frozen. |
| Snapshot manifest | Sidecar that identifies and binds a graph payload | None. 0 of 47 tracked files have one (inventory). | `manifest.json` next to `graph.json` (section 4). |
| Import plan | Dry-run of the operations for one target and scope | None | Per target. Bound to the graph hash, target, scope and base revision (section 5). |
| Import receipt | Durable record of the effects applied to one target | None. The closest is `storage/fixtures/rsoxs_snapshot_promote.json`, which records a build, not an import. | Private operational state, one per target (section 5). |
| Edit journal | Approved UI edits recorded against a base revision | None. Edits go straight to splash-links and the session `workdir/kg.json` (section 6.1). | Durable and scoped. Reconciled into a new snapshot (section 6). |
| Data-release descriptor | Git commit, paths and hashes selected for promotion | None | Created after the commit exists (release_contract.md). |

## 2. Frozen json2kg behavior [OBSERVED]

Source: `app/modules/json2kg.py` at `222b1e7`, sha256 `81dba522cff892cda7d5bf1e98a67b10cb7ccb990972d0b995c76b65e755527f`.
The working-tree copy on `forge_merge` is byte-identical.

### 2.1 Input

- `convert_terms_to_graph` (L457-482) and `main` (L505-533) both use `json.load`, then apply these rules:
  - `terms = data["terms"]` when `data` is a dict that contains `"terms"`. Otherwise `terms = data` (L471, L514).
    A top-level list is therefore accepted. A dict without `"terms"` is iterated as if it were the term list. That
    iterates the dict's keys (strings), raises `AttributeError`, and makes the CLI exit 1. Fixture:
    `error_no_terms_key`.
  - `code_snippets` comes from `data.get("code_snippets", [])` when `data` is a dict (L472, L515).
  - `default_id_prefix` is `metadata.id_prefix` when present and truthy, otherwise `"matkg"` (L474-476, L517-519).
- A term record is read through `term`/`name`, `id`, `id_prefix`, `category`, `raw_category`, `definition`, `pages`,
  `source_papers`, `context_snippets`, `formula`, `formula_validation`, `properties`, publication fields
  (`_PUB_FIELDS`, L26-38), `source_metadata`, `publications`, `relations`, and any extra key (section 2.4).
- A relation must be a dict. It is read through `related_id` (preferred) or `related_term`, `relation`, `evidence`
  (list or scalar), and `raw_predicate` or `raw_relation` (L376-414). A non-dict relation raises `AttributeError`.
  Fixture: `error_relation_not_object`.
- `build_graph(raw_terms, code_snippets=None, *, default_id_prefix="matkg", strict_snippets=True)` (L263-269) is the
  in-process API. `strict_snippets=False` is reachable only from callers: `tiled_sim.py:751` and
  `bl1101_ingest.py:2057-2062`.

### 2.2 Output shape and serialization

- `build_graph` returns `{"things": [...], "associations": [...]}` (L454). Callers add `metadata` only when the input
  metadata is a non-empty dict, and copy it verbatim (L478-479, L521-522).
- The serializer is `json.dumps(graph, indent=2, ensure_ascii=False)` written as UTF-8 with **no trailing newline**
  (L481, L523). Key order is insertion order, which makes the bytes deterministic for a given input file. Fixture
  outputs are byte-compared.
- Node order: accepted CodeSnippet nodes first, in snippet order. Then term nodes and relation-target stubs in
  first-seen order (L285-397). Edge order: term relations in term order, then `has_code_snippet` wiring (L416-452).
  No sorting happens inside json2kg. (`bl1101_ingest.records_to_graph` calls `sort_graph` after json2kg:
  `bl1101_ingest.py:1592-1598`, `:2063`.)
- Node key layouts are frozen in the test as `TERM_NODE_KEYS` (24 keys, L326-351), `STUB_NODE_KEYS` (12 keys,
  L384-397) and `SNIPPET_NODE_KEYS` (37 keys, L222-260). Snippet nodes have `type: "matkg:CodeSnippet"`. They have no
  `raw_category`, `institutions`, `journal`, `volume`, `issue`, `pages_range`, `abstract_text` or `keywords`.
- Edge keys are `subject`, `predicate`, `object`, `has_evidence`, plus `raw_predicate` only when the input supplied
  `raw_predicate` or `raw_relation` (L406-413).

### 2.3 Identifiers

- `make_id(term, prefix)` (L85-100):
  - If the value starts with a known CURIE prefix (`matkg:` or `beamline:`, L58, `split_curie` L76-82), that prefix
    is kept regardless of `prefix`. In the local part, `_` becomes `-`, and spaces and every character outside
    `[A-Za-z0-9-]` are removed.
  - Otherwise `<prefix>:` is prepended, spaces are removed, and every character outside `[A-Za-z0-9-]` is removed.
    `_` is removed, not converted.
  - Consequences frozen in `test_identifier_rules_are_frozen_explicitly` and `fixtures/expected/api_cases.expected.json`:
    - `"A B"`, `"AB"` and `"A.B"` all become `matkg:AB`. Distinct labels collide, and their nodes merge.
    - `"a_b c"` becomes `matkg:abc`, but `"matkg:Foo_Bar baz"` becomes `matkg:Foo-Barbaz`.
    - Non-ASCII letters are dropped: `"Polymère β-phase_x"` becomes `matkg:Polymre-phasex`, and `"α"` becomes
      `matkg:` (empty local part).
    - `tiled:Thing` is not a known CURIE and becomes `matkg:tiledThing`.
- Term node id: `make_id(term.id or name, prefix=term.id_prefix or default_id_prefix)` (L308-310). A record with
  neither `term` nor `name` gets `name="UNKNOWN"` and id `<prefix>:UNKNOWN`. All such records merge into one node.
- Relation target id: `make_id(related_id or related_term, prefix=<subject term's prefix>)` (L377-380). A CURIE target
  keeps its own prefix: `beamline:SynthMotor` can point to `matkg:SynthPolymerA`.
- CodeSnippet id (L204-219):
  - An explicit `id` is used with `make_id(id, snip.id_prefix or prefix)`.
  - Otherwise the id is
    `make_id("snippet_{function_name}_{source_paper}_p{page}_{md5(code)[:8]}")`, or
    `make_id("snippet_{source_paper}_p{page}_{md5[:8]}")` when there is no `function_name`. Underscores and dots are
    stripped (non-CURIE rule). The defaults are `source_paper="unknown"` and `page=0`. A present-but-null
    `source_paper` yields the literal `None` in the id. `code_snippet=None` hashes the empty string (`d41d8cd9`).
- IDs depend only on input values. They do not depend on time, randomness or hash seed. The CLI output is
  byte-identical under `PYTHONHASHSEED` 0, 1 and 4242 (`test_cli_output_is_independent_of_hash_seed`).

### 2.4 Node construction, field retention and merge

- New term node (L325-352):
  - `description` is `definition` or `"N/A"`.
  - List fields go through `ensure_list`.
  - `publications` comes from `normalize_publications` (L134-181), in this priority order:
    1. An explicit `publications` list. Non-dicts are skipped, empty values dropped, and the list is sorted by
       `source_paper`.
    2. `source_metadata`, one entry per source sorted by key, keeping only `_PUB_FIELDS`.
    3. Scalar publication fields, used only when there is at most one source paper. With two or more papers and
       neither of the above, `publications` is `[]`. Scalar metadata is not spread across papers.
- Passthrough (`copy_passthrough_slots`, L61-73): the overlay slots `importance`, `photon_energy_eV`,
  `absorption_edge`, `scattering_technique`, `polarization` and `technique_type`, and **any other non-empty input
  key**, are copied onto the node unless:
  - the node already has the key,
  - the key is input-only (`_TERM_INPUT_ONLY`, L48-57: `term`, `name`, `definition`, `relations`, `source_paper`,
    `paper_authors`, `id_prefix`, `related_id`), or
  - the key is a publication field.

  Unknown fields such as `confidence`, `evidence_ref` or `prov_metadata` on a term are therefore retained on the node.
- Category remaps (L315-322):
  - A term categorized `CodeSnippet` becomes `Unknown`, with `raw_category: "CodeSnippet"`.
  - `XRayScatteringAnalysis` becomes `ExperimentalTechnique`, with `raw_category` set.
  - Real CodeSnippet nodes come only from `code_snippets`.
- Merge when the id already exists (L353-373):
  - The category is upgraded only from `None`, `""` or `Unknown`. `raw_category` is updated with it.
  - The name is replaced only if it is empty, `UNKNOWN`, or a CURIE string.
  - The description is filled only if it is `None`, `""` or `"N/A"`.
  - `source_papers` is unioned in order.
  - Passthrough adds new keys but never overwrites existing ones.
  - **Not merged:** `pages`, `context_snippets`, `properties`, `formula`, `formula_validation`, publication fields
    and `publications`. The first occurrence wins.
- Stub targets carry only the 12 stub keys. A later term with that id upgrades the stub but does not add the
  term-node publication keys (`SynthDevice` in the fixture).

### 2.5 Edges and evidence

- `predicate = "rel:" + relation`, defaulting to `rel:RELATED_TO` (L399).
- The first `(subject, predicate, object)` wins. Later duplicates are dropped together with their evidence (L400-403).
- `has_evidence` is `"; ".join(ensure_list(evidence))`, or `null` when there is no evidence (L405-410).
- A relation with an empty target is skipped (L378-379).
- Corpus fact (inventory): `has_evidence` is null on **every** edge of all 47 tracked files. Evidence text actually
  lives in `node.context_snippets[] = {text, source_paper, page}`. That text is verbatim page text by construction:
  `extract_terms.py:845-859` takes the first `context_length=50` tokens (`:546`) of the sentence containing the term,
  or of the whole page when no sentence matches.

### 2.6 Code snippets and wiring

- Acceptance rules (`_accept_code_snippet`, L184-201):
  - Empty code is rejected.
  - `curated` snippets are always accepted.
  - Otherwise a snippet needs at least 150 characters and an anchor (`function_name`, or a `def`, `class` or
    `import` statement).
  - With `strict_snippets=True`, `()` and `[]` must also balance.
  - Rejected snippets are logged at DEBUG (L288-293), which is visible with `-v`.
- An identical snippet (same id) is added once (L299-300).
- Provenance fields are retained verbatim on the node (L247-259): `source_type`, `repo_url`, `repo_owner`,
  `repo_name`, `repo_default_branch`, `repo_commit_sha`, `source_file_path`, `source_file_url`,
  `source_start_line`, `source_end_line`, `repository_license`, `license_warning` and `source_score`.
  `domain_features` is normalized by L115-131: values are stringified and entries without a name or value dropped.
- Wiring (L416-452) runs for each non-snippet node, in insertion order, and for each of its `source_papers`:
  1. Link snippets on the same `(paper, page)`.
  2. Then link any snippet from that paper not yet wired by any node, through the `(paper, 0)` index.

  Paper-level fallback snippets therefore attach only to the **first** node from that paper in insertion order.
  Later nodes from the same paper get no fallback link. Wiring edges have `has_evidence: null`. Frozen by
  `test_code_snippet_nodes_and_wiring`.

### 2.7 Hazards that are part of the frozen behavior

These are recorded, not fixed. Changing any of them is a deliberate behavior change that needs its own migration
note and new fixtures.

1. **Input aliasing.** `ensure_list` returns the caller's list object (L112), so node list fields alias the input
   lists. On merge, `papers.append` (L369-372) mutates the first term's `source_papers` in the caller's data.
   Re-running `build_graph` on the same in-memory input therefore gives a different result. In the confirmed case,
   `publications` drops from `[{"source_paper": "p1.pdf"}]` to `[]`. Determinism holds only for freshly loaded input,
   which is always the case for the CLI and `convert_terms_to_graph`. Frozen by
   `test_observed_hazard_build_graph_aliases_and_mutates_input_lists`. [INFERENCE] In-process callers that reuse a
   terms object (`tiled_sim.py:960`, `bl1101_ingest.py:2057`) should be checked by T23B before wrapping.
2. **Label collisions and empty ids** (section 2.3). Distinct scientific terms can merge silently.
3. **First-occurrence-wins fields** (section 2.4): pages, snippets and properties from later duplicate records are lost.
4. **Order-dependent snippet wiring** (section 2.6).
5. **Non-atomic write.** `output_json.write_text` writes directly to the destination (L481, L523). A crash mid-write
   can leave a torn file. On parse or build failure nothing is written, because the exception is raised before the
   write. Fixtures `error_*` assert that no output exists afterwards. (`promote_rsoxs_snapshot` does write a temp file
   and `replace` it: `tiled_sim.py:980-996`.)
6. **Stale self-hash in promoted snapshots.** `promote_rsoxs_snapshot` computes `sha256_file(tmp)` (`tiled_sim.py:982`),
   then inserts `graph_snapshot` containing that hash and rewrites the file (`:994-996`). The embedded hash can never
   equal the final file's hash. The inventory confirms the mismatch for `matkg_rsoxs_v2.json` and the bl1101
   snapshots. The sidecar `storage/fixtures/rsoxs_snapshot_promote.json` records `7608e224…`, while the working-tree
   file hashes to `2e5b6d64…`. Section 4 forbids self-hashes for this reason.

### 2.8 CLI contract

The command is `python3 app/modules/json2kg.py [-h] [--verbose|-v] input_json output_json` (L485-502). Every case below
is recorded in `fixtures/expected/cli_behavior.json`.

| Case | Exit | Output file | Stream |
|---|---|---|---|
| Success | 0 | Written | stdout: `INFO: Wrote <N> nodes (<S> snippets) and <E> edges → <output>` (L524-530) |
| `-v` | 0 | Byte-identical to the non-verbose output | Adds `DEBUG: Skipping snippet …` lines |
| Missing input, invalid JSON, dict without `terms`, non-dict relation, any other exception | 1 | Not written | stdout: `ERROR: Failed: <exception text>` (L531-533). Nothing on stderr. |
| Missing arguments | 2 | Not written | stderr: argparse usage |
| `--help` | 0 | Not written | Help text on stdout |

Logging is configured with `basicConfig(stream=sys.stdout)` (L509). Logs go to **stdout**, not stderr.

### 2.9 Writers of KG JSON in the baseline

Only the first five rows use json2kg.

| Writer | Path pattern | Uses json2kg |
|---|---|---|
| `app/run_pipeline_cborg.py:105,145` | `storage/kg/matkg_<model>_<checkpoint>_<ts>.json` | `convert_terms_to_graph` |
| `app/modules/f2w_agent/kg_update.py:104-117` (`rebuild_kg`), `coordinator.py:186-196` | `workdir/kg.json` | `convert_terms_to_graph` |
| `app/modules/tiled_sim.py:941-1005` (`promote_rsoxs_snapshot`), `scripts/promote_rsoxs_snapshot.py` | `storage/kg/matkg_rsoxs_vN.json`. Refuses to overwrite (`:954`). | `build_graph` + metadata |
| `app/modules/tiled_sim.py:751` | Overlay graph | `build_graph(strict_snippets=False)` |
| `app/modules/bl1101_ingest.py:2056-2065` | `storage/kg/matkg_bl1101_vN.json` | `build_graph` + `sort_graph` |
| `scripts/ingest_rsoxs_v3.py:447-463` | `storage/kg/matkg_rsoxs_v3.json` | **No.** Different payload (`nodes`/`edges`, `label`/`entityType`/`papers`/`slots`, `prov_metadata`). |
| `f2w_agent/api.py:1204-1207, 2587, 3387-3393` | Session graph, splash sync, uploads | No. These rewrite JSON in the same `json.dumps` style. |

### 2.10 Compatibility fixtures

Layout of `tests/contracts/json_kg/`:

- `fixtures/inputs/*.json` are synthetic term inputs. They contain no paper text.
- `fixtures/expected/*.graph.json` are the exact bytes the baseline CLI wrote. The generator checks that the CLI
  bytes equal the `convert_terms_to_graph` bytes before saving.
- `fixtures/expected/cli_behavior.json` and `fixtures/expected/api_cases.expected.json` record CLI behavior and the
  `build_graph`/`make_id`/snippet-id tables.
- `fixtures/MANIFEST.json` holds sha256 values for every fixture file.
- `tools/generate_fixtures.py` regenerates the fixtures. Run it against the baseline export only.
- `tools/kg_inventory.py` produced the inventory.
- `test_json_kg_contract.py` holds the assertions.

Verified command (fresh copy of the clean export, with `tests/contracts/json_kg` and `docs/migration/data_releases`
copied in):

```bash
cd <copy-of-f2w-222b1e7>
env -i PATH=<venv>/bin:/usr/bin:/bin HOME=<tmp> TMPDIR=<tmp> LANG=en_US.UTF-8 \
  <venv>/bin/python -m pytest -q -p no:cacheprovider tests/contracts/json_kg
# 36 passed
```

The existing `tests/test_json2kg.py` and `tests/test_json2kg_extended.py` also pass in the same run (54 passed in
total with the contract suite). T23B keeps all of them green. A changed expected file means a changed contract. It is
not a fixture refresh.

## 3. What existing JSON consumers rely on [OBSERVED]

Any snapshot layout must keep these working unchanged (section 7):

- **Directory listing is flat.** `list_storage_kg_json_files` uses `storage/kg/*.json` (`f2w_agent/api.py:465-474`).
  It resolves symlinks before making the path relative, so a symlink would be listed under its target's path.
  Nested `storage/kg/<corpus>/<id>/graph.json` files are not listed.
- **Graph identity comes from the filename.** `graph_id_for_path` (`multi_kg.py:93-106`) maps:
  - `matkg_rsoxs_vN.json` to `rsoxs_vN`,
  - any `matkg_bl1101_vN.json` to `bl1101`,
  - the x-ray demo to `xray_demo`,
  - anything else to its stem without `matkg_`.

  A file named `graph.json` would get graph_id `graph`, and every snapshot would collide.
- **Defaults and "latest" are filename-based.** `default_json_graph_paths` (`multi_kg.py:169-218`) picks the highest
  `matkg_rsoxs_vN.json` and `matkg_bl1101_vN.json`. It falls back to `storage/kg/matkg_rsoxs_v1.json` and
  `matkg_bl1101_v1.json` (`:13-16`). The same convention appears in `scripts/start_agent_backend.sh:87-115` and
  `scripts/start_rsoxs_stack.sh:116-130`. Schema overlay selection is also filename-based
  (`multi_kg.py:131-146`).
- **Payload tolerance.** Viewer and subset code reads `things`/`nodes` and `associations`/`edges`
  (`api.py:1341-1342`). Uploaded graphs must have list `things` and `associations` (`api.py:3387-3393`).
- **Path guard.** A runtime JSON path must resolve under `storage/kg` or `workdir/uploads` (`api.py:3396-3410`).
  Nested paths are allowed.
- **The default graphs are not tracked.** `.gitignore:182-183` ignores `storage/kg/*.json` and `*.json.bak`. The 47
  tracked paths are grandfathered, and the defaults (`matkg_rsoxs_v*`, `matkg_bl1101_v*`) exist only in developer
  working trees. A clean checkout or CI image has none of the default graphs. [INFERENCE] The ignore pattern does not
  match nested `storage/kg/<corpus>/<id>/graph.json`.

## 4. Immutable snapshot and manifest [PROPOSED]

### 4.1 Snapshot

- A snapshot is the directory `storage/kg/<corpus>/<snapshot-id>/` containing:
  - `graph.json`: the json2kg payload, unchanged, serialized exactly as in section 2.2.
  - `manifest.json`: the sidecar.
  - Optionally `validation.json`: a sanitized result.
- `snapshot-id` is `v<N>` per corpus, continuing today's numbering so that `graph_id` stays `<corpus>_v<N>` (or
  `bl1101`).
- Frozen means:
  - The bytes never change.
  - A repair, rebuild, disclosure split or reconciled edit creates `v<N+1>` with `parent` set.
  - Writers create the directory atomically: write to a temp directory, then rename. They refuse an existing target,
    as `promote_rsoxs_snapshot` already does (`tiled_sim.py:954`).
- A mutable working file (`workdir/kg.json`, `matkg_<model>_<ts>.json`) is a draft. It is never a publication
  artifact.

### 4.2 Manifest

Schema: `docs/migration/data_releases/schemas/kg_snapshot_manifest.schema.json`. Example:
`docs/migration/data_releases/examples/manifest.example.json`. The schema is validated in the contract tests.

The manifest binds:

- `graph.sha256` and `size_bytes`, over the exact stored bytes,
- the payload format and serialization rule,
- counts,
- `compat_paths`, which must hash identically to the graph,
- `parent` (snapshot id, graph hash, optionally the parent's commit), and
- `created`: code commit, json2kg sha256, run id and profile.

It also records:

- `schema` (path, sha256, version and `import_fingerprint`),
- `extraction.revisions[]`: every contributing run and settings revision. A multi-run corpus lists them all; it is
  never assigned one model. Legacy runs whose provenance is lost carry `unknown: true`.
- `corpus_revision` (source count, plus a checksum over the sorted per-source sha256 values),
- `inputs[]` (terms snapshot and edit journal, each by hash and reference),
- `evidence` (policy version, checksum of private references, required private assets), and
- `validation` and `disclosure` (status and report hash).

Rules:

1. The graph payload is unchanged. New identity metadata goes in the manifest, never into `graph.json`, so old
   readers see no new fields. Existing `metadata` passthrough (L478-479) continues as-is.
2. Neither file contains its own hash or its containing Git commit. This avoids the defect in section 2.7.6 and the
   circularity GIT_KG_PROMOTION_SPEC 1 warns about. The schema rejects `data_commit`.
3. References are repository-relative paths or `private:` references. Absolute local paths and bearer URLs are
   rejected by schema pattern.
4. Hashes are computed over the bytes as stored. Nothing is re-serialized or normalized before hashing or at
   deployment.
5. Building and validating a snapshot needs no Tiled client, server, network or credentials (D21). json2kg imports
   only the standard library (L15-22).

## 5. JSON-to-Tiled import plan and receipt [PROPOSED]

The importer (T08B, `app/modules/graph/imports/tiled_json.py` per JSON_KG_PUBLICATION_SPEC 3) **consumes a frozen
snapshot only**. It performs no parsing, extraction, LLM calls, downloads, bibliographic enrichment or ID
re-derivation. A test asserts zero calls to those adapters.

### 5.1 Pre-flight (no target contact)

1. The path resolves inside `storage/kg/<corpus>/<id>/`.
2. `sha256(graph.json)` equals `manifest.graph.sha256`, and the manifest validates against the schema.
3. When imported from a release, the bytes also match the release descriptor (release_contract.md).
4. The schema sha256 and import fingerprint match the importer's supported set.
5. Disclosure is `pass` (or an owner waiver) for the target's projection. Under O-D1, stored snippets do not fail
   storage disclosure, but the target projection still applies T21A rules.
6. Required private evidence assets are present and match their hashes. If they are missing, the import is
   **blocked**; nothing is rebuilt.
7. Caller authority and target identity come from the server-side profile (D27/D30/D34), never from model arguments.

### 5.2 Plan

The plan records:

- input `graph_sha256` and `manifest_sha256`,
- `snapshot_id`, `corpus`, `graph_id`,
- target `{profile, tiled_instance, scope}`,
- `expected_base_revision`, meaning the target's currently active revision for this scope, or none,
- adapter name and version,
- the deterministic ID mapping: JSON id to remote key. JSON ids are retained, and remote ids are recorded separately.
- operation counts by kind: entity, edge, alias/revision, evidence, code-link, metadata,
- the projection policy hash (which fields are public or protected), and
- `plan_sha256`.

Approval binds `plan_sha256`. Any change to the file, schema, target or base revision invalidates it.

### 5.3 Apply

- Every operation is journaled before and after its effect.
- Completion is keyed by (snapshot, adapter version, target, scope), not by snapshot hash alone.
- Retry reconciles completed effects without duplicating assertions or links.
- Only Tiled 0.2.19 capabilities verified on the target are used. Multi-request transactions or native upsert are not
  assumed.

### 5.4 Verify and activate

- Read back nodes, edges, evidence and code links, including content fields, for the imported projection. Counts
  alone are not enough.
- Then write the durable receipt and activate the revision.
- On failure, the JSON is untouched and the import can be retried without inference.

### 5.5 Receipt

A receipt is private operational state, one per target. It contains:

- `receipt_id`, `plan_sha256`, `graph_sha256`, `manifest_sha256`, `snapshot_id`,
- `data_commit` when imported from a release,
- target and scope, and the adapter version,
- `executed_as` (the writer identity reference, never the credential),
- `requested_by`/`approved_by` per D34/D30 (`local-operator` or a verified Tiled admin subject),
- operation outcomes and the ID map hash,
- the verification result,
- the activated revision, and
- timestamps.

A receipt for one target never satisfies another. Local, staging and production each need their own plan, approval
and receipt.

## 6. Edits and reconciliation into a new JSON version

### 6.1 Today [OBSERVED]

- Node, relationship and snippet edits require splash graph mode. `update_graph_node` raises in JSON mode
  (`api.py:2978-2981`).
- An edit writes splash-links through GraphQL (`kg_update.splash_update_entity`) and rewrites the session graph file
  with `_save_session_graph` (`api.py:1204-1207`, `:3040`). The session graph is `workdir/kg.json`, copied from the
  configured graph once in splash mode (`coordinator.py:200-207`).
- Edits never write `storage/kg`.
- New snippet ids are minted as `matkg:snippet<slug><uuid5[:8]>` (`api.py:1184-1189`). This differs from json2kg's
  md5 scheme; both must be preserved.
- [INFERENCE] A later `rebuild_kg` from terms (`kg_update.py:104-117`) rebuilds `workdir/kg.json` from the terms
  file. Edits held only in that file and not in the terms input would be lost from the file, though not from
  splash-links. No journal exists today.

### 6.2 Contract [PROPOSED]

1. **Journal before acknowledging.** Each approved edit is appended durably to a scoped edit journal. An entry holds:
   - `edit_id`, `base_snapshot_id`, `base_graph_sha256`,
   - target and scope,
   - profile and authorization mode,
   - actor (`local-operator` or a verified admin subject),
   - the operation (`node_patch`, `relationship_add`, `relationship_remove`, `snippet_upsert`, `snippet_unlink`),
   - stable JSON ids,
   - the field-level `before` and `after` values, or their hashes for protected text, and
   - the acknowledgement state.

   The scoped Tiled effect may then be applied and acknowledged without rewriting the corpus.
2. **Reconcile into a new version.** Load the base `graph.json` bytes, replay the journal operations in order (a JSON
   patch, not a json2kg rebuild), and serialize with the section 2.2 rule. This yields a new snapshot `v<N+1>` with
   `parent.relation = "edit_reconciliation"` and `edit_reconciliation{journal_sha256, applied_edit_count}`.
3. **Edits survive rebuilds.** If the corpus is rebuilt from terms, the journal is replayed on the rebuilt graph. If
   a target id no longer exists after the rebuild, that is a recorded conflict, never a silent drop.
4. **Conflicts are explicit.** Re-importing a snapshot whose lineage does not include acknowledged edits for that
   target is refused, unless a reviewed conflict resolution is attached. Last-writer-wins never happens by accident.
5. **Cross-environment.** An edit becomes part of a release only after reconciliation, validation and commit, and
   promotion of that new version. A local approval never authorizes an ALS import.

## 7. Old JSON consumers keep working [PROPOSED]

- **Payload unchanged.** `graph.json` is json2kg output. No new top-level or node fields are introduced by
  versioning.
- **Flat compatibility copy.** Every snapshot also exists at today's flat name, for example
  `storage/kg/matkg_rsoxs_v4.json`. The copy is a byte-identical file (same sha256, so Git stores one blob). It is
  listed in `manifest.compat_paths` and checked by release validation. It is **not a symlink**, because
  `list_storage_kg_json_files` resolves symlinks (section 3), which would rename the graph to its target's path.
  With the copy in place, the following keep working with no code change:
  - `glob("*.json")`,
  - `graph_id_for_path`,
  - "highest vN" defaults,
  - schema-overlay lookup,
  - start scripts, and
  - `.env` graph settings.
- **Ignore rule (integrator action).** `.gitignore:182` ignores the flat names, so committing a compat copy needs
  `git add -f` or a negation rule. That is outside this task's write scope.
- **Legacy files stay where they are.** The 47 tracked flat files are not moved or rewritten. They can be referenced
  by a descriptor with role `legacy_flat_graph`, but stay unpromotable until they get a manifest (`parent.relation =
  "legacy_adoption"`, `extraction.revisions[].unknown = true` where needed) and pass disclosure review
  (release_contract.md section 4).
- **Different payload family.** `matkg_rsoxs_v3.json` (`nodes`/`edges`) is not json2kg output. The importer must
  either support it as a separately named format or require a json2kg-format successor. [Open question]

## 8. Open questions for the owner

1. **Verbatim context text: resolved by O-D1 (2026-10-12, `docs/adr/content-disclosure.md:136-137`).**
   - Stored KG JSON keeps its verbatim `context_snippets` in Git and in the image. This is an owner exception to D19
     for stored artifacts only.
   - Snippet volume is informational. The current totals are 17.9 M distinct characters across the tracked files,
     and up to 34 k characters from one paper in one file.
   - Importer projections and anything served to users still go through T21A.
   - Remaining question: whether a per-snapshot snippet-volume figure should go into `manifest.evidence`.
2. **Legacy files.** Should the 38 legacy core-10 files, 5 empty placeholders and the `.bak` be adopted, archived or
   left untracked going forward? They have no recoverable extraction provenance.
3. **Hazards.** Should any section 2.7 hazard (aliasing, label collisions, wiring order) be fixed in a later,
   explicitly versioned json2kg? Fixing one changes ids or outputs, so it would need migration receipts (B24).
4. **rsoxs_v3.** Is it a supported import format, or must it be regenerated through json2kg?
5. **Corpus names and the ignore rule.** Confirm the `<corpus>` names (`rsoxs`, `bl1101`, ...) and approve a
   `.gitignore` negation for compat copies.
