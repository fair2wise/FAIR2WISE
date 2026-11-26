# Break log (owner away, from 2026-11-26)

Running log of the unattended work on `forge_merge`: date, what was done, the commit, the CI run, decisions taken for the
owner, anything unfinished. The backlog and the rules are the owner's message of 2026-11-25 (pilot re-pinned to 987762a2,
run 45713088709). Model calls used so far: **13 of the 400 allowed** (the generalisation check, cap stated 24).

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
- **Tier 1 A, items 1 and 2 (in progress at this entry): aliases and the KG retry; paper-search order.** The hard-coded
  `pleco -> Loricariidae` map is removed. Terms gain `aliases` (from the model's register_term call and from the text by shape:
  `term (X)`, `X (term)`, `also known as`), at extraction and in Find supporting text; the KG node carries them and retrieval
  resolves them like names. A question the KG cannot answer is retried once with up to five alternative terms the model
  proposes (one model call per such turn, shown the KG's own words). The candidate search asks the registries the selected
  KG's own papers came from first, else OpenAlex first (arXiv is no longer first by default).
  - Decision taken: the retry runs for every graph source (JSON and live), once per turn, only after an insufficient first
    verdict; a failed or non-JSON helper answer is ignored and the turn goes on as before.
  - Decision taken: other domain word lists found and **left in place, listed for the owner**: `GENERAL_CATEGORY_ALIASES`
    and `GENERAL_RELATION_ALIASES` in `app/modules/term_extractor/schema.py` (materials-science class and relation aliases
    used to map a model's category onto the shipped MatKG schema; inert for a user schema without those classes, and part of
    the frozen shipped-schema behaviour); the RSoXS/ops wording in the chat judge prompts (`prompts.py`, `retrieval_agent.py`:
    NRSS, CyRSoXS, Nika, P-RSoXS, 11.0.1.2, YBCO) which belongs to the shipped RSoXS corpus and its frozen prompt hashes;
    `_prefer_pdf_urls` host ranking in `download_agent.py` (repositories, not a domain). Removing any of these changes pinned
    behaviour and is the owner's call.
- **Tier 1 A, item 5 (in progress at this entry): passage coverage.** Built in the approved order: short factual clauses,
  OCR-tolerant matching of quoted sentences (the page's own text is stored), the `table_row` evidence kind (row plus column
  header, page header stripped, shown as **table row** on the card), and pages with no text layer per paper in the recovery
  result and the extraction log.
  - Decision taken: a table row counts as `located` in the coverage counts (no new counter, so the frozen counter shapes do
    not move); the snippet itself carries `kind: table_row`.
  - Decision taken: no OCR pass for scanned pages (it would need the Docling OCR models in the image and that was not checked);
    the count is reported instead.
  - Decision taken: relation evidence over table rows is not built; the relation rules are unchanged.
- **Tier 1 B (in progress at this entry): passages plan leftovers.** The deterministic suite test with the 90/50/80 thresholds
  (`tests/extraction/test_passage_suite_thresholds.py`); the recovery job's searched-and-empty record (`recovery_state.json`
  per bundle, keyed by the paper's sha256 and the rules' revision, `searches_skipped` reported; `RECOVERY_REVISION` bumped to 2
  so pairs the old rules found empty are searched again under the new ones); the design page statuses. The per-paper note for
  continued KGs needs no code: the fork copies the parent's `passages` block, so the note appears only for a paper with no
  coverage entry (covered by the existing route test). The live 200 KB Tiled write and the `read_state` timing ran locally (throwaway
  Tiled 0.2.18): the 200 KB write 19 ms; `read_state` over 101 nodes (one of 200 KB, 100 of 3 KB) 29 ms; after 100 more
  nodes of 20 KB each 62 ms; the double read 128 ms. Recorded on the design page.
  - Decision taken: "80" in the owner's 90/50/80 is read as the share of stored passages that are the model's quoted sentence
    verified in the paper (the rest located); the test also requires every stored passage to be found in the page it names.
- **Tier 1 C (in progress at this entry):** the Harvester's "Schema (label only)" is now "Schema to record with the run" with
  help text that says what it does next to "Create or edit a schema".
- **Tier 2 D (in progress at this entry): the UI contract sweep.** `tests/api_v1/test_ui_contracts.py` drives the real `:local`
  app (generated PDFs, a user schema, two bundles, a harvest run under its real job id, finished passages and repair jobs, a
  staged publication) through every `/v1` request the UI makes and records the real answers in
  `ui/src/app/components/__fixtures__/ui_contracts.json` (written with `F2W_WRITE_FIXTURES=1`, compared by shape otherwise);
  `ui/src/app/components/uiContracts.test.ts` feeds each pinned answer to the UI's own parser and fails on a default fallback.
  The non-`/v1` routes (`/chat`, `/graph`, `/settings`, `/sessions`) stay pinned byte for byte by `tests/contracts/legacy_ui`.
  Mismatches found by the sweep: none left between the UI's parsers and the real answers after this week's fixes. Two
  findings of the inventory, not bugs: the UI never requests `GET /v1/publications` or `GET /v1/operations` (only the
  `/v1/operations/*` sub-routes), so those two are not pinned; `POST .../candidates/{id}/undo` on a candidate nobody filed by
  hand is a 409 (the UI only offers Undo after a filing). The Ollama listing and the operations routes keep their own fakes.
- **Tier 2 G: generalisation check** done (13 model calls, cap 24, `lbl/cborg-chat`): battery electrolytes, zebrafish
  development, synchrotron beamline instrumentation; table on the schema-generation design page. No swaps were needed in any
  domain, attributes 9 / 12 / 42, located passage coverage 50 / 71 / 66%.
- **Tier 2 F: security review** written (`security-review-2026-11.md`) with three fixes in code: aliases bounded (80 chars, 20
  per term), attribute values capped (500 chars), table-row passages capped (600 chars), and the cancel set of the schema
  generator bounded (1,000 ids). One open point for the owner (`note_error` text may name a provider base URL).
- **Tier 2 E (planned, not started at this entry): real-backend browser smoke suite.** It will extend the T18C harness
  (`tests/contracts/legacy_ui/closure/launch_full_backend.py`, `tests/e2e/live_backend/full/`, kept outside `ui/` so the
  frozen UI tree is untouched): a launcher variant that mounts a generated library (`F2W_LIBRARY_DIR`), a workdir, stubs the
  extraction model (`Orchestrator._build_llm`, the scripted tool-calling model of the threshold suite), the schema
  generator's client (a canned valid YAML) and the model listing, then `*.full.ts` specs for: start and watch an extraction,
  the coverage block, Find supporting text, a node card with passages and relation evidence, the schema panel end to end
  (generate, validate, save, preview), a harvest run with the Needs-a-PDF list and a dropped PDF, export and download, and
  the chat turns that got stuck. Off the push path; a `[ci full]` job with Chrome and the Tiled interpreter.
- **Tier 3 (design only):** `design-tier3-2026-11.md` holds H (numeric and aggregate questions), I (second pass for relations
  without evidence), J (bioRxiv through OpenAlex: source ids looked up), M (the rest of section 7 of the standalone plan),
  each with its questions; K (extraction speed: profiled, 0.39 s of non-model work for six pages; the model loop at about
  7 requests per page is the whole cost; five levers listed) and L (RSoXS rebuild from the tracked terms file: 29,561 nodes
  against the shipped 7,022, the shipped graph came from an earlier, smaller terms file; recommendation: do not swap, a
  trimmed rebuild would be a new release through the release contract). Neither used a model call; the shipped KG is
  unchanged.
- **Tier 4 N:** the hard-coded placeholder `TILED_API_KEY` in `scripts/start_agent_backend.sh` is gone (a random key per
  start when unset, never printed; docs updated). Five clean worktrees whose commits are on `fair2wise/forge_merge` were
  removed (`f2w-wt-mpdf`, `f2w-wt-passages`, `f2w-wt-schema`, `f2w-wt-schema-ui`, `f2w-wt-snip-old`); kept: `f2w-wt-design`
  (`linkml_design`, a design page commit not on forge_merge), `f2w-wt-snip-new` (`snippets_wip` lineage, kept unpushed by
  the owner's instruction) and an agent's locked worktree. `GET /v1/jobs/{id}/stream` (V05) is **not** retired: the UI does
  not call it, but it is part of the frozen `/v1` contract pinned by the route tables, the packaging consumer contract and
  the live security tests; retiring it is the owner's call. There is no Harvester stream route to retire (the Harvester
  polls the job logs).
