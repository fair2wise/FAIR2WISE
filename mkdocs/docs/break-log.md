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
  coverage entry (covered by the existing route test). The live 200 KB Tiled write and the `read_state` timing run locally once
  the gate is idle (script `tiled_big_property.py` in the session scratchpad; numbers below when done).
  - Decision taken: "80" in the owner's 90/50/80 is read as the share of stored passages that are the model's quoted sentence
    verified in the paper (the rest located); the test also requires every stored passage to be found in the page it names.
- **Tier 1 C (in progress at this entry):** the Harvester's "Schema (label only)" is now "Schema to record with the run" with
  help text that says what it does next to "Create or edit a schema".
