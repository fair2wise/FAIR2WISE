"""A CONFLICT fails the job on its first run unless its raise site says ``retryable=True`` (T-conflict-default).

``ErrorCode.CONFLICT`` is permanent by default (it is not in ``RETRYABLE_CODES``; owner decision 2026-11-09 after
the T-conflict-audit, ``docs/migration/reviews/H5/conflict_retryability.md``). ``PipelineRunner`` requeues a job
(``MAX_CONFLICT_REQUEUES`` times, with backoff) only on a conflict raised with ``retryable=True``: a busy lock, a
lease another run holds, a read that raced a writer. A conflict that needs a human or a changed input (a stale
source or plan hash, an ownership or identity clash, an "already exists" state, an unreconciled edit) fails at once
whether or not it spells out ``retryable=False``. Each permanent cluster below drives the real raising code from a
stage and asserts the first run ends ``FAILED`` with no ``conflict_requeue``; the transient clusters are the
control and must still requeue. The AST audit at the end makes a new transient CONFLICT site get reviewed.
"""

from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timezone
import fcntl
from pathlib import Path
from typing import Any, Callable

import pytest

from app.modules.canonicalization.apply_adapters import contract_error
from app.modules.canonicalization.gates import ApplyRefused
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.graph import edit_journal as ej
from app.modules.graph import identity_adapter as ida
from app.modules.graph.imports import corpus_scope as cs
from app.modules.graph.imports.journal import Lease
from app.modules.graph.snapshots import load_snapshot
from app.modules.jobs import JobService, JobStore, Pipeline, StageSpec
from app.modules.library.bundles import BundleStore
from app.modules.literature.staging import _exclusive
from tests.jobs.jobs_fakes import inputs, local_ctx

APP = Path(__file__).resolve().parents[2] / "app"


def run_once(tmp_path: Path, handler: Callable[[Any], Any]) -> tuple[JobState | None, int, ContractError | None]:
    """Run one job whose only stage is ``handler``; return (state after first run, requeues, raised error)."""
    seen: list[ContractError] = []

    def stage(sc: Any) -> Any:
        try:
            return handler(sc)
        except ContractError as exc:
            seen.append(exc)
            raise

    store = JobStore(tmp_path / "j.sqlite3")
    service = JobService(store)
    service.register("ingest", Pipeline([StageSpec("go", stage)]))
    job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
    done = service.work_one()
    assert done is not None
    return done[1], store.conflict_requeues(job_id), (seen[0] if seen else None)


def _final_row(row_id: str, graph: str = "scope1", node: str = "n1") -> dict[str, Any]:
    return {"id": row_id, "uri": ida.entity_uri(graph, node), "name": node, "entityType": "Thing",
            "properties": {ida.OWNER_KEY: ida.LITERATURE_OWNER, ida.GRAPH_KEY: graph, ida.ENTITY_KEY: node}}


def _bad_uri_row() -> dict[str, Any]:
    row = _final_row("e1")
    row["uri"] = "f2w:kg/scope1/garbage"
    return row


def _edit(seq: int) -> ej.EditRecord:
    return ej.EditRecord(
        edit_id="e-1", seq=seq, state="recorded", target="t", scope=("s",), base_snapshot_id="v1",
        base_graph_sha256="0" * 64, profile="local", mode="local_trusted", actor="local-operator",
        operation="node_patch", ids=("n1",), before={}, after={}, recorded_at=datetime.now(timezone.utc))


def _identity_clash(_sc: Any) -> None:
    ledger = ida.IdentityLedger()
    ledger.bind("g", "n", "t1")
    ledger.bind("g", "n", "t2")


def _journal_seq_gap(tmp_path: Path) -> Callable[[Any], None]:
    def handler(_sc: Any) -> None:
        journal = ej.EditJournal(tmp_path / "edits.jsonl")
        journal.append(_edit(1))
        journal.append(_edit(3))
    return handler


def _snapshot_vs_manifest(tmp_path: Path) -> Callable[[Any], None]:
    def handler(_sc: Any) -> None:
        d = tmp_path / "snap"
        d.mkdir()
        (d / "graph.json").write_text('{"things": []}', encoding="utf-8")
        (d / "manifest.json").write_text('{"graph": {"sha256": "' + "f" * 64 + '"}}', encoding="utf-8")
        load_snapshot(d / "graph.json")
    return handler


def _bundle_exists(tmp_path: Path) -> Callable[[Any], None]:
    def handler(_sc: Any) -> None:
        store = BundleStore(tmp_path / "bundles")
        store.create("kg-a", schema={"id": "s1"})
        store.create("kg-a", schema={"id": "s1"})
    return handler


PERMANENT: dict[str, Callable[[Path], Callable[[Any], None]]] = {
    "stale source/plan refusal (apply_adapters.contract_error)":
        lambda tp: (lambda _sc: (_ for _ in ()).throw(contract_error(ApplyRefused("stale_source", "moved")))),
    "identity clash (IdentityLedger.bind)": lambda tp: _identity_clash,
    "edit journal sequence (EditJournal.append)": _journal_seq_gap,
    "snapshot vs manifest hash (load_snapshot)": _snapshot_vs_manifest,
    "bundle already exists (BundleStore.create)": _bundle_exists,
    "two final entities for one node (classify_scope)":
        lambda tp: (lambda _sc: cs.classify_scope("scope1", [_final_row("a"), _final_row("b")], [])),
    "unrecognized entity uri (classify_scope)":
        lambda tp: (lambda _sc: cs.classify_scope("scope1", [_bad_uri_row()], [])),
}


@pytest.mark.parametrize("name", sorted(PERMANENT))
def test_a_permanent_conflict_fails_the_job_on_its_first_run(tmp_path, name):
    state, requeues, exc = run_once(tmp_path, PERMANENT[name](tmp_path))
    assert exc is not None and exc.code is ErrorCode.CONFLICT, name
    assert exc.retryable is False
    assert state is JobState.FAILED  # not QUEUED: no bouncing five times over minutes
    assert requeues == 0


def _held_lock(tmp_path: Path) -> Callable[[Any], None]:
    lock = tmp_path / "pkg" / "import.lock"

    def handler(_sc: Any) -> None:
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a") as other:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # another worker's import
            with _exclusive(lock):
                pass
    return handler


def _held_import_lease(tmp_path: Path) -> Callable[[Any], None]:
    def clock() -> datetime:
        return datetime.now(timezone.utc)

    def handler(_sc: Any) -> None:
        Lease(tmp_path / "lease.json", clock).acquire()
        Lease(tmp_path / "lease.json", clock).acquire()
    return handler


def _scan_changes_between_reads(_sc: Any) -> None:
    class Client:
        calls = 0

        def scan_entities(self) -> list[dict[str, Any]]:
            self.calls += 1
            return [_final_row(f"r{self.calls}")]

        def scan_links(self) -> list[dict[str, Any]]:
            return []

    cs.read_scope_state(Client(), "scope1")  # type: ignore[arg-type]


TRANSIENT = {
    "package import lock held by another worker": _held_lock,
    "import lease held by another run": _held_import_lease,
    "target scan changed between the two reads": lambda tp: _scan_changes_between_reads,
}


@pytest.mark.parametrize("name", sorted(TRANSIENT))
def test_a_transient_conflict_still_requeues(tmp_path, name):
    state, requeues, exc = run_once(tmp_path, TRANSIENT[name](tmp_path))
    assert exc is not None and exc.code is ErrorCode.CONFLICT and exc.retryable is True, name
    assert state is JobState.QUEUED
    assert requeues == 1


def _unmarked_conflict(_sc: Any) -> None:
    raise ContractError(ErrorCode.CONFLICT, "a new site that forgot to decide")


def test_an_unmarked_conflict_is_permanent_and_fails_the_job_at_once(tmp_path):
    """The safe default: forgetting ``retryable=`` fails the job once with a clear message instead of bouncing it
    ``MAX_CONFLICT_REQUEUES`` times over minutes; an operator can still retry by hand."""
    assert ContractError(ErrorCode.CONFLICT, "x").retryable is False
    assert ContractError(ErrorCode.CONFLICT, "x", retryable=True).retryable is True
    assert ContractError(ErrorCode.UNAVAILABLE, "x").retryable is True  # the flip is CONFLICT-only
    state, requeues, exc = run_once(tmp_path, _unmarked_conflict)
    assert exc is not None and exc.code is ErrorCode.CONFLICT and exc.retryable is False
    assert state is JobState.FAILED
    assert requeues == 0


#: CONFLICT sites reviewed as transient (``retryable=True``): a busy lock, a lease another run holds, a read that
#: raced a writer, an optimistic-concurrency race the caller re-reads. ``(file under app/, function)`` -> number
#: of sites. Permanent sites need no entry: with or without ``retryable=False`` they fail the job at once.
EXPLICIT_TRANSIENT = Counter({
    ("modules/graph/backends/tiled.py", "TiledGraphBackend._one_hop"): 1,
    ("modules/graph/backends/tiled_mutations.py", "TiledMutationClient._scan"): 1,
    ("modules/graph/imports/corpus_scope.py", "plan_replacement"): 1,
    ("modules/graph/imports/corpus_scope.py", "read_scope_state"): 1,
    ("modules/graph/imports/journal.py", "Lease.acquire"): 2,
    ("modules/library/publish.py", "LibraryPublisher._adopt"): 1,  # the version number was taken by a racing publish
    ("modules/graph/imports/journal.py", "Lease.check"): 1,
    ("modules/graph/imports/save_recovery.py", "execute_save"): 1,
    ("modules/graph/imports/save_recovery.py", "recover_failed_saves"): 1,
    ("modules/graph/imports/tiled_json.py", "ImportVerificationError.__init__"): 1,
    ("modules/graph/imports/tiled_json.py", "read_state"): 1,
    ("modules/graph/legacy_save.py", "TiledSaveBackend._refuse_during_switch"): 1,
    ("modules/jobs/publication.py", "Publication._activate"): 1,
    ("modules/jobs/publication.py", "Publication._import"): 1,
    ("modules/jobs/publication.py", "Publication._op_apply"): 1,
    ("modules/jobs/publish_store.py", "PublishStore.activate"): 1,
    ("modules/jobs/service.py", "JobService.cancel"): 1,
    ("modules/jobs/store.py", "JobStore.transition_without_lease"): 2,
    ("modules/library/extraction.py", "BundleBusy.__init__"): 1,
    ("modules/library/extraction.py", "LibraryExtractor.run"): 1,
    ("modules/literature/staging.py", "_exclusive"): 1,
    ("modules/literature/staging.py", "import_package"): 1,
})


def _conflict_sites() -> tuple[Counter[tuple[str, str]], list[tuple[str, str, str]]]:
    """``(sites with a literal retryable=True, sites whose retryable= is not a literal True/False/absent)``."""
    transient: Counter[tuple[str, str]] = Counter()
    computed: list[tuple[str, str, str]] = []

    def visit(node: ast.AST, scope: list[str], rel: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = [*scope, child.name]
            if (isinstance(child, ast.Call) and child.args and isinstance(child.args[0], ast.Attribute)
                    and child.args[0].attr == "CONFLICT" and isinstance(child.args[0].value, ast.Name)
                    and child.args[0].value.id == "ErrorCode"):
                flags = [k.value for k in child.keywords if k.arg == "retryable"]
                if flags and not (isinstance(flags[0], ast.Constant) and flags[0].value in (True, False)):
                    computed.append((rel, ".".join(scope), ast.unparse(flags[0])))
                elif flags and flags[0].value is True:  # type: ignore[attr-defined]
                    transient[(rel, ".".join(scope))] += 1
            visit(child, inner, rel)

    for path in sorted(APP.rglob("*.py")):
        visit(ast.parse(path.read_text(encoding="utf-8")), [], path.relative_to(APP).as_posix())
    return transient, computed


def test_every_transient_conflict_site_is_reviewed_and_no_site_computes_its_retryability():
    """A ``ContractError(ErrorCode.CONFLICT, ...)`` is permanent unless it passes a literal ``retryable=True``,
    and every such transient site is listed here with its reasoning in the audit table. A permanent site may say
    ``retryable=False`` (clearer) or nothing (the default); a computed ``retryable=<expr>`` is refused because the
    audit could not classify it."""
    transient, computed = _conflict_sites()
    assert computed == []
    assert transient == EXPLICIT_TRANSIENT
