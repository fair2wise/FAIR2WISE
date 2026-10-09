> Historical record. Superseded where inconsistent by Draft v7 and OWNER_CLARIFICATIONS_V7.md.

# Owner deployment decisions incorporated in Draft v6

## Fixed requirements

- GitHub Actions builds and publishes :local, :als-dev, :als-prod.
- :local contains the full F2W UI/backend/agent stack and managed Tiled Graph; no bundled source papers.
- :als-dev contains no managed Tiled server and uses https://tiled-staging.als.lbl.gov/.
- :als-prod contains no managed Tiled server and uses https://tiled.als.lbl.gov/.
- Every image serves beneath /fair2wise; Ansible resolves and deploys the ALS FQDN separately.
- Local imports versioned JSON KGs into local Tiled; ALS variants import into their respective central servers. FORGE production targets the production pairing, not a disconnected local graph.
- F2W writes with an admin account; application policy and requested/approved/executed actor audit remain necessary.
- Papers are mounted during deployment, not baked into containers. Private derived content/state and runtime secrets must not leak into images or public routes.
- All prior fixed requirements remain: app/modules, forge_merge, frontend compatibility, JSON-first creation, current schema semantics, exact relevant quotations, Tiled 0.2.19, existing model settings/classes/QA and binary tests.

## What is not yet decided

O03: approve the previous fixed per-job resolved-generation-settings/revised-resume recommendation? No new answer was supplied.

O05: user-facing interactions will likely not write to Tiled, but that is TBD. Choose candidate/session JSON edits followed by operator import, scoped authorized admin-backed edits, or an explicit read-only deployment exception. Do not silently override the current edit compatibility requirement. Candidate-only editing is a proposal, not approved behavior.

O06: choose tag-moving triggers. Proposed: local/dev after successful trusted forge_merge checks; prod only via approved release/protected dispatch. Exact triggers are not yet an owner requirement.

O07: provide the existing Ansible role/repository, host/mount access conventions, secret-delivery mechanism and backup responsibility/retention/RPO/RTO. FQDN can remain an inventory value; it is not compiled into images. No backup product or schedule has been selected.

O08: confirm whether local bundles a model-serving process or uses configured CBORG/Ollama, and which CPU platforms must be supported. The draft does not add model weights or hidden downloads.

## Important reconciliations

Admin technical execution replaces assumed per-user Tiled delegation for publication; server owner ACL support is still a fact supplied by the owner, but does not restrict an admin credential by itself. Application scope checks are mandatory.

The requested /fair2wise path supersedes old container localhost/root URL defaults, not API request/response/SSE semantics. A configuration-only frontend build at this prefix is permitted by the owner requirement; feature rewrites are not.

Mounted files provide persistence, not backup. Exact mount roots, permissions and consistent restore requirements must be recorded before production release. Image creation, image promotion, Ansible rollout and JSON graph publication are four distinct actions.

No deployment, application test, Git commit, branch creation or human approval was performed by this revision.
