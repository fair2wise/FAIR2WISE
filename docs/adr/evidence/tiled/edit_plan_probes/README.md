# Tiled 0.2.18 edit-plan probes (evidence record)

Probe tests behind `docs/migration/ui_contracts/tiled_graph_editing_plan.md`,
run 2026-10-16 against disposable Tiled 0.2.18 servers (all 10 passed). They were
kept here as uncollected evidence until T20C; T28C moved them, bodies unchanged,
to `tests/tiled/test_edit_plan_probes.py`, where they run in the CI Tiled job
(`python-tiled-suites`) with the fixtures in `tests/fixtures/tiled_server.py` and
skip without `F2W_TILED_PYTHON`. To rerun locally:

    F2W_TILED_PYTHON=<tiled venv>/bin/python python -m pytest tests/tiled/test_edit_plan_probes.py -s -q

The `OBSERVATIONS` block printed by `test_zz_report` is the recorded evidence.
