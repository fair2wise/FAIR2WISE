"""``library_relations``: repair the relations of an existing KG bundle on a user schema, with no model call (``:local`` only).

Owner decision 2026-11-25 (plecos run): the relations extracted before the user-schema repairs existed are stored the way the
model wrote them, so a bundle holds ``Measurement -has_measurement-> Species`` (invalid as given, valid reversed) and
``Locality -country-> "Brazil"`` (a value, filed as a link). This job applies the extraction's own rules
(:func:`~app.modules.term_extractor.schema.judge_relations`, :func:`~app.modules.term_extractor.schema.mark_invalid_relations`)
to the stored terms:

* a relation on a slot whose range is a value type becomes a property of the term (``properties``), and the relation goes;
* a relation that is invalid as given but valid with subject and object exchanged moves to the other term, verified, with
  its evidence;
* any other invalid relation stays and is marked ``verified: false``; a relation whose object is not a term of the bundle
  cannot be judged and is left as it is.

The counts are reported per distinct pattern. **One previous generation is kept**: before the first write, ``terms.json`` and
``kg.json`` are copied to ``previous/`` inside the bundle directory (a later repair replaces that copy, so exactly one
generation back is always there). The new files replace the old ones atomically and the KG is rebuilt from the new terms.
Idempotent: a second run finds nothing to change and writes nothing. Only a bundle locked to a user schema is accepted; a
shipped schema's relations are judged as they always were and this job refuses them (``not_user_schema``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import TYPE_CHECKING, Any

from . import bundles as B
from ..contracts.errors import ContractError, ErrorCode
from ..jobs.keys import OperationInputs
from ..jobs.pipeline import StageBusy, StageContext, StageResult
from ..term_extractor.models import union_evidence
from ..term_extractor.schema import SchemaHelper, judge_relations, mark_invalid_relations
from ..term_extractor.store import TermStore
from .jobs import library_inputs
from .passages_job import bundle_is_locked, write_json_atomic
from .user_schemas import is_user_schema_id

if TYPE_CHECKING:
    from .service import LibraryService

logger = logging.getLogger(__name__)

#: Directory inside a bundle that holds the one previous generation of ``terms.json`` and ``kg.json``.
PREVIOUS_DIRNAME = "previous"
#: Typed ``reason`` values of the start route's refusals.
REASON_NOT_USER_SCHEMA = "not_user_schema"
REASON_BUSY = "bundle_busy"
REASON_IN_PROGRESS = "relations_in_progress"
#: Outcomes of one relation (``patterns[].outcome`` and the ``counts`` keys).
OUTCOMES = ("swapped", "attributes", "invalid", "unknown_object", "unchanged")
#: Revision of the repair logic (part of the manifest block).
REPAIR_REVISION = "1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def repair_terms(doc: Mapping[str, Any], helper: SchemaHelper) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply the user-schema relation rules to a terms document; returns ``(new document, report)``.

    The document is not modified; the returned one is a deep copy with the repaired terms. The report holds ``counts``
    (one per :data:`OUTCOMES`, plus ``relations`` and ``terms_changed``) and ``patterns``: one row per distinct
    ``(outcome, relation, subject class, object class)`` with its count, most frequent first.
    """
    out = json.loads(json.dumps(doc))
    terms = [t for t in out.get("terms") or [] if isinstance(t, dict)]
    by_key: dict[str, dict[str, Any]] = {}
    for term in terms:
        by_key.setdefault(TermStore.normalize(str(term.get("term") or "")), term)

    def category_of(name: str) -> str:
        found = by_key.get(TermStore.normalize(name))
        return str(found.get("category") or "") if found else ""

    counts = dict.fromkeys(OUTCOMES, 0)
    counts["relations"] = counts["terms_changed"] = 0
    patterns: dict[tuple[str, str, str, str], int] = {}

    def note(outcome: str, relation: str, subject_class: str, object_class: str) -> None:
        counts[outcome] += 1
        key = (outcome, relation, subject_class, object_class)
        patterns[key] = patterns.get(key, 0) + 1

    moves: list[tuple[dict[str, Any], dict[str, Any]]] = []   # (owner term, relation dict) to add after the pass
    for term in terms:
        relations = [r for r in term.get("relations") or [] if isinstance(r, dict) and r.get("relation") and r.get("related_term")]
        counts["relations"] += len(relations)
        before = json.dumps(relations, sort_keys=True)
        subject_class = str(term.get("category") or "")
        # Every stored relation is judged afresh: the earlier run marked the reversed ones verified=false.
        work = {"term": term.get("term"), "category": subject_class,
                "relations": [{**r, "verified": True} for r in relations if r["relation"] in helper.slots]}
        skipped = [r for r in relations if r["relation"] not in helper.slots]
        judged = judge_relations(helper, work, category_of)
        marked = mark_invalid_relations(helper, work, category_of)
        held = {(p.get("property"), str(p.get("value"))) for p in term.get("properties") or [] if isinstance(p, dict)}
        for item in judged.attributes:
            note("attributes", item["property"], subject_class, "")
            if (item["property"], item["value"]) not in held:
                term.setdefault("properties", []).append({"property": item["property"], "value": item["value"], "unit": "", "context": ""})
                held.add((item["property"], item["value"]))
        for item in judged.swapped:
            note("swapped", item["relation"], item["subject_class"], item["object_class"])
            original = next(r for r in relations if r["relation"] == item["relation"] and r["related_term"] == item["subject"])
            owner = by_key[TermStore.normalize(item["subject"])]
            moves.append((owner, {**original, "related_term": str(term.get("term") or ""), "verified": True}))
        for subject_cls, pred, object_cls in [*judged.invalid, *marked]:
            note("invalid", pred, subject_cls, object_cls)
        kept = work["relations"]
        for rel in kept:
            if rel.get("verified") and not category_of(str(rel["related_term"])):
                note("unknown_object", str(rel["relation"]), subject_class, "")
                original = next(r for r in relations if r["relation"] == rel["relation"] and r["related_term"] == rel["related_term"])
                rel["verified"] = bool(original.get("verified", False))   # cannot be judged: left as it was
            elif rel.get("verified"):
                note("unchanged", str(rel["relation"]), subject_class, category_of(str(rel["related_term"])))
        term["relations"] = [*kept, *skipped]
        if json.dumps(term["relations"], sort_keys=True) != before:
            counts["terms_changed"] += 1
    changed_owners: set[int] = set()
    for owner, rel in moves:
        existing = next((r for r in owner.setdefault("relations", []) if isinstance(r, dict)
                         and (r.get("relation"), r.get("related_term")) == (rel["relation"], rel["related_term"])), None)
        if existing is None:
            owner["relations"].append(rel)
        else:
            existing["verified"] = True
            evidence = existing.setdefault("evidence", [])
            if isinstance(evidence, list):
                union_evidence(evidence, rel.get("evidence") or [])
            if not evidence:
                existing.pop("evidence", None)
        changed_owners.add(id(owner))
    counts["terms_changed"] += len(changed_owners)
    rows = [{"outcome": o, "relation": r, "subject_class": sc, "object_class": oc, "count": n}
            for (o, r, sc, oc), n in sorted(patterns.items(), key=lambda kv: (-kv[1], kv[0]))]
    return out, {"counts": counts, "patterns": rows}


def locked_user_schema_file(user_schemas: Any, bundle: B.Bundle) -> Path:  # noqa: ANN401  # Any: UserSchemaStore (duck-typed in tests)
    """The stored file of the user schema a bundle locked; the file must still hold the bytes the bundle recorded.

    Raises:
        ContractError: ``INVALID_INPUT`` ``not_user_schema`` for a shipped schema, ``NOT_FOUND`` for a deleted version,
            ``CONFLICT`` when the stored file no longer matches the locked sha256.
    """
    block = bundle.manifest["schema"]
    if not is_user_schema_id(block.get("id")):
        raise ContractError(ErrorCode.INVALID_INPUT, "this KG is on a shipped schema", reason=REASON_NOT_USER_SCHEMA)
    stored = user_schemas.get(str(block["id"]))
    if block.get("sha256") and block["sha256"] != stored.sha256:
        raise ContractError(ErrorCode.CONFLICT, "the stored schema file no longer matches the one this KG locked", retryable=False)
    return Path(user_schemas.path_for(stored.id))


class LibraryRelations:
    """Starts and runs ``library_relations`` for the bundles of one ``:local`` deployment.

    Args:
        service: The library (its root is where the bundle lock lives through the store).
        store: Bundle store under ``<F2W_WORKDIR>/kgs``.
        schema_file_for: ``bundle -> path of its locked user schema file`` (the API passes the user schema store's
            lookup, which also checks the locked sha256); raises :class:`ContractError` when it cannot be found.
        clock: ISO timestamp source for the manifest (tests inject one).
    """

    def __init__(self, service: LibraryService, store: B.BundleStore, *, schema_file_for: Callable[[B.Bundle], Path],
                 clock: Callable[[], str] = _now) -> None:
        self.service = service
        self.store = store
        self._schema_file_for = schema_file_for
        self._clock = clock

    # ------------------------------------------------------------------ route side
    def inputs(self, name: str, *, nonce: str) -> OperationInputs:
        """The job inputs: the bundle name and a nonce (identical inputs would name the same finished job)."""
        return library_inputs({"bundle": name}, nonce=nonce, corpus_scope=(B.validate_name(name).lower(),))

    def check_startable(self, name: str, *, open_jobs: Sequence[tuple[str, str]] = ()) -> dict[str, Any]:
        """Refuse with a typed error unless a run could start now; returns ``{name, schema}`` otherwise.

        Raises:
            ContractError: ``NOT_FOUND`` (no such bundle), ``INVALID_INPUT`` ``not_user_schema`` (the bundle is on a
                shipped schema), ``CONFLICT`` ``bundle_busy`` (a running job holds the bundle) or ``relations_in_progress``
                (a repair of this bundle is queued or running); whatever ``schema_file_for`` raises for a lost schema file.
        """
        bundle = self.store.get(name)
        if not is_user_schema_id(bundle.schema):
            raise ContractError(ErrorCode.INVALID_INPUT, "this KG is on a shipped schema; its relations are already judged the "
                                "way that schema defines them", reason=REASON_NOT_USER_SCHEMA)
        self._schema_file_for(bundle)
        if open_jobs:
            raise ContractError(ErrorCode.CONFLICT, "the relations of this KG are already being repaired", retryable=False,
                                reason=REASON_IN_PROGRESS)
        if bundle_is_locked(self.store, name):
            raise ContractError(ErrorCode.CONFLICT, "another job is working on this KG now; try again when it ends",
                                retryable=False, reason=REASON_BUSY)
        return {"name": name, "schema": bundle.schema}

    # ------------------------------------------------------------------ the job
    def run(self, ctx: StageContext) -> StageResult:
        """The ``library_relations`` stage body (see the module docstring)."""
        from .extraction import BundleBusy, bundle_lock

        name = B.validate_name(str(ctx.inputs.params.get("bundle", "")))
        try:
            with bundle_lock(self.store, name, ctx.job.job_id):
                return self._run_locked(ctx, name)
        except BundleBusy as exc:
            if exc.holder == ctx.job.job_id:
                raise StageBusy("an earlier run of this job still holds the KG bundle") from exc
            raise

    def _run_locked(self, ctx: StageContext, name: str) -> StageResult:
        bundle = self.store.get(name)
        if not is_user_schema_id(bundle.schema):
            raise ContractError(ErrorCode.INVALID_INPUT, "this KG is on a shipped schema", reason=REASON_NOT_USER_SCHEMA)
        helper = SchemaHelper(str(self._schema_file_for(bundle)), sanitize_descriptions=True)
        terms_path, kg_path = bundle.file(B.TERMS_FILE), bundle.file(B.KG_FILE)
        doc = json.loads(terms_path.read_text(encoding="utf-8"))
        ctx.log(f"judging the relations of bundle {name} against {bundle.schema} (no model is used)")
        new_doc, report = repair_terms(doc, helper)
        counts = report["counts"]
        for row in report["patterns"]:
            if row["outcome"] == "unchanged":
                continue
            shape = row["relation"] if row["outcome"] == "attributes" else f"{row['subject_class']} -{row['relation']}-> {row['object_class']}"
            ctx.log(f"relation {shape}: {row['outcome']} {row['count']} times",
                    level="warning" if row["outcome"] in ("invalid", "unknown_object") else "info")
        changed = counts["swapped"] + counts["attributes"] > 0 or counts["terms_changed"] > 0
        ctx.progress(**{f"repair_{k}": v for k, v in counts.items()})
        if changed:
            ctx.guard()   # a stop requested now leaves the old files
            self._keep_previous(bundle, terms_path, kg_path)
            write_json_atomic(terms_path, new_doc)
            self._rebuild(bundle)
        self._record(name, report, changed)
        ctx.log("done: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + ("" if changed else "; nothing to change"))
        return StageResult(progress={**{f"repair_{k}": v for k, v in counts.items()}, "done": 1, "total": 1},
                           data={"bundle": name, "relations_repair": {**report, "changed": changed,
                                                                      "previous_kept": changed}})

    # ------------------------------------------------------------------ files
    @staticmethod
    def _keep_previous(bundle: B.Bundle, terms_path: Path, kg_path: Path) -> None:
        """Copy ``terms.json`` and ``kg.json`` to ``previous/`` (replacing the copy there): exactly one generation back."""
        previous = bundle.directory / PREVIOUS_DIRNAME
        previous.mkdir(mode=0o700, exist_ok=True)
        for source in (terms_path, kg_path):
            if source.is_file():
                fd, tmp = tempfile.mkstemp(dir=previous, prefix=f".{source.name}.", suffix=".tmp")
                os.close(fd)
                shutil.copyfile(source, tmp)
                Path(tmp).replace(previous / source.name)

    @staticmethod
    def _rebuild(bundle: B.Bundle) -> None:
        """Rebuild ``kg.json`` from the new terms into a temporary file, then replace the old one (atomic)."""
        from ..graph import kg_update

        kg = bundle.file(B.KG_FILE)
        fd, tmp = tempfile.mkstemp(dir=kg.parent, prefix=f".{kg.name}.", suffix=".tmp")
        os.close(fd)
        try:
            kg_update.rebuild_kg(str(bundle.file(B.TERMS_FILE)), tmp)
            Path(tmp).replace(kg)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _record(self, name: str, report: Mapping[str, Any], changed: bool) -> None:
        """Write the additive ``relations_repair`` block (last run, counts, whether a previous generation was kept)."""
        bundle = self.store.get(name)
        manifest = dict(bundle.manifest)
        earlier = manifest.get("relations_repair") if isinstance(manifest.get("relations_repair"), Mapping) else {}
        manifest["relations_repair"] = {"last_run": self._clock(), "revision": REPAIR_REVISION, "counts": dict(report["counts"]),
                                        "changed": changed,
                                        "previous_kept": bool(changed or (earlier or {}).get("previous_kept"))}
        B._write_json(bundle.directory / B.MANIFEST_FILE, manifest)  # noqa: SLF001 - the bundle package's atomic writer


__all__ = ("LibraryRelations", "OUTCOMES", "locked_user_schema_file", "PREVIOUS_DIRNAME", "REASON_BUSY", "REASON_IN_PROGRESS", "REASON_NOT_USER_SCHEMA",
           "REPAIR_REVISION", "repair_terms")
