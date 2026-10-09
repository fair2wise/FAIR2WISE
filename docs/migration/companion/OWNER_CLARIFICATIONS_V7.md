# Owner decisions incorporated in Draft v7

## Settled by the latest answers

| ID | Owner decision | Plan consequence |
| --- | --- | --- |
| D30 | Users sign into Tiled; admins can write, other users chat-only unless otherwise noted | Native Tiled identity/admin verification; admin-only ingestion, curation approval, publication and editing; no separate named allowlist or assumed OAuth2-proxy. D27 technical service writer remains unchanged. |
| D31 | Versioned JSON KGs are committed to the repo | Promote identical frozen, hash-identified Git artifacts validated in staging to production mounts/import; no production extraction rebuild. Commit safe KG data/manifests, not full papers or private processing state. |
| D32 | Owner works with Alex to merge into FORGE | Agent prepares/tests consumer patch. Owner/Alex settle target ref and remote merge; F2W forge_merge rule is not a FORGE branch assignment. |
| D33 | Central Tiled statements are confirmed | Owner confirms both named central services have Tiled Graph 0.2.19 and acknowledges admin-writer coordination. Exact account/secret references still come from deployment; no identities are invented. |

O05 is resolved. Existing graph edits are preserved for authenticated admins, with server-side denial for non-admins. Existing login/capability UI integration is a narrow deliberate addition; old scientific semantics and allowed API/SSE workflows remain preserved. Ordinary per-user model preferences/session history remain chat support, not permission to change global credentials, data or quotas.

## Requirements versus verification

Tiled authentication and server-version support are owner-confirmed requirements. This draft did not sign into or probe central servers. H1 records the supported native login/role contract and exact artifact references; disposable tests and approved central smoke checks verify implementation/configuration. Historical inability to fetch public package metadata does not override the owner's confirmation.

Runtime operation approvals come from authenticated Tiled admins. F2W engineering/scientific migration gates still come from the project owner. FORGE remote merge is coordinated with Alex. None is a substitute for the others, and no gate or test has been marked passed.

## Remaining inputs already carried forward

O03: fixed per-job effective generation settings and explicit revised resume is still a proposal; these answers do not settle it.
O06: exact image-tag promotion triggers/protected release events remain to be configured.
O07: actual Ansible role/inventory, mount roots/UIDs, secret references and backup/restore ownership/retention remain deployment inputs. Authentication is no longer an open system-selection question; use Tiled. No operator's name or live credential was supplied.
O08: local inference-server inclusion and advertised CPU architectures remain as before.

The FORGE target branch is supplied with Alex at T15 before a remote write, not guessed as a new F2W gate. Do not re-ask whether central Graph 0.2.19 exists, who owns the Tiled test provisioner, or whether non-admin users can publish.
