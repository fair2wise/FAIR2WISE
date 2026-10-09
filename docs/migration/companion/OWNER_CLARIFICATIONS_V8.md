# Owner clarifications incorporated in Draft v8

## New fixed decision D34

The local deployment grants every user all application capabilities without a Tiled login, account, personal token or admin-role check. This applies to the local UI, Python/CLI, FastAPI, agent Tools and background jobs. It is not a new question for H1.

ALS profiles retain native Tiled sign-in, chat-only ordinary users and admin-only privileged application actions under D30. The existing separate backend writer account remains under D27.

## Consequences

- Local frontend opens directly; administrative controls are not gated by user identity or role. Local audit/approval uses an explicit local-operator identity, not a fictional authenticated person.
- Trusted startup configuration selects the profile. ALS rejects local_trusted; browser/model/request fields cannot select it and failed ALS authentication never enables it.
- Local launcher binds to loopback by default; wider exposure is an explicit operator choice. Backend Tiled service credentials still protect graph communication.
- Explicit approvals, H0-H7 owner gates, schema/QA/evidence rules, rate limits, JSON versioning, target scope and private-source handling remain unchanged.
- Local grants/approvals never authorize ALS production writes. No source/runtime code is changed by this planning update.

## Previously open inputs remain unchanged

O03 (per-job generation settings), O06 (image promotion events), O07 (actual Ansible/mount/secrets/backup references), and O08 (local inference bundling/platform support) remain at their recorded gates. This revision does not silently settle them. O05 is resolved by D30/D34. Source snapshot pins, forge_merge, all-paper scope, Tiled 0.2.19, JSON-to-Tiled publication and owner/Alex FORGE coordination are unchanged.

## Evidence boundary

This is an owner-requested plan revision, not a deployment or application implementation. Existing donor and F2W inventories retain their source-snapshot scope. Historical OWNER_CLARIFICATIONS_V5/V6/V7 and CHANGES files are history, not current competing requirements. No human gate or application test is marked approved/passed by this edit.
