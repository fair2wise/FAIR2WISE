"""User-facing disclosure projection (task T21B, ``result.disclose``).

Every payload that leaves the process for a user surface (FastAPI JSON, SSE
event data, ``GET /traces/{turn_id}``, graph node detail, model-proxy answers,
graph exports, errors) goes through one :class:`DisclosureProjector`. The rules
come from ``docs/adr/content-disclosure.md`` and the field classes in
``docs/migration/disclosure/route_field_map.json``:

* **Quotes (Q1-Q7).** Text presented as a quotation must match the retained
  source version at its locator, checked by a :class:`QuoteVerifier` that reads
  the source under a :class:`~.content_access.SourceAccessScope` and returns
  only a verdict, never text. ``EXACT`` is shown verbatim when it matches as is;
  ``NORMALIZED`` only under the recorded whitespace/hyphenation profile;
  ``PARAPHRASE``/``NONE`` and failed checks are shown as unquoted cited
  statements (``MatchType`` docstring, access_contract C12). Omissions are
  marked ``[…]``. Quotes are bounded per quote, per response and cumulatively
  per session and per actor (:class:`DisclosureBudget`,
  :class:`DisclosureLedger`); over budget a quote becomes its citation alone.
* **Source echo.** Long verbatim runs of retrieved source text in an answer
  count against the same budget and are clipped when it is spent, so "print
  the whole paper" and adjacent-span enumeration fail (N1, N3).
* **Field classes.** internal-only fields are dropped or made opaque
  (``rounds`` becomes a summary, local paths become ``f2w://`` ids, raw
  exception text becomes a typed message with a correlation id);
  source-serving trace content (prompts, merged context with page text) is
  replaced by SHA-256 digests and lengths.
* **Legacy compatibility.** Wire shapes, key order, citation tokens
  (``[KG: ..]``, ``[PDF: f p.N]``, ``[OPS: f §h]``, ``[MP: ..]``,
  ``[TILED: ..]``), descriptions, the 600-char pending abstract and the
  1600-char publication abstract cut stay as the frozen UI expects.

Projection never consults capabilities: an ordinary ALS user, an ALS admin and
the ``:local`` operator get the same projection (N12). Importing this module
has no side effects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import re
import threading
import time
from typing import Any, cast
import uuid

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..contracts.evidence import EvidenceRef, MatchType, SourceLocator
from ..contracts.json_types import JsonValue
from .content_access import SourceAccessScope, SourceKind

# --------------------------------------------------------------------------- configuration

#: Owner decision O-D2 (2026-10-12): "don't worry about quote limits as long as they are
#: relevant". The default budget sets no numeric quote limits; a quote is relevant when it
#: verifies against a source retrieved and cited for the turn. STRICT_BUDGET keeps the
#: earlier proposed numbers for deployments that configure limits.
BUDGET_STATUS = "owner-O-D2-relevance-only"

#: Versioned Q1 normalization profile: whitespace runs and line-break hyphenation only.
NORMALIZATION_PROFILE = "ws-hyphen-v1"

#: Internal-omission marker (Q3).
OMISSION = "[…]"

QUOTE_OMITTED = "[quotation omitted: disclosure limit]"
SOURCE_TEXT_OMITTED = "[… source text omitted; see citation …]"


@dataclass(frozen=True)
class DisclosureBudget:
    """Reviewed bounds for quoted source text (ADR Q5). Defaults are PROPOSED.

    All defaults are pending owner decision **O-D2**. They are chosen so the
    existing F2W behaviour still passes: the 600-char pending-card abstract
    (``api.py:6524``), the 1600-char candidate publication abstract
    (``api.py:6572``) and the 800-char ``extra_fields`` cut (``api.py:598``).
    One paper-evidence page is up to 7000 chars (``paper_evidence_agent.py:16``),
    so the session budget stays below half a page per work.

    Attributes:
        max_quote_chars: Longest single quote shown (longer verified quotes are
            clipped with ``[…]``).
        quote_min_words: Quoted spans shorter than this are treated as names or
            terms, not source quotations.
        max_quotes_per_response: Verbatim quotes per response.
        max_quoted_chars_per_work_per_response: Verbatim chars per work per response.
        max_session_quoted_chars_per_work: Cumulative distinct verbatim chars per
            work per session within ``window_seconds``.
        max_actor_quoted_chars_per_work: Same, per actor across sessions.
        window_seconds: Ledger window.
        echo_min_chars: Unquoted verbatim source runs at least this long count as
            disclosed source text.
        echo_shingle_words: Word n-gram size used to detect verbatim runs.
        max_abstract_chars: Publication ``abstract_text`` cut.
        max_pending_abstract_chars: Pending-card ``abstract`` cut.
        max_property_chars: String cut for ``properties``/``extra_fields`` values.
        max_description_chars: ``GraphNode.description`` cut.
        max_code_snippet_chars: ``code_snippet`` cut.
        max_progress_text_chars: Cut for free text in progress/rounds data.
        max_error_chars: Longest error message passed through as written.
        max_region_fraction: Largest share of one source region (page/section) that
            one response may reproduce verbatim, counting quotes and echoed runs
            (``None``: no region rule). Blocks page/section dumps without a count limit.
        status: Approval status of these numbers.
    """

    max_quote_chars: int | None = 600
    quote_min_words: int = 6
    max_quotes_per_response: int | None = 5
    max_quoted_chars_per_work_per_response: int | None = 1500
    max_session_quoted_chars_per_work: int | None = 3000
    max_actor_quoted_chars_per_work: int | None = 6000
    window_seconds: int = 7 * 24 * 3600
    echo_min_chars: int = 200
    echo_shingle_words: int = 8
    max_abstract_chars: int = 1600
    max_pending_abstract_chars: int = 600
    max_property_chars: int = 800
    max_description_chars: int = 4000
    max_code_snippet_chars: int = 8000
    max_progress_text_chars: int = 600
    max_error_chars: int = 300
    max_region_fraction: float | None = None
    status: str = BUDGET_STATUS


#: The earlier proposed numeric limits, available by configuration.
STRICT_BUDGET = DisclosureBudget(status="configured-strict")

#: Default (O-D2): no numeric quote limits (``None`` means unlimited), but one response may
#: not reproduce more than 40% of any single page/section verbatim (owner: "no count limits,
#: block page dumps"). Field cuts that exist today (abstracts, properties, descriptions,
#: errors) are unchanged.
DEFAULT_BUDGET = DisclosureBudget(
    max_quote_chars=None,
    max_quotes_per_response=None,
    max_quoted_chars_per_work_per_response=None,
    max_session_quoted_chars_per_work=None,
    max_actor_quoted_chars_per_work=None,
    max_region_fraction=0.4,
)

# --------------------------------------------------------------------------- patterns

CITATION_RE = re.compile(r"\[(?:KG|PDF|OPS|MP|TILED)\s*:\s*[^\[\]\n]+\]")

# Keys whose values are private evidence, source text, prompts, crops, paths or secrets.
_PRIVATE_KEY_RE = re.compile(
    r"(?i)^(?:context_snippets?|has_evidence|evidence_text|snippets?|full_?text|page_?texts?|pages_text|"
    r"raw_?text|source_text|parsed_text|section_text|sections|chunks?|chunk_text|text_excerpts?|excerpts?|"
    r"merged_context|messages|prompts?|system_prompt|raw_judge_output|raw_classifier_output|"
    r"leeway_system_prompt|crops?|crop_(?:url|path)|image_(?:url|path|bytes)|figure_(?:url|path|image|crop)|"
    r"thumbnail(?:_url)?|pdf_(?:path|bytes|text)|pdf_urls?|local_path|file_path|storage_path|abs_path|"
    r"traceback|exc_info|approval_token|api_key|token|secret|password|authorization)$"
)

# Values that point at served artifacts, private mounts or local hosts.
_ARTIFACT_VALUE_RE = re.compile(
    r"(?i)(?:^file:|^https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?(?:/|$)|"
    r"(?:^|/)(?:papers|static|assets|crops|figures|artifacts|page_images|source_index|blobs?)/\S*)"
)

# The same, for ``/v1`` bodies: ``file:`` URIs and served-artifact paths only. Loopback ``http``
# URLs are kept there because the deployment's Tiled target is a loopback URL on ``:local``
# (publication plans, pointers and approvals name it) and serves no artifact.
_API_ARTIFACT_VALUE_RE = re.compile(
    r"(?i)(?:^file:|(?:^|/)(?:papers|static|assets|crops|figures|artifacts|page_images|source_index|blobs?)/\S*)"
)
# A ``sections`` value that is a list of section *names* (``corpus-audit`` parameters), not section text.
_SECTION_NAME_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")

# Absolute POSIX paths, home-relative paths, Windows paths and fixture placeholders.
_PATH_TAIL = r"[^\s'\"<>\]\)\[,;]*"
_ABS_PATH_RE = re.compile(
    r"(?:^|(?<=[\s'\"(=\[,:;]))(?:"
    r"(?:file://)?/[A-Za-z0-9._@+-]+/" + _PATH_TAIL          # /two/or/more segments
    + r"|(?:~|<WORKSPACE>|<REPO>|<HOME>)(?:[/\\]" + _PATH_TAIL + r")?"
    + r"|[A-Za-z]:\\" + _PATH_TAIL + r")"
)
_SECRET_RE = re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{8,}|bearer\s+[A-Za-z0-9._-]{8,}|api[_-]?key\s*[=:]\s*\S+)")
_RAW_EXC_RE = re.compile(
    r"(?i)(?:traceback \(most recent call last\)|file \"[^\"]+\", line \d+|object has no attribute|"
    r"object is not (?:subscriptable|callable|iterable)|unsupported operand|positional argument|"
    r"unexpected keyword|\[errno \d+\]|no such file or directory|permission denied|is not defined|"
    r"\b(?:Key|Index|Attribute|Type|ZeroDivision|Name|Assertion|Runtime|OS|Import)Error\b|"
    r"list index out of range|connection refused|httpconnectionpool|max retries exceeded|"
    r"status code \d{3}|<[a-z_.]+ object at 0x[0-9a-f]+>)"
)
_FAILED_SAFELY_RE = re.compile(r"^(?P<head>[^:\n]{1,80}?failed safely):\s.*$", re.S)

_CODE_MESSAGES = {
    ErrorCode.INVALID_INPUT: "The request could not be processed",
    ErrorCode.DENIED: "Not permitted",
    ErrorCode.UNAVAILABLE: "A required service is unavailable",
    ErrorCode.UNSUPPORTED_CAPABILITY: "The operation is not supported",
    ErrorCode.NOT_FOUND: "Not found",
    ErrorCode.CONFLICT: "The request conflicts with the current state",
    ErrorCode.STALE_APPROVAL: "The approval no longer matches the current plan",
    ErrorCode.BUDGET_EXHAUSTED: "A configured limit was reached",
    ErrorCode.CANCELLED: "The request was cancelled",
    ErrorCode.INTERNAL: "Internal error",
}
_STATUS_CODES = {400: ErrorCode.INVALID_INPUT, 401: ErrorCode.DENIED, 403: ErrorCode.DENIED,
                 404: ErrorCode.NOT_FOUND, 409: ErrorCode.CONFLICT, 422: ErrorCode.INVALID_INPUT,
                 429: ErrorCode.BUDGET_EXHAUSTED, 502: ErrorCode.UNAVAILABLE, 503: ErrorCode.UNAVAILABLE}

# Paths that would serve source bodies, crops or private stores; no user route may match (N2).
SOURCE_SERVING_PATH_RE = re.compile(
    r"(?i)^/(?:fair2wise/)?(?:api/)?(?:papers?|pdfs?|static|assets|crops?|figures?|artifacts?|page_images|"
    r"source_index|blobs?|files?|fulltext|sections?|chunks?)(?:/|$)"
)


def source_route_denied(path: str) -> bool:
    """Return whether ``path`` addresses source-serving content and must be refused."""
    return bool(SOURCE_SERVING_PATH_RE.match(str(path or "")))


def citation_tokens(text: str) -> list[str]:
    """Return the visible citation tokens in ``text`` in order."""
    return CITATION_RE.findall(str(text or ""))


def _digest(value: object) -> dict[str, Any] | None:
    """SHA-256 and length of ``value`` (joined if a list of messages); ``None`` stays ``None``."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        text = "\n".join(str((m or {}).get("content") if isinstance(m, Mapping) else m) for m in value)
    else:
        text = value if isinstance(value, str) else repr(value)
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "chars": len(text)}


def _basename(path: object) -> str:
    """Last component of a path-like value (no directories reach the user)."""
    return re.split(r"[/\\]", str(path))[-1]


def _clip(text: str, limit: int | None) -> str:
    """Cut ``text`` to ``limit`` chars at a word boundary and mark the omission (``None``: no cut)."""
    if limit is None or len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip() + " " + OMISSION


# --------------------------------------------------------------------------- quote matching


def normalize_for_match(text: str) -> str:
    """Apply :data:`NORMALIZATION_PROFILE`: join line-break hyphenation, collapse whitespace."""
    joined = re.sub(r"(\w)-[ \t]*\r?\n[ \t]*(\w)", r"\1\2", str(text or ""))
    return re.sub(r"\s+", " ", joined).strip()


_OMISSION_SPLIT_RE = re.compile(r"\s*(?:\[…\]|\[\.\.\.\])\s*")


@dataclass(frozen=True)
class QuoteVerification:
    """Verdict of :class:`QuoteVerifier`; carries no source text.

    Attributes:
        verified: Whether the displayed text matches the retained source.
        match_type: ``EXACT``, ``NORMALIZED`` or ``PARAPHRASE`` (failed).
        normalization_profile: Profile used, for ``NORMALIZED`` matches.
        locator: The locator checked.
        region: Ledger region id (work/version/locator).
        spans: Matched ``(start, end)`` offsets in the normalized locator text.
    """

    verified: bool
    match_type: MatchType
    normalization_profile: str | None
    locator: SourceLocator
    region: str
    spans: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class SourceRef:
    """A retrieved source region used in a turn (no text).

    Attributes:
        work_id: Canonical Work identifier (or legacy ``source_paper``).
        source_version: Retained source version identifier.
        locator: Region inside that version.
        citation: Visible citation token for the region, e.g. ``[PDF: f.pdf p.3]``.
    """

    work_id: str
    source_version: str
    locator: SourceLocator
    citation: str | None = None


@dataclass(frozen=True)
class EchoMatch:
    """A verbatim run of retrieved source text found in displayed text."""

    start: int
    end: int
    source: SourceRef
    region: str
    spans: tuple[tuple[int, int], ...]


#: Hook that returns retained source text at ``(source_version, locator)``, or ``None``.
SourceTextReader = Callable[[str, SourceLocator], str | None]


def _region(work_id: str, source_version: str, locator: SourceLocator) -> str:
    return "|".join((str(work_id), str(source_version), str(locator.page or ""), str(locator.section or ""),
                     str(locator.figure or ""),
                     "" if locator.char_span is None else f"{locator.char_span[0]}-{locator.char_span[1]}"))


def _find_segments(segments: Sequence[str], haystack: str) -> list[tuple[int, int]] | None:
    """Find ``segments`` in order, non-overlapping; return their spans or ``None``."""
    spans: list[tuple[int, int]] = []
    pos = 0
    for segment in segments:
        index = haystack.find(segment, pos)
        if index < 0:
            return None
        spans.append((index, index + len(segment)))
        pos = index + len(segment)
    return spans


class QuoteVerifier:
    """Verify displayed quotes against retained source text inside ``source.read``.

    The reader runs only through ``scope.read(SourceKind.PARSED_TEXT, ...)``, so
    a verifier built without a valid internal grant cannot read anything. The
    projector receives :class:`QuoteVerification` verdicts and echo spans only.

    Args:
        reader: ``(source_version, locator) -> text`` hook for the retained text.
        scope: Trusted internal read scope.
    """

    def __init__(self, reader: SourceTextReader, scope: SourceAccessScope) -> None:
        if not isinstance(scope, SourceAccessScope):
            raise TypeError("QuoteVerifier requires a SourceAccessScope")
        self._reader = reader
        self._scope = scope
        self._region_lengths: dict[str, int] = {}

    def region_length(self, region: str) -> int | None:
        """Length of a region's retained text, if it has been read in this verifier."""
        return self._region_lengths.get(region)

    def _text(self, work_id: str, source_version: str, locator: SourceLocator) -> str | None:
        text = self._scope.read(SourceKind.PARSED_TEXT, lambda: self._reader(source_version, locator),
                                work_id=work_id)
        if not (isinstance(text, str) and text):
            return None
        self._region_lengths[_region(work_id, source_version, locator)] = len(text)
        return text

    def verify(self, quote: str, *, work_id: str, source_version: str,
               locator: SourceLocator) -> QuoteVerification:
        """Check ``quote`` (may contain ``[…]`` omissions) at ``locator`` of ``source_version``."""
        region = _region(work_id, source_version, locator)
        failed = QuoteVerification(False, MatchType.PARAPHRASE, None, locator, region)
        # Omissions at either end are allowed; every remaining segment must be substantive.
        segments = [s for s in _OMISSION_SPLIT_RE.split(str(quote or "").strip()) if s.strip()]
        if not segments or any(len(s.strip()) < 3 for s in segments):
            return failed
        source = self._text(work_id, source_version, locator)
        if source is None:
            return failed
        normalized_source = normalize_for_match(source)
        normalized_segments = [normalize_for_match(s) for s in segments]
        spans = _find_segments(normalized_segments, normalized_source)
        if spans is None:
            return failed
        if _find_segments(segments, source) is not None:
            return QuoteVerification(True, MatchType.EXACT, None, locator, region, tuple(spans))
        return QuoteVerification(True, MatchType.NORMALIZED, NORMALIZATION_PROFILE, locator, region, tuple(spans))

    @staticmethod
    def _source_spans(indices: Sequence[int],
                      src_words: Sequence[tuple[int, int, str]]) -> tuple[tuple[int, int], ...]:
        """Merge consecutive source word indices into char intervals (spaces between them included)."""
        spans: list[tuple[int, int]] = []
        prev = None
        for idx in indices:
            start, end = src_words[idx][0], src_words[idx][1]
            if prev is not None and idx == prev + 1:
                spans[-1] = (spans[-1][0], end)
            else:
                spans.append((start, end))
            prev = idx
        return tuple(spans)

    def find_echoes(self, text: str, sources: Sequence[SourceRef], *, min_chars: int, shingle_words: int,
                    exclude: Sequence[tuple[int, int]] = ()) -> list[EchoMatch]:
        """Find verbatim runs of any ``sources`` text inside ``text``.

        Args:
            text: Displayed text.
            sources: Retrieved regions to compare against.
            min_chars: Shortest run reported.
            shingle_words: Word n-gram size.
            exclude: Spans of ``text`` already handled (verified quotes).
        """
        words = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\S+", text)]
        if len(words) < shingle_words:
            return []
        excluded = [False] * len(words)
        for i, (start, end, _w) in enumerate(words):
            excluded[i] = any(start < e and end > s for s, e in exclude)
        out: list[EchoMatch] = []
        for ref in sources:
            source = self._text(ref.work_id, ref.source_version, ref.locator)
            if source is None:
                continue
            norm = normalize_for_match(source)
            src_words = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\S+", norm)]
            index: dict[tuple[str, ...], list[int]] = {}
            for j in range(len(src_words) - shingle_words + 1):
                key = tuple(w for _s, _e, w in src_words[j:j + shingle_words])
                index.setdefault(key, []).append(j)
            covered: dict[int, int] = {}
            for i in range(len(words) - shingle_words + 1):
                if any(excluded[i:i + shingle_words]):
                    continue
                key = tuple(w for _s, _e, w in words[i:i + shingle_words])
                hits = index.get(key)
                if hits:
                    for k in range(shingle_words):
                        covered.setdefault(i + k, hits[0] + k)
            region = _region(ref.work_id, ref.source_version, ref.locator)
            run: list[int] = []
            for i in cast(list[Any], sorted(covered) + [None]):  # None is a flush sentinel; `i` is int | None
                if run and (i is None or i != run[-1] + 1):
                    start, end = words[run[0]][0], words[run[-1]][1]
                    if end - start >= min_chars:
                        out.append(EchoMatch(start, end, ref, region,
                                             self._source_spans(sorted({covered[r] for r in run}), src_words)))
                    run = []
                if i is not None:
                    run.append(i)
        out.sort(key=lambda m: m.start)
        merged: list[EchoMatch] = []
        for match in out:
            if merged and match.start < merged[-1].end:
                continue
            merged.append(match)
        return merged


# --------------------------------------------------------------------------- viewer and ledger


@dataclass(frozen=True)
class Viewer:
    """Who receives a projection; built only from a server-built context.

    Attributes:
        actor: Stable actor key (issuer + subject).
        session: Server-bound chat session, if any.
        correlation_id: Request correlation id shown in typed errors.
    """

    actor: str
    session: str | None
    correlation_id: str

    @classmethod
    def from_context(cls, context: RequestContext) -> Viewer:
        """Build a viewer from ``context``; capabilities are deliberately ignored."""
        if not isinstance(context, RequestContext):
            raise TypeError("Viewer requires a server-built RequestContext")
        principal = context.principal
        return cls(actor=f"{principal.issuer or 'local'}:{principal.subject}", session=context.session_id,
                   correlation_id=context.request_id)

    def ledger_keys(self) -> list[tuple[str, str]]:
        """Return ``(scope, key)`` pairs the ledger enforces for this viewer."""
        keys = [("actor", self.actor)]
        if self.session:
            keys.append(("session", f"{self.actor}/{self.session}"))
        return keys


def _union_length(intervals: Iterable[tuple[int, int]]) -> int:
    total, cur_s, cur_e = 0, None, None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cast(int, cur_s)  # cur_s is set whenever cur_e is
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cast(int, cur_s)  # cur_s is set whenever cur_e is
    return total


class DisclosureLedger:
    """Cumulative distinct quoted chars per (actor/session, work) within a window (Q5, Q6).

    Intervals are recorded in normalized locator-text coordinates per region,
    so re-quoting the same span costs nothing and adjacent spans add up.

    Args:
        budget: Limits to enforce.
        clock: Monotonic seconds; injectable for tests.
    """

    def __init__(self, budget: DisclosureBudget = DEFAULT_BUDGET, *, clock: Callable[[], float] = time.monotonic):
        self._budget = budget
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str, str], list[tuple[float, str, int, int]]] = {}

    def _limit(self, scope: str) -> int | None:
        return (self._budget.max_session_quoted_chars_per_work if scope == "session"
                else self._budget.max_actor_quoted_chars_per_work)

    def _live(self, key: tuple[str, str, str], now: float) -> list[tuple[float, str, int, int]]:
        cutoff = now - self._budget.window_seconds
        live = [e for e in self._entries.get(key, []) if e[0] >= cutoff]
        self._entries[key] = live
        return live

    @staticmethod
    def _distinct(entries: Iterable[tuple[float, str, int, int]]) -> int:
        by_region: dict[str, list[tuple[int, int]]] = {}
        for _t, region, s, e in entries:
            by_region.setdefault(region, []).append((s, e))
        return sum(_union_length(v) for v in by_region.values())

    def used(self, viewer: Viewer, work_id: str) -> dict[str, int]:
        """Return distinct chars used per scope for ``work_id``."""
        now = self._clock()
        with self._lock:
            return {scope: self._distinct(self._live((scope, key, work_id), now))
                    for scope, key in viewer.ledger_keys()}

    def try_consume(self, viewer: Viewer, work_id: str, region: str, spans: Sequence[tuple[int, int]]) -> bool:
        """Record ``spans`` if every scope stays within its limit; else record nothing."""
        now = self._clock()
        new = [(now, region, int(s), int(e)) for s, e in spans if e > s]
        with self._lock:
            for scope, key in viewer.ledger_keys():
                limit = self._limit(scope)
                if limit is None:
                    continue
                live = self._live((scope, key, work_id), now)
                if self._distinct(live + new) > limit:
                    return False
            for scope, key in viewer.ledger_keys():
                self._entries.setdefault((scope, key, work_id), []).extend(new)
        return True


# --------------------------------------------------------------------------- paths


@dataclass(frozen=True)
class PathRoots:
    """Known absolute roots mapped to opaque labels (longest prefix wins).

    Example: ``PathRoots.of(workdir="/srv/f2w/run", repo="/srv/f2w")`` turns
    ``/srv/f2w/run/uploads/g.json`` into ``f2w://workdir/uploads/g.json``.
    """

    roots: tuple[tuple[str, str], ...] = ()

    @classmethod
    def of(cls, **labels: str) -> PathRoots:
        """Build roots from ``label=absolute_path`` pairs."""
        pairs = [(str(path).rstrip("/\\"), label) for label, path in labels.items() if path]
        pairs.sort(key=lambda item: len(item[0]), reverse=True)
        return cls(tuple(pairs))

    def redact(self, path: str) -> str:
        """Return the opaque ``f2w://`` id for one absolute path."""
        raw = path[len("file://"):] if path.startswith("file://") else path
        for prefix, label in self.roots:
            if raw == prefix or raw.startswith(prefix + "/") or raw.startswith(prefix + "\\"):
                tail = raw[len(prefix):].lstrip("/\\").replace("\\", "/")
                return f"f2w://{label}/{tail}" if tail else f"f2w://{label}"
        base = re.split(r"[/\\]", raw.rstrip("/\\"))[-1] if raw.strip("/\\~") else ""
        return f"f2w://path/{base}" if base and not base.startswith("<") else "f2w://path"


# --------------------------------------------------------------------------- projector


@dataclass
class _ResponseState:
    quotes: int = 0
    per_work: dict[str, int] = field(default_factory=dict)
    per_region: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _QuoteCandidate:
    start: int
    end: int
    inner_start: int
    inner_end: int
    kind: str  # "quote" or "block"
    citation: str | None


class DisclosureProjector:
    """Apply ``result.disclose`` to every outgoing user payload.

    Args:
        budget: Disclosure bounds (defaults pending O-D2).
        verifier: Quote verifier; without one no quote can be shown verbatim.
        ledger: Cumulative per-session/actor ledger; created if omitted.
        roots: Absolute roots for opaque path ids.
        citation_resolver: Optional hook mapping a citation token to a
            :class:`SourceRef` when the turn supplied no explicit evidence.
    """

    def __init__(
        self,
        *,
        budget: DisclosureBudget = DEFAULT_BUDGET,
        verifier: QuoteVerifier | None = None,
        ledger: DisclosureLedger | None = None,
        roots: PathRoots = PathRoots(),
        citation_resolver: Callable[[str], SourceRef | None] | None = None,
    ) -> None:
        self.budget = budget
        self.verifier = verifier
        self.ledger = ledger or DisclosureLedger(budget)
        self.roots = roots
        self.citation_resolver = citation_resolver

    # ------------------------------------------------------------------ strings and errors

    def redact_paths(self, text: str) -> str:
        """Replace absolute/local paths in ``text`` with opaque ``f2w://`` ids."""
        if not isinstance(text, str) or not text:
            return text
        return _ABS_PATH_RE.sub(lambda m: self.roots.redact(m.group(0)), text)

    def _safe_str(self, value: object, limit: int | None = None) -> JsonValue:
        if not isinstance(value, str):
            return value
        text = _SECRET_RE.sub("[redacted]", self.redact_paths(value))
        return _clip(text, limit or self.budget.max_progress_text_chars)

    def _typed(self, code: ErrorCode, viewer: Viewer) -> str:
        return f"{_CODE_MESSAGES[code]} (error: {code.value}; ref: {viewer.correlation_id})"

    def project_error_message(self, message: object, viewer: Viewer, *, code: ErrorCode = ErrorCode.INTERNAL) -> str:
        """Return a user-safe error message.

        Messages that look like raw exception text, carry secrets or exceed
        ``max_error_chars`` become a typed message with the correlation id;
        others keep their wording with local paths made opaque.
        """
        text = "" if message is None else str(message)
        if not text.strip() or _RAW_EXC_RE.search(text) or _SECRET_RE.search(text) \
                or len(text) > self.budget.max_error_chars:
            return self._typed(code, viewer)
        return self.redact_paths(text)

    def project_exception(self, exc: BaseException, viewer: Viewer) -> tuple[ErrorCode, str]:
        """Map an exception to ``(code, user message)``; raw ``str(exc)`` never leaks.

        ``ContractError`` messages are safe by contract; ``ValueError`` messages
        are the legacy user-facing validation texts and pass the message check;
        everything else becomes a typed message.
        """
        if isinstance(exc, ContractError):
            return exc.code, self.project_error_message(str(exc), viewer, code=exc.code)
        if isinstance(exc, FileNotFoundError):
            return ErrorCode.NOT_FOUND, self._typed(ErrorCode.NOT_FOUND, viewer)
        if isinstance(exc, ValueError):
            return ErrorCode.INVALID_INPUT, self.project_error_message(str(exc), viewer,
                                                                       code=ErrorCode.INVALID_INPUT)
        return ErrorCode.INTERNAL, self._typed(ErrorCode.INTERNAL, viewer)

    def project_http_error(self, status_code: int, body: object, viewer: Viewer) -> JsonValue:
        """Project an error body (``{"detail": ...}`` or plain text), keeping its shape."""
        code = _STATUS_CODES.get(int(status_code), ErrorCode.INTERNAL)
        if isinstance(body, Mapping) and "detail" in body:
            detail = body["detail"]
            out = dict(body)
            if isinstance(detail, str):
                out["detail"] = self.project_error_message(detail, viewer, code=code)
            else:
                out["detail"] = self._scrub(detail, depth=0)
            return out
        if isinstance(body, str):
            return self.project_error_message(body, viewer, code=code)
        return self._scrub(body, depth=0)

    # ------------------------------------------------------------------ generic scrub

    def _artifact_value(self, value: object) -> bool:
        return isinstance(value, str) and bool(_ARTIFACT_VALUE_RE.search(value.strip()))

    def _scrub(self, value: object, *, depth: int, limit: int | None = None) -> JsonValue:
        """Drop private keys and artifact values, redact paths, clip strings (unknown data)."""
        if depth > 8:
            return None
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            for key, item in value.items():
                if _PRIVATE_KEY_RE.match(str(key)) or self._artifact_value(item):
                    continue
                out[key] = self._scrub(item, depth=depth + 1, limit=limit)
            return out
        if isinstance(value, (list, tuple)):
            return [self._scrub(v, depth=depth + 1, limit=limit) for v in value
                    if not self._artifact_value(v)][:200]
        if isinstance(value, str):
            return self._safe_str(value, limit)
        return value

    # ------------------------------------------------------------------ quotes

    def _consume(self, viewer: Viewer, state: _ResponseState, work_id: str, region: str,
                 spans: Sequence[tuple[int, int]], chars: int, *, counts_as_quote: bool) -> bool:
        max_quotes = self.budget.max_quotes_per_response
        if counts_as_quote and max_quotes is not None and state.quotes >= max_quotes:
            return False
        max_work = self.budget.max_quoted_chars_per_work_per_response
        if max_work is not None and state.per_work.get(work_id, 0) + chars > max_work:
            return False
        fraction = self.budget.max_region_fraction
        region_spans = [(int(a), int(b)) for a, b in spans if b > a]
        if fraction is not None:
            length = self.verifier.region_length(region) if self.verifier is not None else None
            if not length:
                return False  # unknown region size: cannot show the share is bounded
            covered = _union_length(state.per_region.get(region, []) + region_spans)
            if covered > fraction * length:
                return False
        if not self.ledger.try_consume(viewer, work_id, region, spans):
            return False
        state.per_region.setdefault(region, []).extend(region_spans)
        state.per_work[work_id] = state.per_work.get(work_id, 0) + chars
        if counts_as_quote:
            state.quotes += 1
        return True

    def _decide_quote(self, text: str, target: SourceRef | None, claimed: MatchType, viewer: Viewer,
                      state: _ResponseState, *, clip_unverified: bool = True) -> dict[str, Any]:
        """Core Q1-Q5 decision for one quote. Returns a public decision record.

        A verified quote is shown verbatim (clipped to ``max_quote_chars`` with
        ``[…]``) while the per-response and ledger budgets allow, otherwise it is
        replaced by its citation. Anything unverified is a statement, never
        verbatim; inside running text it keeps its wording (Q4: facts stay) and
        the echo guard bounds any verbatim source it still contains.
        """
        clean = str(text or "").strip()
        clipped = _clip(clean, self.budget.max_quote_chars)
        decision: dict[str, Any] = {
            "text": clipped, "verbatim": False, "match_type": MatchType.PARAPHRASE.value,
            "normalization_profile": None, "citation": target.citation if target else None,
            "locator": self._public_locator(target), "omitted": False, "omission_reason": None,
        }

        def unverified(match_type: MatchType = MatchType.PARAPHRASE) -> dict[str, Any]:
            shown = clipped if clip_unverified else clean
            decision.update(text=shown, match_type=match_type.value, omitted=shown != clean,
                            omission_reason="too_long" if shown != clean else None)
            if match_type is MatchType.PARAPHRASE and claimed in (MatchType.EXACT, MatchType.NORMALIZED):
                decision["omission_reason"] = "unverified"
            return decision

        if claimed in (MatchType.PARAPHRASE, MatchType.NONE):
            return unverified(claimed)
        if target is None or self.verifier is None:
            return unverified()
        verdict = self.verifier.verify(clipped, work_id=target.work_id, source_version=target.source_version,
                                       locator=target.locator)
        if not verdict.verified:
            return unverified()
        decision.update(omitted=clipped != clean, omission_reason="too_long" if clipped != clean else None)
        if not self._consume(viewer, state, target.work_id, verdict.region, verdict.spans,
                             sum(e - s for s, e in verdict.spans), counts_as_quote=True):
            decision.update(text=None, omitted=True, omission_reason="budget",
                            match_type=verdict.match_type.value)
            return decision
        decision.update(verbatim=True, match_type=verdict.match_type.value,
                        normalization_profile=verdict.normalization_profile)
        return decision

    @staticmethod
    def _public_locator(target: SourceRef | None) -> dict[str, Any] | None:
        """Q2 locator: work, version, page/section/figure/offsets; no paths or access hashes."""
        if target is None:
            return None
        loc = target.locator
        out: dict[str, Any] = {"work_id": target.work_id, "source_version": target.source_version}
        for name in ("page", "section", "figure", "panel"):
            value = getattr(loc, name)
            if value is not None:
                out[name] = value
        if loc.char_span is not None:
            out["char_span"] = list(loc.char_span)
        return out

    def project_quotes(self, evidence: Sequence[tuple[EvidenceRef, str | None]], viewer: Viewer,
                       *, state: _ResponseState | None = None) -> list[dict[str, Any]]:
        """Project structured evidence quotes ``[(EvidenceRef, citation_token)]``."""
        state = state or _ResponseState()
        out = []
        for ref, citation in evidence:
            target = SourceRef(ref.work_id, ref.source_version, ref.locator, citation)
            decision = self._decide_quote(ref.quote or "", target, ref.match_type, viewer, state)
            decision["evidence_id"] = ref.evidence_id
            out.append(decision)
        state.decisions.extend(out)
        return out

    def _quote_candidates(self, text: str) -> list[_QuoteCandidate]:
        found: list[_QuoteCandidate] = []
        for pattern in (re.compile(r'"([^"\n]+)"'), re.compile(r"“([^”]+)”")):
            for m in pattern.finditer(text):
                found.append(_QuoteCandidate(m.start(), m.end(), m.start(1), m.end(1), "quote", None))
        for m in re.finditer(r"(?m)(?:^>[ \t]?[^\n]*(?:\n|$))+", text):
            found.append(_QuoteCandidate(m.start(), m.end(), m.start(), m.end(), "block", None))
        found.sort(key=lambda c: c.start)
        out: list[_QuoteCandidate] = []
        for cand in found:
            if out and cand.start < out[-1].end:
                continue
            inner = self._block_inner(text[cand.inner_start:cand.inner_end]) if cand.kind == "block" \
                else text[cand.inner_start:cand.inner_end]
            inner_wo_cites = CITATION_RE.sub("", inner)
            if len(re.findall(r"\w+", inner_wo_cites)) < self.budget.quote_min_words:
                continue
            tail = re.match(r"[ \t]*[,.;:]?[ \t]*(\[(?:KG|PDF|OPS|MP|TILED)\s*:[^\[\]\n]+\])", text[cand.end:])
            cites = citation_tokens(inner) if cand.kind == "block" else []
            citation = tail.group(1) if tail else (cites[-1] if cites else None)
            out.append(_QuoteCandidate(cand.start, cand.end, cand.inner_start, cand.inner_end, cand.kind,
                                       citation))
        return out

    @staticmethod
    def _block_inner(block: str) -> str:
        return "\n".join(re.sub(r"^>[ \t]?", "", line) for line in block.rstrip("\n").split("\n"))

    def _target_for(self, inner: str, citation: str | None,
                    evidence: Sequence[tuple[EvidenceRef, str | None]],
                    sources: Sequence[SourceRef]) -> tuple[SourceRef | None, MatchType]:
        norm = normalize_for_match(CITATION_RE.sub("", inner))
        for ref, token in evidence:
            if ref.quote and normalize_for_match(ref.quote) == norm:
                return SourceRef(ref.work_id, ref.source_version, ref.locator, token or citation), ref.match_type
        if citation:
            for src in sources:
                if src.citation and src.citation.replace(" ", "") == citation.replace(" ", ""):
                    return src, MatchType.EXACT
            if self.citation_resolver is not None:
                resolved = self.citation_resolver(citation)
                if isinstance(resolved, SourceRef):
                    return resolved, MatchType.EXACT
        return None, MatchType.EXACT

    def project_text(
        self,
        text: object,
        viewer: Viewer,
        *,
        evidence: Sequence[tuple[EvidenceRef, str | None]] = (),
        sources: Sequence[SourceRef] = (),
        state: _ResponseState | None = None,
    ) -> JsonValue:
        """Project answer-like text: verify quotes, bound them, clip source echo.

        Citation tokens are never removed or rewritten.

        Args:
            text: Displayed text (answer, description, model-proxy content).
            viewer: Recipient.
            evidence: Explicit evidence for quotes, with citation tokens.
            sources: Source regions retrieved for this turn (for echo detection
                and citation-based verification).
            state: Shared per-response budget state.
        """
        if not isinstance(text, str) or not text:
            return text
        state = state if state is not None else _ResponseState()
        pieces: list[str] = []
        kept: list[tuple[int, int]] = []
        pos = 0
        length = 0
        for cand in self._quote_candidates(text):
            pieces.append(text[pos:cand.start])
            length += len(pieces[-1])
            raw_inner = text[cand.inner_start:cand.inner_end]
            inner = self._block_inner(raw_inner) if cand.kind == "block" else raw_inner
            target, claimed = self._target_for(inner, cand.citation, evidence, sources)
            quote_body = CITATION_RE.sub("", inner).strip() if cand.kind == "block" else inner
            decision = self._decide_quote(quote_body, target, claimed, viewer, state, clip_unverified=False)
            decision["citation"] = decision["citation"] or cand.citation
            state.decisions.append(decision)
            block_cites = " ".join(citation_tokens(inner)) if cand.kind == "block" else ""
            if decision["verbatim"]:
                shown = decision["text"]
                if cand.kind == "block":
                    body = "\n".join("> " + line for line in shown.split("\n"))
                    replacement = body + ((" " + block_cites) if block_cites else "") + "\n"
                else:
                    replacement = text[cand.start] + shown + text[cand.end - 1]
                kept.append((length, length + len(replacement)))
            elif decision["omission_reason"] == "budget":
                replacement = QUOTE_OMITTED + ((" " + block_cites) if block_cites else "")
                if cand.kind == "block":
                    replacement += "\n"
            else:
                # Q4: unverifiable or paraphrase -> unquoted cited statement.
                replacement = decision["text"] + ((" " + block_cites) if block_cites else "")
                if cand.kind == "block":
                    replacement += "\n"
            pieces.append(replacement)
            length += len(replacement)
            pos = cand.end
        pieces.append(text[pos:])
        projected = "".join(pieces)
        return self._clip_echoes(projected, viewer, sources, kept, state)

    def _clip_echoes(self, text: str, viewer: Viewer, sources: Sequence[SourceRef],
                     kept: Sequence[tuple[int, int]], state: _ResponseState) -> str:
        if self.verifier is None or not sources:
            return text
        echoes = self.verifier.find_echoes(text, sources, min_chars=self.budget.echo_min_chars,
                                           shingle_words=self.budget.echo_shingle_words, exclude=kept)
        if not echoes:
            return text
        out: list[str] = []
        pos = 0
        for echo in echoes:
            out.append(text[pos:echo.start])
            run = text[echo.start:echo.end]
            chars = sum(e - s for s, e in echo.spans)
            max_quote = self.budget.max_quote_chars
            if (max_quote is None or len(run) <= max_quote) and self._consume(
                    viewer, state, echo.source.work_id, echo.region, echo.spans, chars, counts_as_quote=False):
                out.append(run)
            else:
                # Citation tokens are never added or removed; the model's own tokens stay in place.
                out.append(SOURCE_TEXT_OMITTED)
            state.decisions.append({"echo": True, "kept": out[-1] == run, "citation": echo.source.citation,
                                    "chars": len(run)})
            pos = echo.end
        out.append(text[pos:])
        return "".join(out)

    # ------------------------------------------------------------------ graph

    def project_publication(self, pub: object) -> JsonValue:
        """Bibliographic record: drop private keys, cut ``abstract_text``, redact paths."""
        if not isinstance(pub, Mapping):
            return pub
        out: dict[str, Any] = {}
        for key, value in pub.items():
            if _PRIVATE_KEY_RE.match(str(key)) or self._artifact_value(value):
                continue
            if key in ("abstract_text", "abstract") and isinstance(value, str):
                out[key] = _clip(self.redact_paths(value), self.budget.max_abstract_chars)
            elif key == "supporting_nodes":
                out[key] = self._scrub(value, depth=1)
            else:
                out[key] = self._scrub(value, depth=1, limit=self.budget.max_property_chars)
        return out

    def _project_properties(self, props: object) -> JsonValue:
        limit = self.budget.max_property_chars
        if isinstance(props, list):
            out = []
            for item in props:
                if isinstance(item, Mapping):
                    name = str(item.get("property") or item.get("name") or item.get("key") or "")
                    if _PRIVATE_KEY_RE.match(name) or self._artifact_value(item.get("value")):
                        continue
                    out.append(self._scrub(item, depth=1, limit=limit))
                elif not self._artifact_value(item):
                    out.append(self._scrub(item, depth=1, limit=limit))
            return out
        if isinstance(props, Mapping):
            return self._scrub(props, depth=0, limit=limit)
        return props

    def _project_code(self, value: object) -> JsonValue:
        if not isinstance(value, str):
            return value
        return _clip(value, self.budget.max_code_snippet_chars)

    def _project_url(self, value: object) -> JsonValue:
        if not isinstance(value, str) or not value:
            return value
        if self._artifact_value(value) or not re.match(r"(?i)^https?://", value):
            return None
        return value

    def _project_repo_path(self, value: object) -> JsonValue:
        if isinstance(value, str) and _ABS_PATH_RE.fullmatch(value):
            return self.roots.redact(value)
        return value

    def project_linked_snippet(self, snippet: object) -> JsonValue:
        """Project a ``LinkedCodeSnippet`` dict."""
        if not isinstance(snippet, Mapping):
            return snippet
        out: dict[str, Any] = {}
        for key, value in snippet.items():
            if key == "code_snippet":
                out[key] = self._project_code(value)
            elif key == "publications":
                out[key] = [self.project_publication(p) for p in value] if isinstance(value, list) else value
            elif key in ("repo_url", "source_file_url"):
                out[key] = self._project_url(value)
            elif key == "source_file_path":
                out[key] = self._project_repo_path(value)
            else:
                out[key] = self._scrub(value, depth=1)
        return out

    def project_graph_node(self, node: object, viewer: Viewer, *,
                           evidence: Sequence[tuple[EvidenceRef, str | None]] = (),
                           sources: Sequence[SourceRef] = (), state: _ResponseState | None = None) -> JsonValue:
        """Project a ``GraphNode`` dict, keeping its key order and wire shape."""
        if not isinstance(node, Mapping):
            return node
        state = state if state is not None else _ResponseState()
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                text = self.project_text(value, viewer, evidence=evidence, sources=sources, state=state)
                out[key] = _clip(self.redact_paths(text), self.budget.max_description_chars)
            elif key == "publications":
                out[key] = [self.project_publication(p) for p in value] if isinstance(value, list) else value
            elif key == "code_snippet":
                out[key] = self._project_code(value)
            elif key == "linked_code_snippets":
                out[key] = [self.project_linked_snippet(s) for s in value] if isinstance(value, list) else value
            elif key in ("properties", "extra_fields"):
                out[key] = self._project_properties(value)
            elif key in ("repo_url", "source_file_url"):
                out[key] = self._project_url(value)
            elif key == "source_file_path":
                out[key] = self._project_repo_path(value)
            elif _PRIVATE_KEY_RE.match(str(key)):
                continue
            else:
                out[key] = self._scrub(value, depth=1)
        return out

    def project_graph_payload(self, payload: object, viewer: Viewer, *, state: _ResponseState | None = None,
                              sources: Sequence[SourceRef] = ()) -> JsonValue:
        """Project ``GraphPayload`` (or the SSE ``{nodes, edges}`` graph)."""
        if not isinstance(payload, Mapping):
            return payload
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if key == "nodes" and isinstance(value, list):
                out[key] = [self.project_graph_node(n, viewer, state=state, sources=sources) for n in value]
            elif key == "edges" and isinstance(value, list):
                out[key] = [{k: e[k] for k in ("source", "target", "predicate") if k in e}
                            if isinstance(e, Mapping) else e for e in value]
            elif key == "source_path":
                out[key] = self.redact_paths(value) if isinstance(value, str) else value
        return out

    # ------------------------------------------------------------------ chat

    def _project_candidate(self, cand: object) -> JsonValue:
        if not isinstance(cand, Mapping):
            return None
        out = {k: cand.get(k) for k in ("id", "doi", "title", "publication_year", "score", "repository")
               if k in cand}
        if "abstract" in cand:
            out["abstract"] = _clip(str(cand.get("abstract") or ""), self.budget.max_pending_abstract_chars)
        return self._scrub(out, depth=1)

    def project_pending(self, pending: object) -> JsonValue:
        """Project the pending download/extraction card (``ChatResponse.pending``)."""
        if not isinstance(pending, Mapping):
            return pending
        out: dict[str, Any] = {}
        for key, value in pending.items():
            if key == "papers" and isinstance(value, list):
                out[key] = [self._pending_paper(p) for p in value]
            elif key == "candidate":
                out[key] = self._pending_paper(value)
            elif _PRIVATE_KEY_RE.match(str(key)):
                continue
            else:
                out[key] = self._scrub(value, depth=1)
        return out

    def _pending_paper(self, paper: object) -> JsonValue:
        if not isinstance(paper, Mapping):
            return paper
        out: dict[str, Any] = {}
        for key, value in paper.items():
            if key == "abstract" and isinstance(value, str):
                out[key] = value[:self.budget.max_pending_abstract_chars]
            elif key == "source_paper" and isinstance(value, str):
                out[key] = re.split(r"[/\\]", value)[-1]
            elif _PRIVATE_KEY_RE.match(str(key)) or self._artifact_value(value):
                continue
            else:
                out[key] = self._scrub(value, depth=1)
        return out

    _VERDICT_KEYS = ("status", "sufficient", "direct_evidence_count", "no_evidence", "graph_source_requested",
                     "graph_source_used", "leeway_used")

    def _project_verdict(self, verdict: object) -> JsonValue:
        if not isinstance(verdict, Mapping):
            return None
        out: dict[str, Any] = {k: verdict[k] for k in self._VERDICT_KEYS if k in verdict}
        if "selected" in verdict:
            out["selected"] = [str(s) for s in verdict.get("selected") or [] if isinstance(s, (str, int))]
        if "missing_topics" in verdict:
            out["missing_topics"] = [self._safe_str(str(t), 200) for t in verdict.get("missing_topics") or []]
        if "selected_hits" in verdict:
            out["selected_hits"] = [{k: h.get(k) for k in ("id", "label", "score", "graph_id") if k in h}
                                    for h in verdict.get("selected_hits") or [] if isinstance(h, Mapping)]
        for meta in ("hybrid_rag", "live_tiled", "materials_project"):
            if isinstance(verdict.get(meta), Mapping):
                out[meta] = {k: v for k, v in verdict[meta].items()
                             if isinstance(v, (bool, int, float)) or k in ("status", "skipped", "source")}
        for corpora in ("corpora", "corpora_skipped"):       # multi-corpus chat turn: corpus names and sizes only
            if isinstance(verdict.get(corpora), list):
                out[corpora] = [
                    {k: (self._safe_str(v, 200) if isinstance(v, str) else v) for k, v in c.items()
                     if k in ("corpus", "graph_id", "snapshot", "nodes", "edges", "reason")
                     and isinstance(v, (str, int))}
                    for c in verdict[corpora] if isinstance(c, Mapping)]
        if verdict.get("error"):
            out["error_code"] = ErrorCode.INTERNAL.value
        return out

    def _project_round(self, rnd: object) -> JsonValue:
        """``rounds[]`` summary (ADR 4.2): counts, ids, statuses, typed errors."""
        if not isinstance(rnd, Mapping):
            return None
        out: dict[str, Any] = {}
        for key, value in rnd.items():
            if key == "round":
                out[key] = value
            elif key in ("retrieval", "retrieval_after"):
                out[key] = self._project_verdict(value)
            elif key == "candidate_search" and isinstance(value, Mapping):
                out[key] = {k: value[k] for k in ("status", "count") if k in value}
                if "candidates" in value:
                    out[key]["candidates"] = [self._project_candidate(c) for c in value.get("candidates") or []]
                if value.get("error"):
                    out[key]["error_code"] = ErrorCode.INTERNAL.value
            elif key == "debate" and isinstance(value, Mapping):
                out[key] = {k: self._scrub(value[k], depth=1) for k in
                            ("selected_action", "reason", "hypothesis", "objections", "candidate_titles",
                             "candidate_indices") if k in value}
            elif key == "download" and isinstance(value, Mapping):
                out[key] = {k: value[k] for k in ("status", "count", "failed", "skipped", "semantic_rejected")
                            if k in value}
                if "downloaded" in value:
                    out[key]["downloaded"] = [_basename(p) for p in value.get("downloaded") or []]
                if value.get("error"):
                    out[key]["error_code"] = ErrorCode.INTERNAL.value
            elif key == "pending_pdfs" and isinstance(value, list):
                out[key] = [re.split(r"[/\\]", str(p))[-1] for p in value]
            elif key == "extraction" and isinstance(value, Mapping):
                out[key] = {k: value[k] for k in ("status", "extraction_mode", "unique_terms", "processed_files",
                                                  "processed_pages_total", "processed_pages_with_terms")
                            if k in value}
                if "pdf_results" in value:
                    out[key]["pdf_results"] = [
                        {k: (re.split(r"[/\\]", str(r[k]))[-1] if k == "filename" else r[k])
                         for k in ("filename", "extraction_state", "selected_pages", "processed_pages_total",
                                   "processed_pages_with_terms", "source_pages_total") if k in r}
                        for r in value.get("pdf_results") or [] if isinstance(r, Mapping)]
                if value.get("error") or value.get("status") == "error":
                    out[key]["error_code"] = ErrorCode.INTERNAL.value
            elif key in ("kg", "splash", "reload") and isinstance(value, Mapping):
                out[key] = {k: v for k, v in value.items()
                            if k in ("status", "nodes", "edges", "node_count", "graph_source_requested",
                                     "graph_source_used")}
                if value.get("error") or value.get("status") == "error":
                    out[key]["error_code"] = ErrorCode.INTERNAL.value
            elif key == "extraction_report" and isinstance(value, Mapping):
                out[key] = {k: value[k] for k in ("status", "sufficient", "used_pages") if k in value}
                if "missing_topics" in value:
                    topics = value["missing_topics"] or []
                    out[key]["missing_topics"] = [self._safe_str(str(t), 200) for t in topics]
            elif key == "error":
                out["error_code"] = ErrorCode.INTERNAL.value
            # Anything else is internal state and is dropped.
        return out

    _CHAT_KEYS = ("status", "answer", "sufficient", "node_ids", "publications", "confidence", "rounds", "graph",
                  "graph_source_requested", "graph_source_used", "workdir", "pending", "orchestration", "turn_id")

    def project_chat_response(
        self,
        payload: object,
        viewer: Viewer,
        *,
        evidence: Sequence[tuple[EvidenceRef, str | None]] = (),
        sources: Sequence[SourceRef] = (),
    ) -> JsonValue:
        """Project a ``ChatResponse`` dict (``/chat``, ``/chat/action``, SSE ``complete``/``error``)."""
        if not isinstance(payload, Mapping):
            return payload
        state = _ResponseState()
        status = str(payload.get("status") or "")
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if key not in self._CHAT_KEYS:
                continue
            if key == "answer":
                if status.endswith("_error") and isinstance(value, str):
                    value = self._project_error_answer(value, viewer)
                out[key] = self.project_text(value, viewer, evidence=evidence, sources=sources, state=state)
            elif key == "publications":
                out[key] = [self.project_publication(p) for p in value] if isinstance(value, list) else value
            elif key == "rounds":
                out[key] = [r for r in (self._project_round(x) for x in value or []) if r is not None] \
                    if isinstance(value, list) else []
            elif key == "graph":
                out[key] = self.project_graph_payload(value, viewer, state=state)
            elif key == "workdir":
                out[key] = self.redact_paths(value) if isinstance(value, str) else value
            elif key == "pending":
                out[key] = self.project_pending(value)
            elif key == "orchestration":
                out[key] = self._scrub(value, depth=1)
            else:
                out[key] = value
        return out

    def _project_error_answer(self, answer: str, viewer: Viewer) -> str:
        match = _FAILED_SAFELY_RE.match(answer)
        if match:
            return f"{match.group('head')} ({self._typed(ErrorCode.INTERNAL, viewer)})."
        return self.project_error_message(answer, viewer) if _RAW_EXC_RE.search(answer) else answer

    # ------------------------------------------------------------------ SSE

    def project_progress(self, data: object, viewer: Viewer) -> JsonValue:
        """Project SSE ``progress`` data (also used for trace ``progress[].data``)."""
        if not isinstance(data, Mapping):
            return data
        out: dict[str, Any] = {}
        for key, value in data.items():
            if key == "graph":
                out[key] = self.project_graph_payload(value, viewer)
            elif key == "titles" and isinstance(value, list):
                out[key] = [re.split(r"[/\\]", str(t))[-1] for t in value]
            elif key in ("phase", "turn_id"):
                out[key] = value
            elif _PRIVATE_KEY_RE.match(str(key)) or self._artifact_value(value):
                continue
            elif key == "error":
                out["error_code"] = ErrorCode.INTERNAL.value
            else:
                out[key] = self._scrub(value, depth=1)
        return out

    def project_sse_event(self, event: str, data: object, viewer: Viewer, *,
                          evidence: Sequence[tuple[EvidenceRef, str | None]] = (),
                          sources: Sequence[SourceRef] = ()) -> JsonValue:
        """Project one SSE event's data, keeping the event name and key order."""
        if event == "progress":
            return self.project_progress(data, viewer)
        if event == "error" and isinstance(data, Mapping) and data.get("status") == "stream_error":
            return {k: (self.project_error_message(v, viewer) if k == "message" else v)
                    for k, v in data.items() if k in ("status", "message", "turn_id")}
        return self.project_chat_response(data, viewer, evidence=evidence, sources=sources)

    # ------------------------------------------------------------------ traces

    def project_trace_for(self, record: object, context: RequestContext, *,
                          sources: Sequence[SourceRef] = ()) -> JsonValue:
        """Return the trace for ``GET /traces/{turn_id}`` as this caller may see it.

        Owner decision (2026-10-12): admins get the raw trace from the route. A
        server-built context holding ``trace.read_any`` (every ``:local`` user; on ALS
        only verified Tiled admins) receives the unprojected record; everyone else
        receives :meth:`project_trace`. Ownership of the turn is checked by the route
        (own-or-any), not here.
        """
        if not isinstance(context, RequestContext):
            raise TypeError("project_trace_for requires a server-built RequestContext")
        if context.has("trace.read_any"):
            return record
        return self.project_trace(record, Viewer.from_context(context), sources=sources)

    def project_trace(self, record: object, viewer: Viewer, *, sources: Sequence[SourceRef] = ()) -> JsonValue:
        """Project a trace detail record for ``GET /traces/{turn_id}`` (ADR 4.3).

        Prompts, merged context and raw model outputs become SHA-256 digests and
        lengths; ``env.settings`` and errors' raw messages are dropped.
        """
        if not isinstance(record, Mapping):
            return record
        out: dict[str, Any] = {}
        for key, value in record.items():
            if key in ("schema_version", "turn_id", "session_id", "parent_turn_id", "started_at", "finished_at"):
                out[key] = value
            elif key == "env" and isinstance(value, Mapping):
                env = {k: value.get(k) for k in ("git_sha", "backend", "models") if k in value}
                if isinstance(value.get("graphs"), list):
                    env["graphs"] = [{"graph": _basename(g.get("path") or ""), "sha256": g.get("sha256")}
                                     for g in value["graphs"] if isinstance(g, Mapping)]
                out[key] = env
            elif key == "request" and isinstance(value, Mapping):
                out[key] = self._project_trace_request(value)
            elif key == "orchestration" and isinstance(value, Mapping):
                out[key] = {k: (_digest(v) if k == "raw_classifier_output" else self._scrub(v, depth=1))
                            for k, v in value.items()}
            elif key == "progress" and isinstance(value, list):
                out[key] = [self._project_trace_progress(p, viewer) for p in value if isinstance(p, Mapping)]
            elif key == "retrieval" and isinstance(value, Mapping):
                out[key] = self._project_trace_retrieval(value)
            elif key == "llm_calls" and isinstance(value, list):
                out[key] = [self._project_llm_call(c) for c in value if isinstance(c, Mapping)]
            elif key == "response" and isinstance(value, Mapping):
                out[key] = self._project_trace_response(value, viewer, sources)
            elif key == "errors" and isinstance(value, list):
                out[key] = [{"code": ErrorCode.INTERNAL.value, "type": str(e.get("type") or ""),
                             "where": self._safe_str(str(e.get("where") or ""), 120)}
                            for e in value if isinstance(e, Mapping)]
        return out

    def _project_trace_request(self, req: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in req.items():
            if key == "history" and isinstance(value, list):
                out[key] = [{"role": m.get("role"), "content": self._safe_str(str(m.get("content") or ""), 4000)}
                            for m in value if isinstance(m, Mapping)]
            elif key in ("message", "effective_question"):
                out[key] = self._safe_str(value, 4000)
            elif key in ("graph_source", "json_graph_paths", "source_rag", "use_live_tiled", "ui_settings",
                         "batch_id"):
                out[key] = self._scrub(value, depth=1)
        return out

    def _project_trace_progress(self, item: Mapping[str, Any], viewer: Viewer) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in item.items():
            if key == "data":
                out[key] = self.project_progress(value, viewer)
            elif key in ("name", "offset_ms"):
                out[key] = value
            elif key == "message":
                out[key] = self._safe_str(value)
        return out

    def _project_trace_retrieval(self, ret: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in ret.items():
            if key in ("merged_context", "raw_judge_output", "leeway_system_prompt"):
                out[key] = _digest(value)
            elif key == "parsed_verdict":
                if isinstance(value, Mapping):
                    verdict: dict[str, Any] = {k: value.get(k) for k in ("sufficient",) if k in value}
                    if "missing_topics" in value:
                        verdict["missing_topics"] = [self._safe_str(str(t), 200)
                                                     for t in value.get("missing_topics") or []]
                    out[key] = verdict
                else:
                    out[key] = _digest(value)
            elif key == "selected_hits" and isinstance(value, list):
                out[key] = [{k: h.get(k) for k in ("id", "label", "score", "graph_id") if k in h}
                            for h in value if isinstance(h, Mapping)]
            elif key in ("hybrid_rag", "live_tiled") and isinstance(value, Mapping):
                out[key] = {k: v for k, v in value.items()
                            if isinstance(v, (bool, int, float)) or k in ("status", "skipped", "source")}
            elif key in ("sufficient", "no_evidence", "direct_evidence_count", "leeway_used",
                         "layout_ops_only_filter") and not isinstance(value, (str, Mapping, list)):
                out[key] = value
            elif key in ("missing_topics",) and isinstance(value, list):
                out[key] = [self._safe_str(str(t), 200) for t in value]
            elif key == "route_flags" and isinstance(value, Mapping):
                out[key] = {k: v for k, v in value.items() if v is None or isinstance(v, (bool, int, float))}
            elif value is None:
                out[key] = None
        return out

    def _project_llm_call(self, call: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in call.items():
            if key in ("label", "model", "latency_ms", "timeout", "system_prompt_sha256"):
                out[key] = value
            elif key == "messages":
                out["message_count"] = len(value) if isinstance(value, list) else 0
                out["prompt"] = _digest(value)
            elif key == "response":
                out[key] = _digest(value)
            elif key == "error":
                out[key] = None if not value else {"code": ErrorCode.INTERNAL.value,
                                                   "type": str((value or {}).get("type") or "")}
        return out

    def _project_trace_response(self, resp: Mapping[str, Any], viewer: Viewer,
                                sources: Sequence[SourceRef] = ()) -> dict[str, Any]:
        out: dict[str, Any] = {}
        status = str(resp.get("status") or "")
        for key, value in resp.items():
            if key == "answer":
                text = self._project_error_answer(value, viewer) if status.endswith("_error") and isinstance(
                    value, str) else value
                out[key] = self.project_text(text, viewer, sources=sources)
            elif key == "citations":
                out[key] = self._scrub(value, depth=1)
            elif key == "publications":
                out[key] = [self.project_publication(p) for p in value] if isinstance(value, list) else value
            elif key == "pending":
                out[key] = self.project_pending(value)
            elif key in ("status", "sufficient", "confidence", "elapsed_seconds"):
                out[key] = value
        return out

    # ------------------------------------------------------------------ other DTOs

    def _redact_value_paths(self, value: object) -> JsonValue:
        if isinstance(value, str):
            return self.redact_paths(value)
        if isinstance(value, list):
            return [self._redact_value_paths(v) for v in value]
        if isinstance(value, Mapping):
            return {k: self._redact_value_paths(v) for k, v in value.items()}
        return value

    def project_settings(self, payload: object, viewer: Viewer) -> JsonValue:
        """Project ``AgentSettingsResponse``: opaque paths, scrubbed Tiled error, no URI credentials."""
        if not isinstance(payload, Mapping):
            return payload
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if key == "tiled_error":
                out[key] = None if value is None else self.project_error_message(value, viewer,
                                                                                 code=ErrorCode.UNAVAILABLE)
            elif key == "tiled_uri" and isinstance(value, str):
                out[key] = re.sub(r"(?<=://)[^/@\s]+@", "", value).split("?", 1)[0]
            else:
                out[key] = self._redact_value_paths(value)
        return out

    def project_paths_only(self, payload: object) -> JsonValue:
        """Project DTOs whose only internal-only content is local paths (health, session reset)."""
        return self._redact_value_paths(payload)

    # ------------------------------------------------------------------ /v1 bodies

    def project_api_response(self, body: object) -> JsonValue:
        """Project a ``/v1`` response body or ``job_event`` SSE payload (AUD-06).

        ``/v1`` DTOs are typed, but several carry text derived from sources or from the
        local machine: the curation evidence excerpt, case payloads and model attempts,
        job pause reasons and artifact refs, operation-report previews and warnings,
        publication plans. Every body gets the D19 scrub the legacy routes apply to
        unknown payloads: private keys (snippets, prompts, source text, crops, local
        paths, secrets) are dropped, ``file:`` URIs and served-artifact paths are dropped,
        absolute paths become opaque ``f2w://`` ids, secrets are redacted and every string
        is clipped to ``max_progress_text_chars`` (600 characters, the B10 excerpt bound).

        Two rules differ from :meth:`_scrub`, for the API's own data: lists are not cut
        to 200 items (``/v1`` lists are bounded by their ``limit`` parameters and a
        polling client must see every event), and loopback ``http`` URLs are kept
        (the Tiled target on ``:local``). A ``sections`` key is kept when its value is a
        list of section names (the ``corpus-audit`` parameters echo), never when it holds
        text. Quote verification needs the turn's retrieved regions, which no ``/v1``
        route has, so no verbatim quote is ever confirmed here; the clip is the bound.
        """
        return self._scrub_api(body, depth=0)

    @staticmethod
    def _api_section_names(key: str, value: object) -> bool:
        return key.lower() == "sections" and isinstance(value, (list, tuple)) and all(
            isinstance(item, str) and _SECTION_NAME_RE.fullmatch(item) for item in value)

    @staticmethod
    def _api_artifact_value(value: object) -> bool:
        return isinstance(value, str) and bool(_API_ARTIFACT_VALUE_RE.search(value.strip()))

    def _scrub_api(self, value: object, *, depth: int) -> JsonValue:
        if depth > 16:
            return None
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            for key, item in value.items():
                name = str(key)
                if _PRIVATE_KEY_RE.match(name) and not self._api_section_names(name, item):
                    continue
                if self._api_artifact_value(item):
                    continue
                out[key] = self._scrub_api(item, depth=depth + 1)
            return out
        if isinstance(value, (list, tuple)):
            return [self._scrub_api(v, depth=depth + 1) for v in value if not self._api_artifact_value(v)]
        if isinstance(value, str):
            return self._safe_str(value)
        return value

    def project_model_chat(self, payload: object, viewer: Viewer, *, sources: Sequence[SourceRef] = ()) -> JsonValue:
        """Project model-compatible proxy responses (``/api/chat``, ``/v1/chat/completions``)."""
        if not isinstance(payload, Mapping):
            return payload
        out = dict(payload)
        state = _ResponseState()
        if isinstance(out.get("message"), Mapping):
            msg = dict(out["message"])
            msg["content"] = self.project_text(msg.get("content"), viewer, sources=sources, state=state)
            out["message"] = msg
        if isinstance(out.get("choices"), list):
            choices = []
            for choice in out["choices"]:
                if isinstance(choice, Mapping) and isinstance(choice.get("message"), Mapping):
                    choice = dict(choice)
                    msg = dict(choice["message"])
                    msg["content"] = self.project_text(msg.get("content"), viewer, sources=sources, state=state)
                    choice["message"] = msg
                choices.append(choice)
            out["choices"] = choices
        if isinstance(out.get("error"), str):
            out["error"] = self.project_error_message(out["error"], viewer, code=ErrorCode.INVALID_INPUT)
        return out

    def project_route_response(self, method: str, path: str, status_code: int, body: object,
                               viewer: Viewer, *, sources: Sequence[SourceRef] = (),
                               evidence: Sequence[tuple[EvidenceRef, str | None]] = ()) -> JsonValue:
        """Dispatch a legacy route response body to its projection.

        Args:
            method: HTTP method.
            path: Request path (concrete, not the template).
            status_code: Response status.
            body: Decoded JSON body or response text.
            viewer: Recipient.
            sources: Source regions retrieved for the turn (chat and model-proxy answers).
            evidence: Explicit quote evidence for the turn.
        """
        if int(status_code) >= 400:
            return self.project_http_error(status_code, body, viewer)
        p = path.split("?", 1)[0]
        if p in ("/chat", "/chat/action"):
            return self.project_chat_response(body, viewer, evidence=evidence, sources=sources)
        if p.startswith("/traces/"):
            return self.project_trace(body, viewer, sources=sources)
        if p == "/settings":
            return self.project_settings(body, viewer)
        if p in ("/health", "/session/reset") or p.startswith("/session/"):
            return self.project_paths_only(body)
        if p in ("/graph",) or p.startswith("/graph/neighborhood/"):
            return self.project_graph_payload(body, viewer)
        if p.startswith("/graph/node/"):
            return self.project_graph_node(body, viewer)
        if p == "/graph/nodes/search" and isinstance(body, Mapping):
            out = dict(body)
            out["results"] = [dict(r, node=self.project_graph_node(r.get("node"), viewer))
                              if isinstance(r, Mapping) else r for r in body.get("results") or []]
            return out
        if p == "/graph/upload" and isinstance(body, Mapping):
            out = dict(body)
            out["graph"] = self.project_graph_payload(body.get("graph"), viewer)
            if isinstance(body.get("graph_path"), str):
                out["graph_path"] = self.redact_paths(body["graph_path"])
            return out
        if p == "/publications/search" and isinstance(body, Mapping):
            out = dict(body)
            out["publications"] = [self.project_publication(x) for x in body.get("publications") or []]
            return out
        if p in ("/api/chat", "/v1/chat/completions"):
            return self.project_model_chat(body, viewer, sources=sources)
        if isinstance(body, (Mapping, list)):
            return self._scrub(body, depth=0)
        return self._safe_str(body) if isinstance(body, str) else body

    # ------------------------------------------------------------------ exports

    def split_graph_export(self, kg: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Split a MatKG JSON into a public part and private evidence (ADR section 5).

        ``context_snippets`` and edge ``has_evidence`` text move to the private
        list; the public part keeps every fact and the same keys, with each
        snippet replaced by ``{evidence_ref, source_paper, page}``. The private
        list must go to a controlled store, never to a user surface.
        """
        private: list[dict[str, Any]] = []

        def refs(owner: str, items: object) -> list[dict[str, Any]]:
            out = []
            for idx, item in enumerate(items if isinstance(items, list) else []):
                item = item if isinstance(item, Mapping) else {"text": str(item)}
                text = str(item.get("text") or "")
                ref = "ev:" + hashlib.sha256(f"{owner}|{idx}|{text}".encode()).hexdigest()[:16]
                private.append({"evidence_ref": ref, "owner": owner, **dict(item)})
                out.append({"evidence_ref": ref, "source_paper": item.get("source_paper"),
                            "page": item.get("page")})
            return out

        public: dict[str, Any] = {}
        for key, value in kg.items():
            if key == "things" and isinstance(value, list):
                public[key] = [
                    {k: (refs(str(t.get("id")), v) if k == "context_snippets" else v) for k, v in t.items()}
                    if isinstance(t, Mapping) else t for t in value]
            elif key == "associations" and isinstance(value, list):
                public[key] = [
                    {k: (refs(f"{a.get('subject')}|{a.get('predicate')}|{a.get('object')}", v)
                         if k == "has_evidence" else v) for k, v in a.items()}
                    if isinstance(a, Mapping) else a for a in value]
            else:
                public[key] = value
        return public, private


def new_correlation_id() -> str:
    """Return a fresh correlation id for requests that have none."""
    return uuid.uuid4().hex[:12]
