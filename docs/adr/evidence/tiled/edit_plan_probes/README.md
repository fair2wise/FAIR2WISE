# Tiled 0.2.18 edit-plan probes (evidence, not collected)

Probe tests behind `docs/migration/ui_contracts/tiled_graph_editing_plan.md`,
run 2026-10-16 against disposable Tiled 0.2.18 servers (all 10 passed). They are
kept here as evidence only; proposed task T20C moves them into `tests/tiled/`.
To rerun, put them in a directory with a `conftest.py` that adds the repo root
and `tests/fixtures` to `sys.path` and imports `tiled_base`,
`tiled_session_instance` and `tiled_acl_instance` from `tiled_server`, then run
pytest with `F2W_TILED_PYTHON` pointing at the Tiled venv.
