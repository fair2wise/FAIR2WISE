# Migration decision record - Draft v8

| ID | Owner decision | Engineering consequence |
| --- | --- | --- |
| D01 | Repository `fair2wise/FAIR2WISE`, branch `rsoxs` | Resolve and record `refs/heads/rsoxs` to an exact commit at H0. Never substitute another branch. |
| D02 | Keep code in `app/modules/` | No src-layout migration. Package/import isolation is solved without moving the domain tree. |
| D03 | Splash is deprecated and replaced by Tiled Graph | Required Tiled publication/query/edit adapter in release one. No new Splash backend. KG creation continues to produce versioned JSON, imported through a separate adapter. |
| D04 | Paper figures are mandatory | Figure/panel/table/q-axis/scale capability and tests block the integrated pilot and release. |
| D05 | Preserve existing F2W LLM paths/classes | New runtime and ported services adapt to those classes; donor inference clients/defaults do not replace them. |
| D06 | Project owner approves | All H0-H7 decisions are made by the requesting F2W owner. Other people may advise but are not required approvers. |
| D07 | ALL FORGE paper infrastructure; import F2W agents; support FastAPI | Paper workflows, operations, routes, figures and evaluation must be accounted for; Python and FastAPI are both release requirements. |
| D08 | Preserve F2W QA constraints | Capture prompts, evidence gates, citations, fallback, approval, context and provider behavior from the exact branch; preserve by default. |
| D09 | A couple of months | Planning envelope: approximately eight weeks, with staged gates and bounded parallel work, not a guarantee or an automatic scope reduction. |
| D10 | Commit migration work to `forge_merge` | Create or safely resume this branch from the reviewed `rsoxs` baseline. No migration commits to `main` or `rsoxs`; no history reset or force-push. |
| D11 | Existing F2W frontend remains backward compatible | Freeze current browser source, API schemas, streams, settings, sessions, citations and graph-edit workflows. Demonstrate the old frontend against the new backend, not only updated clients. |
| D12 | Promote F2W scripts to agent-callable Knowledge Operations | Reusable services under app/modules/operations; existing Tools/Skills, typed results and complete-scope code execution; scripts remain CLI facades. |
| D13 | Support application-managed and central Tiled Graph | Both deployment modes are first-release requirements through the same graph adapter; no automatic replication or requirement to run both at once. |
| D14 | This application provisions the disposable Tiled test server | Application-owned launcher/fixture creates, seeds, checks and tears down isolated test instances; never use the central server as a destructive test target. |
| D15 | Owner confirms required Tiled per-owner writes and team awareness | Server permission support remains owner-confirmed. Application writes now use the D27 admin principal with independent scope checks; test server ACLs separately from application authorization. |
| D16 | Splash is already fully replaced by Tiled Graph | Port any remaining Splash-derived functionality to Tiled; do not require a live Splash service, a new adapter or a speculative recovery project. Audit supplied legacy exports only if found. |
| D17 | Binary extraction and chat pass/fail suite is required | T11 owns the suite; H3 approves cases/oracles and H4/H6 require the executed binary report. Gold data supports these tests; metrics cannot replace them. |
| D18 | Frontend-selected F2W models; respect limits for every LLM | Preserve settings/classes; reuse and extend shared rate/concurrency admission for all callers and providers. No fixed donor model, automatic paid fallback or invented dollar budget. |
| D19 | Source papers stay agent-private; relevant exact quotations are allowed | Preserve cited, bounded exact excerpts in answers and graph-detail evidence. Do not serve complete papers, raw sections, original crops or a reconstructable source-text export. |
| D20 | Preserve existing logic and add schema loading, validation and versioning | Keep current classes, inheritance, relation meanings and QA behavior. No measurement/technique semantic split or historical reclassification in this migration. |
| D21 | KG creation remains versioned JSON files | Preserve json2kg and its field/identity behavior. Build, validate and freeze JSON before a separate idempotent Tiled Graph import; no parser/LLM activity in that import. |
| D22 | Pin Tiled to 0.2.19 | Use an exact reviewed artifact/build, not a floating latest, release candidate or silent fallback. Managed/test instances target this version; external connections verify the compatible deployed contract. |
| D23 | GitHub Actions builds and publishes three tagged images | Required tags :local, :als-dev, :als-prod from the reviewed application source; tested digests and protected promotion; Ansible rollout is separate. |
| D24 | :local includes Tiled, UI and backend agents | Self-contained application image with supervised services and Tiled 0.2.19; source papers and durable state are mounted, not baked. |
| D25 | ALS variants use their respective central Tiled servers | :als-dev -> https://tiled-staging.als.lbl.gov/; :als-prod -> https://tiled.als.lbl.gov/; neither bundles a managed Tiled server. |
| D26 | All variants serve under /fair2wise; Ansible sets ALS FQDN | Same-origin prefixed UI/API, assets, streams and redirects; no compiled FQDN, double prefix or localhost browser endpoint. |
| D27 | F2W writes to Tiled using an admin account | The configured F2W admin account remains the technical writer. In :local, any user may initiate all application actions without sign-in. In :als-dev/:als-prod, verify the caller through that environment's Tiled; privileged actions require admin status. |
| D28 | Papers are deployment-mounted, never bundled in images | Private PDFs/parsed artifacts/crops/state remain outside build layers; explicit runtime mounts and access controls. Backup policy details remain a deployment input. |
| D29 | Publication target follows the image environment | local publishes to local Tiled; ALS variants publish to their respective central servers. FORGE production uses production F2W/Tiled; no implicit replication. |
| D30 | ALS users sign into Tiled; only Tiled admins may perform privileged work | Applies to :als-dev and :als-prod only. Verify identity/admin status server-side; non-admin users are chat-only. No separate maintainer allowlist or assumed OAuth2-proxy. D34 defines the local exception. |
| D31 | Versioned JSON KGs are committed to the F2W repository | Commit frozen, disclosure-reviewed JSON and manifests. Promote the exact Git-identified, hash-verified artifact that passed staging validation; production import does not rerun extraction. Papers/private processing state stay mounted and out of Git/images. |
| D32 | Project owner coordinates FORGE merge with Alex | Prepare and test the consumer adapter; owner and Alex choose the FORGE target branch and merge. F2W forge_merge/H0-H7 approval does not authorize a FORGE push or merge. |
| D33 | Central staging and production run Tiled Graph 0.2.19 | Owner confirms central version/Graph availability and coordination for admin writing. Preserve that premise; record artifact/credential references and run authorized deployment checks without claiming prior runtime verification. |
| D34 | :local is trusted full-access mode with no user sign-in requirement | Any local user can use all application capabilities, including approval, publication and graph editing, regardless of Tiled login or role. Select local_trusted server-side; audit as local-operator. ALS never falls back to this mode. |

Resolved: O01 preserves schema semantics; O02 permits relevant exact quotations; O04 pins owner-confirmed central Tiled 0.2.19; O05 is profile-specific under D30/D34: full access without sign-in locally, Tiled-authenticated admin actions and ordinary-user chat on ALS.

Open: O03 job settings; O06 image promotion events; O07 remaining actual Ansible/mount/secret/backup inputs; O08 inference bundling/platform scope. D30/D34 select profile-specific access, so O07 is not an auth-system selection. The owner/Alex choose FORGE target ref and merge at T15. No implementation gate, test run or production change is approved by these planning decisions alone.
