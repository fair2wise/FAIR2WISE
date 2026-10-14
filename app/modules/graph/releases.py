"""Release readiness and Git-pinned verification of frozen KG snapshots (T23B, release_contract.md).

Three checks, none of which needs Tiled, a network or credentials:

* :func:`check_disclosure` - the commit-ready rules R1, R3-R7 (R2 is :func:`verify_snapshot_dir`/
  :func:`verify_git_release`). Verbatim evidence-snippet volume (R6) is information only (owner exception
  O-D1); it is recorded, never a blocker and never truncated.
* :func:`verify_snapshot_dir` - hash-check a snapshot in a working tree (tamper detection after freezing).
* :func:`verify_git_release` - select bytes by ``(data_commit, path, sha256)`` from a Git repository: the
  commit must be a full object id (a branch, tag or abbreviation never selects data), the blob listed by
  ``git ls-tree`` must hash to the pinned SHA-256, and the manifest and flat copies must agree.

Git is read with fixed argument lists (``subprocess`` without a shell) and a scrubbed environment. Only this
module talks to Git; snapshot creation does not import it for that purpose and calls no subprocess.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Collection, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from .snapshot_manifest import CORPUS_RE, KGManifest, parse_manifest

DISCLOSURE_CHECK_VERSION = "1"

_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPO_PATH = re.compile(r"^(?!/)(?!.*\.\.)(?!.*\\)[A-Za-z0-9._/-]+$")
_SNAPSHOT_FILE = re.compile(r"^storage/kg/([a-z0-9][a-z0-9_-]*)/(v[1-9][0-9]*)/(graph|manifest|validation)\.json$")
_FLAT_FILE = re.compile(r"^storage/kg/matkg_([a-z0-9][a-z0-9_-]*)_(v[1-9][0-9]*)\.json$")
SNAPSHOT_FILES = ("graph.json", "manifest.json", "validation.json")


class ReleaseVerificationError(ValueError):
    """A snapshot or release failed verification.

    Attributes:
        code: Stable reason (``bad_commit``, ``bad_path``, ``missing``, ``wrong_hash``, ``tampered``,
            ``manifest_mismatch``, ``layout``, ``not_a_blob``, ``git``, ``schema_unsupported``).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256."""
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- disclosure / readiness

# R3: token shapes. Values are never echoed into reports; only a hash-based allowlist key is.
_SECRET_PATTERNS = (
    ("sk-token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("api-key-assignment", re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*['\"]?[^\s'\",;)]{8,}")),
    ("password-assignment", re.compile(r"(?i)\bpassword\s*[=:]\s*['\"]?[^\s'\",;)]{4,}")),
)
_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{40,}")
_HEX_ONLY = re.compile(r"^[0-9a-fA-F]+$")
_ENTROPY_BITS = 4.5
# R4: absolute local paths; not preceded by a name character so URL path segments do not match.
_LOCAL_PATH = re.compile(
    r"(?<![A-Za-z0-9_.~-])(?:/Users/|/home/|/private/|/tmp/|/global/|/pscratch/)|\b[A-Za-z]:\\|file://"
)
# R5: signed or tokenized URLs.
_BEARER_URL = re.compile(r"(?i)X-Amz-Signature|[?&](?:token|access_token|sig|signature)=")
# R7: fields that carry whole parsed bodies; DocumentChunk nodes need review.
_BODY_FIELDS = frozenset({"full_text", "body", "page_text", "parsed_body", "section_text", "raw_text",
                          "full_body", "markdown_body"})
_REVIEW_CATEGORIES = frozenset({"DocumentChunk"})
_FORBIDDEN_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".db", ".sqlite", ".sqlite3")


def allowlist_key(path: str, node_id: str, field_name: str, value: str) -> str:
    """Key of a reviewed R3/R4 exception: SHA-256 of ``(path, node id, field, value)``."""
    return sha256_bytes(json.dumps([path, node_id, field_name, value], ensure_ascii=False).encode("utf-8"))


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(c / len(text) * math.log2(c / len(text)) for c in counts.values())


@dataclass(frozen=True)
class Finding:
    """One disclosure finding. Secret values are never stored; ``allowlist_key`` identifies them."""

    rule: str
    severity: str  # "reject" or "review"
    location: str
    detail: str
    allowlist_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready form."""
        return {"rule": self.rule, "severity": self.severity, "location": self.location,
                "detail": self.detail, "allowlist_key": self.allowlist_key}


@dataclass
class DisclosureReport:
    """Deterministic, path-free result of :func:`check_disclosure` (its hash goes into the manifest)."""

    check_version: str
    status: str  # pass | fail | owner_waiver
    findings: List[Finding] = field(default_factory=list)
    snippet_volume: Dict[str, Any] = field(default_factory=dict)
    waiver_ref: Optional[str] = None

    @property
    def commit_ready(self) -> bool:
        """True when no unwaived reject/review finding remains."""
        return self.status in ("pass", "owner_waiver")

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready body."""
        return {
            "check_version": self.check_version, "status": self.status, "waiver_ref": self.waiver_ref,
            "findings": [f.to_dict() for f in self.findings],
            "informational": {"rule": "R6", "note": "evidence-snippet volume is information only (O-D1)",
                              **self.snippet_volume},
        }

    def canonical_bytes(self) -> bytes:
        """Report bytes: sorted keys, two-space indent, trailing newline."""
        return (json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    @property
    def sha256(self) -> str:
        """Hash of :meth:`canonical_bytes`."""
        return sha256_bytes(self.canonical_bytes())


def check_paths(paths: Iterable[str]) -> List[Finding]:
    """R1: only ``storage/kg/<corpus>/v<N>/{graph,manifest,validation}.json`` and the flat copy are allowed."""
    findings: List[Finding] = []
    for raw in paths:
        path = str(raw).replace("\\", "/")
        if _SNAPSHOT_FILE.match(path) or _FLAT_FILE.match(path):
            continue
        why = "forbidden file type" if path.lower().endswith(_FORBIDDEN_SUFFIXES) else "outside the allowlist"
        if any(part in path.split("/") for part in ("workdir", "source_index", "papers")) or ".env" in path:
            why = "private or mutable location"
        findings.append(Finding("R1", "reject", path, f"{why}; not a frozen snapshot path"))
    return findings


def _walk(value: Any, where: str) -> Iterator[Tuple[str, str]]:
    """Yield ``(location, string)`` for every string in a JSON value."""
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _walk(item, f"{where}.{key}" if where else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{where}[{index}]")


def _records(graph: Mapping[str, Any]) -> Iterator[Tuple[str, str, Any]]:
    """``(record id, field, value)`` for every node and edge field."""
    nodes = graph.get("things") if "things" in graph else graph.get("nodes")
    edges = graph.get("associations") if "associations" in graph else graph.get("edges")
    for node in nodes or []:
        if isinstance(node, Mapping):
            for key, value in node.items():
                yield str(node.get("id")), str(key), value
    for edge in edges or []:
        if isinstance(edge, Mapping):
            rid = f"{edge.get('subject')}|{edge.get('predicate')}|{edge.get('object')}"
            for key, value in edge.items():
                yield rid, str(key), value
    meta = graph.get("metadata")
    if isinstance(meta, Mapping):
        for key, value in meta.items():
            yield "metadata", str(key), value


def _snippet_volume(graph: Mapping[str, Any]) -> Dict[str, Any]:
    """R6 statistics: lengths of snippet-like values, per value and per source paper. Information only."""
    nodes = graph.get("things") if "things" in graph else graph.get("nodes")
    per_paper: Dict[str, int] = {}
    lengths: List[int] = []
    for node in nodes or []:
        if not isinstance(node, Mapping):
            continue
        for snip in node.get("context_snippets") or []:
            if isinstance(snip, Mapping) and isinstance(snip.get("text"), str):
                lengths.append(len(snip["text"]))
                paper = str(snip.get("source_paper") or "unknown")
                per_paper[paper] = per_paper.get(paper, 0) + len(snip["text"])
        for key in ("abstract_text", "description", "feature_source_text"):
            text = node.get(key)
            if isinstance(text, str) and (key != "description" or len(text) > 500):
                lengths.append(len(text))
    return {
        "snippet_value_count": len(lengths), "max_value_chars": max(lengths, default=0),
        "total_chars": sum(lengths), "papers": len(per_paper),
        "max_chars_for_one_paper": max(per_paper.values(), default=0),
    }


def check_disclosure(
    graph: Mapping[str, Any],
    *,
    paths: Sequence[str] = (),
    allowlist: Collection[str] = frozenset(),
    waiver_ref: Optional[str] = None,
) -> DisclosureReport:
    """Commit-ready disclosure check of one graph payload (R1, R3-R7). The payload is only read.

    Args:
        graph: Parsed graph JSON.
        paths: Repository-relative paths the commit would contain (R1).
        allowlist: Reviewed R3/R4 exceptions as :func:`allowlist_key` values.
        waiver_ref: Owner waiver reference; turns a failing status into ``owner_waiver``.
    """
    findings: List[Finding] = list(check_paths(paths))
    for record_id, field_name, value in _records(graph):
        if field_name in _BODY_FIELDS and isinstance(value, str) and value.strip():
            findings.append(Finding("R7", "reject", f"{record_id}:{field_name}",
                                    "full parsed body stored as a field"))
        if field_name == "category" and value in _REVIEW_CATEGORIES:
            findings.append(Finding("R7", "review", f"{record_id}:{field_name}",
                                    f"category {value} may carry raw document bodies; needs review"))
        for where, text in _walk(value, field_name):
            key = allowlist_key("graph.json", record_id, field_name, text)
            if key in allowlist:
                continue
            location = f"{record_id}:{where}"
            hit = False
            for name, pattern in _SECRET_PATTERNS:
                if pattern.search(text):
                    findings.append(Finding("R3", "reject", location, f"secret-like value ({name})", key))
                    hit = True
                    break
            if not hit:
                for token in _TOKEN.findall(text):
                    if not _HEX_ONLY.match(token) and _entropy(token) >= _ENTROPY_BITS:
                        findings.append(Finding("R3", "reject", location, "high-entropy token", key))
                        break
            if _LOCAL_PATH.search(text):
                findings.append(Finding("R4", "reject", location, "absolute local path", key))
            if _BEARER_URL.search(text):
                findings.append(Finding("R5", "reject", location, "signed or tokenized URL", key))
    findings.sort(key=lambda f: (f.rule, f.location, f.detail))
    status = "pass" if not findings else ("owner_waiver" if waiver_ref else "fail")
    return DisclosureReport(DISCLOSURE_CHECK_VERSION, status, findings, _snippet_volume(graph),
                            waiver_ref if status == "owner_waiver" else None)


# --------------------------------------------------------------------------- working-tree verification


@dataclass(frozen=True)
class VerifiedSnapshot:
    """A snapshot whose bytes matched its manifest."""

    manifest: KGManifest
    graph_bytes: bytes
    graph_sha256: str
    manifest_sha256: str
    compat_paths: Tuple[str, ...]


def _read(path: Path, what: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReleaseVerificationError("missing", f"{what} is unreadable: {exc.strerror}") from None


def _graph_counts(blob: bytes) -> Tuple[int, int]:
    try:
        data = json.loads(blob.decode("utf-8"))
        nodes = data.get("things") if "things" in data else data.get("nodes")
        edges = data.get("associations") if "associations" in data else data.get("edges")
        return len(nodes), len(edges)
    except (UnicodeDecodeError, ValueError, AttributeError, TypeError):
        raise ReleaseVerificationError("tampered", "graph.json is not a KG document") from None


def verify_snapshot_dir(snapshot_dir: Path) -> VerifiedSnapshot:
    """Hash-check ``storage/kg/<corpus>/<snapshot-id>/`` as it stands on disk.

    Checks: the layout is exactly the frozen one (no extra files), the manifest validates, ``graph.json``
    hashes to ``graph.sha256`` (size and counts too), the validation report hashes to
    ``validation.report_sha256``, and each flat compatibility copy next to the corpus directory is a regular
    file (not a symlink) with identical bytes. Any edit after freezing raises ``tampered``.
    """
    snapshot_dir = Path(snapshot_dir)
    if snapshot_dir.is_symlink() or not snapshot_dir.is_dir():
        raise ReleaseVerificationError("layout", "snapshot directory is missing or a symlink")
    corpus, snapshot_id = snapshot_dir.parent.name, snapshot_dir.name
    if not CORPUS_RE.match(corpus) or not re.match(r"^v[1-9][0-9]*$", snapshot_id):
        raise ReleaseVerificationError("layout", "not a storage/kg/<corpus>/v<N> directory")
    present = sorted(p.name for p in snapshot_dir.iterdir())
    if "graph.json" not in present or "manifest.json" not in present or set(present) - set(SNAPSHOT_FILES):
        raise ReleaseVerificationError("layout", f"unexpected snapshot contents: {present}")
    manifest_bytes = _read(snapshot_dir / "manifest.json", "manifest.json")
    try:
        manifest = parse_manifest(manifest_bytes)
    except ValueError as exc:
        raise ReleaseVerificationError("manifest_mismatch", f"manifest does not validate: {exc}") from None
    if (manifest.corpus, manifest.snapshot_id) != (corpus, snapshot_id):
        raise ReleaseVerificationError("manifest_mismatch",
                                       "manifest corpus/snapshot_id do not match the directory")
    graph_bytes = _read(snapshot_dir / "graph.json", "graph.json")
    if sha256_bytes(graph_bytes) != manifest.graph.sha256 or len(graph_bytes) != manifest.graph.size_bytes:
        raise ReleaseVerificationError("tampered", "graph.json no longer matches manifest graph.sha256")
    if _graph_counts(graph_bytes) != (manifest.graph.node_count, manifest.graph.edge_count):
        raise ReleaseVerificationError("tampered", "graph.json counts differ from the manifest")
    if "validation.json" in present:
        report = _read(snapshot_dir / "validation.json", "validation.json")
        if sha256_bytes(report) != manifest.validation.report_sha256:
            raise ReleaseVerificationError("tampered", "validation.json no longer matches the manifest")
    compat: List[str] = []
    for item in manifest.compat_paths or []:
        flat = snapshot_dir.parents[1] / Path(item.path).name
        if flat.is_symlink() or not flat.is_file():
            raise ReleaseVerificationError("tampered", f"compat copy {item.path} is missing or a symlink")
        if sha256_bytes(_read(flat, item.path)) != item.sha256 or item.sha256 != manifest.graph.sha256:
            raise ReleaseVerificationError("tampered", f"compat copy {item.path} differs from graph.json")
        compat.append(item.path)
    return VerifiedSnapshot(manifest, graph_bytes, manifest.graph.sha256, sha256_bytes(manifest_bytes),
                            tuple(compat))


# --------------------------------------------------------------------------- Git-pinned verification

_GIT_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0"}


def _git(repo: Path, *args: str) -> bytes:
    """Run ``git -C <repo> ...`` with an argument list (no shell) and a scrubbed environment."""
    try:
        done = subprocess.run(["git", "-C", str(repo), "--no-pager", *args], capture_output=True, check=False,
                              env=_GIT_ENV, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseVerificationError("git", f"git could not run: {type(exc).__name__}") from None
    if done.returncode != 0:
        raise ReleaseVerificationError("git", f"git {args[0]} failed")
    return done.stdout


@dataclass(frozen=True)
class VerifiedArtifact:
    """One artifact read from a Git commit and hash-checked."""

    path: str
    sha256: str
    git_blob: str
    data: bytes


def verify_git_artifact(repo: Path, commit: str, path: str, sha256: str,
                        *, git_blob: Optional[str] = None) -> VerifiedArtifact:
    """Read ``path`` at ``commit`` and require its bytes to hash to ``sha256``.

    Raises:
        ReleaseVerificationError: ``bad_commit`` (not a full object id, or not a commit), ``bad_path``,
            ``missing`` (no such path at that commit), ``not_a_blob`` (directory, symlink or submodule),
            ``wrong_hash`` (blob id or SHA-256 differs from the pinned values).
    """
    if not _GIT_COMMIT.match(commit or ""):
        raise ReleaseVerificationError("bad_commit", "data_commit must be a full lowercase Git object id")
    if not _REPO_PATH.match(path or "") or path.endswith("/"):
        raise ReleaseVerificationError("bad_path", "path must be repository-relative without '..'")
    if not _SHA256.match(sha256 or ""):
        raise ReleaseVerificationError("wrong_hash", "sha256 must be 64 lowercase hex characters")
    if _git(repo, "cat-file", "-t", commit).strip() != b"commit":
        raise ReleaseVerificationError("bad_commit", "object is not a commit")
    listing = _git(repo, "ls-tree", "-z", commit, "--", path)
    if not listing:
        raise ReleaseVerificationError("missing", f"{path} does not exist at the pinned commit")
    meta, _, name = listing.rstrip(b"\0").partition(b"\t")
    mode, kind, blob = meta.decode().split(" ")
    if name.decode() != path or kind != "blob" or mode not in ("100644", "100755"):
        raise ReleaseVerificationError("not_a_blob", f"{path} is not a regular file at the pinned commit")
    if git_blob is not None and git_blob != blob:
        raise ReleaseVerificationError("wrong_hash", f"{path}: git blob differs from the pinned blob id")
    data = _git(repo, "cat-file", "blob", blob)
    if sha256_bytes(data) != sha256:
        raise ReleaseVerificationError("wrong_hash", f"{path}: bytes at the pinned commit do not match sha256")
    return VerifiedArtifact(path, sha256, blob, data)


@dataclass(frozen=True)
class VerifiedRelease:
    """A Git-selected snapshot: graph, manifest and compat copies all checked."""

    data_commit: str
    manifest: KGManifest
    graph: VerifiedArtifact
    manifest_artifact: VerifiedArtifact
    compat: Tuple[VerifiedArtifact, ...]


def verify_git_release(
    repo: Path,
    data_commit: str,
    artifacts: Sequence[Mapping[str, Any]],
    *,
    supported_schema_fingerprints: Optional[Collection[str]] = None,
) -> VerifiedRelease:
    """Verify a release selection ``(data_commit, path, sha256)`` for graph, manifest and flat copies.

    Args:
        repo: Git repository (working tree or bare); only objects are read, never the working tree.
        data_commit: Full commit id.
        artifacts: Descriptor-style entries ``{"role": graph|manifest|compat_copy, "path", "sha256",
            "git_blob"?}``: exactly one graph and one manifest, any number of compat copies.
        supported_schema_fingerprints: When given, the manifest's ``schema.import_fingerprint`` must be in it.

    Raises:
        ReleaseVerificationError: any hash, blob, manifest, layout or compat mismatch.
    """
    by_role: Dict[str, List[Mapping[str, Any]]] = {}
    for item in artifacts:
        by_role.setdefault(str(item.get("role")), []).append(item)
    if len(by_role.get("graph", [])) != 1 or len(by_role.get("manifest", [])) != 1:
        raise ReleaseVerificationError("layout", "exactly one graph and one manifest artifact are required")
    if set(by_role) - {"graph", "manifest", "compat_copy"}:
        raise ReleaseVerificationError("layout", "unsupported artifact role")
    loaded: Dict[str, List[VerifiedArtifact]] = {}
    for role, items in by_role.items():
        for item in items:
            loaded.setdefault(role, []).append(verify_git_artifact(
                repo, data_commit, str(item.get("path")), str(item.get("sha256")), git_blob=item.get("git_blob")))
    graph, manifest_art = loaded["graph"][0], loaded["manifest"][0]
    match = _SNAPSHOT_FILE.match(graph.path)
    sibling = graph.path.rsplit("/", 1)[0] + "/manifest.json"
    if not match or match.group(3) != "graph" or manifest_art.path != sibling:
        raise ReleaseVerificationError(
            "layout", "graph and manifest must be storage/kg/<corpus>/<id>/{graph,manifest}.json")
    try:
        manifest = parse_manifest(manifest_art.data)
    except ValueError as exc:
        raise ReleaseVerificationError("manifest_mismatch", f"manifest does not validate: {exc}") from None
    if (manifest.corpus, manifest.snapshot_id) != (match.group(1), match.group(2)):
        raise ReleaseVerificationError("manifest_mismatch", "manifest corpus/snapshot_id do not match the path")
    if manifest.graph.sha256 != graph.sha256 or manifest.graph.size_bytes != len(graph.data):
        raise ReleaseVerificationError("manifest_mismatch",
                                       "manifest graph.sha256 differs from the graph artifact")
    if _graph_counts(graph.data) != (manifest.graph.node_count, manifest.graph.edge_count):
        raise ReleaseVerificationError("manifest_mismatch", "graph counts differ from the manifest")
    declared = {c.path: c.sha256 for c in manifest.compat_paths or []}
    for art in loaded.get("compat_copy", []):
        if art.data != graph.data or declared.get(art.path) != art.sha256:
            raise ReleaseVerificationError("manifest_mismatch", f"compat copy {art.path} is not the graph bytes")
    if supported_schema_fingerprints is not None and (
            manifest.schema_block.import_fingerprint not in supported_schema_fingerprints):
        raise ReleaseVerificationError("schema_unsupported",
                                       "manifest schema fingerprint is not in the supported set")
    return VerifiedRelease(data_commit, manifest, graph, manifest_art, tuple(loaded.get("compat_copy", [])))
