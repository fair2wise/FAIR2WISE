"""Load registered schemas through the existing implementations and fingerprint them (T22B).

No loader is reimplemented here. LinkML assets are opened with ``SchemaView`` exactly as
``term_extractor.schema.SchemaHelper`` does (``SchemaView(str(path))``), and the helpers themselves are
constructed by the existing classes. Non-LinkML assets use ``yaml.safe_load`` as ``bl1101_ingest`` and
``tiled_sim`` do today and are refused by the LinkML loader.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any

from linkml_runtime.utils.schemaview import SchemaView
import yaml

from .registry import (
    NORMALIZER_LEGACY,
    NORMALIZER_TERM_EXTRACTOR,
    REGISTRY_VERSION,
    RegistryError,
    SchemaEntry,
    pre_s4_bytes,
    sha256_bytes,
)


class SchemaLoadError(RuntimeError):
    """An asset could not be loaded or its import closure could not be resolved."""


def linkml_runtime_version() -> str:
    """Installed ``linkml-runtime`` version (it supplies ``linkml:types``)."""
    return importlib.metadata.version("linkml-runtime")


def _require_linkml(entry: SchemaEntry) -> None:
    if not entry.is_linkml:
        raise RegistryError(f"{entry.path} is {entry.classification}, not a LinkML schema")


def load_schema_view(entry: SchemaEntry, *, allow_known_broken: bool = False) -> SchemaView:
    """``SchemaView`` over ``entry`` (same constructor call as ``SchemaHelper``).

    Raises:
        RegistryError: not a LinkML asset, or known-broken and ``allow_known_broken`` is false.
    """
    _require_linkml(entry)
    if entry.known_broken and not allow_known_broken:
        raise RegistryError(f"{entry.path} is registered as known-broken: {entry.known_issues[0]}")
    return SchemaView(str(entry.abs_path))


def load_helper(
    entry: SchemaEntry, *, implementation: str = NORMALIZER_TERM_EXTRACTOR, fuzzy_cutoff: int = 80
) -> Any:  # Any: either of two SchemaHelper implementations
    """Construct the existing ``SchemaHelper`` for ``entry``.

    Args:
        entry: Registered LinkML schema to load; must not be known-broken.
        implementation: :data:`NORMALIZER_TERM_EXTRACTOR` (``term_extractor/schema.py``) or
            :data:`NORMALIZER_LEGACY` (the divergent copy in ``extract_terms.py``, T22A F4).
        fuzzy_cutoff: Fuzzy-match score cutoff passed to the helper.

    Raises:
        RegistryError: not a LinkML asset, known-broken, or an unknown ``implementation``.
    """
    _require_linkml(entry)
    if entry.known_broken:
        raise RegistryError(f"{entry.path} is registered as known-broken: {entry.known_issues[0]}")
    if implementation == NORMALIZER_TERM_EXTRACTOR:
        from ..term_extractor.schema import SchemaHelper
    elif implementation == NORMALIZER_LEGACY:
        # heavy import: only on the legacy path
        from ..extract_terms import SchemaHelper  # type: ignore[assignment]  # legacy copy (T22A F4)
    else:
        raise RegistryError(f"unknown normalizer implementation {implementation!r}")
    return SchemaHelper(str(entry.abs_path), fuzzy_cutoff=fuzzy_cutoff)


def load_yaml_asset(entry: SchemaEntry) -> Any:
    """Parse a non-LinkML asset (vocabulary, mapping, seed, example) with ``yaml.safe_load``."""
    if entry.is_linkml:
        raise RegistryError(f"{entry.path} is a LinkML schema; use load_schema_view")
    return yaml.safe_load(entry.abs_path.read_text(encoding="utf-8"))


class _NoPreS4Form(Exception):
    pass


def _file_hash(data: bytes, pre_s4: bool) -> str:
    if not pre_s4:
        return sha256_bytes(data)
    before = pre_s4_bytes(data)
    if before is None:
        raise _NoPreS4Form()
    return sha256_bytes(before)


def closure_files(entry: SchemaEntry, *, pre_s4: bool = False) -> list[tuple[str, str]]:
    """Sorted ``(file name, sha256)`` pairs of the resolved import closure (local files only).

    The closure comes from ``SchemaView.imports_closure()``, so import resolution is LinkML's own.
    ``linkml:*`` imports resolve inside ``linkml_runtime`` and are covered by its version in the fingerprint.

    Args:
        entry: Registered LinkML schema whose imports are hashed.
        pre_s4: Hash each file as it was before the H3 S4 licence-line change (:func:`pre_s4_bytes`).

    Raises:
        SchemaLoadError: an import is unresolvable, or (``pre_s4``) a file of the closure never had the
            pre-S4 licence line.
    """
    _require_linkml(entry)
    sv = SchemaView(str(entry.abs_path))
    root_name = sv.schema.name
    base = entry.abs_path.parent
    root_hash = entry.pre_s4_sha256 if pre_s4 else entry.sha256  # bytes as discovered
    if root_hash is None:
        raise SchemaLoadError(f"{entry.path} has no pre-S4 form")
    pairs: dict[str, str] = {entry.file: root_hash}
    for name in sv.imports_closure():
        if name == root_name or name.startswith("linkml:"):
            continue
        source = sv.schema_map[name].source_file
        path = Path(source) if source else base / f"{name}.yaml"
        if not path.is_absolute():
            path = base / path
        try:
            pairs[path.name] = _file_hash(path.read_bytes(), pre_s4)
        except OSError as exc:
            raise SchemaLoadError(f"import {name!r} of {entry.path} is unresolvable: {exc.strerror}") from None
        except _NoPreS4Form:
            raise SchemaLoadError(f"import {name!r} of {entry.path} has no pre-S4 form") from None
    return sorted(pairs.items())


def closure_fingerprint(entry: SchemaEntry, *, pre_s4: bool = False) -> str:
    """Deterministic fingerprint of ``entry``'s resolved import closure.

    SHA-256 over canonical JSON of the registry version, the ``linkml-runtime`` version and the sorted
    ``(file name, file sha256)`` pairs. A one-byte change in any file of the closure changes it; moving the
    checkout, changing the working directory or re-running does not.

    ``pre_s4=True`` gives the fingerprint the same closure had before the H3 S4 licence-line change (T22C),
    computed from the reconstructed bytes, never from a stored value.
    """
    body = {
        "registry_version": REGISTRY_VERSION,
        "linkml_runtime": linkml_runtime_version(),
        "files": [list(p) for p in closure_files(entry, pre_s4=pre_s4)],
    }
    blob = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def pre_s4_fingerprint(entry: SchemaEntry) -> str | None:
    """Fingerprint ``entry``'s closure had before the S4 licence change, or ``None`` if it has no such form.

    Only base-set entries whose whole closure carried ``license: MIT`` have one; a T22C schema set never did.
    """
    if entry.schema_set is not None or entry.pre_s4_sha256 is None:
        return None
    try:
        return closure_fingerprint(entry, pre_s4=True)
    except SchemaLoadError:
        return None


def probe_known_issue(entry: SchemaEntry) -> str | None:
    """Re-observe the F1 defect of ``rsoxs_schema_extract`` without changing it.

    Returns the error text when ``class_induced_slots('RSoXSMeasurement')`` fails, else ``None``.
    """
    sv = SchemaView(str(entry.abs_path))
    try:
        sv.class_induced_slots("RSoXSMeasurement")
    except Exception as exc:  # the defect is the point
        return f"{type(exc).__name__}: {exc}"
    return None
