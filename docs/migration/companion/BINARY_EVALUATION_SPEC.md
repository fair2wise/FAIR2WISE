# Required binary evaluation contract

**Draft v8. Required by the owner. Proposed test files/cases are not executed tests.** T11 owns the extraction and chat pass/fail suite. Gold bundles, held-out examples and diagnostic metrics support it; they are not a competing acceptance mechanism.

## 1. Case definition

Each case records `case_id`, `suite`, `required`, `case_revision`, `source_refs`, `schema_ref`, `input`, `expected_rules`, `forbidden_outcomes`, `assertion_method`, and `owner_approval_ref`. Required membership is frozen for a reviewed gate. Expected rules are source-backed or explicit F2W policy, not invented after observing candidate output.

Extraction cases assert expected records/values/units/source locations, absent-information handling and approved classes/relations. Chat cases assert existing F2W evidence rules, citations, active-paper scope, numerical-claim support, conceptual leeway and explicit fallback/approval behavior. No forced refusal-only or unrestricted general-knowledge mode is introduced.

## 2. Binary verdict and execution are separate

For a completed valid case, `verdict` is exactly PASS or FAIL. Store `execution_status` separately: completed, errored, timed_out, skipped or not_run. Noncompleted cases have no scientific verdict. A missing verdict for a required case makes gate acceptance NOT_PASS; it must never be omitted from the denominator to make a green report.

A gate passes only when the expected required-case manifest matches, every required case completed, every required verdict is PASS, and all additional human gates/approvals are satisfied. Optional diagnostic cases never rescue a failure. An agent cannot waive a required case or edit the rubric to hide a defect. Genuine test-specification corrections require owner approval and a new case revision.

Results include actual/expected evidence references, reason codes, timestamps, sanitized errors, source/schema/configuration hashes and runner/judge revisions. Preserve distinction between a scientific FAIL and provider outage so failures can be diagnosed without confusing infrastructure with content quality. JSON and JUnit-compatible reports should expose both.

## 3. Assertion strategy

Prefer deterministic checks for schema, IDs, locator existence, arithmetic, unit conversion, coverage, state, source scope, permissions, settings and serialization. A quote-location check alone does not establish that an answer is supported; semantic entailment/summary completeness needs the owner-approved binary rubric and source evidence. An LLM judge may assist through the configured F2W provider, but a malformed/unresolved judgment is not PASS. The owner reviews ambiguous/disputed labels; evaluation agents cannot approve their own gold.

Do not demand byte-identical prose from live models. Freeze meaningful expected facts, permissible alternative phrasings/units and forbidden claims. Any live repetition policy (number of repetitions and how verdicts aggregate) is declared before running; never rerun until a favorable sample appears. Tests evaluate one source/schema/configuration revision at a time.

## 4. Proposed layout

- `evals/binary/cases/extraction/`, `chat/`, `figures/`, `operations/`: versioned cases and references, no distributed private PDFs.
- `evals/binary/assertions/`: reusable deterministic assertions and controlled semantic rubric adapters.
- `evals/binary/runner.py`, `reports.py`: fixed required manifests, invocation, execution tracking, safe reports.
- `tests/contracts/`, `tests/parity/`, `tests/integration/`, frozen-browser project: existing and added engineering checks.
- `binary_case_inventory.json` in this handoff: initial proposed cases to implement and approve, not proof of existing test coverage.

Executable command names should be finalized in the agent's H1/H3 implementation. This handoff does not invent a working command for a runner that has not been built.

## 5. Stage gates

| Gate | Binary evidence |
| --- | --- |
| H0 | Capture existing F2W baseline and pre-existing failures; do not claim prior source inspection ran the suite. |
| H1/H2 | Freeze interfaces and model/disclosure/operation contracts; first unchanged QA and deterministic-summary wrappers pass fake-model/API/frozen-browser checks. |
| H3 | Owner approves required extraction/chat/figure/operation cases, source truth, schema version, metric/counting definitions and semantic rubric. |
| H4 | Integrated text/figure/table/operation candidate pilot passes all reviewed required cases on app-provisioned real Tiled plus isolated source artifacts. |
| H5 | Additional scoped publication, owner-permission, interruption, replay and rollback cases pass before target application approval. |
| H6 | Repeat mandatory suite with installed F2W, FORGE agents, FastAPI, frozen UI and both Tiled modes; no public source-data leakage. |
| H7 | Canary-specific cases and approved retirement checks pass; prior gates and rollback evidence remain valid. |

## 6. Model choice and data handling

Use the frontend-selected F2W model/provider and existing classes; headless evaluation resolves the same persisted settings/caller configuration. The owner notes free CBORG models are available; this plan pins no model price or catalog and imposes no paid-model requirement. Respect all configured/provider concurrency and rate limits; no bypass for judges, retries or tests. Fake inference is default CI; live semantic runs are explicit and source-authorized.

Private source PDFs, crops, raw prompts and full tool bodies are not public reports. Report IDs, locators, derived findings and sanitized failure reasons; Relevant exact cited quotations are permitted; test their preservation as well as full-source leakage prevention. A reviewer can consult their independently authorized source copy.

Diagnostic extraction recall, error rates and performance curves are retained where useful. They help explain why a case failed and prioritize work; they do not replace PASS/FAIL or quietly introduce a threshold-based substitute for the user's suite.

## Draft v8 integration requirements

Versioned JSON remains the KG creation artifact; Tiled 0.2.19 consumes it through the separate adapter in JSON_KG_PUBLICATION_SPEC.md. Preserve current scientific schema logic and inheritance; the measurement/technique split is not an acceptance requirement. Relevant exact cited quotations are allowed while complete source papers and original crops remain internal. Any earlier conditional wording about O01/O02/O04 in source-derived commentary is superseded by OWNER_CLARIFICATIONS_V8.md; source observations themselves remain unchanged. O03 remains pending alongside the deployment choices in OWNER_CLARIFICATIONS_V8.md, with donor behavior documented in FORGE_JOB_SETTINGS_AUDIT.md.

## Versioned JSON, exact quotes and job settings

JSON-01 through JSON-10 cover artifact-first creation, immutable versions, inference-free import, scoped replay/receipts, schema/hash verification, field/evidence retention and edit reconciliation. QUOTE-01/02 cover permitted exact citations and misquotation rejection. SCH-05 asserts preserved current inheritance, not a new technique/measurement split. TIL-07 checks the owner-selected 0.2.19 build. LLM-06/07 remain draft expectations pending O03; the owner approves the settings rule before they become required oracles.

The current proposed-case count is recorded in VALIDATION_REPORT.json and binary_case_inventory.json. None is an executed passing result. Required case membership and detailed source/rubric fixtures still require H3 approval.

## Deployment acceptance added in v6

DEP-01 onward define binary checks for all three image variants, supervised local Tiled, no managed server in ALS images, environment targets, /fair2wise assets/API/deep links/OpenAPI/SSE, trusted proxies, secret/corpus exclusion across layers, mounted persistence/restore, admin application-scope guard, truthful actor audit, protected digest promotion and JSON-first environment-bound import. These are not executed tests. D30/D34 editing cases require no-login full access locally and verified Tiled admin access with non-admin denial on ALS; no stale O05 ambiguity remains. A Docker build is not a passing browser integration, a mounted directory is not backup evidence, and a successful admin API call is not proof of application authorization.

## 9. New v7 role and Git-artifact cases

AUTH-01 onward retain ALS sign-in, environment-bound identity/admin status, no writer-account escalation, denied ordinary-user effects, audit/readback and revocation checks. AUTH-09 now tests prefixed ALS sign-in rather than requiring local login. LOCAL-01 onward prove all local application capabilities without login, truthful local-operator approvals/audit, no per-user identity dependency, loopback-default exposure, and rejection of trusted-mode requests/configuration on ALS. REL-01 onward retain exact-byte Git promotion, target-specific approval/receipts and coordinated FORGE merge behavior. These are proposed tests; no application execution is claimed.

T25A/T26A join H1; T25B joins H2; T25C local/ALS profile cases and T26B join H4; T26C joins H5 and repeats at H6. The owner confirms the requirements, but binary case execution remains not_run with no verdict until the implementing agent runs it. Existing case membership cannot be silently weakened to resolve incompatibility.
