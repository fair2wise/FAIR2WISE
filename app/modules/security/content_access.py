"""Source-access authority for trusted internal agent reads (task T21B).

Implements ``source.read`` from ``docs/adr/content-disclosure.md`` section 2:
the agent runtime may read private sources (PDF bytes, parsed bodies, sections,
page/figure crops, ``storage/source_index`` chunks, private evidence) inside
the process. A user surface never may.

Authority comes from exactly two things:

1. a server-built :class:`~app.modules.contracts.context.RequestContext`, and
2. an :class:`InternalReadGrant` issued by a :class:`SourceAccessAuthority`
   that trusted host wiring (``application.py``) constructs at startup.

A grant is sealed with a per-authority random key and bound to one request. A
request body, header, query flag, ``ui_settings`` key, model or Tool argument,
caller label or saved record cannot create or widen one (ADR rule A2): the
checks accept only a live grant object whose seal verifies, never a mapping,
string or boolean. ``source.read`` is deliberately not part of the frozen
capability vocabulary in ``contracts/context.py``, so it cannot be smuggled
into ``RequestContext.capabilities`` either.

A Python import inside a fully trusted host is not a sandbox (CONTENT_ACCESS_POLICY
section 2): this module stops confused-deputy and input-flag escalation, not
malicious host code. Importing it has no side effects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import hashlib
import hmac
import secrets
import threading
from typing import Any, NoReturn, TypeVar
import uuid

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode

#: Internal capability name (ADR section 2.1). Never a member of ``contracts.CAPABILITIES``.
SOURCE_READ = "source.read"

T = TypeVar("T")


class SourceKind(StrEnum):
    """Private source material an internal read may touch."""

    PDF_BYTES = "pdf_bytes"
    PARSED_TEXT = "parsed_text"
    SECTION = "section"
    PAGE_CROP = "page_crop"
    FIGURE_CROP = "figure_crop"
    SOURCE_INDEX_CHUNK = "source_index_chunk"
    PRIVATE_EVIDENCE = "private_evidence"


class ReadPurpose(StrEnum):
    """Why trusted service code reads a private source."""

    RETRIEVAL = "retrieval"
    PAPER_EVIDENCE = "paper_evidence"
    EXTRACTION = "extraction"
    FIGURE_ANALYSIS = "figure_analysis"
    QUOTE_VERIFICATION = "quote_verification"
    VALIDATION = "validation"
    KNOWLEDGE_OPERATIONS = "knowledge_operations"


class SourceAccessDenied(ContractError):
    """Raised when an internal source read is not authorized.

    The caller-visible message is fixed; ``reason`` is for server logs only.

    Args:
        reason: Internal reason for the denial.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(ErrorCode.DENIED, "source access denied", retryable=False)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class InternalReadGrant:
    """A sealed, request-bound permission for internal source reads.

    Only :meth:`SourceAccessAuthority.issue` produces a valid grant. Building one
    by hand, copying one with changed fields, or reusing one for another request
    fails the seal or binding check.

    Attributes:
        grant_id: Random identifier, recorded in audit/trace.
        request_id: ``RequestContext.request_id`` the grant is bound to.
        subject: ``Principal.subject`` of that context.
        purpose: Why the read happens.
        kinds: Source kinds the grant covers.
        corpus_scope: Corpora the grant covers; empty means the context's scope.
        work_ids: Works the grant covers; empty means any work in scope.
        issued_at: Issue time (UTC).
        expires_at: Expiry time (UTC).
    """

    grant_id: str
    request_id: str
    subject: str
    purpose: ReadPurpose
    kinds: frozenset[SourceKind]
    corpus_scope: frozenset[str]
    work_ids: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    _seal: bytes = field(default=b"", repr=False, compare=False)

    def __reduce__(self) -> NoReturn:
        raise TypeError("InternalReadGrant is a live object and cannot be serialized")

    def payload(self) -> bytes:
        """Return the canonical bytes covered by the seal."""
        parts = [
            self.grant_id, self.request_id, self.subject, self.purpose.value,
            ",".join(sorted(k.value for k in self.kinds)),
            ",".join(sorted(self.corpus_scope)), ",".join(sorted(self.work_ids)),
            self.issued_at.isoformat(), self.expires_at.isoformat(),
        ]
        return "\x1f".join(parts).encode("utf-8")


class SourceAccessAuthority:
    """Issues and verifies internal read grants for one trusted runtime.

    Construct exactly one per process from startup wiring (ADR rule A1) and pass
    it to tools as a Python object. The sealing key is generated at construction,
    never read from configuration, a request or the environment.

    Args:
        max_ttl: Longest lifetime a grant may have.
        clock: Returns the current UTC time; injectable for tests.
    """

    def __init__(self, *, max_ttl: timedelta = timedelta(minutes=30),
                 clock: Callable[[], datetime] | None = None) -> None:
        self._key = secrets.token_bytes(32)
        self._max_ttl = max_ttl
        self._clock = clock or _now
        self._revoked: set[str] = set()
        self._lock = threading.Lock()

    def __reduce__(self) -> NoReturn:
        raise TypeError("SourceAccessAuthority cannot be serialized")

    def _sign(self, grant: InternalReadGrant) -> bytes:
        return hmac.new(self._key, grant.payload(), hashlib.sha256).digest()

    def issue(
        self,
        context: RequestContext,
        *,
        purpose: ReadPurpose,
        kinds: Iterable[SourceKind],
        corpus_scope: Iterable[str] = (),
        work_ids: Iterable[str] = (),
        ttl: timedelta | None = None,
    ) -> InternalReadGrant:
        """Issue a grant for trusted service code acting on ``context``.

        Args:
            context: Server-built request context (never request data).
            purpose: Enumerated read purpose; strings are refused.
            kinds: Enumerated source kinds; strings are refused.
            corpus_scope: Corpora to cover; must lie inside ``context.corpus_scope``
                when the context restricts corpora.
            work_ids: Works to cover; empty means any work in scope.
            ttl: Lifetime, capped at ``max_ttl``.

        Returns:
            The sealed grant.

        Raises:
            SourceAccessDenied: For any input that is not a trusted object.
        """
        if not isinstance(context, RequestContext):
            raise SourceAccessDenied("grant requires a server-built RequestContext")
        if not isinstance(purpose, ReadPurpose):
            raise SourceAccessDenied("purpose must be a ReadPurpose member")
        kind_set = frozenset(kinds)
        if not kind_set or not all(isinstance(k, SourceKind) for k in kind_set):
            raise SourceAccessDenied("kinds must be non-empty SourceKind members")
        corpora = frozenset(str(c) for c in corpus_scope)
        if context.corpus_scope and not corpora <= context.corpus_scope:
            raise SourceAccessDenied("grant corpus scope exceeds the request scope")
        life = min(ttl or self._max_ttl, self._max_ttl)
        if life <= timedelta(0):
            raise SourceAccessDenied("grant lifetime must be positive")
        issued = self._clock()
        grant = InternalReadGrant(
            grant_id=uuid.uuid4().hex, request_id=context.request_id, subject=context.principal.subject,
            purpose=purpose, kinds=kind_set, corpus_scope=corpora,
            work_ids=frozenset(str(w) for w in work_ids), issued_at=issued, expires_at=issued + life,
        )
        object.__setattr__(grant, "_seal", self._sign(grant))
        return grant

    def revoke(self, grant: InternalReadGrant) -> None:
        """Revoke ``grant`` for the rest of this authority's life."""
        if isinstance(grant, InternalReadGrant):
            with self._lock:
                self._revoked.add(grant.grant_id)

    def check(
        self,
        context: RequestContext,
        grant: Any,
        *,
        kind: SourceKind,
        corpus: str | None = None,
        work_id: str | None = None,
    ) -> None:
        """Raise unless ``grant`` authorizes reading ``kind`` for ``context``.

        Raises:
            SourceAccessDenied: When any check fails.
        """
        if not isinstance(context, RequestContext):
            raise SourceAccessDenied("no server-built RequestContext")
        if not isinstance(grant, InternalReadGrant):
            # Mappings ({"agent": true}), strings ("internal"), booleans, headers: never authority.
            raise SourceAccessDenied(f"not a grant object: {type(grant).__name__}")
        if not grant._seal or not hmac.compare_digest(grant._seal, self._sign(grant)):
            raise SourceAccessDenied("grant seal does not verify")
        with self._lock:
            if grant.grant_id in self._revoked:
                raise SourceAccessDenied("grant revoked")
        if grant.request_id != context.request_id or grant.subject != context.principal.subject:
            raise SourceAccessDenied("grant is bound to another request")
        if self._clock() >= grant.expires_at:
            raise SourceAccessDenied("grant expired")
        if not isinstance(kind, SourceKind) or kind not in grant.kinds:
            raise SourceAccessDenied(f"grant does not cover {kind!r}")
        if corpus is not None:
            allowed = grant.corpus_scope or context.corpus_scope
            if allowed and corpus not in allowed:
                raise SourceAccessDenied("corpus outside grant scope")
            if context.corpus_scope and corpus not in context.corpus_scope:
                raise SourceAccessDenied("corpus outside request scope")
        if grant.work_ids and (work_id is None or work_id not in grant.work_ids):
            raise SourceAccessDenied("work outside grant scope")

    def is_authorized(self, context: Any, grant: Any, *, kind: SourceKind,
                      corpus: str | None = None, work_id: str | None = None) -> bool:
        """Boolean form of :meth:`check`."""
        try:
            self.check(context, grant, kind=kind, corpus=corpus, work_id=work_id)
        except SourceAccessDenied:
            return False
        return True

    def scope(self, context: RequestContext, grant: InternalReadGrant) -> SourceAccessScope:
        """Bind ``context`` and ``grant`` into a scope handed to internal tools."""
        if not isinstance(grant, InternalReadGrant):
            raise SourceAccessDenied("not a grant object")
        return SourceAccessScope(self, context, grant)


class SourceAccessScope:
    """Trusted read scope passed to agents and tools as a Python object.

    Every read goes through :meth:`read`, which re-checks the grant first. A
    scope holds no source content and cannot be serialized.

    Args:
        authority: Issuing authority.
        context: Request context the grant is bound to.
        grant: The grant.
    """

    __slots__ = ("_authority", "_context", "_grant")

    def __init__(self, authority: SourceAccessAuthority, context: RequestContext,
                 grant: InternalReadGrant) -> None:
        if not isinstance(authority, SourceAccessAuthority):
            raise SourceAccessDenied("scope requires a SourceAccessAuthority")
        self._authority = authority
        self._context = context
        self._grant = grant

    def __reduce__(self) -> NoReturn:
        raise TypeError("SourceAccessScope cannot be serialized")

    def __repr__(self) -> str:
        return f"SourceAccessScope(grant={self._grant.grant_id}, purpose={self._grant.purpose.value})"

    @property
    def grant_id(self) -> str:
        """Identifier of the bound grant, for audit records."""
        return self._grant.grant_id

    @property
    def purpose(self) -> ReadPurpose:
        """Purpose of the bound grant."""
        return self._grant.purpose

    def require(self, kind: SourceKind, *, corpus: str | None = None, work_id: str | None = None) -> None:
        """Raise :class:`SourceAccessDenied` unless the scope covers the read."""
        self._authority.check(self._context, self._grant, kind=kind, corpus=corpus, work_id=work_id)

    def allows(self, kind: SourceKind, *, corpus: str | None = None, work_id: str | None = None) -> bool:
        """Boolean form of :meth:`require`."""
        return self._authority.is_authorized(self._context, self._grant, kind=kind, corpus=corpus,
                                             work_id=work_id)

    def read(self, kind: SourceKind, reader: Callable[[], T], *, corpus: str | None = None,
             work_id: str | None = None) -> T:
        """Check the grant, then run ``reader`` and return its result.

        Args:
            kind: Kind of source being read.
            reader: Zero-argument callable that performs the actual read.
            corpus: Corpus of the source, when known.
            work_id: Work of the source, when known.
        """
        self.require(kind, corpus=corpus, work_id=work_id)
        return reader()


def user_surface_source_access(*_args: Any, **_kwargs: Any) -> bool:
    """Return whether a user-facing surface may receive source bodies: always ``False``.

    Exists so serializers can state the rule explicitly. No profile, admin role,
    ``:local`` full access (D34) or caller flag changes the answer (ADR section 2.1).
    """
    return False
