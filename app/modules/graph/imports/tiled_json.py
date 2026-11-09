"""JSON-to-Tiled import adapter: plan, authorized apply, verification, receipt (T08B).

Adapted from als-computing/forge_rsoxs_analysis src/als_knowledge_agent/graph/import_staged.py and
src/als_knowledge_agent/graph/ingest_journal.py (BSD-3-Clause): explicit dry-run/apply, single
writer, journal and receipt mechanics. The graph schema is F2W's (``literature_writer``), not FORGE's.

D21: this module consumes a saved, verified snapshot. It never parses documents, calls a model,
downloads or re-extracts, and re-derives no ids (``assign_edge_ids`` only numbers repeated triples,
exactly as the readers do).

Flow::

    plan_import()  -> ImportPlan        read-only: preflight, complete target scan, diff, plan hash
    (human) create_approval()           security.authorization; binds the exact plan hash/target/scope
    apply_import() -> ImportOutcome     authorize, lease, stages, verify by readback, receipt

Tiled 0.2.18 facts used (capabilities_0.2.18.md): properties are create-only, so a changed node is a
new revision entity (created under a ``pending`` uri, the old one moved to a ``retired`` uri, the new
one moved to the final uri; the T08A readers ignore both non-final uris); links cannot change
endpoints or properties, so a changed link is created anew and the old one deleted (ownership
checked); ``updateEntity`` always sends ``accessBlob``; there is no upsert and uris are not unique, so
every replay reconciles against a complete scan instead of trusting the journal.

Stage order: ``entities`` (create / supersede) -> ``links`` (create, then delete stale) ->
``retire`` (nodes absent from the snapshot) -> verify -> receipt. A crash anywhere leaves a state the
next ``apply_import`` of the same plan reconciles without duplicate facts. No record not owned by
this literature graph (a FORGE entity, another graph) is ever written; their fingerprints are
compared before and after.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, cast

from app.modules.contracts.changes import ApprovalRecord, ChangePlan, ImportReceipt
from app.modules.contracts.context import DeploymentProfile, RequestContext
from app.modules.security.authorization import (
    DeploymentSettings,
    require,
    target_in_deployment,
    verify_approval,
)
from app.modules.security.tiled_identity import normalize_tiled_target

from .. import edit_journal as ej
from .. import identity_adapter as ida
from .. import literature_writer as lw
from ..backends.tiled_client import TiledCredentialRejected
from ..backends.tiled_mutations import TiledMutationClient
from ..identity_adapter import IdentityLedger, assign_edge_ids
from ..ports import ContractError, ErrorCode
from ..releases import (
    ReleaseVerificationError,
    VerifiedRelease,
    VerifiedSnapshot,
    verify_snapshot_dir,
)
from ..snapshot_manifest import KGManifest
from .journal import ImportJournal, Lease, operation_id
from .receipts import ReceiptArtifact, ReceiptExtras, ReceiptStore, make_artifact, receipt_valid_for

ADAPTER_VERSION = "f2w-tiled-json-import/1+tiled-0.2.18"
STAGES = ("entities", "links", "retire")
Clock = Callable[[], datetime]
FaultHook = Callable[[str, int], None]
Source = Path | str | VerifiedSnapshot | VerifiedRelease


class InjectedCrash(BaseException):
    """Raised by test fault hooks to simulate a process crash (the lease is deliberately not released)."""


class ImportVerificationError(ContractError):
    """Readback verification failed; no receipt was written and nothing is reported published."""

    def __init__(self, mismatches: Sequence[str]) -> None:
        """Record the mismatch descriptions (the message shows the first five); the error is retryable."""
        super().__init__(ErrorCode.CONFLICT, f"import verification failed: {list(mismatches)[:5]}",
                         retryable=True)
        self.mismatches = list(mismatches)


@contextmanager
def _writer_errors() -> Iterator[None]:
    """Map Tiled's HTTP 401/403 to the import client's credential to ``WRITER_UNAUTHORIZED``.

    The import client only ever carries the runtime writer credential, so a rejection here is a
    writer problem (access_contract.md section 12), never a caller's missing capability. No retry with
    another key and no fallback.
    """
    try:
        yield
    except TiledCredentialRejected as exc:
        raise ContractError(ErrorCode.WRITER_UNAUTHORIZED,
                            f"the Tiled writer credential was rejected (HTTP {exc.status}); "
                            "graph writes are blocked", retryable=False) from None


def _canon(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: object) -> str:
    return hashlib.sha256(_canon(value).encode()).hexdigest()


# --------------------------------------------------------------------------- source (preflight)

@dataclass(frozen=True)
class SourceSnapshot:
    """A verified snapshot: bytes already hash-checked, graph parsed from those same bytes."""

    manifest: KGManifest
    graph_sha256: str
    manifest_sha256: str
    graph_id: str
    nodes: dict[str, dict[str, Any]]
    edges: list[tuple[str, dict[str, Any]]]
    data_commit: str | None = None
    ancestry: tuple[str, ...] = ()

    @property
    def lineage(self) -> list[str]:
        """The snapshot and its full ancestor chain (T23C), nearest first.

        ``ancestry`` is walked from the sibling snapshot directories when the source is a directory; for a
        release object with no directory it falls back to the recorded parent, and the caller may add known
        ancestors through the ``lineage`` argument of the plan functions.
        """
        ids = [self.manifest.snapshot_id]
        chain = list(self.ancestry) or ([self.manifest.parent.snapshot_id] if self.manifest.parent else [])
        return ids + [i for i in chain if i not in ids]


def default_supported_schema_fingerprints(registry: Any = None) -> frozenset[str]:  # noqa: ANN401  # Any: schema registry (duck-typed: donor object or typed record)
    """Import fingerprints of every selectable registered schema in use (computed, not hard-coded).

    Includes each base schema's fingerprint from before the H3 S4 licence-line change, so a snapshot built
    before T22C still imports. Excludes schema sets pending owner review (T22C): publishing a graph bound to
    one needs an explicit ``supported_schema_fingerprints`` from the caller.
    """
    from app.modules.schema.loading import closure_fingerprint, pre_s4_fingerprint
    from app.modules.schema.registry import SchemaRegistry

    registry = registry or SchemaRegistry.discover()
    usable = [e for e in registry if e.is_linkml and not e.known_broken and not e.pending_review]
    current = {closure_fingerprint(e) for e in usable}
    before = {pre_s4_fingerprint(e) for e in usable}
    return frozenset(current | {fp for fp in before if fp})


def walk_ancestry(snapshot_dir: Path, manifest: KGManifest, *, limit: int = 1000) -> tuple[str, ...]:
    """Parent chain of a snapshot read from sibling ``<corpus>/<vN>/manifest.json`` files (T23C).

    The walk stops at the first missing or unreadable ancestor manifest (the chain is then shorter, which can
    only make an import look *less* protected-safe, never hide a conflict) and on any cycle.
    """
    seen = [manifest.snapshot_id]
    current = manifest.parent.snapshot_id if manifest.parent else None
    while current and current not in seen and len(seen) <= limit:
        seen.append(current)
        try:
            raw = json.loads((snapshot_dir.parent / current / "manifest.json").read_text(encoding="utf-8"))
            parent = (raw.get("parent") or {}).get("snapshot_id")
        except (OSError, ValueError, AttributeError):
            break
        current = parent if isinstance(parent, str) else None
    return tuple(seen[1:])


def load_source(source: Source, *, supported_schema_fingerprints: Collection[str]) -> SourceSnapshot:
    """Preflight a frozen snapshot (json_kg_contract.md 5.1; no target contact).

    ``source`` is a ``storage/kg/<corpus>/<vN>`` directory, or an already verified snapshot/release
    (T23B ``verify_snapshot_dir`` / ``verify_git_release``). A directory is re-verified here.

    Raises:
        ContractError: ``INVALID_INPUT`` for any verification, schema, validation or disclosure refusal.
    """
    source_dir: Path | None = None
    try:
        if isinstance(source, (str, Path)):
            source_dir = Path(source)
            source = verify_snapshot_dir(source_dir)
    except ReleaseVerificationError as exc:
        raise ContractError(ErrorCode.INVALID_INPUT, f"snapshot verification failed: {exc}") from None
    if isinstance(source, VerifiedRelease):
        manifest, graph_bytes, commit = source.manifest, source.graph.data, source.data_commit
        graph_sha, manifest_sha = source.graph.sha256, source.manifest_artifact.sha256
    else:
        manifest, graph_bytes, commit = source.manifest, source.graph_bytes, None
        graph_sha, manifest_sha = source.graph_sha256, source.manifest_sha256
    if hashlib.sha256(graph_bytes).hexdigest() != manifest.graph.sha256 or graph_sha != manifest.graph.sha256:
        raise ContractError(ErrorCode.INVALID_INPUT, "graph bytes do not match the manifest")
    fp = manifest.schema_block.import_fingerprint
    if not fp or fp not in supported_schema_fingerprints:
        raise ContractError(ErrorCode.INVALID_INPUT,
                            "manifest schema import_fingerprint is not in the supported set")
    if manifest.validation.status != "pass":
        raise ContractError(ErrorCode.INVALID_INPUT, "the snapshot's validation did not pass")
    if manifest.disclosure.status not in ("pass", "owner_waiver"):
        raise ContractError(ErrorCode.INVALID_INPUT,
                            f"disclosure status {manifest.disclosure.status!r} blocks import")
    try:
        data = json.loads(graph_bytes.decode("utf-8"))
        raw_nodes = data.get("things") or data.get("nodes") or []
        raw_edges = data.get("associations") or data.get("edges") or []
    except (ValueError, AttributeError):
        raise ContractError(ErrorCode.INVALID_INPUT, "graph.json is not a KG document") from None
    nodes = {n["id"]: n for n in raw_nodes if isinstance(n, dict) and n.get("id")}
    edges = cast(
        list[tuple[str, dict[str, Any]]],  # Any: untyped KG edge payload; the generator yields only dicts
        list(assign_edge_ids(e for e in raw_edges if isinstance(e, dict))),
    )
    ancestry = walk_ancestry(source_dir, manifest) if source_dir is not None else ()
    return SourceSnapshot(manifest, graph_sha, manifest_sha, manifest.graph_id, nodes, edges, commit, ancestry)


# --------------------------------------------------------------------------- target state

@dataclass
class TargetState:
    """Complete scan of the target, classified for one graph."""

    graph_id: str
    rows: dict[str, dict[str, Any]]
    link_rows: dict[str, dict[str, Any]]
    final: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)   # tag -> node -> row
    retired: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    active_links: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # edge id -> rows
    foreign: dict[str, str] = field(default_factory=dict)
    revision: str = "none"

    def pending_for(self, tag: str | None) -> dict[str, dict[str, Any]]:
        """Return the in-flight rows of plan ``tag`` by node id (empty when ``tag`` is ``None`` or unknown)."""
        return self.pending.get(tag or "", {})

    def retired_for(self, tag: str | None) -> dict[str, dict[str, Any]]:
        """Return the rows retired by plan ``tag`` by node id (empty when ``tag`` is ``None`` or unknown)."""
        return self.retired.get(tag or "", {})


def _tag_of(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def read_state(client: TiledMutationClient, graph_id: str, *, double_read: bool = True) -> TargetState:
    """Complete scan (twice, compared, when ``double_read``) or an error. A partial scan never plans.

    Raises:
        TruncatedGraphRead: A scan bound was reached.
        ContractError: ``UNAVAILABLE`` (retryable), ``CONFLICT`` (changed during the read, duplicate
            final uris, or a record claiming this graph with an unrecognized uri).
    """
    entities, links = client.scan_entities(), client.scan_links()
    if double_read:
        again_e, again_l = client.scan_entities(), client.scan_links()
        if _sha([entities, links]) != _sha([again_e, again_l]):
            raise ContractError(ErrorCode.CONFLICT, "the target changed while it was being read; retry",
                                retryable=True)
    return classify_state(graph_id, entities, links)


def classify_state(graph_id: str, entities: list[dict[str, Any]], links: list[dict[str, Any]]) -> TargetState:
    """Split scanned rows into this graph's owned records and everything else."""
    st = TargetState(graph_id, {e["id"]: e for e in entities}, {lk["id"]: lk for lk in links})
    owned: list[Any] = []
    for row in entities:
        try:
            kind = lw.classify_entity(row, graph_id)
        except ValueError as exc:
            raise ContractError(ErrorCode.CONFLICT, str(exc), retryable=False) from None
        if kind is None or kind == lw.RELEASE:  # a T08C release marker is not a per-version record
            st.foreign["e:" + row["id"]] = _sha(row)
            continue
        node = row["properties"][ida.ENTITY_KEY]
        owned.append(("e", row))
        if kind == "final":
            if node in st.final:
                raise ContractError(ErrorCode.CONFLICT,
                                    f"two Tiled entities claim ({graph_id}, {node}); no upsert in 0.2.18",
                                    retryable=False)
            st.final[node] = row
        else:
            slot = (st.pending if kind == lw.PENDING else st.retired).setdefault(_tag_of(row["uri"]), {})
            if node in slot:
                raise ContractError(ErrorCode.CONFLICT, f"two {kind} revisions of ({graph_id}, {node})",
                    retryable=False)
            slot[node] = row
    for row in links:
        eid = lw.link_owned(row, graph_id)
        if eid is None:
            st.foreign["l:" + row["id"]] = _sha(row)
            continue
        owned.append(("l", row))
        st.active_links.setdefault(eid, []).append(row)
    body = sorted([k, _sha({f: r.get(f) for f in ("id", "uri", "name", "entityType", "properties", "subjectId",
                                                  "objectId", "predicate")})] for k, r in owned)
    st.revision = "none" if not body else "scan:" + _sha(body)
    return st


# --------------------------------------------------------------------------- plan

@dataclass(frozen=True)
class Op:
    """One planned operation."""

    kind: str
    key: str
    old_id: str | None = None

    @property
    def token(self) -> str:
        """Return ``"<kind>:<key>"``, the operation's identifier."""
        return f"{self.kind}:{self.key}"

    @property
    def stage(self) -> str:
        """Return the import stage (one of ``STAGES``) that applies this operation kind."""
        return {"create_node": "entities", "supersede_node": "entities", "create_link": "links",
                "retire_link": "links", "retire_node": "retire"}[self.kind]


def compute_ops(src: SourceSnapshot, st: TargetState,
                tag: str | None = None) -> tuple[list[Op], dict[str, int]]:
    """Diff the snapshot against the scanned target. ``tag`` (a plan tag) recognizes in-flight revisions."""
    ops: list[Op] = []
    counts = {"unchanged_entities": 0, "unchanged_links": 0}
    kept: dict[str, str] = {}
    graph_id = src.graph_id
    pend, ret = st.pending_for(tag), st.retired_for(tag)
    for node_id, node in src.nodes.items():
        spec: Any = lw.entity_spec(graph_id, node)  # Any: the name is reused for a LinkSpec below
        final = st.final.get(node_id)
        if final is not None and lw.entity_matches(final, spec):
            kept[node_id] = final["id"]
            counts["unchanged_entities"] += 1
        elif final is not None:
            ops.append(Op("supersede_node", node_id, final["id"]))
        elif node_id in pend or node_id in ret:
            ops.append(Op("supersede_node", node_id, ret[node_id]["id"] if node_id in ret else None))
        else:
            ops.append(Op("create_node", node_id))
    for node_id, row in st.final.items():
        if node_id not in src.nodes:
            ops.append(Op("retire_node", node_id, row["id"]))
    expected = set()
    for eid, edge in src.edges:
        if edge.get("subject") not in src.nodes or edge.get("object") not in src.nodes:
            continue
        expected.add(eid)
        spec = lw.link_spec(graph_id, eid, edge)
        actives = st.active_links.get(eid, [])
        s_id, o_id = kept.get(spec.subject), kept.get(spec.object)
        good = [lk for lk in actives if s_id and o_id and lw.link_matches(lk, spec, s_id, o_id)]
        if not good:
            ops.append(Op("create_link", eid))
        else:
            counts["unchanged_links"] += 1
        for lk in actives:
            if not good or lk is not good[0]:
                ops.append(Op("retire_link", f"{eid}@{lk['id']}", lk["id"]))
    for eid, rows in st.active_links.items():
        if eid not in expected:
            ops.extend(Op("retire_link", f"{eid}@{lk['id']}", lk["id"]) for lk in rows)
    order = {k: i for i, k in enumerate(("create_node", "supersede_node", "create_link", "retire_link",
                                         "retire_node"))}
    ops.sort(key=lambda o: (order[o.kind], o.key))
    return ops, counts


def dangling_edges(src: SourceSnapshot) -> list[dict[str, Any]]:
    """Edges whose endpoint is not a node of the snapshot (json2kg stubs): reported, never dropped silently."""
    out = []
    for eid, edge in src.edges:
        missing = [side for side in ("subject", "object") if edge.get(side) not in src.nodes]
        if missing:
            out.append({"edge_id": eid, "subject": edge.get("subject"), "predicate": edge.get("predicate"),
                        "object": edge.get("object"), "missing": missing})
    return out


def _plan_body(detail: Mapping[str, Any], *, snapshot_id: str, input_sha256: str, target: str,
               scope: Sequence[str], base_revision: str, validation_sha256: str) -> dict[str, Any]:
    ops = [Op(*o) for o in detail["ops"]]
    new_entities = sum(1 for o in ops if o.kind in ("create_node", "supersede_node"))
    return {
        "snapshot_id": snapshot_id, "input_sha256": input_sha256, "target": target,
        "corpus_scope": list(scope), "base_revision": base_revision, "creates": new_entities,
        "links": sum(1 for o in ops if o.kind == "create_link"),
        "supersessions": sum(1 for o in ops if o.kind in ("supersede_node", "retire_node", "retire_link")),
        "affected_ids": [f"plan-detail:{_sha(detail)}"] + [o.token for o in ops],
        "validation_report_sha256": validation_sha256,
    }


def build_change_plan(detail: Mapping[str, Any], **kw: Any) -> ChangePlan:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
    """Deterministic :class:`ChangePlan` for ``detail``; ``plan_sha256`` covers every field below."""
    body = _plan_body(detail, **kw)
    digest = _sha(body)
    return ChangePlan(plan_id=f"plan-{digest[:16]}", plan_sha256=digest, **body)


@dataclass(frozen=True)
class ImportPlan:
    """Immutable plan: the contract :class:`ChangePlan` plus the detail its hash binds.

    ``detail`` holds the adapter version, manifest hash, graph id, owner scope, access blob, edit
    resolutions hash, every operation, the dangling-stub report and counts. Its hash is one of the
    ``affected_ids`` entries, so approving ``change.plan_sha256`` approves exactly this detail.
    """

    change: ChangePlan
    detail: dict[str, Any]

    @property
    def ops(self) -> list[Op]:
        """Return the planned operations decoded from ``detail``."""
        return [Op(*o) for o in self.detail["ops"]]

    @property
    def is_noop(self) -> bool:
        """Return whether the plan has no operations, so the target already matches the snapshot."""
        return not self.detail["ops"]

    @property
    def tag(self) -> str:
        """Return the plan tag: the first 16 characters of the plan hash, which marks its in-flight rows."""
        return self.change.plan_sha256[:16]

    @property
    def summary(self) -> dict[str, Any]:
        """Dry-run report (counts, affected ids, dangling stubs)."""
        return {"plan_sha256": self.change.plan_sha256, "target": self.change.target,
                "creates": self.change.creates, "links": self.change.links,
                "supersessions": self.change.supersessions, "unchanged": self.detail["counts"],
                "dangling": self.detail["dangling"], "noop": self.is_noop,
                "affected_ids": list(self.change.affected_ids)}

    def verify_integrity(self) -> None:
        """Recompute the plan from its detail; any edit to detail or counts is refused."""
        c = self.change
        try:
            expect = build_change_plan(self.detail, snapshot_id=c.snapshot_id, input_sha256=c.input_sha256,
                                       target=c.target, scope=c.corpus_scope, base_revision=c.base_revision,
                                       validation_sha256=c.validation_report_sha256)
        except (TypeError, KeyError, ValueError):
            raise ContractError(ErrorCode.INVALID_INPUT, "import plan detail is malformed") from None
        if expect != c:
            raise ContractError(ErrorCode.STALE_APPROVAL, "import plan was modified after it was computed")

    def to_json(self) -> dict[str, Any]:
        """Return the JSON-serializable form that :meth:`from_json` reads."""
        return {"change": self.change.model_dump(mode="json"), "detail": self.detail}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ImportPlan:
        """Rebuild a plan from :meth:`to_json` output; call :meth:`verify_integrity` before trusting it."""
        return cls(ChangePlan.model_validate(data["change"]), json.loads(_canon(data["detail"])))


def target_for(client: TiledMutationClient, namespace: str | None = None) -> str:
    """Contract target: normalized base URL, plus ``#namespace`` when given."""
    base = normalize_tiled_target(client.base_url)
    if namespace is None:
        return base
    if not re.match(r"^[A-Za-z0-9_.-]+$", namespace):
        raise ContractError(ErrorCode.INVALID_INPUT, "namespace must be a simple token")
    return f"{base}#{namespace}"


def _check_client(client: TiledMutationClient, settings: DeploymentSettings, target: str) -> None:
    if not target_in_deployment(target, settings):
        raise ContractError(ErrorCode.DENIED, "target is not on this deployment's Tiled")
    if normalize_tiled_target(client.base_url) != target.split("#", 1)[0]:
        raise ContractError(ErrorCode.INVALID_INPUT, "client is not connected to the plan target")
    allowed = {cast(DeploymentProfile, settings.profile).value} | ({"test"} if settings.is_local else set())
    if client.profile not in allowed:
        raise ContractError(ErrorCode.DENIED, "client profile does not match the deployment profile")


def plan_import(source: Source, *, client: TiledMutationClient, settings: DeploymentSettings,
                supported_schema_fingerprints: Collection[str], namespace: str | None = None,
                access_blob: Mapping[str, Any] | None = None,
                edit_journal: ej.EditJournal | None = None,
                resolutions: Sequence[ej.ConflictResolution] = (), lineage: Sequence[str] = ()) -> ImportPlan:
    """Dry-run: preflight the snapshot, read the whole target, and return the exact plan. No writes.

    Raises:
        ContractError: preflight refusal; ``UNAVAILABLE`` if Tiled cannot be read;
            ``WRITER_UNAUTHORIZED`` if Tiled rejects the writer credential (401/403); ``CONFLICT`` for an
            unreconciled acknowledged edit or an inconsistent target.
        TruncatedGraphRead: The target could not be read completely (a partial scan never plans).
    """
    src = load_source(source, supported_schema_fingerprints=supported_schema_fingerprints)
    target = target_for(client, namespace)
    _check_client(client, settings, target)
    scope = (src.manifest.corpus,)
    ej.check_import(edit_journal, target=target, scope=scope, lineage=[*src.lineage, *lineage],
                    resolutions=resolutions)
    with _writer_errors():
        state = read_state(client, src.graph_id)
    ops, counts = compute_ops(src, state)
    detail = {
        "adapter_version": ADAPTER_VERSION, "manifest_sha256": src.manifest_sha256, "graph_id": src.graph_id,
        "owner": ida.LITERATURE_OWNER, "access_blob": dict(access_blob or {}),
        "resolutions_sha256": ej.resolutions_sha256(resolutions), "data_commit": src.data_commit,
        "projection_policy": "all-node-fields+edge-fields/owner-marked/no-snippet-filter(O-D1)",
        "ops": [[o.kind, o.key, o.old_id] for o in ops], "counts": counts, "dangling": dangling_edges(src),
        "leftover_revisions": sum(len(v) for v in (*state.pending.values(), *state.retired.values())),
    }
    change = build_change_plan(detail, snapshot_id=src.manifest.snapshot_id, input_sha256=src.graph_sha256,
                               target=target, scope=scope, base_revision=state.revision,
                               validation_sha256=src.manifest.validation.report_sha256)
    return ImportPlan(change, detail)


# --------------------------------------------------------------------------- apply

@dataclass(frozen=True)
class ImportOutcome:
    """Result of :func:`apply_import`."""

    status: str            # applied | noop | already_applied
    receipt: ReceiptArtifact
    receipt_path: Path | None
    resumed: bool = False


def _slug(text: str) -> str:
    head = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:40]
    return head + "-" + hashlib.sha256(text.encode()).hexdigest()[:10]


def verify_projection(src: SourceSnapshot, st: TargetState, foreign_before: Mapping[str, str]) -> list[str]:
    """Compare the readback to the snapshot and return mismatch descriptions (empty when it matches).

    Checks every node and edge by content rather than count, that nothing extra or in flight remains,
    and that every pre-existing foreign record is untouched.
    """
    bad: list[str] = []
    gid = src.graph_id
    for node_id, node in src.nodes.items():
        row = st.final.get(node_id)
        if row is None:
            bad.append(f"missing entity {node_id}")
        elif not lw.entity_matches(row, lw.entity_spec(gid, node)):
            bad.append(f"entity {node_id} differs from the snapshot")
    for node_id in st.final:
        if node_id not in src.nodes:
            bad.append(f"extra entity {node_id}")
    if any(st.pending.values()):
        bad.append("a pending revision is still in flight")
    seen = set()
    for eid, edge in src.edges:
        if edge.get("subject") not in src.nodes or edge.get("object") not in src.nodes:
            continue
        seen.add(eid)
        s, o = st.final.get(edge["subject"]), st.final.get(edge["object"])
        rows = st.active_links.get(eid, [])
        spec = lw.link_spec(gid, eid, edge)
        if len(rows) != 1:
            bad.append(f"edge {eid}: {len(rows)} active links")
        elif not (s and o and lw.link_matches(rows[0], spec, s["id"], o["id"])):
            bad.append(f"edge {eid} differs from the snapshot")
    bad.extend(f"extra link {eid}" for eid in st.active_links if eid not in seen)
    for key, digest in foreign_before.items():
        if st.foreign.get(key) != digest:
            bad.append(f"foreign record {key} changed or vanished")
    return bad[:50]


def apply_import(ctx: RequestContext, plan: ImportPlan, approval: ApprovalRecord | None, *,
                 source: Source, client: TiledMutationClient, settings: DeploymentSettings,
                 supported_schema_fingerprints: Collection[str], state_dir: Path, clock: Clock,
                 lease_seconds: float = 120.0, fault: FaultHook | None = None) -> ImportOutcome:
    """Apply an approved plan, verify it by readback, and write the per-target receipt.

    Order of refusals (nothing is written to Tiled or to ``state_dir`` before they pass): capability
    and scope, plan integrity, client/target binding, approval (``verify_approval``: profile, target,
    issuer, writer, plan hash, expiry), source bytes, then the base revision. A zero-operation plan
    whose target already holds a verified receipt for the same revision returns ``noop`` untouched.

    Raises:
        ContractError: ``DENIED``, ``STALE_APPROVAL``, ``CONFLICT`` (lease, stale source or target),
            ``UNAVAILABLE`` (retryable; the snapshot is untouched), ``WRITER_UNAUTHORIZED`` (Tiled
            rejected the writer credential with 401/403), or :class:`ImportVerificationError`.
    """
    fault = fault or (lambda _stage, _i: None)
    require(ctx, "graph.publish")
    change = plan.change
    if ctx.profile is not settings.profile or ctx.mode is not settings.mode:
        raise ContractError(ErrorCode.DENIED, "context belongs to another deployment profile")
    if ctx.corpus_scope and not set(change.corpus_scope) <= ctx.corpus_scope:
        raise ContractError(ErrorCode.DENIED, "plan scope is outside the request scope")
    plan.verify_integrity()
    _check_client(client, settings, change.target)
    store = ReceiptStore(Path(state_dir) / "receipts")
    graph_id = plan.detail["graph_id"]
    if plan.is_noop:  # nothing to write: a valid receipt for this very revision answers it
        with _writer_errors():
            state = read_state(client, graph_id)
        for art in store.find(snapshot_sha256=change.input_sha256, adapter_version=plan.detail["adapter_version"],
                              graph_id=graph_id):
            if receipt_valid_for(art, target=change.target, revision=state.revision) \
                    and list(art.extras.corpus_scope) == list(change.corpus_scope):
                return ImportOutcome("noop", art, None)
    if approval is None:
        raise ContractError(ErrorCode.DENIED, "an ApprovalRecord authorizing this plan is required")
    verify_approval(approval, change, settings, at=clock())
    src = load_source(source, supported_schema_fingerprints=supported_schema_fingerprints)
    if src.graph_sha256 != change.input_sha256 or src.manifest_sha256 != plan.detail["manifest_sha256"] \
            or src.graph_id != graph_id:
        raise ContractError(ErrorCode.CONFLICT, "the source snapshot no longer matches the plan", retryable=False)

    root = Path(state_dir) / _slug(change.target) / _slug(graph_id)
    run_dir = root / "plans" / plan.tag
    existing = store.find(plan_sha256=change.plan_sha256)
    journal = ImportJournal(run_dir / "journal.jsonl", clock)
    lease = Lease(root / "lease.json", clock, lease_seconds).acquire()
    try:
        with _writer_errors():
            outcome = _run(ctx, plan, approval, src, client, store, journal, lease, run_dir, clock, fault,
                           existing)
    except Exception as exc:  # a crash (BaseException) deliberately leaves the lease to expire
        if journal.started():
            journal.append("run_failed", error=type(exc).__name__,
                           code=getattr(getattr(exc, "code", None), "value", None))
        lease.release()
        raise
    lease.release()
    return outcome


def _run(ctx: RequestContext, plan: ImportPlan, approval: ApprovalRecord, src: SourceSnapshot,
         client: TiledMutationClient, store: ReceiptStore, journal: ImportJournal, lease: Lease,
         run_dir: Path, clock: Clock, fault: FaultHook, existing: list[ReceiptArtifact]) -> ImportOutcome:
    change, detail = plan.change, plan.detail
    graph_id, access = detail["graph_id"], detail["access_blob"]
    resumed = journal.started()
    state = read_state(client, graph_id)
    for art in existing:  # receipt written but the run did not finish: answer from it if still true
        if receipt_valid_for(art, target=change.target, revision=state.revision):
            return ImportOutcome("already_applied", art, None, resumed=True)
    ops, _counts = compute_ops(src, state, plan.tag)
    planned = {o.token for o in plan.ops}
    if resumed:
        stray = [o.token for o in ops if o.token not in planned]
        if stray:
            raise ContractError(ErrorCode.STALE_APPROVAL,
                                f"the target changed outside this plan since it started: {stray[:3]}")
    elif state.revision != change.base_revision:
        raise ContractError(ErrorCode.STALE_APPROVAL, "the target revision is not the plan's base revision")
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_file = run_dir / "plan.json"
    if not plan_file.exists():
        plan_file.write_text(json.dumps(plan.to_json(), indent=2, sort_keys=True), encoding="utf-8")
    foreign_file = run_dir / "foreign.json"
    if foreign_file.exists():
        foreign_before = json.loads(foreign_file.read_text(encoding="utf-8"))
    else:
        foreign_before = dict(state.foreign)
        foreign_file.write_text(json.dumps(foreign_before, sort_keys=True), encoding="utf-8")
    already = len(planned) - len({o.token for o in ops}) if resumed else 0
    journal.append("run_started" if not resumed else "run_resumed", plan_sha256=change.plan_sha256,
                   base_revision=change.base_revision, already_applied=already, executed_as=approval.executed_as,
                   requested_by=approval.requested_by, approved_by=approval.approved_by)

    ids: dict[str, str] = {n: r["id"] for n, r in state.final.items()
                           if n in src.nodes and lw.entity_matches(r, lw.entity_spec(graph_id, src.nodes[n]))}
    done = journal.done_ids()
    tag = plan.tag

    def step(stage: str, i: int, op: Op) -> str:
        oid = operation_id(change.plan_sha256, stage, op.kind, op.key)
        fault(stage, i)
        lease.check()  # fencing: immediately before the effect
        journal.append("intent", op_id=oid, kind=op.kind, key=op.key, replay=oid in done)
        return oid

    def finish(oid: str, op: Op, **extra: object) -> None:
        journal.append("done", op_id=oid, kind=op.kind, key=op.key, **extra)

    by_stage = {s: [o for o in ops if o.stage == s] for s in STAGES}
    for i, op in enumerate(by_stage["entities"]):
        spec: Any = lw.entity_spec(graph_id, src.nodes[op.key])  # Any: the name is reused for a LinkSpec below
        oid = step("entities", i, op)
        if op.kind == "create_node":
            ids[op.key] = client.create_entity(entity_type=spec.entity_type, name=spec.name, uri=spec.uri,
                                               properties=spec.properties, access_blob=access)
            fault("entities_effect", i)
        else:
            _supersede(client, state, spec, graph_id, tag, access, ids, fault, i)
        finish(oid, op, tiled_id=ids[op.key])
    fault("after_entities", 0)
    for i, op in enumerate(o for o in by_stage["links"] if o.kind == "create_link"):
        edge = dict(src.edges)[op.key]
        spec = lw.link_spec(graph_id, op.key, edge)
        oid = step("links", i, op)
        lid = client.create_link(subject_id=ids[spec.subject], predicate=spec.predicate,
                                 object_id=ids[spec.object], properties=spec.properties, access_blob=access)
        fault("links_effect", i)
        finish(oid, op, tiled_id=lid)
    for i, op in enumerate(o for o in by_stage["links"] if o.kind == "retire_link"):
        oid = step("links", 1000 + i, op)
        assert op.old_id is not None  # a retire op always names what it retires
        client.delete_link(state.link_rows[op.old_id], graph_id)
        finish(oid, op)
    fault("after_links", 0)
    for i, op in enumerate(by_stage["retire"]):
        oid = step("retire", i, op)
        assert op.old_id is not None  # a retire op always names what it retires
        client.set_entity_uri(state.rows[op.old_id], graph_id,
                              lw.state_uri(graph_id, op.key, lw.RETIRED, tag), access)
        finish(oid, op)
    fault("before_verify", 0)

    final = read_state(client, graph_id)
    mismatches = verify_projection(src, final, foreign_before)
    if mismatches:
        journal.append("verify_failed", mismatches=mismatches[:10])
        raise ImportVerificationError(mismatches)
    ledger = IdentityLedger()
    for node_id, row in final.final.items():
        ledger.bind(graph_id, node_id, row["id"])
    (run_dir / "ledger.json").write_text(json.dumps(ledger.to_json(), sort_keys=True), encoding="utf-8")
    fault("before_receipt", 0)
    counts = {
        "created_entities": sum(1 for o in plan.ops if o.kind == "create_node"),
        "superseded_entities": sum(1 for o in plan.ops if o.kind == "supersede_node"),
        "retired_entities": sum(1 for o in plan.ops if o.kind == "retire_node"),
        "created_links": change.links,
        "deleted_links": sum(1 for o in plan.ops if o.kind == "retire_link"),
        "unchanged_entities": detail["counts"]["unchanged_entities"],
        "unchanged_links": detail["counts"]["unchanged_links"],
        "dangling_unimported": len(detail["dangling"]),
        "reconciled_on_replay": max([r.get("already_applied", 0) for r in journal.records()
                                     if r.get("event") in ("run_resumed", "run_started")] or [0]),
    }
    now = clock()
    receipt = ImportReceipt(
        receipt_id=f"receipt-{_sha([change.plan_sha256, final.revision])[:20]}", plan_id=change.plan_id,
        plan_sha256=change.plan_sha256, approval_id=approval.approval_id, snapshot_sha256=src.graph_sha256,
        target=change.target, executed_as=approval.executed_as, adapter_version=detail["adapter_version"],
        id_mapping_sha256=ledger.sha256(), outcomes=counts, verified=True, completed_at=now)
    extras = ReceiptExtras(
        manifest_sha256=src.manifest_sha256, snapshot_id=src.manifest.snapshot_id, graph_id=graph_id,
        corpus_scope=list(change.corpus_scope), requested_by=approval.requested_by,
        approved_by=approval.approved_by,
        profile=approval.profile.value, authorization_mode=approval.mode.value, base_revision=change.base_revision,
        resulting_revision=final.revision, data_commit=src.data_commit,
        operation_counts={"entities": change.creates, "links": change.links,
                          "supersessions": change.supersessions},
        dangling_unimported=len(detail["dangling"]))
    artifact = make_artifact(receipt, extras)
    path = store.write(artifact)
    journal.append("completed", receipt_id=receipt.receipt_id, resulting_revision=final.revision)
    return ImportOutcome("applied", artifact, path, resumed=resumed)


def _supersede(client: TiledMutationClient, state: TargetState, spec: lw.EntitySpec, graph_id: str, tag: str,
               access: Mapping[str, Any], ids: dict[str, str], fault: FaultHook, i: int) -> None:
    """Create-new / retire-old for one changed node, resuming from any intermediate state."""
    node = spec.node_id
    pend, ret = state.pending_for(tag).get(node), state.retired_for(tag).get(node)
    if pend is not None:
        new_id = pend["id"]
    else:
        new_id = client.create_entity(entity_type=spec.entity_type, name=spec.name,
                                      uri=lw.state_uri(graph_id, node, lw.PENDING, tag),
                                      properties=spec.properties, access_blob=access)
        fault("entities_effect", i)
    old = state.final.get(node)
    if old is not None and ret is None:
        client.set_entity_uri(old, graph_id, lw.state_uri(graph_id, node, lw.RETIRED, tag), access)
        fault("supersede_retired_old", i)
    client.set_entity_uri({"id": new_id, "properties": spec.properties}, graph_id, spec.uri, access)
    ids[node] = new_id
