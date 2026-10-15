# Binary evaluation report

**Gate status: NOT_PASS**

- Required cases: 108; blocking the gate: 108
- Gate reasons: human_gate_not_approved; human gates: H3=not_approved
- Manifest: `t11a-1` sha256 `66c64efaea2cfccb`; runner `t11a-runner-1`
- Source: commit `944f42048123264676bc2d70b953674c7e81788d`, app tree `56009e41c7402a5d01bbaaf95a8888aaf7c2ea96`, dirty tracked files: False
- Model: fake_or_none (live model used: False)
- Repeat policy: 1 run(s), all_runs_must_pass, retry_on_failure=False
- Started 2026-10-14T23:05:54Z, finished 2026-10-14T23:07:47Z

A verdict exists only for a completed case. Skipped, blocked, timed-out, errored, missing-fixture and judge-invalid cases have no verdict and are neither passes nor scientific failures. No score is computed.

## Counts by execution state

| execution_state | cases |
| --- | ---: |
| completed | 44 |
| timeout | 0 |
| skipped | 8 |
| missing_fixture | 0 |
| blocked | 56 |
| error | 0 |
| judge_invalid | 0 |

Verdicts: {'PASS': 44, 'none': 64}

## Counts by family

| family | execution states |
| --- | --- |
| AUTH | blocked=1, completed=10 |
| DEP | blocked=19, completed=2, skipped=1 |
| EXT | blocked=3, completed=2 |
| FIG | blocked=2, completed=2 |
| JSON | blocked=9, completed=1 |
| LLM | blocked=4, completed=4 |
| LOCAL | blocked=1, completed=8, skipped=1 |
| OPS | blocked=1, completed=3, skipped=1 |
| QA | blocked=3, completed=2 |
| QUOTE | completed=2 |
| REL | blocked=9 |
| SCH | blocked=2, completed=3 |
| SEC | completed=5 |
| TIL | blocked=2, skipped=5 |

## Every case

| case | verdict | execution_state | readiness | coverage | approval | actual / reason |
| --- | --- | --- | --- | --- | --- | --- |
| AUTH-01 | PASS | completed | executable | full | proposed | 45 oracle test(s) passed |
| AUTH-02 | PASS | completed | executable | full | proposed | 16 oracle test(s) passed |
| AUTH-03 | PASS | completed | executable | full | proposed | 19 oracle test(s) passed |
| AUTH-04 | PASS | completed | executable | partial | proposed | 18 oracle test(s) passed |
| AUTH-05 | PASS | completed | executable | partial | proposed | 23 oracle test(s) passed |
| AUTH-06 | PASS | completed | executable | full | proposed | 5 oracle test(s) passed |
| AUTH-07 | PASS | completed | executable | full | proposed | 13 oracle test(s) passed |
| AUTH-08 | PASS | completed | executable | partial | proposed | 7 oracle test(s) passed |
| AUTH-09 | - | blocked | blocked | none | proposed | blocked by T24C (prefix-correct gateway and existing UI/API deployment); T25C (no-login local workflows and real ALS Tiled role enforcement) |
| AUTH-10 | PASS | completed | executable | partial | proposed | 3 oracle test(s) passed |
| AUTH-11 | PASS | completed | executable | full | proposed | 3 oracle test(s) passed |
| DEP-01 | - | blocked | blocked | none | proposed | blocked by T24B (build shared runtime and the three image finals); T24D (GitHub Actions build/publication and protected tags) |
| DEP-02 | - | blocked | blocked | none | proposed | blocked by T24B (build shared runtime and the three image finals) |
| DEP-03 | - | skipped | executable | partial | proposed | 1 oracle test(s) skipped, 6 passed: tests/tiled/test_tiled_server_lifecycle.py::test_external_outage_never_starts_a_server: F2W_TILED_PYTHON is not set (needs tiled 0.2.18) |
| DEP-04 | PASS | completed | executable | partial | proposed | 15 oracle test(s) passed |
| DEP-05 | - | blocked | blocked | none | proposed | blocked by T24C (prefix-correct gateway and existing UI/API deployment); T24B (build shared runtime and the three image finals) |
| DEP-06 | - | blocked | blocked | none | proposed | blocked by T24C (prefix-correct gateway and existing UI/API deployment) |
| DEP-07 | - | blocked | blocked | none | proposed | blocked by T24C (prefix-correct gateway and existing UI/API deployment) |
| DEP-08 | - | blocked | blocked | none | proposed | blocked by T24C (prefix-correct gateway and existing UI/API deployment); T24E (three-profile container, Ansible and restore integration) |
| DEP-09 | - | blocked | blocked | none | proposed | blocked by T24B (build shared runtime and the three image finals); T24D (GitHub Actions build/publication and protected tags) |
| DEP-10 | - | blocked | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-11 | - | blocked | blocked | none | proposed | blocked by T08B (Tiled importer over saved JSON, receipts, writes) |
| DEP-12 | PASS | completed | executable | partial | proposed | 6 oracle test(s) passed |
| DEP-13 | - | blocked | blocked | none | proposed | blocked by T23B (immutable JSON snapshot service); T08B (Tiled importer over saved JSON, receipts, writes) |
| DEP-14 | - | blocked | blocked | none | proposed | blocked by T08B (Tiled importer over saved JSON, receipts, writes) |
| DEP-15 | - | blocked | blocked | none | proposed | blocked by T24D (GitHub Actions build/publication and protected tags) |
| DEP-16 | - | blocked | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-17 | - | blocked | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-18 | - | blocked | blocked | none | proposed | blocked by T10 (durable jobs and curation); T24E (three-profile container, Ansible and restore integration) |
| DEP-19 | - | blocked | blocked | none | proposed | blocked by T25C (no-login local workflows and real ALS Tiled role enforcement); T18C (full frozen-frontend parity on actual Tiled); T14 (FastAPI plus CLI/UI compatibility and job UX) |
| DEP-20 | - | blocked | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-21 | - | blocked | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration); T13 (publication and rollback rehearsal) |
| DEP-22 | - | blocked | blocked | none | proposed | blocked by T24C (prefix-correct gateway and existing UI/API deployment) |
| EXT-01 | - | blocked | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases) |
| EXT-02 | - | blocked | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases) |
| EXT-03 | - | blocked | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases) |
| EXT-04 | PASS | completed | executable | partial | proposed | 6 oracle test(s) passed |
| EXT-05 | PASS | completed | executable | partial | proposed | 4 oracle test(s) passed |
| FIG-01 | - | blocked | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth) |
| FIG-02 | - | blocked | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth) |
| FIG-03 | PASS | completed | executable | partial | proposed | 9 oracle test(s) passed |
| FIG-04 | PASS | completed | executable | full | proposed | 16 oracle test(s) passed |
| JSON-01 | - | blocked | blocked | none | proposed | blocked by T23B (immutable JSON snapshot service) |
| JSON-02 | PASS | completed | executable | full | proposed | 57 oracle test(s) passed |
| JSON-03 | - | blocked | blocked | none | proposed | blocked by T23B (immutable JSON snapshot service) |
| JSON-04 | - | blocked | blocked | none | proposed | blocked by T08B (Tiled importer over saved JSON, receipts, writes) |
| JSON-05 | - | blocked | blocked | none | proposed | blocked by T08B (Tiled importer over saved JSON, receipts, writes) |
| JSON-06 | - | blocked | blocked | none | proposed | blocked by T08B (Tiled importer over saved JSON, receipts, writes); T26B (committed artifact validation and export) |
| JSON-07 | - | blocked | blocked | none | proposed | blocked by T23B (immutable JSON snapshot service); T08B (Tiled importer over saved JSON, receipts, writes) |
| JSON-08 | - | blocked | blocked | none | proposed | blocked by T08B (Tiled importer over saved JSON, receipts, writes) |
| JSON-09 | - | blocked | blocked | none | proposed | blocked by T08B (Tiled importer over saved JSON, receipts, writes); T10 (durable jobs and curation) |
| JSON-10 | - | blocked | blocked | none | proposed | blocked by T08B (Tiled importer over saved JSON, receipts, writes); T10 (durable jobs and curation); T23B (immutable JSON snapshot service) |
| LLM-01 | PASS | completed | executable | partial | proposed | 4 oracle test(s) passed |
| LLM-02 | PASS | completed | executable | full | proposed | 7 oracle test(s) passed |
| LLM-03 | PASS | completed | executable | partial | proposed | 8 oracle test(s) passed |
| LLM-04 | PASS | completed | executable | partial | proposed | 7 oracle test(s) passed |
| LLM-05 | - | blocked | blocked | none | proposed | blocked by T10 (durable jobs and curation); O03 (owner approval of the job-settings rule) |
| LLM-06 | - | blocked | blocked | none | proposed | blocked by T10 (durable jobs and curation); O03 (owner approval of the job-settings rule) |
| LLM-07 | - | blocked | blocked | none | proposed | blocked by T10 (durable jobs and curation); O03 (owner approval of the job-settings rule) |
| LLM-08 | - | blocked | blocked | none | proposed | blocked by T10 (durable jobs and curation) |
| LOCAL-01 | PASS | completed | executable | partial | proposed | 6 oracle test(s) passed |
| LOCAL-02 | - | blocked | blocked | none | proposed | blocked by T25C (no-login local workflows and real ALS Tiled role enforcement); T08B (Tiled importer over saved JSON, receipts, writes); T10 (durable jobs and curation) |
| LOCAL-03 | PASS | completed | executable | full | proposed | 6 oracle test(s) passed |
| LOCAL-04 | PASS | completed | executable | partial | proposed | 6 oracle test(s) passed |
| LOCAL-05 | PASS | completed | executable | partial | proposed | 10 oracle test(s) passed |
| LOCAL-06 | - | skipped | executable | partial | proposed | 1 oracle test(s) skipped, 20 passed: tests/graph/test_graph_tiled_server.py::test_wrong_credential_is_denied_and_stopped_server_is_unavailable: F2W_TILED_PYTHON is not set (needs tiled 0.2.18) |
| LOCAL-07 | PASS | completed | executable | full | proposed | 10 oracle test(s) passed |
| LOCAL-08 | PASS | completed | executable | full | proposed | 2 oracle test(s) passed |
| LOCAL-09 | PASS | completed | executable | partial | proposed | 6 oracle test(s) passed |
| LOCAL-10 | PASS | completed | executable | partial | proposed | 4 oracle test(s) passed |
| OPS-01 | PASS | completed | executable | partial | proposed | 13 oracle test(s) passed |
| OPS-02 | - | blocked | blocked | none | proposed | blocked by H3 (owner definition of how repeated Work references are deduplicated); T08B (Tiled importer over saved JSON, receipts, writes) |
| OPS-03 | - | skipped | executable | full | proposed | 1 oracle test(s) skipped, 3 passed: tests/graph/test_graph_tiled_server.py::test_partial_scan_is_flagged_not_absent: F2W_TILED_PYTHON is not set (needs tiled 0.2.18) |
| OPS-04 | PASS | completed | executable | partial | proposed | 14 oracle test(s) passed |
| OPS-05 | PASS | completed | executable | full | proposed | 78 oracle test(s) passed |
| QA-01 | - | blocked | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases) |
| QA-02 | PASS | completed | executable | partial | proposed | 6 oracle test(s) passed |
| QA-03 | - | blocked | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases) |
| QA-04 | - | blocked | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases) |
| QA-05 | PASS | completed | executable | partial | proposed | 6 oracle test(s) passed |
| QUOTE-01 | PASS | completed | executable | full | proposed | 22 oracle test(s) passed |
| QUOTE-02 | PASS | completed | executable | full | proposed | 12 oracle test(s) passed |
| REL-01 | - | blocked | blocked | none | proposed | blocked by T26B (committed artifact validation and export) |
| REL-02 | - | blocked | blocked | none | proposed | blocked by T26B (committed artifact validation and export); T08B (Tiled importer over saved JSON, receipts, writes) |
| REL-03 | - | blocked | blocked | none | proposed | blocked by T26B (committed artifact validation and export); T08B (Tiled importer over saved JSON, receipts, writes) |
| REL-04 | - | blocked | blocked | none | proposed | blocked by T26B (committed artifact validation and export) |
| REL-05 | - | blocked | blocked | none | proposed | blocked by T24B (build shared runtime and the three image finals); T24D (GitHub Actions build/publication and protected tags) |
| REL-06 | - | blocked | blocked | none | proposed | blocked by T26B (committed artifact validation and export) |
| REL-07 | - | blocked | blocked | none | proposed | blocked by T10 (durable jobs and curation); T26B (committed artifact validation and export); T23B (immutable JSON snapshot service) |
| REL-08 | - | blocked | blocked | none | proposed | blocked by T26C (exact Git snapshot promotion and rollback rehearsal) |
| REL-09 | - | blocked | blocked | none | proposed | blocked by T15 (FORGE imports F2W agents) |
| SCH-01 | PASS | completed | executable | partial | proposed | 5 oracle test(s) passed |
| SCH-02 | PASS | completed | executable | full | proposed | 3 oracle test(s) passed |
| SCH-03 | - | blocked | blocked | none | proposed | blocked by T22B (schema registry, fingerprint and revision binding) |
| SCH-04 | - | blocked | blocked | none | proposed | blocked by T22B (schema registry, fingerprint and revision binding); T10 (durable jobs and curation) |
| SCH-05 | PASS | completed | executable | partial | proposed | 2 oracle test(s) passed |
| SEC-01 | PASS | completed | executable | full | proposed | 32 oracle test(s) passed |
| SEC-02 | PASS | completed | executable | full | proposed | 12 oracle test(s) passed |
| SEC-03 | PASS | completed | executable | full | proposed | 27 oracle test(s) passed |
| SEC-04 | PASS | completed | executable | full | proposed | 14 oracle test(s) passed |
| SEC-05 | PASS | completed | executable | partial | proposed | 33 oracle test(s) passed |
| TIL-01 | - | skipped | executable | full | proposed | 4 oracle test(s) skipped, 2 passed: tests/tiled/test_tiled_server_lifecycle.py::test_stop_retains_data_and_restart_reads_it_back: F2W_TILED_PYTHON is not set (needs tiled 0.2.18); tests/tiled/test_tiled_server_lifecyc... |
| TIL-02 | - | skipped | executable | full | proposed | 2 oracle test(s) skipped, 6 passed: tests/tiled/test_tiled_server_lifecycle.py::test_external_outage_never_starts_a_server: F2W_TILED_PYTHON is not set (needs tiled 0.2.18); tests/tiled/test_tiled_server_lifecycle.py:... |
| TIL-03 | - | skipped | executable | partial | proposed | 3 oracle test(s) skipped, 16 passed: tests/tiled/test_tiled_capabilities_0218.py::test_acl_variant_tag_policy_scopes_graph_reads_and_writes: F2W_TILED_PYTHON is not set (needs tiled 0.2.18); tests/tiled/test_tiled_cap... |
| TIL-04 | - | skipped | executable | partial | proposed | 2 oracle test(s) skipped, 0 passed: tests/tiled/test_tiled_capabilities_0218.py::test_acl_variant_tag_policy_scopes_graph_reads_and_writes: F2W_TILED_PYTHON is not set (needs tiled 0.2.18); tests/tiled/test_tiled_capa... |
| TIL-05 | - | blocked | blocked | none | proposed | blocked by T08B (Tiled importer over saved JSON, receipts, writes); T14 (FastAPI plus CLI/UI compatibility and job UX) |
| TIL-06 | - | blocked | blocked | none | proposed | blocked by T13 (publication and rollback rehearsal); T08B (Tiled importer over saved JSON, receipts, writes) |
| TIL-07 | - | skipped | executable | full | proposed | 11 oracle test(s) skipped, 3 passed: tests/tiled/test_tiled_capabilities_0218.py::test_access_blobs_not_access_tags_and_node_id_not_is_node_bound: F2W_TILED_PYTHON is not set (needs tiled 0.2.18); tests/tiled/test_til... |

## Gate blockers

- AUTH-01: owner_approval:proposed
- AUTH-02: owner_approval:proposed
- AUTH-03: owner_approval:proposed
- AUTH-04: owner_approval:proposed, oracle_coverage:partial
- AUTH-05: owner_approval:proposed, oracle_coverage:partial
- AUTH-06: owner_approval:proposed
- AUTH-07: owner_approval:proposed
- AUTH-08: owner_approval:proposed, oracle_coverage:partial
- AUTH-09: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- AUTH-10: owner_approval:proposed, oracle_coverage:partial
- AUTH-11: owner_approval:proposed
- DEP-01: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-02: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-03: execution_state:skipped, owner_approval:proposed, oracle_coverage:partial
- DEP-04: owner_approval:proposed, oracle_coverage:partial
- DEP-05: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-06: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-07: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-08: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-09: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-10: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-11: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-12: owner_approval:proposed, oracle_coverage:partial
- DEP-13: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-14: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-15: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-16: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-17: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-18: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-19: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-20: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-21: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-22: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- EXT-01: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- EXT-02: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- EXT-03: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- EXT-04: owner_approval:proposed, oracle_coverage:partial
- EXT-05: owner_approval:proposed, oracle_coverage:partial
- FIG-01: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- FIG-02: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- FIG-03: owner_approval:proposed, oracle_coverage:partial
- FIG-04: owner_approval:proposed
- JSON-01: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- JSON-02: owner_approval:proposed
- JSON-03: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- JSON-04: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- JSON-05: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- JSON-06: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- JSON-07: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- JSON-08: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- JSON-09: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- JSON-10: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- LLM-01: owner_approval:proposed, oracle_coverage:partial
- LLM-02: owner_approval:proposed
- LLM-03: owner_approval:proposed, oracle_coverage:partial
- LLM-04: owner_approval:proposed, oracle_coverage:partial
- LLM-05: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- LLM-06: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- LLM-07: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- LLM-08: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- LOCAL-01: owner_approval:proposed, oracle_coverage:partial
- LOCAL-02: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- LOCAL-03: owner_approval:proposed
- LOCAL-04: owner_approval:proposed, oracle_coverage:partial
- LOCAL-05: owner_approval:proposed, oracle_coverage:partial
- LOCAL-06: execution_state:skipped, owner_approval:proposed, oracle_coverage:partial
- LOCAL-07: owner_approval:proposed
- LOCAL-08: owner_approval:proposed
- LOCAL-09: owner_approval:proposed, oracle_coverage:partial
- LOCAL-10: owner_approval:proposed, oracle_coverage:partial
- OPS-01: owner_approval:proposed, oracle_coverage:partial
- OPS-02: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- OPS-03: execution_state:skipped, owner_approval:proposed
- OPS-04: owner_approval:proposed, oracle_coverage:partial
- OPS-05: owner_approval:proposed
- QA-01: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- QA-02: owner_approval:proposed, oracle_coverage:partial
- QA-03: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- QA-04: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- QA-05: owner_approval:proposed, oracle_coverage:partial
- QUOTE-01: owner_approval:proposed
- QUOTE-02: owner_approval:proposed
- REL-01: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- REL-02: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- REL-03: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- REL-04: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- REL-05: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- REL-06: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- REL-07: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- REL-08: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- REL-09: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- SCH-01: owner_approval:proposed, oracle_coverage:partial
- SCH-02: owner_approval:proposed
- SCH-03: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- SCH-04: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- SCH-05: owner_approval:proposed, oracle_coverage:partial
- SEC-01: owner_approval:proposed
- SEC-02: owner_approval:proposed
- SEC-03: owner_approval:proposed
- SEC-04: owner_approval:proposed
- SEC-05: owner_approval:proposed, oracle_coverage:partial
- TIL-01: execution_state:skipped, owner_approval:proposed
- TIL-02: execution_state:skipped, owner_approval:proposed
- TIL-03: execution_state:skipped, owner_approval:proposed, oracle_coverage:partial
- TIL-04: execution_state:skipped, owner_approval:proposed, oracle_coverage:partial
- TIL-05: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- TIL-06: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- TIL-07: execution_state:skipped, owner_approval:proposed
