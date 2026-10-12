# H1 owner decisions log

Decisions the project owner has made during H1 contract work. These are not
the H1 gate approval; that is recorded separately in `decision.json` when the
owner reviews the H1 packet.

## O-D1: verbatim snippets in stored KG JSON (2026-10-12)

**Finding (T21A).** Tracked `storage/kg` JSON holds about 127k verbatim
context snippets. A 300-sample check matched every sampled snippet to its
cited PDF page, and combined they reproduce a median 31% (max 48%) of each
paper's text across 90 papers. `storage/terminology` holds about 141k more.
Under D19 this would make the files a partial source-text export, in the
public repository and in any image that includes `storage/`.

**Decision.** Keep the KG JSON files, snippets included, in the repository and
in the Docker image.

**Scope of the exception.**

- Applies to stored KG artifacts: committing them to Git (D31) and baking them
  into images (H0 E3).
- Does not relax D19 for serving. What routes, SSE, traces, graph detail and
  exports return to users still goes through the T21A disclosure projection.
- Does not cover raw PDFs, full parsed paper bodies, figure crops, the
  source-RAG index (`storage/source_index`, H0 E4), credentials, local paths
  or operational databases.
- `storage/terminology` is already in the image under H0 E3 and stays in Git;
  this decision does not change that.

**Updated in.** `docs/adr/content-disclosure.md` (O-D1); T26A release contract
(relayed to the T23A/T26A worker).

## O-G1: what is tracked in Git (2026-10-12)

(Recorded as "O-D2" in commit 9155d76; renamed because `content-disclosure.md` already uses O-D2 for disclosure budgets.)

**Decision (owner, verbatim intent).** Beamline ops documents are fine to keep
in this repo. Research papers stay gitignored. All KGs are saved in this repo
and not ignored.

**Applied in `0d3d294`.**

- `.gitignore`: the `storage/kg/*.json` and `*.json.bak` rules are removed.
  `papers/` became `papers/**` with a re-include for
  `papers/rsoxs/beamline_blueprint/blueprint_*.html`. No PDF is trackable.
- Committed the 12 previously untracked KGs (`matkg_bl1101_v1`-`v9`,
  `matkg_rsoxs_v1`-`v3`) after a credential and local-path scan, and the BL
  11.0.1.2 blueprint HTML.
- Effect on the H0 baseline deviations: the 15 `test_bl1101_ingest` errors
  were caused by the missing `matkg_bl1101_v7.json`; with it committed, the
  isolated suite at HEAD is 770 passed, 3 failed (the ambient
  `CBORG_API_KEY` and `pixi` cases), 20 skipped, 4 xfailed.

**Still unclassified (left ignored pending the owner):**

| Item | Size | What it is |
| --- | --- | --- |
| `papers/rsoxs/manifest.json`, `papers/tomo/manifest.json` | 2.4 MB, 172 KB | Harvest manifests: paper metadata the source-RAG and harvest code read; no paper text expected |
| `papers/rsoxs/beamline_blueprint/PubTempPubListExport9_11_26_14_05_24.rtf` | 490 KB | Publication-list export stored next to the blueprint |
| `.cache/bl1101/pages`, `readmes` | 120 KB, 2.7 MB | Cached ALS beamline page and READMEs, indexed as ops documents |
| `.cache/bl1101/repos` | 31 MB, part of 1264 files | Cloned third-party Git repositories indexed as ops documents (their own licenses apply) |

## O-T1: Tiled version (2026-10-12)

**Decision.** Use Tiled 0.2.18 for now.

**Why it is needed.** D22 pins exactly 0.2.19, but no final 0.2.19 release
exists (PyPI, GitHub releases and conda-forge stop at 0.2.18; only
0.2.19rc1/rc2 are published).

**Pin.** `tiled==0.2.18` (published 2026-09-02):

| File | SHA-256 |
| --- | --- |
| `tiled-0.2.18-py3-none-any.whl` | `416079d4b5c9acc5498197d48c05a951ee8f7c699ac3b07920ae907a8da7fa06` |
| `tiled-0.2.18.tar.gz` | `2f3943260c46824b162796fcb8a43c3d02f41205e2ccefc64e365b18b6ec1e27` |

**Scope and risks to carry forward.**

- An interim owner exception to D22 for the managed `:local` server and the
  disposable test fixture. Revisit when a final 0.2.19 (or the central
  servers' exact build) is available; changing the pin invalidates H1.
- The T20A API description came from the 0.2.19rc2 wheel. Rc2 changed the
  graph schema from 0.2.18 (`nodeId` -> `isNodeBound`, access blobs -> access
  tags, four catalog migrations). T20B must re-check the capability contract
  against 0.2.18 before building on it.
- The central staging/production servers are reported to run 0.2.19 (D33). A
  local 0.2.18 graph may not match them. External-mode tests must check the
  central server's version and capabilities and fail clearly on mismatch.
- Tiled's `pydantic-settings` constraint still conflicts with the F2W lock;
  the managed server runs in its own environment (`optional-dependencies.md`).

## O-A5, O-A6, O-A7: T25B follow-ups (2026-10-12)

Owner answers to the T25B open questions:

- **O-A5 (JSON graph reading).** "All users on local, and all authenticated
  users on staging/prod should have json graph reading ability." Applied:
  `GET /graph/node/{id}` with `json_graph_path` now needs only
  `chat.read_support` (`authorization.graph_node_read_capabilities`, route
  R09). Per-turn graph choice in chat requests was already open. Changing the
  server-wide default selection (`PUT /settings json_graph_paths`) stays
  admin-only pending the owner (see open items).
- **O-A6 (`extraction_mode`).** Admin-gated. Already implemented: it is not a
  personal settings field, so changing it needs `settings.global`.
- **O-A7 (MkDocs).** "MKDocs should be rendered and hosted on ALS deployment
  target versions (and local)." Requirement for T24B/T24C: build the MkDocs
  site into every image (`:local`, `:als-dev`, `:als-prod`) and serve it
  under the `/fair2wise` prefix (the T24A source audit found the nginx image
  has no `/docs` route today). This is separate from FastAPI's generated
  `/docs`, `/redoc` and `/openapi.json` pages (open item below).

Still open from T25B: `workflow_mode` gating, Tiled service principals as
callers, FastAPI's generated API docs on ALS, `PUT /settings json_graph_paths`
for non-admins, O-A1 (annotations), O-A2 (role-check max age).
