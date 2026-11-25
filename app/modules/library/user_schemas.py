"""Store of user-written and model-written LinkML schemas (design: ``design-schema-generation.md``).

Layout under the state volume::

    <F2W_WORKDIR>/schemas/<name>/<name>@v1.yaml, <name>@v2.yaml, ...

A schema is named independently of any KG; several KGs may use one. The id is ``user/<name>@vN``. Versions are
immutable: a save always writes a *new* ``vN`` (never an overwrite) and a bundle that locked ``vN`` keeps reading
exactly those bytes. The file name is derived here from the validated name and the version number; no path,
file name or version is ever taken from a client.

Decision on the schema's own ``id``/``name``/``version`` fields: the YAML text is stored byte for byte, never
rewritten (rewriting YAML text without a parser round trip is not safe, and a round trip would lose comments and
layout). Instead the top-level ``name`` of the text must equal the store name, and the save is refused with a
line-numbered problem when it does not, so the editor can fix it. The text's ``id`` (an http URI that is never
fetched) and ``version`` stay as the author wrote them: the store's ``vN`` is the identity, the text's own
``version`` is descriptive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import yaml

from ..contracts.errors import ContractError, ErrorCode
from .user_schema_validation import Problem, Report, validate_schema_text

#: Sub-directory of the work directory that holds the store.
SCHEMAS_DIRNAME = "schemas"
#: Id prefix of every user schema.
ID_PREFIX = "user/"
#: Schema names: lower-case start, then lower-case letters, digits, ``_`` or ``-``; at most 64 characters.
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ID_RE = re.compile(r"^user/([a-z][a-z0-9_-]{0,63})@v([1-9][0-9]{0,5})$")
_FILE_RE = re.compile(r"^([a-z][a-z0-9_-]{0,63})@v([1-9][0-9]{0,5})\.yaml$")
#: Most versions of one schema (a runaway client cannot fill the volume).
MAX_VERSIONS = 999
_Versions = list[int]  # alias: inside the class, ``list`` is the method


class SchemaInvalid(ContractError):
    """The text did not pass the validator; ``report`` holds every problem with its line."""

    def __init__(self, report: Report) -> None:
        count = len(report.problems)
        first = report.problems[0] if report.problems else None
        where = f" (line {first.line}: {first.message})" if first else ""
        super().__init__(ErrorCode.INVALID_INPUT, f"the schema is not valid: {count} problem(s){where}",
                         retryable=False, reason="schema_invalid")
        self.report = report


def validate_schema_name(name: object) -> str:
    """``name`` when it is a valid schema name, else ``INVALID_INPUT``."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ContractError(ErrorCode.INVALID_INPUT,
                            "schema name must be 1-64 characters: a lower-case letter, then lower-case letters, "
                            "digits, '_' or '-'")
    return name


def schema_id(name: str, version: int) -> str:
    """``user/<name>@vN``."""
    return f"{ID_PREFIX}{validate_schema_name(name)}@v{int(version)}"


def parse_schema_id(value: object) -> tuple[str, int]:
    """``(name, version)`` of a ``user/<name>@vN`` id; ``INVALID_INPUT`` for anything else."""
    match = _ID_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ContractError(ErrorCode.INVALID_INPUT, "not a user schema id (expected user/<name>@vN)")
    return match.group(1), int(match.group(2))


def is_user_schema_id(value: object) -> bool:
    """True for a well-formed ``user/<name>@vN`` id."""
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


@dataclass(frozen=True)
class UserSchema:
    """One stored version: the text, its hash and where it sits relative to the store (never an absolute path)."""

    id: str
    name: str
    version: int
    text: str
    sha256: str
    created_at: str
    relpath: str

    def as_dict(self, *, with_text: bool = True) -> dict[str, Any]:
        """JSON-safe form (no host path)."""
        out: dict[str, Any] = {"id": self.id, "name": self.name, "version": self.version, "sha256": self.sha256,
                               "created_at": self.created_at}
        if with_text:
            out["text"] = self.text
        return out


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _name_problem(text: str, name: str) -> Problem | None:
    """A problem when the text's top-level ``name`` is not ``name`` (the validator already parsed the text)."""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return None
    if not isinstance(root, yaml.MappingNode):
        return None
    for key, value in root.value:
        if isinstance(key, yaml.ScalarNode) and key.value == "name":
            have = value.value if isinstance(value, yaml.ScalarNode) else None
            if have != name:
                return Problem(value.start_mark.line + 1, f"name must be '{name}' (the schema name you save under); "
                               f"the text says '{have}'", "name_mismatch", value.start_mark.column + 1)
            return None
    return None


class UserSchemaStore:
    """Versioned schema files under ``root`` (``<F2W_WORKDIR>/schemas``).

    Args:
        root: The store directory (created on the first save; a missing root lists nothing).
        bundles_root: Where bundles live (default ``<root>/../kgs``); a schema referenced by any manifest there is
            never deleted.
    """

    def __init__(self, root: Path | str, *, bundles_root: Path | str | None = None) -> None:
        self.root = Path(root)
        self.bundles_root = Path(bundles_root) if bundles_root is not None else self.root.parent / "kgs"

    # ------------------------------------------------------------------ paths
    def _dir(self, name: str) -> Path:
        validate_schema_name(name)
        return self.root / name

    def _file(self, name: str, version: int) -> Path:
        return self._dir(name) / f"{name}@v{int(version)}.yaml"

    def relpath(self, name: str, version: int) -> str:
        """Path of a version relative to the store root (what a manifest records)."""
        validate_schema_name(name)
        return f"{name}/{name}@v{int(version)}.yaml"

    def path_for(self, schema: str) -> Path:
        """Absolute path of the stored file behind ``user/<name>@vN`` (for the extractor); ``NOT_FOUND`` when absent."""
        name, version = parse_schema_id(schema)
        path = self._file(name, version)
        if path.is_symlink() or not path.is_file():
            raise ContractError(ErrorCode.NOT_FOUND, "no such schema")
        return path

    def path_from_relpath(self, relpath: str) -> Path:
        """Absolute path for a manifest's relative path; refuses anything that is not ``<name>/<name>@vN.yaml``."""
        parts = str(relpath).split("/")
        match = _FILE_RE.fullmatch(parts[-1]) if len(parts) == 2 else None
        if match is None or match.group(1) != parts[0]:
            raise ContractError(ErrorCode.INVALID_INPUT, "not a schema store path")
        return self._file(match.group(1), int(match.group(2)))

    # ------------------------------------------------------------------ read
    def _versions(self, name: str) -> list[int]:
        directory = self._dir(name)
        if not directory.is_dir() or directory.is_symlink():
            return []
        found = []
        for child in directory.iterdir():
            match = _FILE_RE.fullmatch(child.name)
            if match and match.group(1) == name and child.is_file() and not child.is_symlink():
                found.append(int(match.group(2)))
        return sorted(found)

    def _names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(c.name for c in self.root.iterdir() if NAME_RE.fullmatch(c.name) and c.is_dir() and not c.is_symlink())

    def get(self, schema: str) -> UserSchema:
        """The stored version ``user/<name>@vN``: text, hash, version, creation time; ``NOT_FOUND`` when absent."""
        name, version = parse_schema_id(schema)
        path = self.path_for(schema)
        try:
            raw = path.read_bytes()
            created = path.stat().st_mtime
        except OSError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "no such schema") from exc
        return UserSchema(id=schema_id(name, version), name=name, version=version, text=raw.decode("utf-8", errors="replace"),
                          sha256=hashlib.sha256(raw).hexdigest(), created_at=_iso(created),
                          relpath=self.relpath(name, version))

    def list(self) -> list[dict[str, Any]]:
        """One row per name: the latest version (without text) and every version, sorted by name."""
        out: list[dict[str, Any]] = []
        for name in self._names():
            versions = self._versions(name)
            if not versions:
                continue
            try:
                latest = self.get(schema_id(name, versions[-1]))
            except ContractError:
                continue
            out.append({"name": name, "latest": latest.as_dict(with_text=False), "versions": versions,
                        "ids": [schema_id(name, v) for v in versions]})
        return out

    def versions(self, name: str) -> _Versions:
        """The stored version numbers of ``name`` (empty when unknown)."""
        return self._versions(name)

    # ------------------------------------------------------------------ write
    def save(self, name: str, text: str) -> UserSchema:
        """Validate ``text`` and store it as the next immutable version of ``name``.

        Raises:
            SchemaInvalid: The validator found problems (the exception carries the :class:`Report`, lines kept), or the
                text's own ``name`` differs from ``name``.
            ContractError: ``INVALID_INPUT`` for a bad name, ``CONFLICT`` when the version limit is reached.
        """
        validate_schema_name(name)
        report = validate_schema_text(text)
        if report.ok:
            mismatch = _name_problem(text, name)
            if mismatch is not None:
                report = Report(False, [mismatch], report.stats)
        if not report.ok:
            raise SchemaInvalid(report)
        directory = self._dir(name)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.mkdir(exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise ContractError(ErrorCode.INVALID_INPUT, "schema directory is a link")
        data = text.encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".new.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            Path(tmp).chmod(0o600)
            while True:
                versions = self._versions(name)
                version = (versions[-1] if versions else 0) + 1
                if version > MAX_VERSIONS:
                    raise ContractError(ErrorCode.CONFLICT, "too many versions of this schema", retryable=False)
                try:
                    os.link(tmp, self._file(name, version))  # fails when the version exists: never an overwrite
                except FileExistsError:
                    continue  # another save took this number first: take the next one
                break
        finally:
            Path(tmp).unlink(missing_ok=True)
        return self.get(schema_id(name, version))

    def diff(self, name: str, v_from: int, v_to: int) -> str:
        """Unified diff text between two stored versions of ``name``."""
        a = self.get(schema_id(name, int(v_from)))
        b = self.get(schema_id(name, int(v_to)))
        return "".join(difflib.unified_diff(a.text.splitlines(keepends=True), b.text.splitlines(keepends=True),
                                            fromfile=a.id, tofile=b.id))

    # ------------------------------------------------------------------ references and delete
    def referenced_ids(self) -> set[str]:
        """Schema ids named by any bundle manifest; raises ``UNAVAILABLE`` when a manifest cannot be read (fail closed)."""
        found: set[str] = set()
        if not self.bundles_root.is_dir():
            return found
        for child in self.bundles_root.iterdir():
            manifest = child / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                value = json.loads(manifest.read_text(encoding="utf-8"))["schema"]["id"]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ContractError(ErrorCode.UNAVAILABLE, "a KG manifest cannot be read, so schema references cannot be "
                                    "checked; nothing was deleted") from exc
            found.add(str(value))
        return found

    def delete(self, schema: str) -> None:
        """Delete one version, only when no bundle manifest references it."""
        name, version = parse_schema_id(schema)
        path = self.path_for(schema)
        if schema_id(name, version) in self.referenced_ids():
            raise ContractError(ErrorCode.CONFLICT, "a KG uses this schema version; it cannot be deleted", retryable=False,
                                reason="schema_in_use")
        path.unlink()
        try:
            self._dir(name).rmdir()  # only when it was the last version
        except OSError:
            pass


def schema_origin(schema_block: dict[str, Any]) -> dict[str, Any]:
    """``schema_origin`` (``user`` or ``shipped``), ``schema_name`` and ``schema_version`` for a manifest's schema block."""
    ident = str(schema_block.get("id") or "")
    if is_user_schema_id(ident):
        name, version = parse_schema_id(ident)
        return {"schema_origin": "user", "schema_name": name, "schema_version": version}
    return {"schema_origin": "shipped", "schema_name": ident.split("@", 1)[0],
            "schema_version": schema_block.get("version")}


def default_store(workdir: Path | str) -> UserSchemaStore:
    """The store of a work directory."""
    return UserSchemaStore(Path(workdir) / SCHEMAS_DIRNAME, bundles_root=Path(workdir) / "kgs")


__all__ = ("ID_PREFIX", "NAME_RE", "SCHEMAS_DIRNAME", "SchemaInvalid", "UserSchema", "UserSchemaStore", "default_store",
           "is_user_schema_id", "parse_schema_id", "schema_id", "schema_origin", "validate_schema_name")
