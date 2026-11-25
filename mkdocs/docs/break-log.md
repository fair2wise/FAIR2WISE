# Break log (owner away, from 2026-11-26)

Running log of the unattended work on `forge_merge`: date, what was done, the commit, the CI run, decisions taken for the
owner, anything unfinished. The backlog and the rules are the owner's message of 2026-11-25 (pilot re-pinned to 987762a2,
run 45713088709). Model calls used so far: **0 of the 400 allowed**.

## 2026-11-26

- **Tier 1 A, item 4: Repair relations** for a bundle on a user schema (`library_relations`, route V100, Extract page button).
  No model. Swaps relations valid only reversed, turns value-slot relations into properties, counts per pattern, keeps one
  previous generation (`previous/terms.json`, `previous/kg.json`), rebuilds the KG, idempotent. Commit a221bb64 (re-pin follows). CI: see the next entry.
  - Decision taken: the job refuses a bundle on a shipped schema (`not_user_schema`) rather than running as a no-op, because
    shipped-schema relations were judged with the old rules on purpose (pinned prompt and behaviour).
  - Decision taken: the previous generation lives inside the bundle directory under `previous/`; nothing outside the bundle
    is written and the bundle listing ignores it.
  - Decision taken: a relation whose object is not a term of the bundle is left as it was and counted `unknown_object`
    (it cannot be judged without the object's class).
