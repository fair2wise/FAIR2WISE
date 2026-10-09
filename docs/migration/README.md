# FORGE to F2W literature migration

Working records for the migration onto the `forge_merge` branch.

| Path | Contents |
|---|---|
| `plan.md` | Migration plan, draft v8.0 (text form of the handoff's `MIGRATION_PLAN`) |
| `auth_spec.md`, `forge_spec.md`, `git_kg_spec.md` | Short-form specs supplied with the plan |
| `companion/` | Companion handoff bundle v8, committed verbatim |
| `reviews/Hx/` | Gate evidence and review packets, one directory per human gate |

## Companion bundle

`companion/` is kept byte-for-byte as supplied so `companion/SHA256SUMS.txt`
stays checkable:

```bash
cd docs/migration/companion && shasum -a 256 -c SHA256SUMS.txt
```

`MIGRATION_PLAN.md` and `MIGRATION_PLAN.docx` are listed in `SHA256SUMS.txt`
but were not supplied, so those two lines fail. `../plan.md` is the plan text
used here and does not hash to the listed `MIGRATION_PLAN.md`.

Do not edit files in `companion/`. Record corrections, discrepancies and
checkout-specific facts under `reviews/` instead.

`companion/scripts/verify_handoff.py` checks static source evidence (hashes,
AST spans, routes, task graph). Run it against a clean export of the baseline
commit, not the working tree:

```bash
git archive 222b1e7 | tar -x -C /tmp/f2w-baseline
python3 -I docs/migration/companion/scripts/verify_handoff.py \
    --f2w-root /tmp/f2w-baseline --forge-root /path/to/forge_rsoxs_analysis-main
```

## Third-party notice

Some bundle files quote short excerpts of, and list symbols and hashes from,
`als-computing/forge_rsoxs_analysis`, distributed under the BSD 3-Clause
License:

> Copyright (c) 2026, The Regents of the University of California, through
> Lawrence Berkeley National Laboratory (subject to receipt of any required
> approvals from the U.S. Dept. of Energy). All rights reserved.

The FORGE source tree itself is not committed to this repository.
