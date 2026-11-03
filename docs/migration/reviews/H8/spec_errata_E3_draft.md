# Spec errata for owner exception E3 (DRAFT for owner review)

Status: DRAFT. Drafted on owner approval of T26A Q5 ("Yes, draft them", `reviews/owner_decisions_2026-11-03.md`, fifth
set). Nothing in this file has been applied. The documents quoted below are frozen (`docs/migration/plan.md`,
`docs/migration/companion/*`) and are not edited by this draft. Apply an entry only after the owner approves it, and
only through the owner-controlled amendment path for frozen files.

## Basis

- **E3** (owner, 2026-10-09; `reviews/H0/license_dependency_audit.md`): `storage/`, including `storage/kg`, may be baked
  into images.
- **O-D1** (2026-10-12): KG JSON is stored and shipped with its verbatim snippets.
- **D3.24** (owner, 2026-11-03, "Extend flat allowlist to legacy names"): flat `storage/kg/matkg_<name>.json` copies are
  the byte source in images and every adopted release lists its flat copy in `compat_paths`. The nested
  `storage/kg/<corpus>/v<N>/graph.json` byte copies are not shipped in images (`.dockerignore`,
  `storage/kg/*/v*/graph.json`); manifests and validation reports are.
- Already recorded in `docs/migration/data_releases/release_contract.md` section 7, which says these texts "need an errata".

What stays true in every entry: baking data in an image never selects data, never authorizes publication, and never
replaces the `(data_commit, path, sha256)` selection and verification (release_contract.md sections 3, 6, 7). Papers,
`storage/source_index/`, the Tiled catalog DB (except a future `:local` image), `.env*` and secrets stay out of every
layer.

## Entry 1: plan.md, section 4.14, last sentence of the third paragraph

File: `docs/migration/plan.md`, line 304 (paragraph beginning "Code image digest, data commit/snapshot hash ...").

Old text:

> Committing a graph does not bake it into an image or authorize publication. Docker build contexts/layers must continue excluding storage/kg corpus artifacts even when tracked by Git. The deployment/release process exports only the reviewed data files from the selected commit into runtime mounts.

New text:

> Committing a graph does not authorize publication, and baking a graph into an image does not authorize publication or select data (owner exception E3: `storage/`, including `storage/kg`, may be baked into images; release_contract.md section 7). Build contexts and layers exclude `storage/source_index/`, papers, secrets and, except in a future `:local` image, the Tiled catalog database; they do not need to exclude KG JSON. Flat `storage/kg/matkg_*.json` copies may be baked as a convenience byte source; nested `storage/kg/<corpus>/v<N>/graph.json` duplicates are not (`.dockerignore`). The deployment/release process still exports the reviewed data files from the selected commit into runtime mounts and verifies their SHA-256 against the release record before any import; an image-baked file is used only if its SHA-256 equals the record's.

Rationale: E3 supersedes the exclusion clause for KG JSON; the remaining text is kept so that "git-tracked" and "baked"
are still not treated as promotion. The nested-copy sentence records D3.24 so the image does not carry each graph twice
(about 220 MB).

## Entry 2: GIT_KG_PROMOTION_SPEC.md, section 4

File: `docs/migration/companion/GIT_KG_PROMOTION_SPEC.md`, line 39 to 41.

Old heading and text:

> ## 4. Deployment is not image bundling
>
> Git-tracked does not mean Docker-bundled. All three images still exclude corpus JSON, papers and private state from their build context and layers. Use allowlisted COPY or an explicit sanitized context; test with a committed fixture snapshot to prove this exclusion.

New heading and text:

> ## 4. Deployment is not image bundling, and an image never selects data
>
> Git-tracked does not mean Docker-bundled, and bundled does not mean selected or published. Under owner exception E3 the images may carry `storage/kg` (including the verbatim snippets, O-D1). All three images still exclude papers, `storage/source_index/`, parsed private evidence, credentials and other private state from their build context and layers, and exclude the Tiled catalog database except in a future `:local` image. Use allowlisted COPY or an explicit sanitized context; test with a committed fixture snapshot that the private paths are absent and that no nested `storage/kg/<corpus>/v<N>/graph.json` duplicate is shipped. An image is built from a clean checkout or a context that excludes untracked KG files. A baked KG file is a convenience byte source only: production import selects `(data_commit, path, sha256)` from the release record and fails if the baked bytes differ.

The next sentence of section 4 ("Ansible/operator export places the approved data on runtime mounts separately from image
rollout. Do not give containers Git push credentials ...") is unchanged.

Rationale: the old fixture test ("prove this exclusion") would now fail on a correct image; the test must check the paths
that are still excluded and the nested-copy rule, and must check that an image cannot supply data whose hash is not in
the record.

## Entry 3: JSON_KG_PUBLICATION_SPEC.md, last paragraph

File: `docs/migration/companion/JSON_KG_PUBLICATION_SPEC.md`, line 87.

Old text:

> Git-tracked graph artifacts remain excluded from every Docker layer and public static route.

New text:

> Git-tracked graph artifacts may be baked into images under owner exception E3 (flat `storage/kg/matkg_*.json` copies; nested `graph.json` duplicates are not shipped) and remain excluded from every public static route; a baked file never selects or publishes data.

Related sentence in line 83 of the same file, old text:

> Snapshots, manifests, edit journals and receipts are deployment mounts, never shipped private image content.

New text:

> Edit journals, receipts and any private snapshot payload are deployment mounts, never shipped private image content. Committed snapshots and manifests may be baked (E3) but are verified against the release record before use.

Rationale: line 83 lists "snapshots, manifests" as mount-only; E3 allows the committed ones in the image. Journals,
receipts and private payloads are still mount-only.

## Entry 4 (optional, same pass): CHANGES_V7.md and plan.md section 5 deployment paragraph

`docs/migration/companion/CHANGES_V7.md` line 4 states "Git-tracked snapshots remain excluded from images". It is a
change log of version 7 and is historical; no edit is proposed. Add this line to the errata index of the next change log
instead: "E3 supersedes the image-exclusion clause for KG JSON (see `reviews/H8/spec_errata_E3_draft.md`)."

`docs/migration/plan.md` line 708 ("Papers are never baked into images. Mount source PDFs, intake, parsed/crop artifacts,
JSON snapshots/receipts, ... outside the static web root.") needs one word change: "JSON snapshots/receipts" becomes
"private JSON payloads, edit journals and receipts", with the sentence "Committed KG snapshots may be baked (E3)" after it.
Decision D28 (line 41) says only papers and is unaffected.

## Not changed by E3 (listed so the owner can see the boundary)

- D24, D28, plan line 267 (what each image contains): no KG statement.
- GIT_KG_PROMOTION_SPEC sections 1 to 3, 5 and 6: selection by commit, path and hash, staging binding, journals and
  rollback are unaffected.
- `.gitattributes`, `.dockerignore` and the release contract already implement the text above; the errata only brings
  the three companion texts into line.

## Open points for the owner

1. Approve Entries 1 to 3 as written, or amend the wording.
2. Entry 4: apply the two small edits, or leave the historical change log and plan line 708 as they are.
3. Who applies the edits (frozen files). Nothing here has been applied.
