# Binary evaluation report

**Gate status: NOT_PASS**

- Required cases: 108; blocking the gate: 68
- Provisional results: True (EXT-04, FIG-03)
- Subject: none (the report is not bound to a KG snapshot)
- Gate reasons: none; human gates: H3=approved
- Manifest: `t11a-1` sha256 `5d387012682b26eb`; runner `t11b-runner-1`
- Source: commit `None`, app tree `None`, dirty tracked files: None
- Model: fake_or_none (live model used: False)
- Judge model: `lbl/cborg-deepthought` (source: cli); model under test: `lbl/cborg-chat`; labelling models: `lbl/gpt-oss-120b`
- Repeat policy: 1 run(s), all_runs_must_pass, retry_on_failure=False
- Started 2026-11-06T23:01:14Z, finished 2026-11-06T23:46:45Z

A verdict exists only for a completed case. Skipped, blocked, timed-out, errored, missing-fixture and judge-invalid cases have no verdict and are neither passes nor scientific failures. No score is computed.

## Counts by execution state

| execution_state | cases |
| --- | ---: |
| completed | 79 |
| timeout | 6 |
| skipped | 1 |
| missing_fixture | 0 |
| blocked | 22 |
| error | 0 |
| judge_invalid | 0 |

Verdicts: {'PASS': 79, 'none': 29}

## Counts by family

| family | execution states |
| --- | --- |
| AUTH | completed=11 |
| DEP | blocked=7, completed=14, skipped=1 |
| EXT | blocked=3, completed=2 |
| FIG | blocked=2, completed=2 |
| JSON | completed=10 |
| LLM | blocked=3, completed=5 |
| LOCAL | blocked=1, completed=9 |
| OPS | timeout=5 |
| QA | blocked=3, completed=1, timeout=1 |
| QUOTE | completed=2 |
| REL | blocked=2, completed=7 |
| SCH | completed=5 |
| SEC | completed=5 |
| TIL | blocked=1, completed=6 |

## Pilot labels (H3 B4)

A verdict resting on provisional (unreviewed model) labels is shown as PROVISIONAL and never passes the gate. Missing labels keep the 13 label cases blocked.

| paper | status | labelling models | items | labelling calls complete |
| --- | --- | --- | ---: | --- |
| A | provisional | lbl/gpt-oss-120b | 137 | True |
| B | provisional | lbl/gpt-oss-120b | 112 | False |
| C | provisional | lbl/gpt-oss-120b | 132 | False |

## Every case

| case | verdict | execution_state | labels | readiness | coverage | approval | actual / reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AUTH-01 | PASS | completed | - | executable | full | approved | 45 oracle test(s) passed |
| AUTH-02 | PASS | completed | - | executable | full | approved | 16 oracle test(s) passed |
| AUTH-03 | PASS | completed | - | executable | full | approved | 19 oracle test(s) passed |
| AUTH-04 | PASS | completed | - | executable | partial | approved | 18 oracle test(s) passed |
| AUTH-05 | PASS | completed | - | executable | partial | approved | 23 oracle test(s) passed |
| AUTH-06 | PASS | completed | - | executable | full | approved | 5 oracle test(s) passed |
| AUTH-07 | PASS | completed | - | executable | full | approved | 13 oracle test(s) passed |
| AUTH-08 | PASS | completed | - | executable | partial | approved | 7 oracle test(s) passed |
| AUTH-09 | PASS | completed | - | executable | partial | approved | 13 oracle test(s) passed |
| AUTH-10 | PASS | completed | - | executable | partial | approved | 3 oracle test(s) passed |
| AUTH-11 | PASS | completed | - | executable | full | approved | 3 oracle test(s) passed |
| DEP-01 | - | skipped | - | executable | partial | approved | 1 oracle test(s) skipped, 17 passed: tests/ci/test_release_scripts.py::test_manifest_records_commit_digests_locks_tiled_and_ui_tree: release manifest needs a git checkout |
| DEP-02 | PASS | completed | - | executable | partial | approved | 10 oracle test(s) passed |
| DEP-03 | PASS | completed | - | executable | partial | approved | 7 oracle test(s) passed |
| DEP-04 | PASS | completed | - | executable | partial | approved | 15 oracle test(s) passed |
| DEP-05 | PASS | completed | - | executable | partial | approved | 26 oracle test(s) passed |
| DEP-06 | PASS | completed | - | executable | partial | approved | 17 oracle test(s) passed |
| DEP-07 | PASS | completed | - | executable | partial | approved | 9 oracle test(s) passed |
| DEP-08 | - | blocked | - | blocked | none | approved | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-09 | PASS | completed | - | executable | partial | approved | 40 oracle test(s) passed |
| DEP-10 | - | blocked | - | blocked | none | approved | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-11 | PASS | completed | - | executable | full | approved | 11 oracle test(s) passed |
| DEP-12 | PASS | completed | - | executable | partial | approved | 6 oracle test(s) passed |
| DEP-13 | PASS | completed | - | executable | partial | approved | 12 oracle test(s) passed |
| DEP-14 | PASS | completed | - | executable | full | approved | 8 oracle test(s) passed |
| DEP-15 | PASS | completed | - | executable | partial | approved | 19 oracle test(s) passed |
| DEP-16 | - | blocked | - | blocked | none | approved | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-17 | - | blocked | - | blocked | none | approved | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-18 | - | blocked | - | blocked | none | approved | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-19 | PASS | completed | - | executable | partial | approved | 23 oracle test(s) passed |
| DEP-20 | - | blocked | - | blocked | none | approved | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-21 | - | blocked | - | blocked | none | approved | blocked by T24E (three-profile container, Ansible and restore integration); T13 (publication and rollback rehearsal) |
| DEP-22 | PASS | completed | - | executable | full | approved | 3 oracle test(s) passed |
| EXT-01 | - | blocked | provisional | needs_h3_rubric | none | approved | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| EXT-02 | - | blocked | provisional | needs_h3_rubric | none | approved | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| EXT-03 | - | blocked | provisional | needs_h3_rubric | none | approved | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| EXT-04 | PROVISIONAL | completed | provisional | executable | partial | approved | 6 oracle test(s) passed |
| EXT-05 | PASS | completed | - | executable | full | approved | 8 oracle test(s) passed |
| FIG-01 | - | blocked | provisional | needs_h3_rubric | none | approved | blocked by H3 (owner-approved gold fixtures and source truth); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -m evals.binary.label) |
| FIG-02 | - | blocked | provisional | needs_h3_rubric | none | approved | blocked by H3 (owner-approved gold fixtures and source truth); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -m evals.binary.label) |
| FIG-03 | PROVISIONAL | completed | provisional | executable | partial | approved | 9 oracle test(s) passed |
| FIG-04 | PASS | completed | - | executable | full | approved | 16 oracle test(s) passed |
| JSON-01 | PASS | completed | - | executable | full | approved | 13 oracle test(s) passed |
| JSON-02 | PASS | completed | - | executable | full | approved | 63 oracle test(s) passed |
| JSON-03 | PASS | completed | - | executable | full | approved | 12 oracle test(s) passed |
| JSON-04 | PASS | completed | - | executable | full | approved | 5 oracle test(s) passed |
| JSON-05 | PASS | completed | - | executable | full | approved | 32 oracle test(s) passed |
| JSON-06 | PASS | completed | - | executable | full | approved | 7 oracle test(s) passed |
| JSON-07 | PASS | completed | - | executable | full | approved | 12 oracle test(s) passed |
| JSON-08 | PASS | completed | - | executable | full | approved | 12 oracle test(s) passed |
| JSON-09 | PASS | completed | - | executable | full | approved | 27 oracle test(s) passed |
| JSON-10 | PASS | completed | - | executable | full | approved | 8 oracle test(s) passed |
| LLM-01 | PASS | completed | - | executable | partial | approved | 6 oracle test(s) passed |
| LLM-02 | PASS | completed | - | executable | full | approved | 7 oracle test(s) passed |
| LLM-03 | PASS | completed | - | executable | partial | approved | 8 oracle test(s) passed |
| LLM-04 | PASS | completed | - | executable | partial | approved | 7 oracle test(s) passed |
| LLM-05 | - | blocked | - | blocked | none | approved | blocked by O03 (owner approval of the job-settings rule) |
| LLM-06 | - | blocked | - | blocked | none | approved | blocked by O03 (owner approval of the job-settings rule) |
| LLM-07 | - | blocked | - | blocked | none | approved | blocked by O03 (owner approval of the job-settings rule) |
| LLM-08 | PASS | completed | - | executable | partial | approved | 10 oracle test(s) passed |
| LOCAL-01 | PASS | completed | - | executable | partial | approved | 18 oracle test(s) passed |
| LOCAL-02 | - | blocked | provisional | blocked | none | approved | blocked by H3 (owner-approved gold fixtures and source truth); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -m evals.binary.label) |
| LOCAL-03 | PASS | completed | - | executable | full | approved | 6 oracle test(s) passed |
| LOCAL-04 | PASS | completed | - | executable | partial | approved | 6 oracle test(s) passed |
| LOCAL-05 | PASS | completed | - | executable | partial | approved | 10 oracle test(s) passed |
| LOCAL-06 | PASS | completed | - | executable | full | approved | 26 oracle test(s) passed |
| LOCAL-07 | PASS | completed | - | executable | full | approved | 10 oracle test(s) passed |
| LOCAL-08 | PASS | completed | - | executable | full | approved | 2 oracle test(s) passed |
| LOCAL-09 | PASS | completed | - | executable | partial | approved | 6 oracle test(s) passed |
| LOCAL-10 | PASS | completed | - | executable | partial | approved | 4 oracle test(s) passed |
| OPS-01 | - | timeout | - | executable | partial | approved | pytest batch timed out |
| OPS-02 | - | timeout | - | executable | partial | approved | pytest batch timed out |
| OPS-03 | - | timeout | - | executable | full | approved | pytest batch timed out |
| OPS-04 | - | timeout | provisional | executable | partial | approved | pytest batch timed out |
| OPS-05 | - | timeout | - | executable | full | approved | pytest batch timed out |
| QA-01 | - | blocked | provisional | needs_h3_rubric | none | approved | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| QA-02 | - | timeout | provisional | executable | partial | approved | pytest batch timed out |
| QA-03 | - | blocked | provisional | needs_h3_rubric | none | approved | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| QA-04 | - | blocked | provisional | needs_h3_rubric | none | approved | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| QA-05 | PASS | completed | - | executable | partial | approved | 6 oracle test(s) passed |
| QUOTE-01 | PASS | completed | - | executable | full | approved | 22 oracle test(s) passed |
| QUOTE-02 | PASS | completed | - | executable | full | approved | 12 oracle test(s) passed |
| REL-01 | PASS | completed | - | executable | partial | approved | 11 oracle test(s) passed |
| REL-02 | PASS | completed | - | executable | partial | approved | 6 oracle test(s) passed |
| REL-03 | PASS | completed | - | executable | partial | approved | 9 oracle test(s) passed |
| REL-04 | PASS | completed | - | executable | partial | approved | 11 oracle test(s) passed |
| REL-05 | PASS | completed | - | executable | partial | approved | 40 oracle test(s) passed |
| REL-06 | PASS | completed | - | executable | partial | approved | 3 oracle test(s) passed |
| REL-07 | PASS | completed | - | executable | partial | approved | 11 oracle test(s) passed |
| REL-08 | - | blocked | - | blocked | none | approved | blocked by T26C (exact Git snapshot promotion and rollback rehearsal) |
| REL-09 | - | blocked | - | blocked | none | approved | blocked by T15 (FORGE imports F2W agents) |
| SCH-01 | PASS | completed | - | executable | full | approved | 9 oracle test(s) passed |
| SCH-02 | PASS | completed | - | executable | full | approved | 3 oracle test(s) passed |
| SCH-03 | PASS | completed | - | executable | full | approved | 12 oracle test(s) passed |
| SCH-04 | PASS | completed | - | executable | full | approved | 11 oracle test(s) passed |
| SCH-05 | PASS | completed | - | executable | full | approved | 5 oracle test(s) passed |
| SEC-01 | PASS | completed | - | executable | full | approved | 32 oracle test(s) passed |
| SEC-02 | PASS | completed | - | executable | full | approved | 12 oracle test(s) passed |
| SEC-03 | PASS | completed | - | executable | full | approved | 27 oracle test(s) passed |
| SEC-04 | PASS | completed | - | executable | full | approved | 14 oracle test(s) passed |
| SEC-05 | PASS | completed | - | executable | partial | approved | 33 oracle test(s) passed |
| TIL-01 | PASS | completed | - | executable | full | approved | 6 oracle test(s) passed |
| TIL-02 | PASS | completed | - | executable | full | approved | 8 oracle test(s) passed |
| TIL-03 | PASS | completed | - | executable | partial | approved | 19 oracle test(s) passed |
| TIL-04 | PASS | completed | - | executable | partial | approved | 2 oracle test(s) passed |
| TIL-05 | PASS | completed | - | executable | partial | approved | 9 oracle test(s) passed |
| TIL-06 | - | blocked | - | blocked | none | approved | blocked by T13 (publication and rollback rehearsal) |
| TIL-07 | PASS | completed | - | executable | full | approved | 14 oracle test(s) passed |

## Gate blockers

- AUTH-04: oracle_coverage:partial
- AUTH-05: oracle_coverage:partial
- AUTH-08: oracle_coverage:partial
- AUTH-09: oracle_coverage:partial
- AUTH-10: oracle_coverage:partial
- DEP-01: execution_state:skipped, oracle_coverage:partial
- DEP-02: oracle_coverage:partial
- DEP-03: oracle_coverage:partial
- DEP-04: oracle_coverage:partial
- DEP-05: oracle_coverage:partial
- DEP-06: oracle_coverage:partial
- DEP-07: oracle_coverage:partial
- DEP-08: execution_state:blocked, oracle_coverage:none
- DEP-09: oracle_coverage:partial
- DEP-10: execution_state:blocked, oracle_coverage:none
- DEP-12: oracle_coverage:partial
- DEP-13: oracle_coverage:partial
- DEP-15: oracle_coverage:partial
- DEP-16: execution_state:blocked, oracle_coverage:none
- DEP-17: execution_state:blocked, oracle_coverage:none
- DEP-18: execution_state:blocked, oracle_coverage:none
- DEP-19: oracle_coverage:partial
- DEP-20: execution_state:blocked, oracle_coverage:none
- DEP-21: execution_state:blocked, oracle_coverage:none
- EXT-01: execution_state:blocked, pilot_labels:provisional, oracle_coverage:none
- EXT-02: execution_state:blocked, pilot_labels:provisional, oracle_coverage:none
- EXT-03: execution_state:blocked, pilot_labels:provisional, oracle_coverage:none
- EXT-04: verdict:PROVISIONAL, pilot_labels:provisional, oracle_coverage:partial
- FIG-01: execution_state:blocked, pilot_labels:provisional, oracle_coverage:none
- FIG-02: execution_state:blocked, pilot_labels:provisional, oracle_coverage:none
- FIG-03: verdict:PROVISIONAL, pilot_labels:provisional, oracle_coverage:partial
- LLM-01: oracle_coverage:partial
- LLM-03: oracle_coverage:partial
- LLM-04: oracle_coverage:partial
- LLM-05: execution_state:blocked, oracle_coverage:none
- LLM-06: execution_state:blocked, oracle_coverage:none
- LLM-07: execution_state:blocked, oracle_coverage:none
- LLM-08: oracle_coverage:partial
- LOCAL-01: oracle_coverage:partial
- LOCAL-02: execution_state:blocked, pilot_labels:provisional, oracle_coverage:none
- LOCAL-04: oracle_coverage:partial
- LOCAL-05: oracle_coverage:partial
- LOCAL-09: oracle_coverage:partial
- LOCAL-10: oracle_coverage:partial
- OPS-01: execution_state:timeout, oracle_coverage:partial
- OPS-02: execution_state:timeout, oracle_coverage:partial
- OPS-03: execution_state:timeout
- OPS-04: execution_state:timeout, pilot_labels:provisional, oracle_coverage:partial
- OPS-05: execution_state:timeout
- QA-01: execution_state:blocked, pilot_labels:provisional, oracle_coverage:none
- QA-02: execution_state:timeout, pilot_labels:provisional, oracle_coverage:partial
- QA-03: execution_state:blocked, pilot_labels:provisional, oracle_coverage:none
- QA-04: execution_state:blocked, pilot_labels:provisional, oracle_coverage:none
- QA-05: oracle_coverage:partial
- REL-01: oracle_coverage:partial
- REL-02: oracle_coverage:partial
- REL-03: oracle_coverage:partial
- REL-04: oracle_coverage:partial
- REL-05: oracle_coverage:partial
- REL-06: oracle_coverage:partial
- REL-07: oracle_coverage:partial
- REL-08: execution_state:blocked, oracle_coverage:none
- REL-09: execution_state:blocked, oracle_coverage:none
- SEC-05: oracle_coverage:partial
- TIL-03: oracle_coverage:partial
- TIL-04: oracle_coverage:partial
- TIL-05: oracle_coverage:partial
- TIL-06: execution_state:blocked, oracle_coverage:none
