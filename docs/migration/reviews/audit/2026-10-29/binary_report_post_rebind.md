# Binary evaluation report

**Gate status: NOT_PASS**

- Required cases: 108; blocking the gate: 108
- Provisional results: False (none)
- Subject: none (the report is not bound to a KG snapshot)
- Gate reasons: human_gate_not_approved; human gates: H3=not_approved
- Manifest: `t11a-1` sha256 `aebb5b89b3ffdf17`; runner `t11b-runner-1`
- Source: commit `62ca16e4f443dba0fd44488b3652fd6c829681a4`, app tree `c5327aa06a69110cd1876b0b416bebbd287716f1`, dirty tracked files: False
- Model: fake_or_none (live model used: False)
- Judge model: `None` (source: none); model under test: `None`; labelling models: none
- Repeat policy: 1 run(s), all_runs_must_pass, retry_on_failure=False
- Started 2026-10-29T21:22:05Z, finished 2026-10-29T21:40:04Z

A verdict exists only for a completed case. Skipped, blocked, timed-out, errored, missing-fixture and judge-invalid cases have no verdict and are neither passes nor scientific failures. No score is computed.

## Counts by execution state

| execution_state | cases |
| --- | ---: |
| completed | 86 |
| timeout | 0 |
| skipped | 0 |
| missing_fixture | 0 |
| blocked | 22 |
| error | 0 |
| judge_invalid | 0 |

Verdicts: {'PASS': 86, 'none': 22}

## Counts by family

| family | execution states |
| --- | --- |
| AUTH | completed=11 |
| DEP | blocked=7, completed=15 |
| EXT | blocked=3, completed=2 |
| FIG | blocked=2, completed=2 |
| JSON | completed=10 |
| LLM | blocked=3, completed=5 |
| LOCAL | blocked=1, completed=9 |
| OPS | completed=5 |
| QA | blocked=3, completed=2 |
| QUOTE | completed=2 |
| REL | blocked=2, completed=7 |
| SCH | completed=5 |
| SEC | completed=5 |
| TIL | blocked=1, completed=6 |

## Pilot labels (H3 B4)

A verdict resting on provisional (unreviewed model) labels is shown as PROVISIONAL and never passes the gate. Missing labels keep the 13 label cases blocked.

| paper | status | labelling models | items | labelling calls complete |
| --- | --- | --- | ---: | --- |
| A | missing | - | 0 | None |
| B | missing | - | 0 | None |
| C | missing | - | 0 | None |

## Every case

| case | verdict | execution_state | labels | readiness | coverage | approval | actual / reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AUTH-01 | PASS | completed | - | executable | full | proposed | 45 oracle test(s) passed |
| AUTH-02 | PASS | completed | - | executable | full | proposed | 16 oracle test(s) passed |
| AUTH-03 | PASS | completed | - | executable | full | proposed | 19 oracle test(s) passed |
| AUTH-04 | PASS | completed | - | executable | partial | proposed | 18 oracle test(s) passed |
| AUTH-05 | PASS | completed | - | executable | partial | proposed | 23 oracle test(s) passed |
| AUTH-06 | PASS | completed | - | executable | full | proposed | 5 oracle test(s) passed |
| AUTH-07 | PASS | completed | - | executable | full | proposed | 13 oracle test(s) passed |
| AUTH-08 | PASS | completed | - | executable | partial | proposed | 7 oracle test(s) passed |
| AUTH-09 | PASS | completed | - | executable | partial | proposed | 13 oracle test(s) passed |
| AUTH-10 | PASS | completed | - | executable | partial | proposed | 3 oracle test(s) passed |
| AUTH-11 | PASS | completed | - | executable | full | proposed | 3 oracle test(s) passed |
| DEP-01 | PASS | completed | - | executable | partial | proposed | 18 oracle test(s) passed |
| DEP-02 | PASS | completed | - | executable | partial | proposed | 10 oracle test(s) passed |
| DEP-03 | PASS | completed | - | executable | partial | proposed | 7 oracle test(s) passed |
| DEP-04 | PASS | completed | - | executable | partial | proposed | 15 oracle test(s) passed |
| DEP-05 | PASS | completed | - | executable | partial | proposed | 25 oracle test(s) passed |
| DEP-06 | PASS | completed | - | executable | partial | proposed | 17 oracle test(s) passed |
| DEP-07 | PASS | completed | - | executable | partial | proposed | 9 oracle test(s) passed |
| DEP-08 | - | blocked | - | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-09 | PASS | completed | - | executable | partial | proposed | 39 oracle test(s) passed |
| DEP-10 | - | blocked | - | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-11 | PASS | completed | - | executable | full | proposed | 11 oracle test(s) passed |
| DEP-12 | PASS | completed | - | executable | partial | proposed | 6 oracle test(s) passed |
| DEP-13 | PASS | completed | - | executable | partial | proposed | 12 oracle test(s) passed |
| DEP-14 | PASS | completed | - | executable | full | proposed | 8 oracle test(s) passed |
| DEP-15 | PASS | completed | - | executable | partial | proposed | 19 oracle test(s) passed |
| DEP-16 | - | blocked | - | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-17 | - | blocked | - | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-18 | - | blocked | - | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-19 | PASS | completed | - | executable | partial | proposed | 23 oracle test(s) passed |
| DEP-20 | - | blocked | - | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration) |
| DEP-21 | - | blocked | - | blocked | none | proposed | blocked by T24E (three-profile container, Ansible and restore integration); T13 (publication and rollback rehearsal) |
| DEP-22 | PASS | completed | - | executable | full | proposed | 3 oracle test(s) passed |
| EXT-01 | - | blocked | missing | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| EXT-02 | - | blocked | missing | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| EXT-03 | - | blocked | missing | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| EXT-04 | PASS | completed | missing | executable | partial | proposed | 6 oracle test(s) passed |
| EXT-05 | PASS | completed | - | executable | full | proposed | 8 oracle test(s) passed |
| FIG-01 | - | blocked | missing | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -m evals.binary.label) |
| FIG-02 | - | blocked | missing | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -m evals.binary.label) |
| FIG-03 | PASS | completed | missing | executable | partial | proposed | 9 oracle test(s) passed |
| FIG-04 | PASS | completed | - | executable | full | proposed | 16 oracle test(s) passed |
| JSON-01 | PASS | completed | - | executable | full | proposed | 13 oracle test(s) passed |
| JSON-02 | PASS | completed | - | executable | full | proposed | 57 oracle test(s) passed |
| JSON-03 | PASS | completed | - | executable | full | proposed | 12 oracle test(s) passed |
| JSON-04 | PASS | completed | - | executable | full | proposed | 5 oracle test(s) passed |
| JSON-05 | PASS | completed | - | executable | full | proposed | 32 oracle test(s) passed |
| JSON-06 | PASS | completed | - | executable | full | proposed | 7 oracle test(s) passed |
| JSON-07 | PASS | completed | - | executable | full | proposed | 12 oracle test(s) passed |
| JSON-08 | PASS | completed | - | executable | full | proposed | 12 oracle test(s) passed |
| JSON-09 | PASS | completed | - | executable | full | proposed | 27 oracle test(s) passed |
| JSON-10 | PASS | completed | - | executable | full | proposed | 8 oracle test(s) passed |
| LLM-01 | PASS | completed | - | executable | partial | proposed | 6 oracle test(s) passed |
| LLM-02 | PASS | completed | - | executable | full | proposed | 7 oracle test(s) passed |
| LLM-03 | PASS | completed | - | executable | partial | proposed | 8 oracle test(s) passed |
| LLM-04 | PASS | completed | - | executable | partial | proposed | 7 oracle test(s) passed |
| LLM-05 | - | blocked | - | blocked | none | proposed | blocked by O03 (owner approval of the job-settings rule) |
| LLM-06 | - | blocked | - | blocked | none | proposed | blocked by O03 (owner approval of the job-settings rule) |
| LLM-07 | - | blocked | - | blocked | none | proposed | blocked by O03 (owner approval of the job-settings rule) |
| LLM-08 | PASS | completed | - | executable | partial | proposed | 10 oracle test(s) passed |
| LOCAL-01 | PASS | completed | - | executable | partial | proposed | 18 oracle test(s) passed |
| LOCAL-02 | - | blocked | missing | blocked | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -m evals.binary.label) |
| LOCAL-03 | PASS | completed | - | executable | full | proposed | 6 oracle test(s) passed |
| LOCAL-04 | PASS | completed | - | executable | partial | proposed | 6 oracle test(s) passed |
| LOCAL-05 | PASS | completed | - | executable | partial | proposed | 10 oracle test(s) passed |
| LOCAL-06 | PASS | completed | - | executable | full | proposed | 26 oracle test(s) passed |
| LOCAL-07 | PASS | completed | - | executable | full | proposed | 10 oracle test(s) passed |
| LOCAL-08 | PASS | completed | - | executable | full | proposed | 2 oracle test(s) passed |
| LOCAL-09 | PASS | completed | - | executable | partial | proposed | 6 oracle test(s) passed |
| LOCAL-10 | PASS | completed | - | executable | partial | proposed | 4 oracle test(s) passed |
| OPS-01 | PASS | completed | - | executable | partial | proposed | 88 oracle test(s) passed |
| OPS-02 | PASS | completed | - | executable | partial | proposed | 24 oracle test(s) passed |
| OPS-03 | PASS | completed | - | executable | full | proposed | 25 oracle test(s) passed |
| OPS-04 | PASS | completed | missing | executable | partial | proposed | 35 oracle test(s) passed |
| OPS-05 | PASS | completed | - | executable | full | proposed | 78 oracle test(s) passed |
| QA-01 | - | blocked | missing | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| QA-02 | PASS | completed | missing | executable | partial | proposed | 6 oracle test(s) passed |
| QA-03 | - | blocked | missing | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| QA-04 | - | blocked | missing | needs_h3_rubric | none | proposed | blocked by H3 (owner-approved gold fixtures and source truth); T11B (live/fake model execution of approved cases); H3 B4 pilot labels (owner-run CBORG labelling of pilot papers A-C, then human review to gold; python -... |
| QA-05 | PASS | completed | - | executable | partial | proposed | 6 oracle test(s) passed |
| QUOTE-01 | PASS | completed | - | executable | full | proposed | 22 oracle test(s) passed |
| QUOTE-02 | PASS | completed | - | executable | full | proposed | 12 oracle test(s) passed |
| REL-01 | PASS | completed | - | executable | partial | proposed | 11 oracle test(s) passed |
| REL-02 | PASS | completed | - | executable | partial | proposed | 6 oracle test(s) passed |
| REL-03 | PASS | completed | - | executable | partial | proposed | 9 oracle test(s) passed |
| REL-04 | PASS | completed | - | executable | partial | proposed | 11 oracle test(s) passed |
| REL-05 | PASS | completed | - | executable | partial | proposed | 39 oracle test(s) passed |
| REL-06 | PASS | completed | - | executable | partial | proposed | 3 oracle test(s) passed |
| REL-07 | PASS | completed | - | executable | partial | proposed | 11 oracle test(s) passed |
| REL-08 | - | blocked | - | blocked | none | proposed | blocked by T26C (exact Git snapshot promotion and rollback rehearsal) |
| REL-09 | - | blocked | - | blocked | none | proposed | blocked by T15 (FORGE imports F2W agents) |
| SCH-01 | PASS | completed | - | executable | full | proposed | 9 oracle test(s) passed |
| SCH-02 | PASS | completed | - | executable | full | proposed | 3 oracle test(s) passed |
| SCH-03 | PASS | completed | - | executable | full | proposed | 12 oracle test(s) passed |
| SCH-04 | PASS | completed | - | executable | full | proposed | 11 oracle test(s) passed |
| SCH-05 | PASS | completed | - | executable | full | proposed | 5 oracle test(s) passed |
| SEC-01 | PASS | completed | - | executable | full | proposed | 32 oracle test(s) passed |
| SEC-02 | PASS | completed | - | executable | full | proposed | 12 oracle test(s) passed |
| SEC-03 | PASS | completed | - | executable | full | proposed | 27 oracle test(s) passed |
| SEC-04 | PASS | completed | - | executable | full | proposed | 14 oracle test(s) passed |
| SEC-05 | PASS | completed | - | executable | partial | proposed | 33 oracle test(s) passed |
| TIL-01 | PASS | completed | - | executable | full | proposed | 6 oracle test(s) passed |
| TIL-02 | PASS | completed | - | executable | full | proposed | 8 oracle test(s) passed |
| TIL-03 | PASS | completed | - | executable | partial | proposed | 19 oracle test(s) passed |
| TIL-04 | PASS | completed | - | executable | partial | proposed | 2 oracle test(s) passed |
| TIL-05 | PASS | completed | - | executable | partial | proposed | 9 oracle test(s) passed |
| TIL-06 | - | blocked | - | blocked | none | proposed | blocked by T13 (publication and rollback rehearsal) |
| TIL-07 | PASS | completed | - | executable | full | proposed | 14 oracle test(s) passed |

## Gate blockers

- AUTH-01: owner_approval:proposed
- AUTH-02: owner_approval:proposed
- AUTH-03: owner_approval:proposed
- AUTH-04: owner_approval:proposed, oracle_coverage:partial
- AUTH-05: owner_approval:proposed, oracle_coverage:partial
- AUTH-06: owner_approval:proposed
- AUTH-07: owner_approval:proposed
- AUTH-08: owner_approval:proposed, oracle_coverage:partial
- AUTH-09: owner_approval:proposed, oracle_coverage:partial
- AUTH-10: owner_approval:proposed, oracle_coverage:partial
- AUTH-11: owner_approval:proposed
- DEP-01: owner_approval:proposed, oracle_coverage:partial
- DEP-02: owner_approval:proposed, oracle_coverage:partial
- DEP-03: owner_approval:proposed, oracle_coverage:partial
- DEP-04: owner_approval:proposed, oracle_coverage:partial
- DEP-05: owner_approval:proposed, oracle_coverage:partial
- DEP-06: owner_approval:proposed, oracle_coverage:partial
- DEP-07: owner_approval:proposed, oracle_coverage:partial
- DEP-08: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-09: owner_approval:proposed, oracle_coverage:partial
- DEP-10: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-11: owner_approval:proposed
- DEP-12: owner_approval:proposed, oracle_coverage:partial
- DEP-13: owner_approval:proposed, oracle_coverage:partial
- DEP-14: owner_approval:proposed
- DEP-15: owner_approval:proposed, oracle_coverage:partial
- DEP-16: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-17: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-18: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-19: owner_approval:proposed, oracle_coverage:partial
- DEP-20: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-21: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- DEP-22: owner_approval:proposed
- EXT-01: execution_state:blocked, pilot_labels:missing, owner_approval:proposed, oracle_coverage:none
- EXT-02: execution_state:blocked, pilot_labels:missing, owner_approval:proposed, oracle_coverage:none
- EXT-03: execution_state:blocked, pilot_labels:missing, owner_approval:proposed, oracle_coverage:none
- EXT-04: pilot_labels:missing, owner_approval:proposed, oracle_coverage:partial
- EXT-05: owner_approval:proposed
- FIG-01: execution_state:blocked, pilot_labels:missing, owner_approval:proposed, oracle_coverage:none
- FIG-02: execution_state:blocked, pilot_labels:missing, owner_approval:proposed, oracle_coverage:none
- FIG-03: pilot_labels:missing, owner_approval:proposed, oracle_coverage:partial
- FIG-04: owner_approval:proposed
- JSON-01: owner_approval:proposed
- JSON-02: owner_approval:proposed
- JSON-03: owner_approval:proposed
- JSON-04: owner_approval:proposed
- JSON-05: owner_approval:proposed
- JSON-06: owner_approval:proposed
- JSON-07: owner_approval:proposed
- JSON-08: owner_approval:proposed
- JSON-09: owner_approval:proposed
- JSON-10: owner_approval:proposed
- LLM-01: owner_approval:proposed, oracle_coverage:partial
- LLM-02: owner_approval:proposed
- LLM-03: owner_approval:proposed, oracle_coverage:partial
- LLM-04: owner_approval:proposed, oracle_coverage:partial
- LLM-05: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- LLM-06: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- LLM-07: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- LLM-08: owner_approval:proposed, oracle_coverage:partial
- LOCAL-01: owner_approval:proposed, oracle_coverage:partial
- LOCAL-02: execution_state:blocked, pilot_labels:missing, owner_approval:proposed, oracle_coverage:none
- LOCAL-03: owner_approval:proposed
- LOCAL-04: owner_approval:proposed, oracle_coverage:partial
- LOCAL-05: owner_approval:proposed, oracle_coverage:partial
- LOCAL-06: owner_approval:proposed
- LOCAL-07: owner_approval:proposed
- LOCAL-08: owner_approval:proposed
- LOCAL-09: owner_approval:proposed, oracle_coverage:partial
- LOCAL-10: owner_approval:proposed, oracle_coverage:partial
- OPS-01: owner_approval:proposed, oracle_coverage:partial
- OPS-02: owner_approval:proposed, oracle_coverage:partial
- OPS-03: owner_approval:proposed
- OPS-04: pilot_labels:missing, owner_approval:proposed, oracle_coverage:partial
- OPS-05: owner_approval:proposed
- QA-01: execution_state:blocked, pilot_labels:missing, owner_approval:proposed, oracle_coverage:none
- QA-02: pilot_labels:missing, owner_approval:proposed, oracle_coverage:partial
- QA-03: execution_state:blocked, pilot_labels:missing, owner_approval:proposed, oracle_coverage:none
- QA-04: execution_state:blocked, pilot_labels:missing, owner_approval:proposed, oracle_coverage:none
- QA-05: owner_approval:proposed, oracle_coverage:partial
- QUOTE-01: owner_approval:proposed
- QUOTE-02: owner_approval:proposed
- REL-01: owner_approval:proposed, oracle_coverage:partial
- REL-02: owner_approval:proposed, oracle_coverage:partial
- REL-03: owner_approval:proposed, oracle_coverage:partial
- REL-04: owner_approval:proposed, oracle_coverage:partial
- REL-05: owner_approval:proposed, oracle_coverage:partial
- REL-06: owner_approval:proposed, oracle_coverage:partial
- REL-07: owner_approval:proposed, oracle_coverage:partial
- REL-08: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- REL-09: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- SCH-01: owner_approval:proposed
- SCH-02: owner_approval:proposed
- SCH-03: owner_approval:proposed
- SCH-04: owner_approval:proposed
- SCH-05: owner_approval:proposed
- SEC-01: owner_approval:proposed
- SEC-02: owner_approval:proposed
- SEC-03: owner_approval:proposed
- SEC-04: owner_approval:proposed
- SEC-05: owner_approval:proposed, oracle_coverage:partial
- TIL-01: owner_approval:proposed
- TIL-02: owner_approval:proposed
- TIL-03: owner_approval:proposed, oracle_coverage:partial
- TIL-04: owner_approval:proposed, oracle_coverage:partial
- TIL-05: owner_approval:proposed, oracle_coverage:partial
- TIL-06: execution_state:blocked, owner_approval:proposed, oracle_coverage:none
- TIL-07: owner_approval:proposed
