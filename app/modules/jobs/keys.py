"""Operation keys and stage compatibility (plan section 11.2).

An operation key names *what a stage computed from what*: corpus scope, source checksum,
Work/source version, stage, parser/extractor versions, model/prompt/schema/vocabulary
revisions and the stage's relevant parameters. A stage may be reused on resume only when its
key is unchanged; each stage declares which inputs it depends on and chains the keys of the
stages it consumes, so changing the parser invalidates parsing and everything downstream of
it while a vocabulary change invalidates only canonicalization and what follows.

Secrets never enter a key: names and values that look like credentials are refused, and the
digest is taken over canonical JSON so it never reveals the inputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from ..json_types import JsonValue

#: Input fields a stage may depend on.
INPUT_FIELDS = (
    "corpus_scope",
    "source_sha256",
    "source_version",
    "parser_version",
    "extractor_version",
    "model",
    "prompt_revision",
    "schema_revision",
    "vocabulary_revision",
    "params",
)

_SECRET_NAME = re.compile(r"(api[_-]?key|secret|token|passw(or)?d|credential|authorization|bearer|cookie)", re.I)
_SECRET_VALUE = re.compile(r"(^sk-[A-Za-z0-9]{12,}|^Bearer\s+\S+|^eyJ[A-Za-z0-9_-]{20,}\.)")


class SecretInKey(ValueError):
    """A key input looked like a credential and was refused."""


def _scan(value: object, path: str) -> None:
    if isinstance(value, Mapping):
        for name, item in value.items():
            if _SECRET_NAME.search(str(name)):
                raise SecretInKey(f"input {path}.{name} looks like a secret; secrets never enter keys or logs")
            _scan(item, f"{path}.{name}")
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _scan(item, f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        raise SecretInKey(f"value at {path} looks like a credential")


def canonical_json(value: object) -> str:
    """Deterministic JSON used for hashing."""

    def default(item: object) -> JsonValue:
        if isinstance(item, (set, frozenset)):
            return sorted(item)
        return str(item)

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=default, ensure_ascii=False)


def digest(value: object) -> str:
    """SHA-256 hex digest of canonical JSON."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OperationInputs:
    """The inputs a job was submitted with (immutable, secret-free)."""

    corpus_scope: tuple[str, ...]
    source_sha256: str = ""
    source_version: str = ""
    parser_version: str = ""
    extractor_version: str = ""
    model: str = ""
    prompt_revision: str = ""
    schema_revision: str = ""
    vocabulary_revision: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "corpus_scope", tuple(sorted(set(self.corpus_scope))))
        _scan(dict(self.params), "params")
        for name in INPUT_FIELDS[1:-1]:
            _scan(getattr(self, name), name)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready mapping."""
        data = {name: getattr(self, name) for name in INPUT_FIELDS}
        data["corpus_scope"] = list(self.corpus_scope)
        data["params"] = dict(self.params)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OperationInputs:
        """Rebuild from :meth:`as_dict` output."""
        kwargs = {name: data.get(name, "") for name in INPUT_FIELDS[1:-1]}
        return cls(
            corpus_scope=tuple(data.get("corpus_scope") or ()), params=dict(data.get("params") or {}), **kwargs
        )

    def job_identity(self, kind: str) -> str:
        """Stable job id: the same kind and inputs always name the same job (J-04)."""
        return "job-" + digest([kind, self.as_dict()])[:24]


def stage_key(
    inputs: OperationInputs,
    stage: str,
    relevant: Sequence[str],
    upstream: Mapping[str, str],
    params_keys: Sequence[str] = (),
) -> str:
    """Key of one stage: its relevant inputs plus the keys of the stages it consumes.

    Args:
        inputs: The operation inputs the key is derived from.
        stage: Stage name, part of the key.
        relevant: Input field names (from :data:`INPUT_FIELDS`) this stage depends on.
        upstream: ``stage name -> key`` of the stages this one consumes.
        params_keys: Parameter names that matter when ``"params"`` is relevant; empty means all.

    Raises:
        ValueError: `relevant` names a field not in :data:`INPUT_FIELDS`.
    """
    unknown = set(relevant) - set(INPUT_FIELDS)
    if unknown:
        raise ValueError(f"unknown key inputs: {sorted(unknown)}")
    data = inputs.as_dict()
    body: dict[str, Any] = {"stage": stage, "upstream": dict(sorted(upstream.items()))}
    for name in sorted(relevant):
        value = data[name]
        if name == "params" and params_keys:
            value = {k: value.get(k) for k in sorted(params_keys)}
        body[name] = value
    return digest(body)


def changed_inputs(old: Mapping[str, Any], new: Mapping[str, Any]) -> tuple[str, ...]:
    """Names of input fields that differ (for the invalidation event reason)."""
    return tuple(name for name in INPUT_FIELDS if old.get(name) != new.get(name))


__all__ = (
    "INPUT_FIELDS",
    "OperationInputs",
    "SecretInKey",
    "canonical_json",
    "changed_inputs",
    "digest",
    "stage_key",
)
