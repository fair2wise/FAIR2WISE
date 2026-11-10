"""``library_publish``: export, adopt and stage a KG bundle's publication plan from one action (``:local`` only).

Owner decision 2026-11-10 (twenty-first set): "Yes: a 'Publish to Tiled' action in the Library KG tab". One job
runs what used to be three manual steps, and stops where the owner takes over:

* ``export``: :meth:`KgLibrary.export` (``matkg_<name>.json`` into the release directory), the code path of
  ``POST /v1/library/kgs/{name}/export``;
* ``adopt``: :func:`app.modules.graph.adoption.adopt_legacy_flat_file` in process, the function behind
  ``f2w-kg-adopt``, with every refusal (R1, R2, R3-R7) surfaced by rule and nothing allowlisted;
* ``stage``: :meth:`F2WOperations.submit_publication` to the deployment's own managed Tiled, the call behind
  ``POST /v1/publications`` (V16);
* ``plan``: waits (bounded) until the publication job has planned and sits in ``awaiting_review``.

Nothing here approves anything: this module never creates, records or submits an approval. The
plan appears on the Pending Approvals page and is approved there with the existing V18 route. Design and error
table: ``docs/migration/reviews/H6/publish_button.md``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any

from . import bundles as B
from ..contracts.context import ActorKind, RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..contracts.jobs import JobState
from ..graph import adoption
from ..graph.snapshot import SnapshotError, SnapshotExistsError
from ..graph.snapshot_manifest import CORPUS_RE
from ..jobs.keys import OperationInputs
from ..jobs.pipeline import Pipeline, StageContext, StageResult, StageSpec
from ..security.authorization import local_operator_context
from .jobs import LIBRARY_PUBLISH_KIND, library_inputs

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from ...api.operations import F2WOperations
    from ..jobs.store import JobStore
    from .kg_service import KgLibrary

#: Environment variables that carry the commit the running image was built from, in order of preference.
COMMIT_ENV_NAMES = ("F2W_CODE_COMMIT", "GIT_COMMIT")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_VERSION_DIR = re.compile(r"^v([1-9][0-9]*)$")
#: Most times an adoption retries with the next version when another publish took the number first.
MAX_ADOPT_ATTEMPTS = 5
#: Default time the ``plan`` stage waits for the publication job to plan (seconds).
DEFAULT_PLAN_WAIT_SECONDS = 120.0
#: Disclosure rules of ``check_disclosure`` that make an adoption ``disclosure_refused`` (R6 is informational).
_DISCLOSURE_RULES = frozenset({"R3", "R4", "R5", "R6", "R7"})
#: Publish-side states that still hold a corpus: a plan waiting for a person, or work in flight.
_OPEN_STATES = (JobState.AWAITING_REVIEW, JobState.QUEUED, JobState.RUNNING)

# Typed reasons (HTTP ``reason`` and the prefix of a failed stage's message).
REASON_UNAVAILABLE = "publication_unavailable"
REASON_NO_COMMIT = "no_commit_label"
REASON_NOTHING = "nothing_to_publish"
REASON_DISCLOSURE = "disclosure_refused"
REASON_ADOPTION = "adoption_refused"
REASON_PENDING = "plan_pending"
REASON_IN_PROGRESS = "publish_in_progress"
REASON_FAILED = "publication_failed"
REASONS = frozenset({REASON_UNAVAILABLE, REASON_NO_COMMIT, REASON_NOTHING, REASON_DISCLOSURE, REASON_ADOPTION,
                     REASON_PENDING, REASON_IN_PROGRESS, REASON_FAILED})
REVIEW_PAGE = "Pending Approvals"


class PublishRefused(ContractError):
    """A publish was refused with a typed ``reason``; the message starts with ``"<reason>: "``."""

    def __init__(self, code: ErrorCode, reason: str, text: str, *, retryable: bool | None = None) -> None:
        super().__init__(code, f"{reason}: {text}", retryable=retryable, reason=reason)


@dataclass(frozen=True)
class Problem:
    """One reason a publish would be refused now (preview) or was refused (confirmed call)."""

    reason: str
    code: ErrorCode
    text: str

    def error(self) -> PublishRefused:
        """The typed error for this problem."""
        return PublishRefused(self.code, self.reason, self.text)

    def to_dict(self) -> dict[str, str]:
        """JSON-ready form for the preview body."""
        return {"reason": self.reason, "code": self.code.value, "message": f"{self.reason}: {self.text}"}


def resolve_code_commit(environ: Mapping[str, str]) -> str | None:
    """The full commit id the image was built from, or ``None`` (nothing is invented, ``.git`` is never read).

    The image's ``GIT_COMMIT`` build ARG also sets the label ``org.opencontainers.image.revision``; a label is not
    visible inside the container, so the running process reads ``F2W_CODE_COMMIT`` (then ``GIT_COMMIT``). A value
    that is not 40 or 64 lower-case hex digits, such as the ARG default ``unknown``, counts as no label.
    """
    for name in COMMIT_ENV_NAMES:
        value = (environ.get(name) or "").strip().lower()
        if _COMMIT_RE.fullmatch(value):
            return value
    return None


def corpus_for_bundle(name: str) -> str:
    """The corpus a bundle adopts as: the bundle name, lower-cased (``x`` is corpus ``x``).

    Raises:
        ContractError: ``INVALID_INPUT`` when ``name`` is not a valid bundle name or not a valid corpus name.
    """
    corpus = B.validate_name(name).lower()
    if not CORPUS_RE.match(corpus):
        raise ContractError(ErrorCode.INVALID_INPUT, "the KG name does not yield a valid corpus name")
    return corpus


def adopted_versions(storage_kg: Path, corpus: str) -> list[int]:
    """Version numbers already taken for ``corpus``: ``v<N>`` directories and ``matkg_<corpus>_v<N>.json`` copies."""
    taken: set[int] = set()
    corpus_dir = Path(storage_kg) / corpus
    if corpus_dir.is_dir():
        for entry in corpus_dir.iterdir():
            match = _VERSION_DIR.match(entry.name)
            if match:
                taken.add(int(match.group(1)))
    flat = re.compile(rf"^matkg_{re.escape(corpus)}_v([1-9][0-9]*)\.json$")
    if Path(storage_kg).is_dir():
        for entry in Path(storage_kg).iterdir():
            match = flat.match(entry.name)
            if match:
                taken.add(int(match.group(1)))
    return sorted(taken)


def next_version(storage_kg: Path, corpus: str) -> int:
    """The next free version: one more than the highest taken one (``1`` when none). Gaps are never reused."""
    taken = adopted_versions(storage_kg, corpus)
    return (taken[-1] + 1) if taken else 1


def _release_for_run(storage_kg: Path, corpus: str, run_id: str) -> int | None:
    """The version this very job already adopted (manifest ``created.run_id``), for a safe re-run of ``adopt``."""
    for number in adopted_versions(storage_kg, corpus):
        try:
            raw = json.loads((Path(storage_kg) / corpus / f"v{number}" / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        created = raw.get("created") if isinstance(raw, dict) else None
        if isinstance(created, dict) and created.get("run_id") == run_id:
            return number
    return None


def _refusal(exc: adoption.AdoptionRefused) -> PublishRefused:
    """Map an adoption refusal to the typed error, naming the rule exactly as the CLI report does."""
    rules = {part for part in exc.rule.split(",") if part}
    detail = str(exc).split(": ", 1)[1] if ": " in str(exc) else str(exc)
    if rules == {adoption.RULE_PLACEHOLDER}:
        return PublishRefused(ErrorCode.INVALID_INPUT, REASON_NOTHING,
                              "the exported KG is an empty placeholder (0 nodes, 0 edges); nothing was adopted or staged")
    if rules & _DISCLOSURE_RULES:
        counts = ", ".join(f"{rule} x{n}" for rule, n in sorted(exc.findings.items())) or exc.rule
        return PublishRefused(ErrorCode.INVALID_INPUT, REASON_DISCLOSURE,
                              f"the commit-ready disclosure check refused this KG ({counts}); nothing was adopted or "
                              "staged. Fix the KG and publish again; an exception is an owner decision, not made here")
    return PublishRefused(ErrorCode.INVALID_INPUT, REASON_ADOPTION,
                          f"adoption refused by rule {exc.rule}: {detail}; nothing was adopted or staged")


def _write_new(path: Path, data: bytes) -> None:
    """Write ``path`` exclusively (never replacing a file) and flush it to disk."""
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


class LibraryPublisher:
    """Preview, submit and run ``library_publish`` for the bundles of one ``:local`` deployment.

    Args:
        library: The KG library service, or a callable returning it (the route's instance, so tests and the app
            share its release directory).
        operations: The operations object whose ``submit_publication`` stages the plan (the V16 call).
        storage_kg: ``<workdir>/storage_kg``, where adopted releases live and the publication reads them.
        environ: Startup environment (the commit label is read from here).
        plan_wait_seconds: How long the ``plan`` stage waits for the publication job to plan.
        idle: Called between polls of the ``plan`` stage (default: sleep one second); tests pass a worker step.
    """

    def __init__(self, library: KgLibrary | Callable[[], KgLibrary], operations: F2WOperations, *,
                 storage_kg: Path, environ: Mapping[str, str],
                 plan_wait_seconds: float = DEFAULT_PLAN_WAIT_SECONDS,
                 idle: Callable[[], None] | None = None) -> None:
        self._library = library
        self.ops = operations
        self.storage_kg = Path(storage_kg)
        self.environ = environ
        self.plan_wait_seconds = plan_wait_seconds
        self._idle = idle or (lambda: time.sleep(1.0))

    # ------------------------------------------------------------------ helpers
    def library(self) -> KgLibrary:
        """The KG library service."""
        return self._library() if callable(self._library) else self._library

    @property
    def target(self) -> str | None:
        """The deployment's own Tiled (the one target the ``:local`` gate allows), or ``None``."""
        target = self.ops.settings.tiled_target
        if not self.ops.settings.is_local or self.ops.publication is None or not target:
            return None
        return target if self.ops.gate.allows(target) else None

    def available(self) -> bool:
        """Whether this deployment can publish a bundle (the ``can_publish`` check, without the caller's rights)."""
        return self.target is not None

    def _worker_context(self, job_id: str) -> RequestContext:
        return local_operator_context(self.ops.settings, request_id=job_id, actor_kind=ActorKind.WORKER, run_id=job_id)

    def _kg_state(self, name: str) -> tuple[str, bool]:
        """``(sha256 of the bundle KG, has nodes or edges)``."""
        data = self.library().store.get(name).file(B.KG_FILE).read_bytes()
        try:
            kg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            kg = None
        has_content = isinstance(kg, dict) and bool(kg.get("things") or kg.get("associations"))
        return hashlib.sha256(data).hexdigest(), has_content

    def _open_publish_jobs(self, corpus: str, *, exclude: str | None = None) -> list[tuple[str, str]]:
        """``(kind, job_id)`` of jobs that still hold ``corpus``: publish plans pending or running, library publishes running."""
        store = self.ops.jobs.store
        held: list[tuple[str, str]] = []
        for kind in ("publish", LIBRARY_PUBLISH_KIND):
            for state in _OPEN_STATES:
                for job in store.list_jobs_scoped(kind=kind, state=state, limit=1000):
                    if corpus in job.inputs.corpus_scope and job.job_id != exclude:
                        held.append((kind, job.job_id))
        return held

    def inputs(self, name: str, commit: str, kg_sha256: str, target: str, *, again: bool) -> OperationInputs:
        """The job inputs (``confirm`` is part of them: the job re-checks it, fail closed)."""
        nonce = os.urandom(8).hex() if again else None
        return library_inputs({"bundle": name, "confirm": True, "code_commit": commit, "target": target,
                               "kg_sha256": kg_sha256}, nonce=nonce, corpus_scope=(corpus_for_bundle(name),))

    # ------------------------------------------------------------------ preview and submit
    def preview(self, name: str, *, again: bool = False) -> dict[str, Any]:
        """What publishing ``name`` would do now, and why it would be refused. Writes nothing.

        Raises:
            ContractError: ``INVALID_INPUT`` for a bad name, ``NOT_FOUND`` for an unknown bundle.
        """
        corpus = corpus_for_bundle(name)
        kg_sha, has_content = self._kg_state(name)  # NOT_FOUND for an unknown bundle
        number = next_version(self.storage_kg, corpus)
        target = self.target
        commit = resolve_code_commit(self.environ)
        problems: list[Problem] = []
        if target is None:
            problems.append(Problem(REASON_UNAVAILABLE, ErrorCode.UNSUPPORTED_CAPABILITY,
                                    "this deployment has no publication to its own Tiled (profile :local with its "
                                    "managed Tiled is required)"))
        if commit is None:
            problems.append(Problem(REASON_NO_COMMIT, ErrorCode.UNSUPPORTED_CAPABILITY,
                                    "this image has no commit label (F2W_CODE_COMMIT / GIT_COMMIT from the "
                                    "GIT_COMMIT build argument); the release manifest needs the code commit and none "
                                    "is invented. Rebuild with --build-arg GIT_COMMIT=$(git rev-parse HEAD) or start "
                                    "the container with -e F2W_CODE_COMMIT=<40-hex>"))
        if not has_content:
            problems.append(Problem(REASON_NOTHING, ErrorCode.INVALID_INPUT,
                                    "the bundle's KG has no nodes and no edges; run an extraction first"))
        previous: dict[str, str] | None = None
        own_id: str | None = None
        if commit is not None and target is not None:
            identity = self.inputs(name, commit, kg_sha, target, again=False)
            own_id = identity.job_identity(LIBRARY_PUBLISH_KIND)
            if not again:
                try:
                    old = self.ops.jobs.store.get(own_id)
                    previous = {"job_id": old.job_id, "state": old.state.value}
                except ContractError:
                    previous = None
        for kind, job_id in self._open_publish_jobs(corpus, exclude=own_id):
            if kind == "publish":
                problems.append(Problem(REASON_PENDING, ErrorCode.CONFLICT,
                                        f"a publication plan for corpus {corpus} already waits for approval or is "
                                        f"running (job {job_id}); approve or cancel it on {REVIEW_PAGE} first"))
            else:
                problems.append(Problem(REASON_IN_PROGRESS, ErrorCode.CONFLICT,
                                        f"another publish of corpus {corpus} is running (job {job_id})"))
            break
        return {"confirmed": False, "name": name, "corpus": corpus, "snapshot_id": f"v{number}",
                "snapshot_ref": f"{corpus}/v{number}", "target": target, "release_file": B.export_file_name(name),
                "previous": previous, "review_page": REVIEW_PAGE, "problems": [p.to_dict() for p in problems],
                "_problems": problems, "_inputs": (commit, kg_sha)}

    def submit(self, ctx: RequestContext, name: str, *, again: bool = False) -> dict[str, Any]:
        """Check and submit the ``library_publish`` job; the first problem is raised as a typed error.

        Returns:
            The preview fields plus ``confirmed: true``, ``job_id``, ``state`` and ``reused`` (the job existed already).
        """
        detail = self.preview(name, again=again)
        problems: Sequence[Problem] = detail.pop("_problems")
        commit, kg_sha = detail.pop("_inputs")
        if problems:
            raise problems[0].error()
        target = str(detail["target"])
        inputs = self.inputs(name, str(commit), str(kg_sha), target, again=again)
        existed = False
        try:
            self.ops.jobs.store.get(inputs.job_identity(LIBRARY_PUBLISH_KIND))
            existed = True
        except ContractError:
            existed = False
        handle = self.ops.jobs.submit(ctx, LIBRARY_PUBLISH_KIND, inputs)
        return {**detail, "confirmed": True, "job_id": handle.job_id, "kind": handle.kind,
                "state": handle.state.value, "reused": existed}

    # ------------------------------------------------------------------ stages
    def pipeline(self) -> Pipeline:
        """The four-stage pipeline of the job."""
        rel = ("corpus_scope", "params")
        return Pipeline([
            StageSpec("export", self._export, relevant=rel, required_capability="library.extract"),
            StageSpec("adopt", self._adopt, relevant=rel, depends_on=("export",)),
            StageSpec("stage", self._stage, relevant=rel, depends_on=("adopt",), required_capability="graph.publish"),
            StageSpec("plan", self._plan, relevant=rel, depends_on=("stage",)),
        ])

    @staticmethod
    def _params(sc: StageContext) -> dict[str, Any]:
        return dict(sc.inputs.params)

    @staticmethod
    def _result(sc: StageContext, stage: str) -> Mapping[str, Any]:
        row = sc.store.stage(sc.job.job_id, stage)
        return row.result if row is not None and row.result is not None else {}

    def _export(self, sc: StageContext) -> StageResult:
        params = self._params(sc)
        if params.get("confirm") is not True:  # the route's confirm semantics, re-checked fail closed
            raise ContractError(ErrorCode.INVALID_INPUT, "exporting writes to the release directory; confirm is required")
        name = str(params.get("bundle", ""))
        corpus_for_bundle(name)
        sc.guard()
        result = self.library().export(name)
        sc.log(f"exported {result['file_name']}")
        return StageResult(data={"file_name": result["file_name"]})

    def _adopt(self, sc: StageContext) -> StageResult:
        params = self._params(sc)
        name = str(params["bundle"])
        corpus = corpus_for_bundle(name)
        commit = str(params.get("code_commit") or "")
        if not _COMMIT_RE.fullmatch(commit):
            raise Problem(REASON_NO_COMMIT, ErrorCode.UNSUPPORTED_CAPABILITY,
                          "this job carries no valid code commit; the image has no commit label").error()
        job_id = sc.job.job_id
        again = _release_for_run(self.storage_kg, corpus, job_id)
        if again is not None:
            sc.log(f"adoption of {corpus}/v{again} was already done by this job")
            return self._adopted(corpus, again, reused=True)
        source = self.library().release_dir() / B.export_file_name(name)
        if source.is_symlink() or not source.is_file():
            raise ContractError(ErrorCode.NOT_FOUND, "the exported KG file is missing; export again")
        blob = source.read_bytes()
        self.storage_kg.mkdir(parents=True, exist_ok=True)
        for _attempt in range(MAX_ADOPT_ATTEMPTS):
            sc.guard()
            number = next_version(self.storage_kg, corpus)
            flat = self.storage_kg / f"matkg_{corpus}_v{number}.json"
            try:
                _write_new(flat, blob)
            except FileExistsError:
                continue
            try:
                outcome = adoption.adopt_legacy_flat_file(flat, storage_kg=self.storage_kg, code_commit=commit,
                                                          run_id=job_id, profile="local")
            except adoption.AdoptionRefused as exc:
                flat.unlink(missing_ok=True)
                raise _refusal(exc) from None
            except SnapshotExistsError:
                flat.unlink(missing_ok=True)  # another publish took this number first; try the next one
                continue
            except SnapshotError as exc:
                flat.unlink(missing_ok=True)
                raise PublishRefused(ErrorCode.INVALID_INPUT, REASON_ADOPTION, f"adoption failed: {exc}") from None
            if (outcome.corpus, outcome.snapshot_id) != (corpus, f"v{number}"):
                raise ContractError(ErrorCode.INTERNAL, "the adopted release is not the one chosen")
            sc.log(f"adopted {corpus}/v{number}")
            return self._adopted(corpus, number, reused=False, graph_sha256=outcome.graph_sha256)
        raise ContractError(ErrorCode.CONFLICT, "could not reserve a release version; try again", retryable=True)

    @staticmethod
    def _adopted(corpus: str, number: int, *, reused: bool, graph_sha256: str | None = None) -> StageResult:
        return StageResult(data={"corpus": corpus, "snapshot_id": f"v{number}", "snapshot_ref": f"{corpus}/v{number}",
                                 "graph_sha256": graph_sha256, "reused": reused})

    def _stage(self, sc: StageContext) -> StageResult:
        params = self._params(sc)
        ref = str(self._result(sc, "adopt").get("snapshot_ref") or "")
        if not ref:
            raise ContractError(ErrorCode.INTERNAL, "no adopted release to stage")
        sc.guard()
        handle = self.ops.submit_publication(self._worker_context(sc.job.job_id), ref, str(params["target"]))
        sc.log(f"staged publication job {handle.job_id} for {ref}; it waits for your approval on {REVIEW_PAGE}")
        return StageResult(data={"publication_job_id": handle.job_id, "snapshot_ref": ref})

    def _plan(self, sc: StageContext) -> StageResult:
        pub_id = str(self._result(sc, "stage").get("publication_job_id") or "")
        publication = self.ops.publication
        if not pub_id or publication is None:
            raise ContractError(ErrorCode.INTERNAL, "no staged publication to wait for")
        deadline = time.monotonic() + self.plan_wait_seconds
        while True:
            sc.guard()
            job = self.ops.jobs.store.get(pub_id)
            record = publication.store.current_plan(pub_id)
            if job.state is JobState.AWAITING_REVIEW and record is not None:
                plan = record.plan
                sc.log(f"plan {plan.plan_sha256[:12]} is awaiting your approval on {REVIEW_PAGE}")
                return StageResult(data={"publication_job_id": pub_id, "publication_state": "awaiting_review",
                                         "plan_sha256": plan.plan_sha256, "plan_id": plan.plan_id,
                                         "creates": plan.creates, "links": plan.links,
                                         "supersessions": plan.supersessions})
            if job.state in (JobState.FAILED, JobState.CANCELLED):
                quarantine = publication.store.quarantined(pub_id)
                why = "; ".join(str(q.get("reason")) for q in quarantine[:3]) or job.state.value
                raise PublishRefused(ErrorCode.CONFLICT, REASON_FAILED, f"the publication job {pub_id} {job.state.value} "
                                     f"while planning ({why}); the release stays adopted")
            if time.monotonic() >= deadline:
                sc.log("the plan is not ready yet; it will appear on Pending Approvals when the worker has planned it",
                       "warning")
                return StageResult(data={"publication_job_id": pub_id, "publication_state": job.state.value,
                                         "plan_sha256": None})
            self._idle()

    # ------------------------------------------------------------------ job view
    @staticmethod
    def view(store: JobStore, job_id: str, publication_view: Callable[[str], Mapping[str, Any] | None]
             ) -> dict[str, Any]:
        """The ``library_publish`` block of ``GET /v1/jobs/{id}/view`` (additive), from stage rows and the live publication job."""
        job = store.get(job_id)
        rows = {row.stage: row for row in store.stages(job_id)}

        def result(stage: str) -> Mapping[str, Any]:
            row = rows.get(stage)
            return row.result if row is not None and row.result is not None else {}

        adopt, staged, planned = result("adopt"), result("stage"), result("plan")
        pub_id = staged.get("publication_job_id")
        live = publication_view(str(pub_id)) if pub_id else None
        plan_info = (live or {}).get("publication") or {}
        plan = plan_info.get("plan") if isinstance(plan_info, Mapping) else None
        failure: dict[str, Any] | None = None
        for stage, row in rows.items():
            if row.error:
                head = row.error.split(": ", 1)[0]
                failure = {"stage": stage, "reason": head if head in REASONS else None, "message": row.error,
                           "code": row.error_code.value if row.error_code is not None else None}
                break
        return {
            "bundle": job.inputs.params.get("bundle"), "corpus": (adopt.get("corpus") or
                                                                   (job.inputs.corpus_scope[0] if job.inputs.corpus_scope else None)),
            "snapshot_id": adopt.get("snapshot_id"), "snapshot_ref": adopt.get("snapshot_ref"),
            "target": job.inputs.params.get("target"),
            "stages": {stage: str(row.status.value) for stage, row in rows.items()},
            "publication_job_id": pub_id,
            "publication_state": (live or {}).get("ui_state") or planned.get("publication_state"),
            "plan": ({"plan_id": plan.get("plan_id"), "plan_sha256": plan.get("plan_sha256"), "creates": plan.get("creates"),
                      "links": plan.get("links"), "supersessions": plan.get("supersessions")}
                     if isinstance(plan, Mapping) else None),
            "review_page": REVIEW_PAGE, "failure": failure,
        }


__all__ = ("COMMIT_ENV_NAMES", "DEFAULT_PLAN_WAIT_SECONDS", "LibraryPublisher", "Problem", "PublishRefused", "REASONS",
           "REVIEW_PAGE", "adopted_versions", "corpus_for_bundle", "next_version", "resolve_code_commit")
