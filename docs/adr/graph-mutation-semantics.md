# ADR: Capability-aware graph ports; JSON-first creation; no fake transactions

Status: Accepted at H1 (2026-10-12) (T01; `docs/migration/reviews/H1/decision.json` H1-8: the ADR set is the H1 baseline)
Date: 2026-10-12
Superseded/amended by: item 3 (the exact edit projection) is still undecided. The legacy Splash-to-Tiled STOP-1..6 (H1 `decision.json` blocking_later_tasks; OPEN_DECISIONS A4) and the Tiled graph editing plan's Q1-Q16 (`docs/migration/ui_contracts/tiled_graph_editing_plan.md`) are pending owner decision (OPEN_DECISIONS A4). T18C F1: the Tiled-backed edit path is not built; a Save without Splash fails safely with the legacy 502 and writes nothing. T08A/T08B implemented the read adapter and the JSON-to-Tiled import on Tiled 0.2.18 (O-T1), not 0.2.19rc2 as inspected here.

## Context

D21: KG creation produces versioned JSON; a separate adapter imports it into
Tiled Graph. Plan section 3: donor graph method names overstate mutability;
do not promise atomic upserts across JSON and Tiled. The T20A inspection of
Tiled 0.2.19rc2 (`tiled-deployment.md`) found entity and link properties are
set only at creation; update inputs have no `properties` field.

## Decision

1. `GraphPort` (`app/modules/contracts/ports.py`) is the only graph access
   surface for new code. Backends advertise `GraphCapabilities`:
   complete listing, bounded traversal, mutable entity properties,
   append-only relations, batch writes, compare-and-swap, owned deletion.
2. An unsupported operation raises `CapabilityError`
   (`ErrorCode.UNSUPPORTED_CAPABILITY`). It never returns an empty or
   successful result.
3. **Edits on create-only backends** project as new revision records plus
   links (create-new / retire-old), recorded in an edit journal and reconciled
   into the next JSON version. The exact projection is decided with T18A/T23A.
4. **Repair and deduplication planning require `complete_listing`.** A capped or
   paged read blocks planning (plan section 11.3).
5. **No cross-store transaction.** Writes are staged, replay-safe stages with
   stable IDs, durable receipts and verification (see
   `job-and-approval-lifecycle.md`).
6. Reads are graph-scoped: results carry `graph_id`, and identities are never
   joined across graphs by display name (B27).

## Consequences

- Legacy UI edit routes (B03) need an explicit projection; if one cannot be
  supported safely it is a STOP for the owner, not a silent no-op.
