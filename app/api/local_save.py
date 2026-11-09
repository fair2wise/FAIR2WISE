"""The UI Save target of the ``:local`` image, resolved lazily after the first publication (T18D).

Owner decision 2026-11-04 (tenth set, ``docs/migration/reviews/owner_decisions_2026-11-03.md``): "Wire lazily
after the first :local publication". A :class:`~app.modules.graph.legacy_save.SaveTarget` needs an active
published snapshot and an edit journal per corpus, neither of which exists when the API process starts on a
fresh image. So nothing is bound at startup: :class:`LocalSaveProvider` resolves the target **at request time**
from the deployment's own publication state and the legacy service asks for it on every Save.

The rule (one target, possibly several corpora):

* The deployment's Tiled is the one target the ``:local`` gate allows (:func:`~.local_publication.local_tiled_target`).
* The corpus a Save edits is the one named by the **active publication pointer** of that target
  (:meth:`~app.modules.jobs.publish_store.PublishStore.pointer`, ``snapshot_ref = "<corpus>/<vN>"``): the last
  publication activated on the image. The session graph the UI shows in its editable mode is that corpus's
  effective record (active snapshot plus in-flight journal entries), so the node being edited is a node of that
  corpus; a node of another corpus is the legacy 404 and the UI's read-only graph sources never reach Save.
* With ``F2W_CORPUS_SCOPE`` set, the pointer's corpus must be in scope; otherwise Save is unavailable.
* The importer's own release record of that corpus (``release.json`` under the state directory) must name the
  same snapshot as live, the frozen ``graph.json`` must exist and its bytes must match the pointer's recorded
  SHA-256 (hash-checked on every read by :class:`~app.modules.graph.legacy_save.TiledSaveBackend`).
* The journal is per corpus: ``<state_dir>/<target>/<corpus>/edits.jsonl``, the same file the publication step
  checks before a later import (``check_import``: an acknowledged edit is never silently lost).

Before the first publication, or when any of the checks above fails, Save is **unavailable**: a typed
:class:`SaveUnavailable` (``UNSUPPORTED_CAPABILITY``) that the legacy route answers with 501 and that makes
``can_save`` false in ``GET /v1/capabilities`` (the frozen UI hides the Save controls), never a 500.

The writer credential is the managed lifecycle's own reader, called lazily per request from the private secret
file; it is never cached here, never logged and never part of a ``repr``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import threading
from typing import Any

from ..modules.deployment import tiled_lifecycle
from ..modules.deployment.tiled_config import ManagedTiledConfig
from ..modules.graph import edit_journal as ej
from ..modules.graph.backends.tiled_mutations import TiledMutationClient
from ..modules.graph.imports.corpus_scope import ReleaseState, scope_dir
from ..modules.graph.imports.tiled_json import Clock
from ..modules.graph.legacy_save import SaveTarget, TiledSaveBackend
from ..modules.graph.ports import ContractError, ErrorCode
from ..modules.jobs.publish_store import Pointer, PublishStore
from ..modules.security.authorization import DeploymentSettings

#: File name of the per-corpus edit journal under the scope's state directory.
JOURNAL_FILENAME = "edits.jsonl"
#: Owner wording (2026-11-03) for the state where Tiled holds no copy the UI could edit (``kg_update``).
UNAVAILABLE_INDICATOR = "JSON available, Tiled unavailable"


class SaveUnavailable(ContractError):
    """Save has no target on this deployment right now (typed ``UNSUPPORTED_CAPABILITY``; never a 500).

    Attributes:
        reason: Machine-readable cause (``no_publication``, ``corpus_out_of_scope``, ``release_mismatch``,
            ``snapshot_missing``, ``malformed_pointer``).
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(ErrorCode.UNSUPPORTED_CAPABILITY, f"{UNAVAILABLE_INDICATOR}: {message}", retryable=False)
        self.reason = reason


def local_edit_journal(state_dir: Path, target: str, scope: str) -> ej.EditJournal:
    """The per-corpus edit journal of ``scope`` on ``target`` (shared by Save and the publication step)."""
    return ej.EditJournal(scope_dir(state_dir, target, scope) / JOURNAL_FILENAME)


class LocalSaveProvider:
    """Resolves the Save backend of the ``:local`` image from the current publication state, per request.

    Args:
        settings: Startup deployment settings (``:local``).
        managed: The managed Tiled of the image (its writer credential is read lazily per request).
        target: The normalized target the publication gate allows (the managed Tiled's loopback URL).
        storage_kg: Root of the frozen snapshots (``<corpus>/<vN>/graph.json``).
        state_dir: Importer state directory (release records, leases, the per-corpus journals).
        store: The publication store of the deployment (the active pointer).
        clock: Timezone-aware time source of the journal records.
        namespace / access_blob: As the publication step writes them.
    """

    def __init__(self, *, settings: DeploymentSettings, managed: ManagedTiledConfig, target: str, storage_kg: Path,
                 state_dir: Path, store: PublishStore, clock: Clock, namespace: str | None = None,
                 access_blob: Mapping[str, Any] | None = None) -> None:
        self.settings, self.target = settings, target
        self.storage_kg, self.state_dir = Path(storage_kg), Path(state_dir)
        self.store, self.clock = store, clock
        self.namespace, self.access_blob = namespace, dict(access_blob or {})
        # The writer credential: read by the managed lifecycle lazily on every call (never cached or logged).
        self._client = TiledMutationClient(target, profile="local",
                                           credential=lambda: tiled_lifecycle.writer_credential(managed))
        self._lock = threading.Lock()
        self._cached: tuple[tuple[str, str, str], TiledSaveBackend] | None = None

    def __repr__(self) -> str:  # never show the client (it holds the credential provider)
        return f"LocalSaveProvider(target={self.target!r}, state_dir={str(self.state_dir)!r})"

    def resolve(self, corpus: str | None = None) -> SaveTarget:
        """The Save target of the active publication, built from the current state (no caching of the decision).

        Without ``corpus`` the corpus is the active pointer's (the last activated one). Naming a ``corpus`` selects
        that corpus's live version from the per-corpus view instead (Model C); no route or UI selector uses it yet.

        Raises:
            SaveUnavailable: No publication is active yet, the pointer's corpus is outside the deployment's corpus
                scope, the importer's release record disagrees with the pointer, or the frozen graph is missing.
        """
        pointer = self.store.pointer(self.target)
        if corpus is not None:
            named = self.store.live_by_corpus(self.target).get(corpus)
            if named is None:
                raise SaveUnavailable("corpus_not_live", f"corpus {corpus!r} has no live publication on this "
                                      "deployment's Tiled")
            pointer = Pointer(self.target, named.pointer_version, named.snapshot_ref, named.snapshot_sha256,
                              named.receipt_id, named.index_ref)
        if pointer is None:
            raise SaveUnavailable("no_publication", "no publication is active on this deployment's Tiled yet; "
                                  "publish a snapshot first")
        corpus, _, snapshot_id = pointer.snapshot_ref.partition("/")
        if not corpus or not snapshot_id or "/" in snapshot_id:
            raise SaveUnavailable("malformed_pointer", f"the active pointer names {pointer.snapshot_ref!r}, "
                                  "not a '<corpus>/<snapshot>' reference")
        if self.settings.corpus_scope and corpus not in self.settings.corpus_scope:
            raise SaveUnavailable("corpus_out_of_scope", f"the active publication is corpus {corpus!r}, outside "
                                  "this deployment's corpus scope")
        release = ReleaseState(scope_dir(self.state_dir, self.target, corpus) / "release.json").read()
        if release.get("pointer") != "live" or release.get("live_snapshot_id") != snapshot_id:
            raise SaveUnavailable("release_mismatch", f"the importer's release record of corpus {corpus!r} does "
                                  f"not name {snapshot_id} as live")
        base = self.storage_kg / corpus / snapshot_id / "graph.json"
        if not base.is_file():
            raise SaveUnavailable("snapshot_missing", f"the frozen snapshot {pointer.snapshot_ref} has no graph.json")
        return SaveTarget(client=self._client, settings=self.settings, state_dir=self.state_dir, clock=self.clock,
                          scope=corpus, journal=local_edit_journal(self.state_dir, self.target, corpus),
                          base_graph_path=base, base_snapshot_id=snapshot_id,
                          base_graph_sha256=pointer.snapshot_sha256, namespace=self.namespace,
                          access_blob=self.access_blob or None)

    def backend(self, corpus: str | None = None) -> TiledSaveBackend:
        """The Save backend for the current publication (one instance per ``(corpus, snapshot, sha256)``).

        Raises:
            SaveUnavailable: As :meth:`resolve`.
        """
        target = self.resolve(corpus)
        key = (target.scope, target.base_snapshot_id, target.base_graph_sha256)
        with self._lock:
            if self._cached is None or self._cached[0] != key:
                self._cached = (key, TiledSaveBackend(target))
            return self._cached[1]

    def available(self) -> bool:
        """Whether a Save could reach a target now (``can_save`` of ``GET /v1/capabilities``)."""
        try:
            self.resolve()
        except ContractError:
            return False
        return True


__all__ = ("JOURNAL_FILENAME", "UNAVAILABLE_INDICATOR", "LocalSaveProvider", "SaveUnavailable", "local_edit_journal")
