# T26A: Git JSON KG release and promotion contract

Gate H1 input. Baseline `222b1e7b130567dbf4a3242697ac2b465794a38d`. Written 2026-10-12. Implements D21/D31 together with
plan 4.14 and GIT_KG_PROMOTION_SPEC.md. It amends their image-exclusion clause per owner decision **E3**
(`docs/migration/reviews/H0/license_dependency_audit.md`).

Labels: **[OBSERVED]** means verified in baseline source or data. **[PROPOSED]** means a contract for T26B/T26C to
implement. **[INFERENCE]** means a reasoned conclusion that has not been verified.

Related documents and files:

- Payload and manifest semantics: `docs/migration/json_kg_contract.md`.
- Current-data evidence: `kg_inventory.json` (in this directory).
- Normative schemas: `schemas/kg_snapshot_manifest.schema.json` and `schemas/data_release_descriptor.schema.json`.
  Section 9 reproduces them. Validated examples are under `examples/`. Both schemas are checked by
  `tests/contracts/json_kg/test_json_kg_contract.py`.

## 1. Identities

Four distinct identities. None of them implies another.

| Identity | Example | Selects |
|---|---|---|
| Data commit + path + SHA-256 | `3333…/storage/kg/rsoxs/v4/graph.json` with sha256 `…` | **The KG bytes.** This is the only thing that selects promoted data. |
| Snapshot id | `rsoxs/v4` | Human and lineage label. Never sufficient on its own. |
| Image digest | `sha256:…` of `:als-prod` | Application code. Never selects data, even when data is baked in (section 7). |
| Import receipt | per-target `receipt_sha256` | Proof that one target holds a verified projection |

A mutable branch tip, a working-tree filename, an image tag or a snapshot id alone is not enough to select production
data (GIT_KG_PROMOTION_SPEC 3.4).

## 2. Repository layout [PROPOSED]

```text
storage/kg/<corpus>/<snapshot-id>/graph.json        # json2kg payload, frozen bytes
storage/kg/<corpus>/<snapshot-id>/manifest.json     # sidecar (schema in section 9.1)
storage/kg/<corpus>/<snapshot-id>/validation.json   # optional sanitized validation result
storage/kg/matkg_<corpus>_v<N>.json                 # flat compatibility copy, byte-identical to graph.json
```

- `<corpus>` matches `^[a-z0-9][a-z0-9_-]*$`, for example `rsoxs` or `bl1101`. `<snapshot-id>` is `v<N>`, continuing
  the current per-corpus numbering.
- **Compatibility [OBSERVED basis].** Current readers discover graphs with a flat `storage/kg/*.json` glob
  (`f2w_agent/api.py:465-474`). They derive `graph_id` from the filename (`multi_kg.py:93-106`) and pick "latest" by
  the `vN` filename (`multi_kg.py:169-218`, `scripts/start_agent_backend.sh:87-115`). The flat copy keeps all of these
  working without code changes.
- **Flat copy rules.**
  - It must hash identically to `graph.json`, so Git stores a single blob.
  - It must not be a symlink. The listing resolves symlinks (`api.py:470-474`), which would surface the graph as
    `.../graph.json` with graph_id `graph`.
  - Release validation fails if any `compat_paths` entry differs from `graph.json`.
- **Existing flat files stay as they are.** The 47 tracked baseline files and the untracked `matkg_rsoxs_v*`/
  `matkg_bl1101_v*` files are not moved or rewritten. A descriptor may name a legacy flat file with role
  `legacy_flat_graph` for audit or rollback reference. A legacy file can be promoted only after it is adopted as
  `storage/kg/<corpus>/v<N>/` with a manifest (`parent.relation: legacy_adoption`) and passes section 4.
- **Ignore rules [OBSERVED].** `.gitignore:182-183` ignores `storage/kg/*.json` and `*.json.bak`, so flat copies need
  `git add -f` or a reviewed negation rule. The nested layout is not matched by that pattern. Changing `.gitignore` is
  an integrator action outside this task.
- **No overwrite.** A frozen directory is never rewritten. A new record set, a repair, a disclosure split or a
  reconciled admin edit creates `v<N+1>` with `parent`. No LLM tool runs `git add`, `commit` or `push`. The operator
  release workflow owns Git writes.

## 3. Hashing and selection [PROPOSED]

- **What is hashed.** SHA-256 is computed over the exact stored bytes. No re-serialization, newline normalization or
  `.gitattributes` text conversion is allowed. KG paths are marked `-text` (binary-safe) by the integrator, so a
  checkout reproduces the committed bytes on any OS. [INFERENCE] Without this, `core.autocrlf` checkouts could change
  bytes.
- **Selection.** A selection is the tuple `(repository, data_commit, path, sha256, git_blob)` for each artifact. The
  checks are:
  - `git ls-tree <data_commit> <path>` must report `git_blob`.
  - `git cat-file -p <git_blob>` (equivalently `git show <commit>:<path>`) piped through `sha256sum` must equal
    `sha256`.
  - The manifest's `graph.sha256` must equal the graph artifact's `sha256`.
- **No circular identity.** No file contains its own hash or the commit that contains it. The data-release descriptor
  (section 9.2) is created **after** the commit exists. It lives in the release system or in a later commit.
- **No stale self-hash.** The baseline `promote_rsoxs_snapshot` embeds a self-hash that can never be correct
  (`tiled_sim.py:982-996`; inventory shows a mismatch for every file that carries one). The release process ignores
  `metadata.graph_snapshot.sha256` and uses only externally computed hashes. [Open question: whether T23B stops
  writing that field. Removing it changes the payload, so it would be a new version.]

## 4. Commit-ready disclosure check [PROPOSED rules, OBSERVED current results]

Run on the candidate files before `git add`, and again in CI on the committed blob. The output is a report whose
sha256 goes into `manifest.disclosure` and into the descriptor.

| Rule | Check | Failure mode |
|---|---|---|
| R1 path allowlist | Only `storage/kg/<corpus>/v<N>/{graph,manifest,validation}.json` and the flat compat copy | Reject PDFs, images and crops, `*.db`/`*.sqlite`, parsed packages, `storage/source_index/`, `papers/`, `.env*`, job, trace or checkpoint stores |
| R2 schema and hash | Manifest validates. Graph hash and compat copies match. Refs are repo-relative or `private:`. | Reject |
| R3 secrets | Token regexes (`sk-…`, `AKIA…`, `ghp_…`, bearer tokens, `api_key=`, `password=`) plus a tuned entropy rule over every string (D3.22: tokens of 40+ characters that are high-entropy and shaped like random base62/base64; slug-shaped ids, UUIDs and long hex runs do not match) | Reject, unless an allowlist entry, keyed by sha256 of (path, node id, field, value), was reviewed |
| R4 local paths | Absolute paths (`/Users/`, `/home/`, `/private/`, `/tmp/`, `/global/`, `/pscratch/`, drive letters, `file://`) in any string | Reject, unless allowlisted as above (for example a reviewed path inside retained public GitHub code) |
| R5 bearer URLs | Signed or tokenized URLs (`X-Amz-Signature`, `token=`, `sig=`) | Reject |
| R6 evidence-snippet volume | Per value and per source paper: lengths of `context_snippets[].text`, `has_evidence`, `abstract_text`, long `description` and `feature_source_text` values | **Informational only (owner decision O-D1).** Recorded in the report and the manifest, never a blocker on its own. Never silently truncate. |
| R7 no raw full-body dumps | A whole parsed paper body, section or page stored as a field (as opposed to bounded per-term evidence snippets); node categories that carry raw document bodies (for example `DocumentChunk`) are flagged | Reject full parsed paper bodies. A flagged category needs review |

**Owner decision O-D1 (2026-10-12, `docs/adr/content-disclosure.md:136-137`).** The owner keeps the KG JSON files in
`storage/kg`, verbatim context snippets included, in the Git repository and in the Docker image. This is an owner
exception to D19 that covers **stored KG artifacts only**. The consequences are:

- A KG is never rejected before commit-ready solely because it contains verbatim evidence snippets. R6 records the
  volume as information.
- R1, R3, R4, R5 and R7 still forbid raw PDFs, full parsed paper bodies, crops, credentials, local paths and
  operational databases, in commits and in images.
- Anything returned to users, whether through routes, SSE, traces or exports, still goes through the T21A disclosure
  projection. The exception is about storage, not serving. Importer projections to Tiled are governed by T21A in the
  same way (json_kg_contract section 5.1 step 5).

An earlier draft of this section made R6 a blocker against owner limits Q and P. That draft is superseded.

### Current results [OBSERVED from kg_inventory.json]

**The 47 tracked files:**

- R1: `view_kg.html` is outside the allowlist. So is `matkg_xray_papers_cborg_chat.json.bak`, as a backup name.
- R2: all 47 fail, because none has a manifest.
- R3, R4 and R5: 0 hits.
- R6 (informational, not a failure, per O-D1): 41 files carry verbatim `context_snippets[].text`, 127,236 values in
  total. The extractor copies page text (`extract_terms.py:845-859`).
  - Every one of the 41 has values of at least 200 characters. 38 have values of at least 500 characters, and 12
    have values of at least 1,000 characters. The longest value is 1,890 characters.
  - In 28 files, one source paper contributes at least 10,000 distinct characters. In 18 files it contributes at
    least 20,000. The largest is 34,127 characters (`matkg_qwen3_235b_257papers.json`).
  - These snippets are why serving them must go through the T21A projection.
  - `has_evidence` is null on every edge, and `abstract_text` is null everywhere.
- R7: no full parsed paper bodies were detected, and none of the tracked files has a `DocumentChunk` category.
  [INFERENCE] This rests on per-value snippet lengths of at most 1,890 characters with no section- or page-length
  fields. The check is a heuristic.
- 5 files are empty placeholders: an identical 40-byte blob containing no data.

**The untracked working-tree snapshots** (not in the 47, but they are the default graphs):

- R2: all 12 lack manifests. The embedded `graph_snapshot.sha256` is wrong wherever it is present.
- R3: `matkg_bl1101_v1`–`v9` each have 1 `api_key` literal inside a GitHub `code_snippet`. It was classified as
  placeholder-like and the value was not recorded. R3 still blocks until it is allowlisted.
- R4: the same files each have 2 `/tmp/` strings in `code_snippet`.
- R7 (flag for review): `bl1101_v7`–`v9` have 9 `DocumentChunk` descriptions of at least 500 characters. These are
  ops-document chunks, not paper bodies. Whether they count as full parsed bodies is an open question (section 10).
- R6 (informational): `rsoxs_v1` and `v2` have 71 and 86 context texts, with a maximum of 1,009 characters.

**Repository visibility.** The repository is public (H0 audit E1). Under O-D1 the stored snippets stay in the
repository and in images. Bytes already on `main`/`222b1e7` remain public whatever happens later. Serving them is
still governed by T21A.

## 5. Staging validation and binary-report binding [PROPOSED]

1. **Candidate.** Build the candidate with the preserved pipeline. Tiled is not required. Freeze the bytes and the
   manifest, and run R1–R7.
2. **Commit and record.** Commit through the reviewed branch workflow. Then create a descriptor with
   `state: candidate`, recording `data_commit` and each artifact's `git_blob` and `sha256`. Re-hash the committed
   blob. Any merge, rebase or manual edit that changes the bytes produces a new hash and requires new validation.
3. **Staging import.** On staging, export the selected artifacts from `data_commit` to the staging mount and verify
   the hashes. Run the binary suite and import through the JSON-to-Tiled adapter.
4. **Binary report.** The report records:
   - `graph_sha256`, `manifest_sha256` and `data_commit`,
   - the image digest and suite id,
   - results, and the report's own `sha256` (the report is a separate file).

   The descriptor copies these values under `staging.binary_report`, and they must match the artifacts.
5. **State change.** A report bound to any other hash does not count. The descriptor moves to `staging_validated`
   only when `passed: true` and every bound hash matches.

## 6. Production promotion [PROPOSED]

1. **Plan.** Create a production-specific plan covering:
   - the descriptor (`release_id`, `data_commit`, paths, hashes),
   - the production target `{profile: als-prod, tiled_instance, scope}`,
   - `expected_base_revision`, and
   - the importer version.
2. **Approval.** Approval comes from a verified production Tiled admin (D27/D30) and binds `plan_sha256`. A staging
   approval, receipt or role is not production authorization.
3. **Transfer.** Export identical bytes from `data_commit` (`git archive` or `git show <commit>:<path>`) to the
   production KG mount. Verify sha256 and blob id, then verify schema and importer compatibility and the presence of
   required private evidence assets, by hash. Missing assets block the promotion: nothing is rebuilt, and nothing is
   added to Git to make it pass.
4. **No re-extraction.** The transfer and import run no parsing, extraction, model calls, canonicalization, ID
   re-resolution or schema migration.
5. **Import and record.** Import with the production technical writer. Verify the readback, record a **production**
   receipt, and activate. The descriptor moves to `production_imported`, with the receipt referenced by hash.

## 7. Image contents: owner exception E3 [OBSERVED decision, PROPOSED consequence]

- The owner decided (E3, 2026-10-09) that **`storage/`, including `storage/kg`, may be baked into images.** O-D1
  (2026-10-12) confirms that KG JSON goes into the image together with its verbatim snippets. This
  supersedes the "exclude storage/kg from every image layer" clauses in plan 4.14, GIT_KG_PROMOTION_SPEC 4 and
  JSON_KG_PUBLICATION_SPEC (v6/v7 additions) for KG JSON. Those companion texts need an errata (integrator action).
  Image exclusion of KG JSON is **not** a release requirement.
- These stay excluded:
  - The Tiled catalog DB, `**/tiled_catalog*.db*`, except in a future `:local` image (`.dockerignore`, commit
    `bfbec85`).
  - `storage/source_index/`, which holds paper-text chunks (E4, `.dockerignore`).
  - `papers/`, `.env*` and the other existing exclusions.
- **Baked data never selects data.** A KG file present in an image is a convenience copy. Production import still
  selects `(data_commit, path, sha256)` from the descriptor and verifies bytes before activating. An image-baked file
  may be used as the byte source only if its sha256 equals the descriptor's. Otherwise the import fails. An image
  built from a different commit can never silently supply data.
- [OBSERVED] `.dockerignore` does not exclude `storage/kg/*.json`. An image built from a developer working tree would
  bake the gitignored untracked snapshots (section 4 lists their R3/R4/R7 hits). CI builds from a clean checkout would
  not. [PROPOSED] Images are built only from clean checkouts, or the build context excludes untracked KG files.
- Baking data into an image does not authorize publication. Image rollout and data promotion stay separate operations.

## 8. Per-target receipts and rollback [PROPOSED]

- **Receipts.** Each target has its own plan, approval and receipt: local, als-dev staging, als-prod and disposable
  rehearsal targets. Completion is keyed by snapshot, adapter version, target and scope. Receipts are private
  operational state. Descriptors reference them by `receipt_sha256` and never embed credentials or tokens.
- **Rollback.** Select an earlier approved descriptor (commit, path, hash), verify the bytes again, and create a new
  target-specific reactivation or import plan with its own approval and receipt. Then record a new descriptor with
  `rollback_to: <earlier release_id>`. The current one becomes `rolled_back`.
  - Git history is not rewritten, and later candidates and audit records are not deleted.
  - Acknowledged edits made after the rolled-back version are a conflict to resolve explicitly (json_kg_contract
    section 6.2). They are never erased silently.
- **Independence.** Code rollback (image digest) and data rollback (descriptor) are chosen independently, subject to
  the `compatibility` block.

## 9. Schemas

### 9.1 KG snapshot manifest (`schemas/kg_snapshot_manifest.schema.json`)

See the schema file (normative). Summary of required fields:

- `manifest_version`, `artifact_type`, `corpus`, `snapshot_id`, `graph_id`
- `graph`: path, sha256, size, format, serialization and counts
- `parent`, `created` (including the code commit and the json2kg sha256), `schema`
- `extraction.revisions[]`, `corpus_revision`, `inputs[]`
- `validation`, `disclosure`

Optional fields are `compat_paths`, `evidence` and `edit_reconciliation`. `additionalProperties: false` rejects
`data_commit` and self-hashes. References must be repo-relative or `private:`.

### 9.2 Data-release descriptor (`schemas/data_release_descriptor.schema.json`)

See the schema file (normative). Summary of required fields:

- `descriptor_version`, `release_id`, `repository`, `data_commit`, `corpus`, `snapshot_id`, `graph_id`
- `artifacts[]`: role, path, sha256, git_blob
- `compatibility`: schema sha256, importer, and the code commits and image digests recorded for audit
- `disclosure`, `state`

Optional fields are `staging.binary_report`, `staging.receipts[]`, `production.plan_sha256`/`approvals[]`/
`receipts[]`, `supersedes` and `rollback_to`.

The full schema texts follow, copied verbatim from the files at time of writing.

#### Manifest schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "f2w:schema:kg_snapshot_manifest:1",
  "title": "F2W KG snapshot manifest (PROPOSED, T23A/T26A)",
  "description": "Sidecar manifest.json committed next to an immutable graph.json. The graph payload is unchanged json2kg output. The manifest never records the Git commit that contains it, nor its own hash.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "manifest_version", "artifact_type", "corpus", "snapshot_id", "graph_id",
    "graph", "parent", "created", "schema", "extraction", "corpus_revision",
    "inputs", "validation", "disclosure"
  ],
  "$defs": {
    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "git_sha": {"type": "string", "pattern": "^[0-9a-f]{40}([0-9a-f]{24})?$"},
    "repo_path": {
      "description": "Repository-relative POSIX path. No absolute paths, no '..', no backslashes.",
      "type": "string",
      "pattern": "^(?!/)(?!.*\\.\\.)(?!.*\\\\)[A-Za-z0-9._/-]+$"
    },
    "private_ref": {
      "description": "Opaque protected reference to private material (never a bearer URL or a local filesystem path).",
      "type": "string",
      "pattern": "^private:[A-Za-z0-9._:/-]+$"
    },
    "ref": {"oneOf": [{"$ref": "#/$defs/repo_path"}, {"$ref": "#/$defs/private_ref"}]}
  },
  "properties": {
    "manifest_version": {"const": "1"},
    "artifact_type": {"const": "f2w.kg_snapshot"},
    "corpus": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]*$"},
    "snapshot_id": {"type": "string", "pattern": "^v[1-9][0-9]*$"},
    "graph_id": {
      "description": "Identity existing readers derive from the compatibility filename (multi_kg.graph_id_for_path), e.g. rsoxs_v4 or bl1101.",
      "type": "string", "minLength": 1
    },
    "graph": {
      "type": "object",
      "additionalProperties": false,
      "required": ["path", "sha256", "size_bytes", "format", "serialization", "node_count", "edge_count"],
      "properties": {
        "path": {"const": "graph.json"},
        "sha256": {"$ref": "#/$defs/sha256"},
        "size_bytes": {"type": "integer", "minimum": 1},
        "format": {"enum": ["f2w.json2kg.things_associations.v1"]},
        "serialization": {"const": "json.dumps(indent=2, ensure_ascii=False); utf-8; no trailing newline"},
        "node_count": {"type": "integer", "minimum": 0},
        "edge_count": {"type": "integer", "minimum": 0},
        "codesnippet_count": {"type": "integer", "minimum": 0}
      }
    },
    "compat_paths": {
      "description": "Flat legacy-reader copies (e.g. storage/kg/matkg_rsoxs_v4.json). Each must be byte-identical to graph.json (same sha256, same Git blob). Never symlinks.",
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["path", "sha256"],
        "properties": {"path": {"$ref": "#/$defs/repo_path"}, "sha256": {"$ref": "#/$defs/sha256"}}
      }
    },
    "parent": {
      "oneOf": [
        {"type": "null"},
        {
          "type": "object",
          "additionalProperties": false,
          "required": ["snapshot_id", "graph_sha256", "relation"],
          "properties": {
            "snapshot_id": {"type": "string"},
            "graph_sha256": {"$ref": "#/$defs/sha256"},
            "data_commit": {"$ref": "#/$defs/git_sha", "description": "Commit of the PARENT (already exists; not circular)."},
            "relation": {"enum": ["rebuild", "edit_reconciliation", "repair", "disclosure_split", "legacy_adoption"]}
          }
        }
      ]
    },
    "created": {
      "type": "object",
      "additionalProperties": false,
      "required": ["at", "tool", "code_commit", "json2kg_sha256"],
      "properties": {
        "at": {"type": "string", "format": "date-time"},
        "tool": {"type": "string"},
        "code_commit": {"$ref": "#/$defs/git_sha", "description": "Code revision that built the graph (an existing commit)."},
        "json2kg_sha256": {"$ref": "#/$defs/sha256"},
        "run_id": {"type": "string"},
        "profile": {"enum": ["local", "als-dev", "als-prod", "ci"]}
      }
    },
    "schema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["name", "path", "sha256"],
      "properties": {
        "name": {"type": "string"},
        "path": {"$ref": "#/$defs/repo_path"},
        "sha256": {"$ref": "#/$defs/sha256"},
        "version": {"type": "string"},
        "import_fingerprint": {"$ref": "#/$defs/sha256", "description": "Hash of the schema-plus-mapping used by the importer (T22B)."}
      }
    },
    "extraction": {
      "type": "object",
      "additionalProperties": false,
      "required": ["revisions"],
      "properties": {
        "revisions": {
          "description": "Every contributing extraction run/settings revision. A multi-run corpus lists several; never one invented model.",
          "type": "array",
          "minItems": 1,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["extractor", "version"],
            "properties": {
              "extractor": {"type": "string"},
              "version": {"type": "string"},
              "settings_revision": {"type": "string"},
              "model": {"type": ["string", "null"]},
              "run_ids": {"type": "array", "items": {"type": "string"}},
              "unknown": {"type": "boolean", "description": "true when provenance of a legacy run cannot be recovered"}
            }
          }
        }
      }
    },
    "corpus_revision": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "source_count", "sources_checksum"],
      "properties": {
        "id": {"type": "string"},
        "source_count": {"type": "integer", "minimum": 0},
        "sources_checksum": {"$ref": "#/$defs/sha256", "description": "sha256 over the sorted newline-joined list of per-source sha256 values."},
        "source_list_ref": {"$ref": "#/$defs/ref"}
      }
    },
    "inputs": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["role", "sha256", "ref"],
        "properties": {
          "role": {"enum": ["terms_snapshot", "code_snippets", "edit_journal", "seed_graph", "mapping_file"]},
          "sha256": {"$ref": "#/$defs/sha256"},
          "ref": {"$ref": "#/$defs/ref"}
        }
      }
    },
    "evidence": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "policy_version": {"type": "string"},
        "private_refs_checksum": {"$ref": "#/$defs/sha256"},
        "required_private_assets": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["ref", "sha256"],
            "properties": {"ref": {"$ref": "#/$defs/private_ref"}, "sha256": {"$ref": "#/$defs/sha256"}}
          }
        }
      }
    },
    "validation": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "report_sha256"],
      "properties": {
        "status": {"enum": ["pass", "fail"]},
        "report_sha256": {"$ref": "#/$defs/sha256"},
        "report_ref": {"$ref": "#/$defs/ref"},
        "suite": {"type": "string"}
      }
    },
    "disclosure": {
      "type": "object",
      "additionalProperties": false,
      "required": ["check_version", "status", "report_sha256"],
      "properties": {
        "check_version": {"type": "string"},
        "status": {"enum": ["pass", "fail", "owner_waiver"]},
        "report_sha256": {"$ref": "#/$defs/sha256"},
        "waiver_ref": {"type": "string"}
      }
    },
    "edit_reconciliation": {
      "type": "object",
      "additionalProperties": false,
      "required": ["journal_sha256", "base_snapshot_id", "applied_edit_count"],
      "properties": {
        "journal_ref": {"$ref": "#/$defs/ref"},
        "journal_sha256": {"$ref": "#/$defs/sha256"},
        "base_snapshot_id": {"type": "string"},
        "applied_edit_count": {"type": "integer", "minimum": 0},
        "conflicts_resolved": {"type": "integer", "minimum": 0}
      }
    }
  }
}
```

#### Descriptor schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "f2w:schema:data_release_descriptor:1",
  "title": "F2W KG data-release descriptor (PROPOSED, T26A)",
  "description": "Release record created AFTER the data commit exists. Selects exact Git commit + repository-relative paths + SHA-256 for promotion. It is never stored inside the commit it names; it may be stored in the release system or in a LATER commit. Receipts and approvals are referenced by hash/ref, never embedded with credentials.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "descriptor_version", "release_id", "repository", "data_commit", "corpus",
    "snapshot_id", "graph_id", "artifacts", "compatibility", "disclosure", "state"
  ],
  "$defs": {
    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "git_sha": {"type": "string", "pattern": "^[0-9a-f]{40}([0-9a-f]{24})?$"},
    "repo_path": {"type": "string", "pattern": "^(?!/)(?!.*\\.\\.)(?!.*\\\\)[A-Za-z0-9._/-]+$"},
    "target": {
      "type": "object",
      "additionalProperties": false,
      "required": ["profile", "tiled_instance"],
      "properties": {
        "profile": {"enum": ["local", "als-dev", "als-prod", "disposable"]},
        "tiled_instance": {"type": "string", "description": "Canonical target identity, e.g. https://tiled-staging.als.lbl.gov/"},
        "scope": {"type": "string", "description": "Owner/corpus scope inside the target"}
      }
    },
    "receipt_ref": {
      "type": "object",
      "additionalProperties": false,
      "required": ["target", "receipt_sha256", "graph_sha256"],
      "properties": {
        "target": {"$ref": "#/$defs/target"},
        "receipt_sha256": {"$ref": "#/$defs/sha256"},
        "receipt_ref": {"type": "string"},
        "graph_sha256": {"$ref": "#/$defs/sha256"},
        "adapter_version": {"type": "string"},
        "completed_at": {"type": "string", "format": "date-time"}
      }
    },
    "approval_ref": {
      "type": "object",
      "additionalProperties": false,
      "required": ["target", "approval_sha256", "approved_by_kind"],
      "properties": {
        "target": {"$ref": "#/$defs/target"},
        "approval_sha256": {"$ref": "#/$defs/sha256"},
        "approved_by_kind": {"enum": ["local-operator", "tiled-admin"]},
        "subject": {"type": "string", "description": "Verified subject for ALS approvals; 'local-operator' for :local"},
        "plan_sha256": {"$ref": "#/$defs/sha256"},
        "expected_base_revision": {"type": ["string", "null"]}
      }
    }
  },
  "properties": {
    "descriptor_version": {"const": "1"},
    "release_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_.-]*$"},
    "repository": {"type": "string", "description": "Canonical repository identity, e.g. github.com/<org>/<repo>"},
    "data_commit": {"$ref": "#/$defs/git_sha"},
    "corpus": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]*$"},
    "snapshot_id": {"type": "string"},
    "graph_id": {"type": "string"},
    "artifacts": {
      "type": "array",
      "minItems": 2,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["role", "path", "sha256", "git_blob"],
        "properties": {
          "role": {"enum": ["graph", "manifest", "validation", "compat_copy", "legacy_flat_graph"]},
          "path": {"$ref": "#/$defs/repo_path"},
          "sha256": {"$ref": "#/$defs/sha256"},
          "git_blob": {"$ref": "#/$defs/git_sha", "description": "Blob id from `git ls-tree <data_commit> <path>`"},
          "size_bytes": {"type": "integer", "minimum": 0}
        }
      }
    },
    "compatibility": {
      "type": "object",
      "additionalProperties": false,
      "required": ["schema_sha256", "importer"],
      "properties": {
        "schema_sha256": {"$ref": "#/$defs/sha256"},
        "import_fingerprint": {"$ref": "#/$defs/sha256"},
        "importer": {
          "type": "object",
          "additionalProperties": false,
          "required": ["name", "version"],
          "properties": {"name": {"type": "string"}, "version": {"type": "string"}}
        },
        "code_commits": {"type": "array", "items": {"$ref": "#/$defs/git_sha"}},
        "image_digests": {
          "type": "array",
          "items": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
          "description": "Images validated with this data. Recorded for audit; image identity never selects data."
        }
      }
    },
    "disclosure": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "report_sha256", "check_version"],
      "properties": {
        "status": {"enum": ["pass", "fail", "owner_waiver"]},
        "report_sha256": {"$ref": "#/$defs/sha256"},
        "check_version": {"type": "string"},
        "waiver_ref": {"type": "string"}
      }
    },
    "staging": {
      "type": "object",
      "additionalProperties": false,
      "required": ["binary_report"],
      "properties": {
        "binary_report": {
          "type": "object",
          "additionalProperties": false,
          "required": ["sha256", "suite", "passed", "graph_sha256", "manifest_sha256", "data_commit"],
          "properties": {
            "sha256": {"$ref": "#/$defs/sha256"},
            "ref": {"type": "string"},
            "suite": {"type": "string"},
            "passed": {"type": "boolean"},
            "graph_sha256": {"$ref": "#/$defs/sha256"},
            "manifest_sha256": {"$ref": "#/$defs/sha256"},
            "data_commit": {"$ref": "#/$defs/git_sha"},
            "image_digest": {"type": "string"}
          }
        },
        "receipts": {"type": "array", "items": {"$ref": "#/$defs/receipt_ref"}}
      }
    },
    "production": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "plan_sha256": {"$ref": "#/$defs/sha256"},
        "approvals": {"type": "array", "items": {"$ref": "#/$defs/approval_ref"}},
        "receipts": {"type": "array", "items": {"$ref": "#/$defs/receipt_ref"}}
      }
    },
    "state": {
      "enum": ["candidate", "staging_validated", "production_approved", "production_imported", "rolled_back", "superseded", "rejected"]
    },
    "supersedes": {"type": ["string", "null"], "description": "release_id this release replaces"},
    "rollback_to": {"type": ["string", "null"], "description": "For a rollback release: the earlier release_id being reactivated"}
  }
}
```

## 10. Open questions for the owner

Owner decision O-R1 (2026-10-30, `docs/migration/reviews/H1/owner_decisions.md` batch 20) answered the T26A questions
as follows.

1. (Resolved by O-D1: snippets may be stored.) Do `DocumentChunk` ops-document descriptions in `bl1101_v7`–`v9`
   count as "full parsed bodies" under R7, or as permitted stored evidence? Still open for `bl1101_v7`, which the
   R7 check flags for review (below).
2. **Q2: adopt.** The existing flat files are adopted: each becomes `storage/kg/<corpus>/v<N>/` with
   `parent.relation: legacy_adoption`, after passing section 4. Done by `scripts/adopt_legacy_kg.py` (below).
3. **Q3: approved and applied** through `.gitattributes` (`storage/kg/** -text`, `storage/kg/*.json -text`,
   `storage/terminology/*.json -text`, `docs/migration/data_releases/releases/** -text`).
4. **Q4: applied** (owner decision O-R1).
5. Errata to plan 4.14, GIT_KG_PROMOTION_SPEC 4 and JSON_KG_PUBLICATION_SPEC for E3. Still an integrator action.
6. Where descriptors live: the release system, or a later commit under `docs/migration/data_releases/releases/`.
   **Still pending.** `scripts/adopt_legacy_kg.py descriptor` only prints to stdout or to a path outside the
   repository, so the answer decides where the output goes and nothing else. No real descriptor is committed.

### Adoption results [OBSERVED, 2026-10-30]

Tool: `app/modules/graph/adoption.py`, CLI `scripts/adopt_legacy_kg.py`. Run on `forge_merge` with
`--code-commit a1d65702679e05eb2b45aa05b95de78b72948976` (the adopting revision: an existing commit, the same rule as
`create_snapshot`). A file is adopted only if all of these pass; nothing is allowlisted and nothing is rewritten.

- R1: a regular (non-symlink) `matkg_*.json` file; nested paths are on the allowlist.
- R2: `things`/`associations` family, bytes equal `json.dumps(indent=2, ensure_ascii=False)` with no trailing
  newline (what the manifest's `serialization` asserts), a non-empty graph, and a passing schema validation.
- R3, R4, R5, R7 via `releases.check_disclosure`. R6 stays informational (O-D1).

Adopted files get `graph.json` (a byte copy: same sha256, not a symlink, flat file untouched), `manifest.json`
(`legacy_adoption`, one `unknown` extraction revision, no inputs, empty source set, schema `matkg_schema`, no invented
hash) and `validation.json`. A name that is not `matkg_<corpus>_v<N>.json` becomes corpus `<name stem>` (the stem
without `matkg_`, lower-cased, other characters mapped to `-`), snapshot `v1`. **D3.24 (owner, "Extend flat allowlist to
legacy names"):** the R1 flat allowlist now admits `storage/kg/matkg_<name>.json` for any plain name (letters, digits,
`_`, `-`), so such a file also gets a `compat_paths` entry and its flat path keeps working; a descriptor lists it as
`compat_copy` like any other. The six files adopted in the first run were frozen before this rule; they keep the
manifests they have (flat file named in a descriptor with role `legacy_flat_graph`) until a new snapshot replaces
them. Nested `graph.json` copies are still not shipped in images (`.dockerignore`).

**Adopted (6):**

| Flat file | Nested release | Nodes / edges |
|---|---|---|
| `matkg_deepseek-r1_70b_100_20250929_004942.json` | `deepseek-r1_70b_100_20250929_004942/v1` | 725 / 533 |
| `matkg_deepseek-r1_70b_25_20250925_103657.json` | `deepseek-r1_70b_25_20250925_103657/v1` | 247 / 165 |
| `matkg_deepseek-r1_70b_50_20250926_144206.json` | `deepseek-r1_70b_50_20250926_144206/v1` | 490 / 344 |
| `matkg_deepseek-r1_70b_75_20250927_214641.json` | `deepseek-r1_70b_75_20250927_214641/v1` | 639 / 467 |
| `matkg_google_gemini-flash-lite_25_20251008_185312.json` | `google_gemini-flash-lite_25_20251008_185312/v1` | 1915 / 227 |
| `matkg_gpt-oss_20b_25_20250930_172105.json` | `gpt-oss_20b_25_20250930_172105/v1` | 992 / 1003 |

**Skipped (53 of 59 top-level entries), by exact rule:**

| Rule | Count | Files |
|---|---|---|
| R1 | 2 | `view_kg.html`, `matkg_xray_papers_cborg_chat.json.bak` (not `matkg_*.json`) |
| placeholder (R2: empty graph) | 5 | the 40-byte files: `matkg_google_gemini-flash-lite_{25,50,75,100}_20260603_182319.json`, `matkg_resources_saxs_algorithms_20260603_181120.json` |
| R2 format | 3 | `matkg_rsoxs_v3.json` (`nodes`/`edges` family, not things/associations); `matkg_bl1101_v8.json` (trailing newline) and `matkg_bl1101_v9.json` (not json2kg serialization) |
| R3 | 36 | 858 "high-entropy token" findings, all in node `id`, edge `subject`/`object` and `source_file_url` strings (no `sk-`, `AKIA`, `ghp_` or bearer match): deepseek-r1 14b x4 and 32b x4, gemini-flash-lite x3 (`100_20251008`, `50_20251008`, `75_20251008`), gpt-oss 120b x4 and 20b x3 (`100`, `50`, `75`), lbl_cborg-chat x4, qwen3_235b x10, `matkg_rsoxs_v1.json`, `matkg_rsoxs_v2.json`, `matkg_with_code.json`, `matkg_xray_papers_cborg_chat.json` |
| R3 + R4 | 6 | `matkg_bl1101_v1`–`v6.json`: R3 x304 and R4 x12 in total (v1–v4: 32 + 2 each; v5, v6: 88 + 2 each) |
| R3 + R4 + R7 | 1 | `matkg_bl1101_v7.json`: R3 x88, R4 x2, R7 x10 (`DocumentChunk` review flags) |

The earlier inventory counted 0 R3 hits on the tracked files and so predicted these files would pass. It used a
different heuristic. The entropy rule in `releases.check_disclosure` flags long chemical-name slugs used as ids. They
look like false positives but they are not judged here. `matkg_bl1101_v1`–`v9` and `matkg_rsoxs_v1`–`v3` are tracked on
`forge_merge` (commit `0d3d294`), so they are in the 59 entries, not in a separate untracked set.

Open items for the owner: (a) a reviewed allowlist (keyed by `allowlist_key`) for the R3 entropy hits, and for the
bl1101 R3/R4 hits and the R7 `DocumentChunk` flags, before those files can be adopted; (b) whether `matkg_rsoxs_v3`
(nodes/edges, an `ingest_rsoxs_v3` product) gets a manifest `format` value of its own, or is adopted after a rebuild;
(c) whether bl1101 v8/v9 are rebuilt in json2kg serialization (that is a new version, never an edit); (d) Q6 above.

**D3.22 resolved (owner: "Tune the rule").** The R3 entropy rule no longer flags slug-shaped ids: a long token must be
high-entropy, change character class between neighbours at least 45% of the time and have under 25% of its characters
in lowercase runs of five or more; UUIDs and 32+ digit hex runs are removed first. On the tracked flat files the tuned
rule reports no R3 finding (the `sk-`, `AKIA`, `ghp_`, bearer, `api_key` and `password` patterns are unchanged and
unaffected). Limit: a random single-case base36 token is not caught by entropy alone; prefixed and assigned secrets
still are. No allowlist and no data-file edit were needed for the entropy hits.

**Images.** The nested `graph.json` copies are excluded from the build context by `.dockerignore`
(`storage/kg/*/v*/graph.json`); manifests and validation reports ship. The flat copies stay the byte source in
images (section 7), and a descriptor still selects data by commit, path and hash.

