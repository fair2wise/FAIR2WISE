"""Release readiness and Git-pinned verification of frozen KG snapshots (T23B, release_contract.md).

Three checks, none of which needs Tiled, a network or credentials:

* :func:`check_disclosure` - the commit-ready rules R1, R3-R7 (R2 is :func:`verify_snapshot_dir`/
  :func:`verify_git_release`). Verbatim evidence-snippet volume (R6) is information only (owner exception
  O-D1); it is recorded, never a blocker and never truncated.
* :func:`verify_snapshot_dir` - hash-check a snapshot in a working tree (tamper detection after freezing).
* :func:`verify_git_release` - select bytes by ``(data_commit, path, sha256)`` from a Git repository: the
  commit must be a full object id (a branch, tag or abbreviation never selects data), the blob listed by
  ``git ls-tree`` must hash to the pinned SHA-256, and the manifest and flat copies must agree.

T26B adds the commit-ready release path (operator facade: ``scripts/prepare_kg_release.py``):

* :func:`validate_commit_candidate` - schema binding, validation.json and manifest hashes, disclosure re-run,
  private dependencies (:func:`check_private_dependencies`), evidence availability
  (:func:`check_evidence_availability`), binding to an executed binary report (:func:`bind_binary_report`;
  a PROVISIONAL or NOT_PASS verdict is recorded as such, never raised) and a Git preflight
  (:func:`check_worktree`). :func:`build_release_record` turns the result into the tested-release record.
* :func:`verify_snapshot_selection` - after the owner commits: the bytes at ``(commit, path)`` must equal the
  tested bytes; :func:`build_data_release_descriptor` then emits the T26A descriptor (state ``candidate``).
* :func:`lookup_snapshot`, :func:`export_snapshot_selection`, :func:`verify_release_dependencies` - immutable
  lookup, hash-checked export to a mount and the promotion-time private-asset gate.

Git is read with fixed argument lists (``subprocess`` without a shell) and a scrubbed environment, and only
through :data:`GIT_READ_ONLY_COMMANDS`: this module cannot add, commit, tag, fetch or push. Only this module
talks to Git; snapshot creation does not import it for that purpose and calls no subprocess.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any, cast

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
# D3.22 tuning. A long token is key-like only if it is high-entropy *and* shaped like random base62/base64:
# character classes (lower, upper, digit, other) change between neighbours at least this often, and few characters
# sit in long lowercase runs. Natural-language slugs used as ids ("snippetanalyzevolfractions...", chemical names,
# ``RSoXSMeasurement-<uuid>``) have long lowercase runs and few class changes. UUIDs and 32+ digit hex runs
# (commit ids, digests) are removed before scanning.
_MIN_CLASS_CHANGE = 0.45
_MAX_WORD_RUN_FRACTION = 0.25
_UUID = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?![A-Za-z0-9])")
_LONG_HEX = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{32,}(?![A-Za-z0-9])")
_WORD_RUN = re.compile(r"[a-z]{5,}")
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


def _char_class(char: str) -> int:
    return 0 if char.islower() else 1 if char.isupper() else 2 if char.isdigit() else 3


def _key_like(token: str) -> bool:
    """R3 entropy rule for one long token (see the D3.22 constants): random-looking, not slug-shaped."""
    if _HEX_ONLY.match(token) or _entropy(token) < _ENTROPY_BITS:
        return False
    changes = sum(_char_class(a) != _char_class(b) for a, b in zip(token, token[1:])) / (len(token) - 1)
    word_chars = sum(len(m.group()) for m in _WORD_RUN.finditer(token)) / len(token)
    return changes >= _MIN_CLASS_CHANGE and word_chars < _MAX_WORD_RUN_FRACTION


@dataclass(frozen=True)
class Finding:
    """One disclosure finding. Secret values are never stored; ``allowlist_key`` identifies them."""

    rule: str
    severity: str  # "reject" or "review"
    location: str
    detail: str
    allowlist_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return {"rule": self.rule, "severity": self.severity, "location": self.location,
                "detail": self.detail, "allowlist_key": self.allowlist_key}


@dataclass
class DisclosureReport:
    """Deterministic, path-free result of :func:`check_disclosure` (its hash goes into the manifest)."""

    check_version: str
    status: str  # pass | fail | owner_waiver
    findings: list[Finding] = field(default_factory=list)
    snippet_volume: dict[str, Any] = field(default_factory=dict)
    waiver_ref: str | None = None

    @property
    def commit_ready(self) -> bool:
        """True when no unwaived reject/review finding remains."""
        return self.status in ("pass", "owner_waiver")

    def to_dict(self) -> dict[str, Any]:
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


def check_paths(paths: Iterable[str]) -> list[Finding]:
    """R1: only ``storage/kg/<corpus>/v<N>/{graph,manifest,validation}.json`` and the flat copy are allowed."""
    findings: list[Finding] = []
    for raw in paths:
        path = str(raw).replace("\\", "/")
        if _SNAPSHOT_FILE.match(path) or _FLAT_FILE.match(path):
            continue
        why = "forbidden file type" if path.lower().endswith(_FORBIDDEN_SUFFIXES) else "outside the allowlist"
        if any(part in path.split("/") for part in ("workdir", "source_index", "papers")) or ".env" in path:
            why = "private or mutable location"
        findings.append(Finding("R1", "reject", path, f"{why}; not a frozen snapshot path"))
    return findings


def _walk(value: Any, where: str) -> Iterator[tuple[str, str]]:
    """Yield ``(location, string)`` for every string in a JSON value."""
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _walk(item, f"{where}.{key}" if where else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{where}[{index}]")


def _records(graph: Mapping[str, Any]) -> Iterator[tuple[str, str, Any]]:
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


def _snippet_volume(graph: Mapping[str, Any]) -> dict[str, Any]:
    """R6 statistics: lengths of snippet-like values, per value and per source paper. Information only."""
    nodes = graph.get("things") if "things" in graph else graph.get("nodes")
    per_paper: dict[str, int] = {}
    lengths: list[int] = []
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


def _string_findings(location: str, text: str, key: str | None) -> list[Finding]:
    """R3/R4/R5 findings for one string value (the value itself is never stored)."""
    found: list[Finding] = []
    hit = False
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            found.append(Finding("R3", "reject", location, f"secret-like value ({name})", key))
            hit = True
            break
    if not hit:
        scanned = _LONG_HEX.sub(" ", _UUID.sub(" ", text))
        for token in _TOKEN.findall(scanned):
            if _key_like(token):
                found.append(Finding("R3", "reject", location, "high-entropy token", key))
                break
    if _LOCAL_PATH.search(text):
        found.append(Finding("R4", "reject", location, "absolute local path", key))
    if _BEARER_URL.search(text):
        found.append(Finding("R5", "reject", location, "signed or tokenized URL", key))
    return found


def check_disclosure(
    graph: Mapping[str, Any],
    *,
    paths: Sequence[str] = (),
    allowlist: Collection[str] = frozenset(),
    waiver_ref: str | None = None,
) -> DisclosureReport:
    """Commit-ready disclosure check of one graph payload (R1, R3-R7). The payload is only read.

    Args:
        graph: Parsed graph JSON.
        paths: Repository-relative paths the commit would contain (R1).
        allowlist: Reviewed R3/R4 exceptions as :func:`allowlist_key` values.
        waiver_ref: Owner waiver reference; turns a failing status into ``owner_waiver``.
    """
    findings: list[Finding] = list(check_paths(paths))
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
            findings.extend(_string_findings(f"{record_id}:{where}", text, key))
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
    compat_paths: tuple[str, ...]


def _read(path: Path, what: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReleaseVerificationError("missing", f"{what} is unreadable: {exc.strerror}") from None


def _graph_counts(blob: bytes) -> tuple[int, int]:
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
    compat: list[str] = []
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

#: The only Git subcommands this module can run. All of them read; none of them writes objects, refs, the
#: index, the working tree or a remote. There is no add, commit, tag, fetch or push path (T26B acceptance:
#: "no automatic Git push"); the owner runs the printed commands by hand.
GIT_READ_ONLY_COMMANDS = frozenset({"cat-file", "ls-tree", "rev-parse", "status", "check-ignore", "hash-object",
                                    "ls-files"})


def _git(repo: Path, *args: str, stdin: bytes | None = None, ok_codes: tuple[int, ...] = (0,)) -> bytes:
    """Run a read-only ``git -C <repo> ...`` with an argument list (no shell) and a scrubbed environment.

    Raises:
        ReleaseVerificationError: ``git_write_refused`` for any subcommand outside
            :data:`GIT_READ_ONLY_COMMANDS` (or ``hash-object -w``), before any process starts; ``git`` when
            Git cannot run or exits with a code outside ``ok_codes``.
    """
    if not args or args[0] not in GIT_READ_ONLY_COMMANDS or (
            args[0] == "hash-object" and any(a == "--write" or a.startswith("-w") for a in args[1:])):
        raise ReleaseVerificationError("git_write_refused",
                                       f"git {args[0] if args else ''} is not a read-only command; the release "
                                       "tooling never writes to Git (the owner runs the printed commands)")
    try:
        # Paths are literal pathspecs (a '*' in a path is not a glob); check-ignore takes plain paths only.
        env = _GIT_ENV if args[0] == "check-ignore" else {**_GIT_ENV, "GIT_LITERAL_PATHSPECS": "1"}
        done = subprocess.run(["git", "-C", str(repo), "--no-pager", *args], capture_output=True, check=False,
                              env=env, timeout=60,
                              **({"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}))
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseVerificationError("git", f"git could not run: {type(exc).__name__}") from None
    if done.returncode not in ok_codes:
        raise ReleaseVerificationError("git", f"git {args[0]} failed")
    return cast(bytes, done.stdout)


@dataclass(frozen=True)
class VerifiedArtifact:
    """One artifact read from a Git commit and hash-checked."""

    path: str
    sha256: str
    git_blob: str
    data: bytes


def verify_git_artifact(repo: Path, commit: str, path: str, sha256: str,
                        *, git_blob: str | None = None) -> VerifiedArtifact:
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
    compat: tuple[VerifiedArtifact, ...]
    validation: VerifiedArtifact | None = None


def verify_git_release(
    repo: Path,
    data_commit: str,
    artifacts: Sequence[Mapping[str, Any]],
    *,
    supported_schema_fingerprints: Collection[str] | None = None,
) -> VerifiedRelease:
    """Verify a release selection ``(data_commit, path, sha256)`` for graph, manifest and flat copies.

    Args:
        repo: Git repository (working tree or bare); only objects are read, never the working tree.
        data_commit: Full commit id.
        artifacts: Descriptor-style entries ``{"role": graph|manifest|validation|compat_copy, "path",
            "sha256", "git_blob"?}``: exactly one graph and one manifest, at most one validation report (it
            must be the sibling ``validation.json`` and hash to ``validation.report_sha256``), any number of
            compat copies.
        supported_schema_fingerprints: When given, the manifest's ``schema.import_fingerprint`` must be in it.

    Raises:
        ReleaseVerificationError: any hash, blob, manifest, layout or compat mismatch.
    """
    by_role: dict[str, list[Mapping[str, Any]]] = {}
    for item in artifacts:
        by_role.setdefault(str(item.get("role")), []).append(item)
    if len(by_role.get("graph", [])) != 1 or len(by_role.get("manifest", [])) != 1:
        raise ReleaseVerificationError("layout", "exactly one graph and one manifest artifact are required")
    if set(by_role) - {"graph", "manifest", "validation", "compat_copy"} or len(by_role.get("validation", [])) > 1:
        raise ReleaseVerificationError("layout", "unsupported artifact role")
    loaded: dict[str, list[VerifiedArtifact]] = {}
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
    validation = (loaded.get("validation") or cast(list[VerifiedArtifact | None], [None]))[0]
    if validation is not None and (validation.path != graph.path.rsplit("/", 1)[0] + "/validation.json"
                                   or validation.sha256 != manifest.validation.report_sha256):
        raise ReleaseVerificationError("manifest_mismatch",
                                       "validation.json is not the report the manifest binds")
    if supported_schema_fingerprints is not None and (
            manifest.schema_block.import_fingerprint not in supported_schema_fingerprints):
        raise ReleaseVerificationError("schema_unsupported",
                                       "manifest schema fingerprint is not in the supported set")
    return VerifiedRelease(data_commit, manifest, graph, manifest_art, tuple(loaded.get("compat_copy", [])),
                           validation)


# =========================================================================== T26B: commit-ready release
#
# prepare (before the owner commits)                 after the owner commits
# ------------------------------------                -------------------------------------------------
# validate_commit_candidate(repo, snapshot, report)   verify_snapshot_selection(repo, record, commit)
#   -> CommitCandidate -> build_release_record()        -> committed bytes == tested bytes, by commit+path
#   (tested bytes + binary verdict + owner commands)  build_data_release_descriptor() (T26A schema 9.2)
#                                                     export_snapshot_selection() / lookup_snapshot()
#
# Nothing here writes to Git. The owner runs the printed `git add` / `git commit` commands; nothing pushes.

RELEASE_RECORD_KIND = "f2w.kg_tested_release"
RELEASE_RECORD_VERSION = "1"
PRIVATE_DEPENDENCY_CHECK_VERSION = "1"
EVIDENCE_CHECK_VERSION = "1"
BINARY_REPORT_KIND = "binary_evaluation_report"
#: Verdicts in increasing order of strength. A verdict is only ever lowered, never raised.
BINARY_VERDICTS = ("NOT_PASS", "PROVISIONAL", "PASS")


class ReleaseRefused(ReleaseVerificationError):
    """A snapshot is not commit-ready, or committed bytes are not the tested bytes.

    Attributes:
        code: Stable refusal reason (see ``REFUSAL_CODES`` in the module docs / the T26B report).
        findings: Supporting findings (private-dependency or disclosure rules); values are never echoed.
    """

    def __init__(self, code: str, message: str, findings: Sequence[Finding] = ()) -> None:
        super().__init__(code, message)
        self.findings = list(findings)


def git_blob_id(data: bytes, object_format: str = "sha1") -> str:
    """Git blob object id of ``data`` (what ``git hash-object --no-filters`` prints), computed without Git."""
    if object_format not in ("sha1", "sha256"):
        raise ReleaseRefused("git", f"unsupported Git object format {object_format!r}")
    return hashlib.new(object_format, b"blob %d\0" % len(data) + data).hexdigest()


def canonical_json_bytes(doc: Any) -> bytes:
    """Sorted keys, two-space indent, UTF-8, trailing newline (records, reports, descriptors)."""
    return (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _nul_split(blob: bytes) -> list[str]:
    return [part.decode("utf-8", "surrogateescape") for part in blob.split(b"\0") if part]


# --------------------------------------------------------------------------- Git working-tree state


def _object_format(repo: Path) -> str:
    try:
        fmt = _git(repo, "rev-parse", "--show-object-format").decode().strip()
    except ReleaseVerificationError:
        fmt = "sha1"  # Git before 2.25 knows only SHA-1 repositories
    return fmt or "sha1"


def _head(repo: Path) -> str | None:
    out = _git(repo, "rev-parse", "-q", "--verify", "HEAD^{commit}", ok_codes=(0, 1)).decode().strip()
    return out or None


def _repo_root(repo: Path) -> Path:
    """``repo`` must be the top of a Git working tree (not a subdirectory, not a bare repository)."""
    repo = Path(repo)
    try:
        inside = _git(repo, "rev-parse", "--is-inside-work-tree").decode().strip()
        top = Path(_git(repo, "rev-parse", "--show-toplevel").decode().strip())
    except ReleaseVerificationError:
        raise ReleaseRefused("not_a_work_tree", "repository is not a Git working tree") from None
    if inside != "true" or top.resolve() != repo.resolve():
        raise ReleaseRefused("not_a_work_tree", "pass the top directory of the Git working tree")
    return repo


def _tree_blobs(repo: Path, commit: str | None, paths: Sequence[str]) -> dict[str, tuple[str, str]]:
    """``path -> (mode, blob)`` for regular entries of ``paths`` at ``commit`` (empty when no commit)."""
    if not commit or not paths:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for entry in _nul_split(_git(repo, "ls-tree", "-z", commit, "--", *paths)):
        meta, _, name = entry.partition("\t")
        mode, kind, blob = meta.split(" ")
        out[name] = (mode, blob if kind == "blob" else kind)
    return out


def _index_blobs(repo: Path, paths: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in _nul_split(_git(repo, "ls-files", "-s", "-z", "--", *paths)):
        meta, _, name = entry.partition("\t")
        out[name] = meta.split(" ")[1]
    return out


def _tracked(repo: Path, paths: Sequence[str]) -> set[str]:
    return set(_nul_split(_git(repo, "ls-files", "-z", "--", *paths))) if paths else set()


def _ignored(repo: Path, paths: Sequence[str]) -> set[str]:
    """Paths Git would ignore (tracked files are never reported: ``check-ignore`` consults the index)."""
    if not paths:
        return set()
    out = _git(repo, "check-ignore", "-z", "--stdin", stdin=b"".join(p.encode() + b"\0" for p in paths),
               ok_codes=(0, 1))
    return set(_nul_split(out))


def check_worktree(repo: Path, candidate: Mapping[str, bytes], *, object_format: str,
                   allow_untracked: bool = False) -> dict[str, Any]:
    """Git preflight for a commit candidate (``candidate`` maps repo paths to the tested bytes).

    Refusals: ``dirty_worktree`` (any staged, modified, deleted, renamed or conflicted path outside the candidate,
    or an untracked one unless ``allow_untracked``), ``frozen_overwrite`` (a candidate path already committed
    with other bytes, or modified in the working tree), ``staged_bytes_differ`` (the index holds other bytes for a
    candidate path) and ``filter_would_change_bytes`` (``.gitattributes``/config filters would store different
    bytes than the tested ones).
    """
    paths = sorted(candidate)
    head = _head(repo)
    entries = _nul_split(_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"))
    dirty: list[str] = []
    untracked_outside = 0
    for entry in entries:
        code, path = entry[:2], entry[3:]
        if path in candidate:
            if code not in ("??", "A ", "AM"):  # new file; "AM" is caught by the staged-bytes check below
                raise ReleaseRefused("frozen_overwrite",
                                     f"{path} is a frozen snapshot path that Git sees as changed ({code.strip()})")
            continue
        if code == "??" and allow_untracked:
            untracked_outside += 1
            continue
        dirty.append(f"{code.strip() or '?'} {path}")
    if dirty:
        raise ReleaseRefused("dirty_worktree",
                             f"the working tree has {len(dirty)} change(s) outside the candidate (first: "
                             f"{dirty[0]}); commit or stash them first so only the tested files are committed")
    committed = _tree_blobs(repo, head, paths)
    staged = _index_blobs(repo, paths)
    already: list[str] = []
    for path in paths:
        blob = git_blob_id(candidate[path], object_format)
        if path in committed:
            if committed[path][1] != blob:
                raise ReleaseRefused("frozen_overwrite",
                                     f"{path} is already committed with different bytes; snapshots are immutable")
            already.append(path)
        if path in staged and staged[path] != blob:
            raise ReleaseRefused("staged_bytes_differ", f"the index holds different bytes for {path}")
        filtered = _git(repo, "hash-object", "--stdin", f"--path={path}", stdin=candidate[path]).decode().strip()
        if filtered != blob:
            raise ReleaseRefused("filter_would_change_bytes",
                                 f"Git attributes or filters would store {path} with other bytes; mark KG paths "
                                 "-text (release_contract.md section 3)")
    return {"object_format": object_format, "base_commit": head, "worktree_clean": True,
            "untracked_outside_candidate": untracked_outside, "already_committed": already}


# --------------------------------------------------------------------------- private dependencies

#: Repository roots whose files may be private (gitignored papers, parsed packages, job state ...).
_PATH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.~/:@%-])(?:\./)?((?:papers|storage|workdir|runs|logs|data|\.cache|AGENT|\.agents)/"
    r"[A-Za-z0-9._/@+%=,~-]*)")
_SOURCE_INDEX = ("storage/source_index/", "source_index/")
_CROP_FIELDS = frozenset({"crop", "crops", "crop_path", "crop_url", "crop_ref", "figure_crop", "figure_crops",
                          "image_path", "image_url", "image_bytes", "image_b64", "figure_image", "page_image"})
_DATA_IMAGE = re.compile(r"(?i)\bdata:image/")


def _walk_keys(value: Any, where: str) -> Iterator[tuple[str, str, Any]]:
    """``(location, key, value)`` for every mapping entry at any depth."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            here = f"{where}.{key}" if where else str(key)
            yield here, str(key), item
            yield from _walk_keys(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_keys(item, f"{where}[{index}]")


def _path_tokens(text: str) -> list[str]:
    out = []
    for match in _PATH_TOKEN.finditer(text):
        token = match.group(1).rstrip(".,;:)]}'\"")
        if token.count("/") >= 1 and ".." not in token and not token.endswith("/"):
            out.append(token)
    return out


def _is_parsed_package(path: str) -> bool:
    return "/parsed/" in f"/{path}" or path.endswith("/paper.json") or path.endswith(".parsed.json")


def _manifest_refs(manifest: KGManifest) -> list[tuple[str, str]]:
    """``(location, ref)`` of every repository reference a manifest makes."""
    refs: list[tuple[str, str]] = [("schema.path", manifest.schema_block.path)]
    refs += [(f"inputs[{i}].ref", item.ref) for i, item in enumerate(manifest.inputs)]
    if manifest.corpus_revision.source_list_ref:
        refs.append(("corpus_revision.source_list_ref", manifest.corpus_revision.source_list_ref))
    if manifest.validation.report_ref:
        refs.append(("validation.report_ref", manifest.validation.report_ref))
    return refs


def check_private_dependencies(
    repo: Path,
    graph: Mapping[str, Any],
    manifest_doc: Mapping[str, Any],
    validation_doc: Mapping[str, Any],
    *,
    candidate_paths: Collection[str],
    allowlist: Collection[str] = frozenset(),
) -> list[Finding]:
    """Refuse dependencies on material that must stay out of Git (task T26B; release_contract.md section 4).

    Rules (all ``reject``):

    * ``D1`` a reference into the source-RAG index (``storage/source_index/``).
    * ``D2`` a repository path, in the graph or a manifest reference, that Git ignores (gitignored ``papers/``,
      ``storage/terminology/*.json`` terms files, job state ...). Tracked files (for example the beamline ops
      documents kept by O-G1) and the candidate's own files are fine.
    * ``D3`` figure crops or page images: a non-empty crop/image field, or an embedded ``data:image/`` value.
    * ``D4`` a parsed paper package (``.../parsed/...``, ``paper.json``, ``*.parsed.json``).
    * ``D5`` a manifest reference that is repository-relative but not tracked in Git (use ``private:``).
    * ``R3``/``R4``/``R5`` credentials, absolute local paths and signed URLs in ``manifest.json`` and
      ``validation.json`` (the graph itself is covered by :func:`check_disclosure`).

    Values are never echoed; a finding names the record, field and rule only.
    """
    findings: list[Finding] = []
    graph_tokens: dict[str, str] = {}
    for record_id, field_name, value in _records(graph):
        for where, key, item in [(field_name, field_name, value)] + list(_walk_keys(value, field_name)):
            if key in _CROP_FIELDS and item not in (None, "", [], {}):
                findings.append(Finding("D3", "reject", f"{record_id}:{where}", "figure crop or page image field"))
        for where, text in _walk(value, field_name):
            if allowlist_key("graph.json", record_id, field_name, text) in allowlist:
                continue
            location = f"{record_id}:{where}"
            if _DATA_IMAGE.search(text):
                findings.append(Finding("D3", "reject", location, "embedded image bytes"))
            for token in _path_tokens(text):
                if token.startswith(_SOURCE_INDEX):
                    findings.append(Finding("D1", "reject", location, "reference into the source-RAG index"))
                elif _is_parsed_package(token):
                    findings.append(Finding("D4", "reject", location, "reference to a parsed paper package"))
                elif token not in candidate_paths:
                    graph_tokens.setdefault(token, location)
    refs = _manifest_refs(parse_manifest(canonical_json_bytes(manifest_doc)))
    repo_refs = {ref: where for where, ref in refs
                 if not ref.startswith("private:") and ref not in candidate_paths}
    for ref, where in repo_refs.items():
        if ref.startswith(_SOURCE_INDEX):
            findings.append(Finding("D1", "reject", f"manifest:{where}", "reference into the source-RAG index"))
        elif _is_parsed_package(ref):
            findings.append(Finding("D4", "reject", f"manifest:{where}", "reference to a parsed paper package"))
    ignored = _ignored(repo, sorted(set(graph_tokens) | set(repo_refs)))
    for token in sorted(ignored & set(graph_tokens)):
        findings.append(Finding("D2", "reject", graph_tokens[token],
                                f"references a gitignored path under {token.split('/', 1)[0]}/"))
    tracked = _tracked(repo, sorted(repo_refs))
    for ref, where in sorted(repo_refs.items()):
        if ref in ignored:
            findings.append(Finding("D2", "reject", f"manifest:{where}",
                                    f"references a gitignored path under {ref.split('/', 1)[0]}/"))
        elif ref not in tracked:
            findings.append(Finding("D5", "reject", f"manifest:{where}",
                                    "repository reference is not tracked in Git; use a private: reference"))
    for label, doc in (("manifest", manifest_doc), ("validation", validation_doc)):
        for where, text in _walk(doc, ""):
            findings.extend(_string_findings(f"{label}:{where}", text, None))
    unique = {(f.rule, f.location, f.detail): f for f in findings}
    return sorted(unique.values(), key=lambda f: (f.rule, f.location, f.detail))


# --------------------------------------------------------------------------- schema binding


def check_schema_binding(repo: Path, manifest: KGManifest, validation_doc: Mapping[str, Any], *,
                         allow_pending_review_schema: bool = False) -> dict[str, Any]:
    """Check that the manifest's schema block binds to a schema of the repository.

    The block must name a selectable schema of the repository, by path, bytes and closure
    fingerprint, and agree with ``validation.json``.

    Refusals: ``schema_fingerprint_missing``, ``schema_unregistered``, ``schema_mismatch`` (path or file hash),
    ``schema_fingerprint_mismatch`` (closure fingerprint, including a pre-S4 fingerprint),
    ``schema_pending_review`` (a schema set such as 0.3.0 that binds only with explicit selection),
    ``schema_unselectable``, ``validation_mismatch`` (validation.json records another fingerprint or a failure).
    """
    from app.modules.schema.loading import SchemaLoadError, closure_fingerprint  # lazy import
    from app.modules.schema.registry import RegistryError, SchemaRegistry

    block = manifest.schema_block
    if not block.import_fingerprint:
        raise ReleaseRefused("schema_fingerprint_missing",
                             "manifest schema.import_fingerprint is missing; the importer cannot bind the schema")
    schema_dir = Path(repo) / "storage" / "schema"
    if not schema_dir.is_dir():
        raise ReleaseRefused("schema_unregistered", "the repository has no storage/schema directory")
    registry = SchemaRegistry.discover(schema_dir)
    try:
        entry = registry.get(block.name)
    except RegistryError:
        raise ReleaseRefused("schema_unregistered", f"schema {block.name!r} is not registered") from None
    if entry.path != block.path or entry.sha256 != block.sha256:
        raise ReleaseRefused("schema_mismatch", f"schema {block.name!r} bytes or path differ from the manifest")
    if not entry.is_linkml or entry.known_broken:
        raise ReleaseRefused("schema_unselectable", f"schema {block.name!r} is not a selectable LinkML schema")
    if entry.pending_review and not allow_pending_review_schema:
        raise ReleaseRefused("schema_pending_review",
                             f"schema {block.name!r} belongs to set {entry.schema_set}, pending owner review; it "
                             "binds only with explicit selection (--allow-pending-review-schema)")
    try:
        actual = closure_fingerprint(entry)
    except SchemaLoadError:
        raise ReleaseRefused("schema_unselectable",
                             f"schema {block.name!r} import closure cannot be resolved") from None
    if block.import_fingerprint != actual:
        raise ReleaseRefused("schema_fingerprint_mismatch",
                             f"schema {block.name!r} closure fingerprint differs from the manifest "
                             "(stale or edited)")
    recorded = (validation_doc.get("schema") or {}) if isinstance(validation_doc, Mapping) else {}
    if recorded.get("import_fingerprint") != actual or validation_doc.get("status") != "pass":
        raise ReleaseRefused("validation_mismatch",
                             "validation.json does not record a passing validation against this schema "
                             "fingerprint")
    return {"name": entry.schema_id, "path": entry.path, "sha256": entry.sha256, "version": entry.version,
            "import_fingerprint": actual, "binding": "verified", "schema_set": entry.schema_set,
            "review_status": entry.review_status}


# --------------------------------------------------------------------------- evidence availability

_WEB_SOURCE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}/)")
_PRIVATE_REF = re.compile(r"^private:(?!.*\.\.)[A-Za-z0-9._:/-]+$")


@dataclass
class EvidenceReport:
    """Path-free availability of every evidence locator and required private asset (hash in the record)."""

    sources: list[dict[str, Any]]
    unlocated_snippets: int

    @property
    def counts(self) -> dict[str, int]:
        """Sources by status."""
        return dict(sorted(Counter(s["status"] for s in self.sources).items()))

    @property
    def status(self) -> str:
        """Overall status of the evidence report.

        ``fail`` (a mismatch or invalid reference), ``incomplete`` (some unavailable, recorded) or
        ``complete``.
        """
        if any(s["status"] in ("mismatch", "invalid") for s in self.sources):
            return "fail"
        return "incomplete" if any(s["status"] == "unavailable" for s in self.sources) else "complete"

    @property
    def required_unavailable(self) -> list[str]:
        """Required private assets that are not available (they block promotion)."""
        return sorted(str(s["ref"] or s["source"]) for s in self.sources
                      if s["required"] and s["status"] != "available")

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready body."""
        return {"report_kind": "f2w.kg_evidence_availability", "check_version": EVIDENCE_CHECK_VERSION,
                "status": self.status, "counts": self.counts, "unlocated_snippets": self.unlocated_snippets,
                "required_unavailable": self.required_unavailable, "sources": self.sources}

    @property
    def sha256(self) -> str:
        """Hash of the canonical report bytes."""
        return sha256_bytes(canonical_json_bytes(self.to_dict()))


def _locators(graph: Mapping[str, Any]) -> tuple[Counter[str], int]:
    """Locator counts per source id, and the number of snippets that carry no source."""
    counts: Counter[str] = Counter()
    unlocated = 0
    nodes = graph.get("things") if "things" in graph else graph.get("nodes")
    edges = graph.get("associations") if "associations" in graph else graph.get("edges")
    for node in nodes or []:
        if not isinstance(node, Mapping):
            continue
        for snip in node.get("context_snippets") or []:
            if isinstance(snip, Mapping):
                source = snip.get("source_paper")
                if isinstance(source, str) and source.strip():
                    counts[source.strip()] += 1
                else:
                    unlocated += 1
        for source in node.get("source_papers") or []:
            if isinstance(source, str) and source.strip():
                counts[source.strip()] += 1
    for edge in edges or []:
        evidence = edge.get("has_evidence") if isinstance(edge, Mapping) else None
        for item in evidence if isinstance(evidence, list) else [evidence]:
            if isinstance(item, Mapping) and isinstance(item.get("source_paper"), str):
                counts[item["source_paper"].strip()] += 1
    return counts, unlocated


def _asset_status(ref: Any, sha256: Any, evidence_root: Path | None) -> tuple[str, str | None]:
    if not isinstance(ref, str) or not _PRIVATE_REF.match(ref) or not isinstance(sha256, str) or not _SHA256.match(
            sha256):
        return "invalid", "inventory_entry_needs_private_ref_and_sha256"
    if evidence_root is None:
        return "unavailable", "no_evidence_root"
    root = Path(evidence_root).resolve()
    target = (root / ref[len("private:"):]).resolve()
    if root != target and root not in target.parents:
        return "invalid", "ref_escapes_evidence_root"
    if not target.is_file():
        return "unavailable", "missing_at_evidence_root"
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return ("available", None) if digest.hexdigest() == sha256 else ("mismatch", "sha256_differs")


def check_evidence_availability(
    repo: Path | None,
    graph: Mapping[str, Any],
    manifest: KGManifest,
    *,
    inventory: Mapping[str, Any] | None = None,
    evidence_root: Path | None = None,
) -> EvidenceReport:
    """Resolve every evidence locator or record it as unavailable; nothing is fetched or rebuilt.

    A locator source is classified as ``external`` (a web source: recorded, never fetched), ``available`` (a file
    tracked in the repository, or a ``private:`` inventory asset whose bytes at ``evidence_root`` hash to the
    inventory's sha256), ``unavailable`` (no inventory entry, no evidence root, or missing there), ``mismatch``
    (present with other bytes) or ``invalid`` (an inventory entry that is not a ``private:`` ref plus sha256).
    Required private assets are checked the same way and marked ``required``: inventory sources flagged
    ``"required": true``, the inventory's ``required_private_assets`` list, and the manifest's
    ``evidence.required_private_assets`` when the manifest model carries that optional block.

    Args:
        repo: Repository root used to find tracked files; ``None`` when there is no repository.
        graph: The graph document whose evidence locators are checked.
        manifest: The release manifest (may carry ``evidence.required_private_assets``).
        inventory: ``{"sources": {<source id>: {"ref": "private:...", "sha256": ..., "required"?: bool}},
            "required_private_assets"?: [{"ref", "sha256"}]}`` (operator supplied; never committed).
        evidence_root: Mounted private store that ``private:`` refs resolve under (never recorded).
    """
    counts, unlocated = _locators(graph)
    entries = dict((inventory or {}).get("sources") or {})
    repo_like = [s for s in counts if "/" in s and not _WEB_SOURCE.match(s) and _REPO_PATH.match(s)]
    tracked = _tracked(repo, repo_like) if repo is not None and repo_like else set()
    sources: list[dict[str, Any]] = []
    for source in sorted(counts):
        row: dict[str, Any] = {"source": source, "locators": counts[source], "required": False, "ref": None,
                               "sha256": None}
        if source in tracked:
            row.update(kind="repository", status="available", reason=None, ref=source)
        elif _WEB_SOURCE.match(source):
            row.update(kind="web", status="external", reason="not_fetched")
        elif source in entries:
            entry = entries[source] if isinstance(entries[source], Mapping) else {}
            status, reason = _asset_status(entry.get("ref"), entry.get("sha256"), evidence_root)
            row.update(kind="private", status=status, reason=reason, required=entry.get("required") is True,
                       ref=entry.get("ref") if status != "invalid" else None,
                       sha256=entry.get("sha256") if status != "invalid" else None)
        else:
            row.update(kind="private", status="unavailable", reason="not_in_inventory")
        sources.append(row)
    evidence = manifest.model_dump(mode="json", by_alias=True).get("evidence") or {}
    required = list(evidence.get("required_private_assets") or []) + list(
        (inventory or {}).get("required_private_assets") or [])
    for asset in (a if isinstance(a, Mapping) else {} for a in required):
        status, reason = _asset_status(asset.get("ref"), asset.get("sha256"), evidence_root)
        sources.append({"source": asset.get("ref"), "locators": 0, "required": True, "kind": "private",
                        "status": status, "reason": reason, "ref": asset.get("ref"),
                        "sha256": asset.get("sha256")})
    return EvidenceReport(sources, unlocated)


def verify_release_dependencies(repo: Path | None, graph: Mapping[str, Any], manifest: KGManifest, *,
                                inventory: Mapping[str, Any] | None = None,
                                evidence_root: Path | None = None) -> EvidenceReport:
    """Promotion-time dependency gate: required private assets must be present with the pinned bytes.

    Raises:
        ReleaseRefused: ``evidence_mismatch`` (an asset or inventory entry is wrong) or ``evidence_unavailable``
            (a required asset is missing). Nothing is rebuilt and nothing is added to Git to make it pass.
    """
    report = check_evidence_availability(repo, graph, manifest, inventory=inventory, evidence_root=evidence_root)
    if report.status == "fail":
        raise ReleaseRefused("evidence_mismatch", "an evidence asset or inventory entry does not match its pin")
    if report.required_unavailable:
        raise ReleaseRefused("evidence_unavailable",
                             f"{len(report.required_unavailable)} required private evidence asset(s) are "
                             "unavailable")
    return report


# --------------------------------------------------------------------------- binary report binding


@dataclass(frozen=True)
class BinaryReportBinding:
    """The binary report a candidate was tested against, and its verdict as recorded (never raised)."""

    sha256: str
    ref: str
    suite: str
    manifest_sha256: str | None
    runner_revision: str | None
    gate_status: str
    verdict: str
    reasons: tuple[str, ...]
    subject: Mapping[str, Any]
    source_commit: str | None
    source_dirty: bool | None

    @property
    def passed(self) -> bool:
        """True only for a PASS verdict (PROVISIONAL and NOT_PASS are not passes)."""
        return self.verdict == "PASS"

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form for the release record."""
        return {"sha256": self.sha256, "ref": self.ref, "suite": self.suite,
                "manifest_sha256": self.manifest_sha256,
                "runner_revision": self.runner_revision, "gate_status": self.gate_status, "verdict": self.verdict,
                "passed": self.passed, "verdict_reasons": list(self.reasons), "subject": dict(self.subject),
                "source_commit": self.source_commit, "source_dirty": self.source_dirty}


def _lower(verdict: str, other: str) -> str:
    return min(verdict, other, key=BINARY_VERDICTS.index)


def bind_binary_report(report_bytes: bytes, *, ref: str, corpus: str, snapshot_id: str, graph_sha256: str,
                       manifest_sha256: str) -> BinaryReportBinding:
    """Bind an executed ``evals.binary`` report to this snapshot and read its verdict.

    The report must carry a ``subject`` block ``{"kind": "kg_snapshot", "corpus", "snapshot_id", "graph_sha256",
    "manifest_sha256"}`` naming the bytes it ran against (the runner's ``--subject-snapshot`` writes it). The
    verdict starts from ``gate.status`` and can only go down. The canonical provisional marker is the boolean
    ``gate.provisional`` the runner writes; a ``PROVISIONAL`` status, or any result whose ``reported_verdict``
    is ``PROVISIONAL`` (the per-result form the flag is derived from, checked so a report whose flag disagrees
    with its rows is still capped), caps it at PROVISIONAL. Blocking cases or a non-completed/non-PASS
    required result cap it at NOT_PASS. Nothing here turns PROVISIONAL or NOT_PASS into PASS.

    Raises:
        ReleaseRefused: ``binary_report_invalid``, ``binary_report_unbound`` (no subject) or
            ``binary_report_mismatch`` (the subject names other bytes or another snapshot).
    """
    if not isinstance(ref, str) or not (_REPO_PATH.match(ref) or _PRIVATE_REF.match(ref)):
        raise ReleaseRefused("binary_report_invalid",
                             "the binary report ref must be repository-relative or private: (no local paths)")
    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ReleaseRefused("binary_report_invalid", "the binary report is not JSON") from None
    if not isinstance(report, Mapping) or report.get("report_kind") != BINARY_REPORT_KIND:
        raise ReleaseRefused("binary_report_invalid", "not an evals.binary report (report_kind)")
    gate = cast(Mapping[str, Any], report.get("gate") if isinstance(report.get("gate"), Mapping) else {})
    status = gate.get("status")
    if status not in BINARY_VERDICTS:
        raise ReleaseRefused("binary_report_invalid", f"unknown gate status {status!r}")
    subject = report.get("subject")
    if not isinstance(subject, Mapping) or not subject.get("graph_sha256") or not subject.get("manifest_sha256"):
        raise ReleaseRefused("binary_report_unbound",
                             "the binary report names no snapshot bytes (subject.graph_sha256/manifest_sha256)")
    if (subject.get("graph_sha256"), subject.get("manifest_sha256")) != (graph_sha256, manifest_sha256) or (
            subject.get("corpus", corpus), subject.get("snapshot_id", snapshot_id)) != (corpus, snapshot_id):
        raise ReleaseRefused("binary_report_mismatch", "the binary report was run against a different snapshot")
    verdict, reasons = str(status), []
    results = [r for r in report.get("results") or [] if isinstance(r, Mapping)]
    if gate.get("provisional") is not None and not isinstance(gate.get("provisional"), bool):
        raise ReleaseRefused("binary_report_invalid", "gate.provisional must be a boolean")
    if gate.get("provisional") or any(r.get("reported_verdict") == "PROVISIONAL" for r in results):
        verdict = _lower(verdict, "PROVISIONAL")
        reasons.append("provisional_results")
    if gate.get("blocking_cases") or any(
            r.get("required") and (r.get("execution_state") != "completed" or r.get("verdict") != "PASS")
            for r in results):
        verdict = _lower(verdict, "NOT_PASS")
        reasons.append("required_case_not_completed_pass")
    reasons += [str(x) for x in gate.get("gate_reasons") or []]
    source = cast(Mapping[str, Any], report.get("source") if isinstance(report.get("source"), Mapping) else {})
    version = report.get("manifest_version")
    return BinaryReportBinding(
        sha256=sha256_bytes(report_bytes), ref=ref, suite=f"evals.binary/{version or 'unknown'}",
        manifest_sha256=report.get("manifest_sha256"), runner_revision=report.get("runner_revision"),
        gate_status=str(status), verdict=verdict, reasons=tuple(sorted(set(reasons))),
        subject={k: subject.get(k) for k in ("kind", "corpus", "snapshot_id", "graph_sha256", "manifest_sha256",
                                             "data_commit") if subject.get(k) is not None},
        source_commit=source.get("commit"), source_dirty=source.get("dirty"))


# --------------------------------------------------------------------------- commit-ready validation


@dataclass(frozen=True)
class CandidateArtifact:
    """A tested file: the bytes Git must later return for ``path``."""

    role: str
    path: str
    sha256: str
    size_bytes: int
    git_blob: str

    def to_dict(self) -> dict[str, Any]:
        """Record form."""
        return {"role": self.role, "path": self.path, "sha256": self.sha256, "size_bytes": self.size_bytes,
                "git_blob": self.git_blob}


@dataclass(frozen=True)
class CommitCandidate:
    """A snapshot that passed every commit-ready check."""

    manifest: KGManifest
    artifacts: tuple[CandidateArtifact, ...]
    schema: Mapping[str, Any]
    disclosure: DisclosureReport
    evidence: EvidenceReport
    binary_report: BinaryReportBinding
    git: Mapping[str, Any]

    @property
    def release_id(self) -> str:
        """``<corpus>-<snapshot>-<graph sha256 prefix>``: stable for the same bytes."""
        return f"{self.manifest.corpus}-{self.manifest.snapshot_id}-{self.manifest.graph.sha256[:12]}"

    @property
    def paths(self) -> list[str]:
        """Repository paths the owner commits."""
        return [a.path for a in self.artifacts]


def validate_commit_candidate(
    repo: Path,
    snapshot: str,
    *,
    binary_report: bytes,
    binary_report_ref: str,
    evidence_inventory: Mapping[str, Any] | None = None,
    evidence_root: Path | None = None,
    allowlist: Collection[str] = frozenset(),
    allow_pending_review_schema: bool = False,
    allow_untracked: bool = False,
) -> CommitCandidate:
    """Validate ``storage/kg/<corpus>/v<N>`` in ``repo`` for commit. Reads only; writes nothing anywhere.

    Order: layout and hashes (:func:`verify_snapshot_dir`, validation.json required), R1 paths, schema binding,
    disclosure re-run (must reproduce the manifest's report hash and be commit-ready), private dependencies,
    evidence availability, binary report binding, then the Git preflight.

    Raises:
        ReleaseRefused: the first failing check (``code`` says which).
    """
    repo = _repo_root(repo)
    rel = str(snapshot).replace("\\", "/").strip("/")
    match = re.match(r"^storage/kg/([a-z0-9][a-z0-9_-]*)/(v[1-9][0-9]*)$", rel)
    if not match or Path(snapshot).is_absolute():
        raise ReleaseRefused("bad_path", "snapshot must be the repository-relative storage/kg/<corpus>/v<N>")
    try:
        verified = verify_snapshot_dir(repo / rel)
    except ReleaseVerificationError as exc:
        raise ReleaseRefused(exc.code, str(exc)) from None
    manifest = verified.manifest
    files = {name: (repo / rel / name) for name in SNAPSHOT_FILES}
    if not files["validation.json"].is_file():
        raise ReleaseRefused("validation_missing", "validation.json is required for a commit candidate")
    if manifest.validation.status != "pass":
        raise ReleaseRefused("validation_failed", "the manifest records a failing schema validation")
    blobs = {f"{rel}/{name}": path.read_bytes() for name, path in files.items()}
    for compat in verified.compat_paths:
        blobs[compat] = verified.graph_bytes
    roles = {f"{rel}/graph.json": "graph", f"{rel}/manifest.json": "manifest",
             f"{rel}/validation.json": "validation"}
    path_findings = check_paths(blobs)
    if path_findings:
        raise ReleaseRefused("private_dependency", "candidate paths are outside the allowlist", path_findings)
    try:
        validation_doc = json.loads(blobs[f"{rel}/validation.json"].decode("utf-8"))
        manifest_doc = json.loads(blobs[f"{rel}/manifest.json"].decode("utf-8"))
        graph = json.loads(verified.graph_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ReleaseRefused("tampered", "a snapshot file is not JSON") from None
    schema = check_schema_binding(repo, manifest, validation_doc,
                                  allow_pending_review_schema=allow_pending_review_schema)
    disclosure = check_disclosure(graph, paths=sorted(blobs), allowlist=allowlist,
                                  waiver_ref=manifest.disclosure.waiver_ref)
    if disclosure.sha256 != manifest.disclosure.report_sha256 or disclosure.status != manifest.disclosure.status:
        raise ReleaseRefused("disclosure_stale", "the disclosure check no longer reproduces the manifest's report "
                             "(different rules, allowlist or bytes)", disclosure.findings)
    if not disclosure.commit_ready:
        raise ReleaseRefused("disclosure_failed", "the disclosure check rejects the graph", disclosure.findings)
    private = check_private_dependencies(repo, graph, manifest_doc, validation_doc, candidate_paths=set(blobs),
                                         allowlist=allowlist)
    if private:
        raise ReleaseRefused("private_dependency",
                             f"{len(private)} private dependency finding(s): {sorted({f.rule for f in private})}",
                             private)
    evidence = check_evidence_availability(repo, graph, manifest, inventory=evidence_inventory,
                                           evidence_root=evidence_root)
    if evidence.status == "fail":
        raise ReleaseRefused("evidence_mismatch", "an evidence inventory entry is invalid or its bytes differ")
    binding = bind_binary_report(binary_report, ref=binary_report_ref, corpus=manifest.corpus,
                                 snapshot_id=manifest.snapshot_id, graph_sha256=verified.graph_sha256,
                                 manifest_sha256=verified.manifest_sha256)
    object_format = _object_format(repo)
    git_state = check_worktree(repo, blobs, object_format=object_format, allow_untracked=allow_untracked)
    artifacts = tuple(
        CandidateArtifact(roles.get(path, "compat_copy"), path, sha256_bytes(data), len(data),
                          git_blob_id(data, object_format))
        for path, data in sorted(blobs.items(), key=lambda kv: (kv[0] not in roles, kv[0])))
    return CommitCandidate(manifest, artifacts, schema, disclosure, evidence, binding, git_state)


_REPOSITORY = re.compile(r"^[A-Za-z0-9.-]+(?:/[A-Za-z0-9._-]+)+$")


def owner_commands(candidate: CommitCandidate, record_name: str, repository: str) -> list[str]:
    """Return the exact commands the owner runs from the repository root.

    Nothing runs them automatically, and there is deliberately no push: publishing the branch is a
    separate, reviewed owner action outside this tool.
    """
    paths = " ".join(candidate.paths)
    manifest = candidate.manifest
    message = (f"data(kg): freeze {manifest.corpus}/{manifest.snapshot_id} "
               f"(graph sha256 {manifest.graph.sha256[:12]}, binary {candidate.binary_report.verdict})")
    return [
        f"git status --short --ignored -- {paths}",
        f"git -c core.autocrlf=false -c core.safecrlf=false add -f -- {paths}",
        "git diff --cached --name-only",
        f"git commit -m {json.dumps(message)} -- {paths}",
        f"python3 scripts/prepare_kg_release.py verify-committed --record {record_name} "
        "--commit \"$(git rev-parse HEAD)\" --out <release-dir>",
        "# no push: publishing the branch is a separate owner decision outside this tool",
    ]


def build_release_record(candidate: CommitCandidate, *, record_name: str, repository: str) -> dict[str, Any]:
    """The tested-release record (``f2w.kg_tested_release`` v1), written BEFORE the commit exists.

    It names the tested bytes (path, sha256, expected Git blob), the checks they passed and the binary report and
    verdict they were tested against. It holds no commit id, no local path and no credential; the T26A data-release
    descriptor is derived from it only after :func:`verify_snapshot_selection` succeeds on the commit.
    """
    if not _REPOSITORY.match(repository or ""):
        raise ReleaseRefused("record_invalid",
                             "repository must be a canonical identity such as github.com/org/repo")
    manifest = candidate.manifest
    record = {
        "record_kind": RELEASE_RECORD_KIND, "record_version": RELEASE_RECORD_VERSION,
        "release_id": candidate.release_id, "state": "tested_uncommitted", "repository": repository,
        "corpus": manifest.corpus, "snapshot_id": manifest.snapshot_id, "graph_id": manifest.graph_id,
        "artifacts": [a.to_dict() for a in candidate.artifacts],
        "schema": dict(candidate.schema),
        "validation": {"status": manifest.validation.status, "report_sha256": manifest.validation.report_sha256,
                       "suite": manifest.validation.suite},
        "disclosure": {"check_version": candidate.disclosure.check_version, "status": candidate.disclosure.status,
                       "report_sha256": candidate.disclosure.sha256, "waiver_ref": candidate.disclosure.waiver_ref,
                       "informational": candidate.disclosure.to_dict()["informational"]},
        "private_dependencies": {"check_version": PRIVATE_DEPENDENCY_CHECK_VERSION, "status": "pass",
                                 "findings": []},
        "evidence": {"report_sha256": candidate.evidence.sha256, **candidate.evidence.to_dict()},
        "binary_report": candidate.binary_report.to_dict(),
        "code": {"code_commit": manifest.created.code_commit, "json2kg_sha256": manifest.created.json2kg_sha256},
        "git": dict(candidate.git),
        "owner_commands": owner_commands(candidate, record_name, repository),
        "automation": {"git_writes": "none", "push": "never"},
    }
    leaks = [where for where, text in _walk(record, "") if _LOCAL_PATH.search(text) or any(
        p.search(text) for _, p in _SECRET_PATTERNS)]
    if leaks:  # defensive: the record is meant to be shareable
        raise ReleaseRefused("private_dependency",
                             f"release record would carry a local path or secret at {leaks[0]}")
    return record


# --------------------------------------------------------------------------- committed == tested

_RECORD_ROLES = ("graph", "manifest", "validation", "compat_copy")


def parse_release_record(data: bytes) -> dict[str, Any]:
    """Load and shape-check a tested-release record."""
    try:
        record = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ReleaseRefused("record_invalid", "the release record is not JSON") from None
    if not isinstance(record, Mapping) or (record.get("record_kind"), record.get("record_version")) != (
            RELEASE_RECORD_KIND, RELEASE_RECORD_VERSION):
        raise ReleaseRefused("record_invalid", "not an f2w.kg_tested_release v1 record")
    if not _REPOSITORY.match(str(record.get("repository"))) or not isinstance(record.get("binary_report"),
                                                                              Mapping):
        raise ReleaseRefused("record_invalid", "the record lacks a repository identity or binary report")
    items = record.get("artifacts")
    if not isinstance(items, list) or not items:
        raise ReleaseRefused("record_invalid", "the record lists no artifacts")
    for item in items:
        if not isinstance(item, Mapping) or item.get("role") not in _RECORD_ROLES or not _REPO_PATH.match(
                str(item.get("path"))) or not _SHA256.match(str(item.get("sha256"))) or not _GIT_COMMIT.match(
                str(item.get("git_blob"))):
            raise ReleaseRefused("record_invalid", "an artifact entry is malformed")
    return dict(record)


@dataclass(frozen=True)
class CommittedRelease:
    """Committed bytes that equal the tested bytes, resolved by commit and path."""

    data_commit: str
    record: Mapping[str, Any]
    release: VerifiedRelease
    artifacts: tuple[VerifiedArtifact, ...]


def verify_snapshot_selection(repo: Path, record: Mapping[str, Any], data_commit: str) -> CommittedRelease:
    """Compare the bytes at ``data_commit`` with the tested bytes in ``record``, path by path.

    Every tested artifact must exist at the commit as a regular blob with the recorded blob id and sha256, the
    snapshot directory at the commit must hold nothing else, the manifest/graph/compat set must verify as a release
    (:func:`verify_git_release`), and the bound schema file at the commit must hash to the recorded schema sha256.
    Import and promotion call this (or :func:`verify_git_release` on a descriptor) before using any byte.

    Raises:
        ReleaseRefused: ``committed_differs_from_tested`` (bytes, blob, missing path or extra file),
            ``object_format_mismatch``, or a :func:`verify_git_release` code.
    """
    record = parse_release_record(canonical_json_bytes(record))
    if (record.get("git") or {}).get("object_format", "sha1") != _object_format(repo):
        raise ReleaseRefused("object_format_mismatch", "the repository uses a different Git object format")
    loaded: list[VerifiedArtifact] = []
    for item in record["artifacts"]:
        try:
            loaded.append(verify_git_artifact(repo, data_commit, item["path"], item["sha256"],
                                              git_blob=item["git_blob"]))
        except ReleaseVerificationError as exc:
            if exc.code in ("bad_commit", "git"):
                raise
            raise ReleaseRefused("committed_differs_from_tested", f"{item['path']}: {exc}") from None
    snapshot_dir = f"storage/kg/{record['corpus']}/{record['snapshot_id']}"
    present = {p for p in _nul_split(_git(repo, "ls-tree", "-z", "--name-only", data_commit, "--",
                                          snapshot_dir + "/"))}
    expected = {i["path"] for i in record["artifacts"] if i["path"].startswith(snapshot_dir + "/")}
    if present != expected:
        raise ReleaseRefused("committed_differs_from_tested",
                             f"the committed snapshot directory differs from the tested set (extra: "
                             f"{sorted(present - expected)}, missing: {sorted(expected - present)})")
    try:
        release = verify_git_release(repo, data_commit, record["artifacts"])
        schema = record.get("schema") or {}
        verify_git_artifact(repo, data_commit, str(schema.get("path")), str(schema.get("sha256")))
    except ReleaseVerificationError as exc:
        raise ReleaseRefused(exc.code if exc.code != "wrong_hash" else "committed_differs_from_tested",
                             str(exc)) from None
    return CommittedRelease(data_commit, record, release, tuple(loaded))


def build_data_release_descriptor(committed: CommittedRelease, *, importer_name: str,
                                  importer_version: str) -> dict[str, Any]:
    """T26A data-release descriptor (``schemas/data_release_descriptor.schema.json``), state ``candidate``.

    Created only after the commit exists and verified equal to the tested bytes. ``staging.binary_report.passed``
    is true only for a recorded PASS verdict; PROVISIONAL and NOT_PASS stay ``false`` (the exact verdict stays in
    the release record with the same ``release_id``). ``data_commit`` there is the commit whose bytes were verified
    identical to the ones the report ran against. The state never advances here: ``staging_validated`` and later
    states belong to the staging import and promotion (T26C).
    """
    record, release = committed.record, committed.release
    binary = record["binary_report"]
    manifest = release.manifest
    descriptor = {
        "descriptor_version": "1", "release_id": record["release_id"], "repository": record["repository"],
        "data_commit": committed.data_commit, "corpus": record["corpus"], "snapshot_id": record["snapshot_id"],
        "graph_id": record["graph_id"],
        "artifacts": [{"role": a["role"], "path": a["path"], "sha256": a["sha256"], "git_blob": a["git_blob"],
                       "size_bytes": a["size_bytes"]} for a in record["artifacts"]],
        "compatibility": {"schema_sha256": manifest.schema_block.sha256,
                          "import_fingerprint": manifest.schema_block.import_fingerprint,
                          "importer": {"name": importer_name, "version": importer_version},
                          "code_commits": [manifest.created.code_commit], "image_digests": []},
        "disclosure": {"status": manifest.disclosure.status, "report_sha256": manifest.disclosure.report_sha256,
                       "check_version": manifest.disclosure.check_version},
        "staging": {"binary_report": {
            "sha256": binary["sha256"], "ref": binary["ref"], "suite": binary["suite"],
            "passed": binary["verdict"] == "PASS" and bool(binary["passed"]),
            "graph_sha256": release.graph.sha256, "manifest_sha256": release.manifest_artifact.sha256,
            "data_commit": committed.data_commit}},
        "state": "candidate", "supersedes": None, "rollback_to": None,
    }
    if manifest.disclosure.waiver_ref:
        descriptor["disclosure"]["waiver_ref"] = manifest.disclosure.waiver_ref
    return descriptor


# --------------------------------------------------------------------------- immutable lookup and export


def lookup_snapshot(repo: Path, data_commit: str, corpus: str, snapshot_id: str) -> VerifiedRelease:
    """Resolve ``storage/kg/<corpus>/<snapshot_id>`` at an immutable commit and hash-check it.

    The manifest is read from the commit; the graph, validation report and compat copies are then pinned by the
    manifest's hashes. A branch, tag or abbreviated id never selects data (``bad_commit``).
    """
    if not CORPUS_RE.match(corpus or "") or not re.match(r"^v[1-9][0-9]*$", snapshot_id or ""):
        raise ReleaseVerificationError("bad_path", "corpus/snapshot_id are not a frozen snapshot name")
    if not _GIT_COMMIT.match(data_commit or ""):
        raise ReleaseVerificationError("bad_commit", "data_commit must be a full lowercase Git object id")
    if _git(repo, "cat-file", "-t", data_commit).strip() != b"commit":
        raise ReleaseVerificationError("bad_commit", "object is not a commit")
    base = f"storage/kg/{corpus}/{snapshot_id}"
    listing = _tree_blobs(repo, data_commit, [f"{base}/manifest.json"])
    if f"{base}/manifest.json" not in listing:
        raise ReleaseVerificationError("missing", f"{base}/manifest.json does not exist at the pinned commit")
    manifest_bytes = _git(repo, "cat-file", "blob", listing[f"{base}/manifest.json"][1])
    try:
        manifest = parse_manifest(manifest_bytes)
    except ValueError as exc:
        raise ReleaseVerificationError("manifest_mismatch", f"manifest does not validate: {exc}") from None
    items = [{"role": "graph", "path": f"{base}/graph.json", "sha256": manifest.graph.sha256},
             {"role": "manifest", "path": f"{base}/manifest.json", "sha256": sha256_bytes(manifest_bytes)}]
    if _tree_blobs(repo, data_commit, [f"{base}/validation.json"]):
        items.append({"role": "validation", "path": f"{base}/validation.json",
                      "sha256": manifest.validation.report_sha256})
    items += [{"role": "compat_copy", "path": c.path, "sha256": c.sha256} for c in manifest.compat_paths or []]
    return verify_git_release(repo, data_commit, items)


def export_snapshot_selection(repo: Path, data_commit: str, artifacts: Sequence[Mapping[str, Any]],
                              dest_root: Path) -> list[dict[str, Any]]:
    """Export pinned artifacts from ``data_commit`` to ``dest_root/<path>`` (a mount), byte for byte.

    The selection is verified first (:func:`verify_git_release`); each file is created exclusively, synced and
    re-hashed after writing. An existing identical file is accepted (idempotent re-export); an existing different
    file, a symlink on the way or a write that does not read back identically is refused. The working tree of
    ``repo`` is never read and nothing is written to Git.

    Raises:
        ReleaseVerificationError: a verification code, ``export_conflict`` or ``export_failed``.
    """
    release = verify_git_release(repo, data_commit, artifacts)
    chosen = [release.graph, release.manifest_artifact, *release.compat] + (
        [release.validation] if release.validation is not None else [])
    root = Path(dest_root)
    if root.is_symlink():
        raise ReleaseVerificationError("export_conflict", "the export root is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    done: list[dict[str, Any]] = []
    for art in chosen:
        target = root.joinpath(*art.path.split("/"))
        for parent in [target, *target.parents]:
            if parent == root:
                break
            if parent.is_symlink():
                raise ReleaseVerificationError("export_conflict", f"{art.path}: a symlink is on the export path")
        target.parent.mkdir(parents=True, exist_ok=True)
        status = "written"
        if os.path.lexists(target):
            if target.read_bytes() != art.data:
                raise ReleaseVerificationError("export_conflict", f"{art.path} exists at the export root with "
                                               "other bytes; exported files are never overwritten")
            status = "already_present"
        else:
            tmp = target.with_name(f".{target.name}.export-{os.getpid()}.tmp")
            try:
                with tmp.open("xb") as handle:
                    handle.write(art.data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.link(tmp, target)  # never replaces an existing file
            except FileExistsError:
                raise ReleaseVerificationError("export_conflict", f"{art.path} appeared during export") from None
            except OSError as exc:
                raise ReleaseVerificationError("export_failed", f"{art.path}: {exc.strerror}") from None
            finally:
                if tmp.exists():
                    tmp.unlink()
        if sha256_bytes(target.read_bytes()) != art.sha256:
            raise ReleaseVerificationError("export_failed",
                                           f"{art.path} does not read back with the pinned sha256")
        done.append({"path": art.path, "sha256": art.sha256, "git_blob": art.git_blob, "status": status})
    return done
