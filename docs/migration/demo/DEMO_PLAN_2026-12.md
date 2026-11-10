# Local demo plan, December 10-11 2026

Owner decision 2026-11-10 (twentieth set, `reviews/owner_decisions_2026-11-03.md`): the first target is a locally
run demo of the `:local` profile on the owner's Mac Studio (or another machine, not the development machine).
`:als-dev` and `:als-prod` work and decisions are deferred. A test against `tiled-staging.als.lbl.gov` is a stretch
goal after the local path is solid.

## What the demo shows (owner's scope)

1. Harvest, extract, build a KG: arXiv harvest in the Library tab, a short live extraction of one paper with a free
   LBL-hosted CBORG model, with a pre-extracted fallback.
2. Publish to Tiled and approve in the UI: adopt a release, stage a publication, approve it on the Pending Approvals
   page, watch it land in the local Tiled.
3. Explore and edit the graph: KG Viewer, Source selector (JSON vs Tiled), neighbourhoods, edit a node and Save to Tiled.
4. QA chat ("a huge part of it"): ask questions answered from the KG, on the free CBORG model.
Not selected: rollback and the Operations tab (they work and stay available as extras).

Stretch (staging Tiled, after the local path is solid): (a) read 11.0.1.2 experiment data from Tiled for QA if it is
available; (b) push a KG into Tiled Graph on staging; (c) read from that Tiled graph during QA.

## Timeline (today 2026-11-10; demo 2026-12-10 or 11)

| Week | Dates | Goal | Exit check |
|---|---|---|---|
| 1 | Nov 10-16 | Scope fixed; demo-machine facts known (owner runs a read-only check script); demo tooling (install, seed, reset, smoke) and the QA readiness review started; GitHub push readiness audited | machine report in hand; QA gap list; GitHub blockers listed |
| 2 | Nov 17-23 | QA over the published Tiled graph; demo corpus and question set chosen; runbook v1; GitHub push and a CI-built image if the owner approves the push | QA answers the demo questions from the Tiled-published release in a rehearsal on this machine |
| 3 | Nov 24-30 | First full rehearsal on the demo machine from a clean install; staging Tiled read-only probe (owner provides the endpoint access); fixes from the rehearsal | rehearsal log with timings; fix list closed |
| 4 | Dec 1-7 | Staging write test only if approved (throwaway corpus, FORGE-owned records verified untouched); code freeze Dec 4; second rehearsal; image tagged and checksummed; offline fallback verified | frozen image digest; two clean rehearsals |
| - | Dec 8-9 | Final rehearsal on the demo machine, backups, printed checklist | go/no-go |

## Workstreams

- **W1 Demo tooling** (`scripts/demo/`): read-only machine check, install, seed (known state: both corpora published), reset, smoke, health; sleep prevention (a suspended machine broke a job lease once); disk and Colima checks.
- **W2 QA path**: what QA retrieves from today, quality on the demo questions with the free model, behaviour when CBORG is unreachable (CBORG only answers from LBLnet or VPN), a fallback, and QA over the Tiled-published graph.
- **W3 Image delivery**: build and test here now; GitHub push and a CI-built, versioned image soon (owner: "we will want to merge onto github soon to build a deployable image"); `docker save` bundle as the offline path.
- **W4 Demo content**: corpus choice, seeded KGs, question list, a short script with timings and fallbacks.
- **W5 Hardening**: what a rehearsal finds; the open owner decisions that touch the demo (neighbourhood cap, pill and layout polish); no new features after Dec 4.
- **W6 Staging Tiled stretch**: read-only probe, experiment-data read for QA, throwaway-corpus write and read-back, ownership isolation. Needs the owner's endpoint access and an API key by environment variable only.

## Deferred for the demo

ALS images and decisions, T24E, T16A/T16B cutover, Splash deletion, the H8 removal lists, T15 (FORGE), phone layout.
Gates stay owner decisions (H4 B4 promotion and C1 still matter for extraction quality; H5 matters before any write to staging).

## Owner actions on the critical path

1. Run `scripts/demo/check_machine.sh` on the Mac Studio and share the output (week 1).
2. Decide when to push `forge_merge` to GitHub and who sets up ghcr (week 1-2).
3. Provide a Tiled staging API key (environment variable only) and say which prefix/namespace a test corpus may use (week 3-4).
4. Review and promote the B4 labels, decide C1 (extraction quality), so the demo extraction path is the approved one.
5. Attend the rehearsals (week 3 and 4).
