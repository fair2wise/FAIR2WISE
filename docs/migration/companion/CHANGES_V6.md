> Historical record. Superseded where inconsistent by Draft v7 and OWNER_CLARIFICATIONS_V7.md.

# Changes from v5 to v6

## Added

DEPLOYMENT_STRATEGY.md, CI_CD_CONTRACT.md, ANSIBLE_HANDOFF.md, DEPLOYMENT_SOURCE_AUDIT.md, DEPLOYMENT_REFERENCES.md, deployment_profiles.json and deployment_source_map.json. T24A-T24E adds explicit image/prefix/CI/Ansible tasks; DEP binary cases add deployment acceptance.

## Updated in place

The main architecture, physical-layout proposal, scope decisions, H1/H4/H6/H7 gates, task graph, source lock, frontend compatibility, Tiled lifecycle/admin test rules, JSON import/edit contract, private content policy, binary suite, kickoff/README and editable Word plan now include deployment. Earlier source facts, source hashes and donor inventories are retained unchanged.

## No silent decisions

O05 (user writeback) remains open despite admin-account selection, because it affects existing UI graph editing. O03 settings remains open. O06 promotions, O07 infrastructure/backup inputs and O08 local inference/platforms remain proposals/questions. No runtime build/test/deploy, Git branch or human approval is claimed.

## Meaning preserved

JSON-first creation, inference-free Tiled import, schema semantics, existing F2W model/QA/UI behavior, paper-only FORGE migration, figures, Knowledge Operations and binary evaluation remain required. Required images are complete application variants, not three tags on the previous agent-only image.
