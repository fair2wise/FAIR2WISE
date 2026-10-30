# ADR: One owner per shared file

Status: Accepted at H1 (2026-10-12) (T01; `docs/migration/reviews/H1/decision.json` H1-8: the ADR set is the H1 baseline)
Date: 2026-10-12
Superseded/amended by: O-U1 and O-U2 (2026-10-16) approved the only two `ui/` changes so far (the `/fair2wise` base in `App.tsx`; the judge-model dropdown) and the matching `FROZEN_UI_TREE` re-pins. AUD-14 records that T11B, T25C and T22C merges touched paths outside their `exclusive_path_ownership`; all were merged by the integration lead as this ADR permits. Machine-readable task status: `docs/migration/task_status.json`.

## Decision

The integration lead owns these shared files and merges all changes to them.
Leaf workstreams propose changes; they do not merge competing definitions
(plan section 8.1).

| Shared file or area | Owner | Notes |
|---|---|---|
| `pyproject.toml` (new), `requirements/*.in`, all locks | integration lead | regenerate locks with pip-tools only |
| `app/modules/contracts/` | integration lead (T01) | DTO changes bump `CONTRACT_VERSION` |
| Tool manifest / registry bootstrap (`agent_runtime/registry.py`, `tools/bootstrap.py`) | integration lead | frozen after app startup |
| API route registration (`app/api/main.py`, `f2w_agent/api.py create_app`) | integration lead | 19 + 6 legacy routes frozen by T18A |
| Schema registry and versions (`app/modules/schema/`, `storage/schema/`) | integration lead (T22) | no semantic change without an owner request |
| `.github/workflows/` | integration lead | CI plan from T18A |
| `Dockerfile`, `.dockerignore`, `docker/` | deployment workstream, merged by integration lead | H0 E3/E4 rules apply |

Exclusive task paths come from `docs/migration/companion/task_graph.json`
(`exclusive_path_ownership`). Workers use scratch branches named
`forge_merge-<task>` from an approved integration commit and never target
`main` or `rsoxs` (BRANCH_WORKFLOW section 3).

## H1 parallel-work record

During H1, five workers wrote only to their task paths in the integration
worktree and made no git state changes. The integration lead reviewed and
committed their output.
