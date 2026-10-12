"""T21B source-access controls: SEC-02, SEC-03, FIG-04, LOCAL-07, LOCAL-09, AUTH-10 (ADR 2, P4, N7, N12)."""
from __future__ import annotations

import dataclasses
import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _disclosure_support as S  # noqa: E402
from app.modules.contracts.context import CAPABILITIES, RequestContext  # noqa: E402
from app.modules.contracts.errors import ContractError, ErrorCode  # noqa: E402
from app.modules.security.content_access import (  # noqa: E402
    SOURCE_READ, InternalReadGrant, ReadPurpose, SourceAccessAuthority, SourceAccessDenied, SourceAccessScope,
    SourceKind, user_surface_source_access)
from app.modules.security.disclosure import DisclosureProjector, QuoteVerifier, Viewer  # noqa: E402

ALL_KINDS = list(SourceKind)


def _authority_and_grant(context=None, **kw):
    context = context or S.local_context()
    authority = SourceAccessAuthority()
    kw.setdefault("kinds", ALL_KINDS)
    grant = authority.issue(context, purpose=ReadPurpose.PAPER_EVIDENCE, **kw)
    return authority, context, grant


# ------------------------------------------------------------------ positive (SEC-02, FIG-04, P4)


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_trusted_internal_read_succeeds_for_every_source_kind(kind):
    authority, context, grant = _authority_and_grant()
    scope = authority.scope(context, grant)
    assert scope.read(kind, lambda: b"%PDF-1.4 private bytes" if kind is SourceKind.PDF_BYTES else "page text",
                      work_id=S.WORK)
    assert scope.allows(kind)


@pytest.mark.parametrize("make_ctx", [S.local_context, lambda: S.als_context(admin=False),
                                      lambda: S.als_context(admin=True)])
def test_internal_reads_work_for_every_profile_runtime(make_ctx):
    """The agent runtime reads sources for any authorized request, including a non-admin chat turn."""
    authority, context, grant = _authority_and_grant(make_ctx(), kinds=[SourceKind.FIGURE_CROP])
    assert authority.scope(context, grant).read(SourceKind.FIGURE_CROP, lambda: "crop-bytes") == "crop-bytes"


def test_scope_limits_kind_corpus_and_work():
    context = S.als_context(admin=False).model_copy(update={"corpus_scope": frozenset({"rsoxs", "tomo"})})
    authority = SourceAccessAuthority()
    grant = authority.issue(context, purpose=ReadPurpose.EXTRACTION, kinds=[SourceKind.PARSED_TEXT],
                            corpus_scope=["rsoxs"], work_ids=[S.WORK])
    scope = authority.scope(context, grant)
    scope.require(SourceKind.PARSED_TEXT, corpus="rsoxs", work_id=S.WORK)
    for kwargs in ({"corpus": "tomo", "work_id": S.WORK}, {"corpus": "rsoxs", "work_id": "work:other"},
                   {"corpus": "rsoxs"}):
        with pytest.raises(SourceAccessDenied):
            scope.require(SourceKind.PARSED_TEXT, **kwargs)
    with pytest.raises(SourceAccessDenied):
        scope.require(SourceKind.PDF_BYTES, corpus="rsoxs", work_id=S.WORK)
    with pytest.raises(SourceAccessDenied):
        authority.issue(context, purpose=ReadPurpose.EXTRACTION, kinds=[SourceKind.PARSED_TEXT],
                        corpus_scope=["secret-corpus"])


def test_reader_is_not_called_when_denied():
    authority, context, grant = _authority_and_grant(kinds=[SourceKind.PARSED_TEXT])
    calls = []
    with pytest.raises(SourceAccessDenied):
        authority.scope(context, grant).read(SourceKind.PAGE_CROP, lambda: calls.append(1))
    assert calls == []


# ------------------------------------------------------------------ negative (SEC-03, LOCAL-07, N7)


CALLER_CLAIMS = [
    {"agent": True}, {"internal": True}, {"agent": "true", "internal": "1"}, {"X-Agent": "1"},
    {"User-Agent": "f2w-agent"}, {"ui_settings": {"internal": True}}, {"trusted": True},
    {"grant_id": "x", "kinds": ["pdf_bytes"]}, "internal", "agent=true", True, 1, None, b"grant",
]


@pytest.mark.parametrize("claim", CALLER_CLAIMS, ids=repr)
def test_caller_flags_never_grant_source_read(claim):
    authority, context, _grant = _authority_and_grant()
    for kind in ALL_KINDS:
        with pytest.raises(SourceAccessDenied):
            authority.check(context, claim, kind=kind)
        assert authority.is_authorized(context, claim, kind=kind) is False
    with pytest.raises(SourceAccessDenied):
        authority.scope(context, claim)


@pytest.mark.parametrize("claim", CALLER_CLAIMS[:8], ids=repr)
def test_request_shaped_context_cannot_obtain_a_grant(claim):
    """A request body or header mapping is not a RequestContext."""
    authority = SourceAccessAuthority()
    with pytest.raises(SourceAccessDenied):
        authority.issue(claim, purpose=ReadPurpose.RETRIEVAL, kinds=[SourceKind.PARSED_TEXT])


def test_issue_accepts_no_flag_arguments():
    authority = SourceAccessAuthority()
    context = S.als_context(admin=False)
    with pytest.raises(TypeError):
        authority.issue(context, purpose=ReadPurpose.RETRIEVAL, kinds=[SourceKind.PARSED_TEXT],
                        agent=True)  # type: ignore[call-arg]
    with pytest.raises(SourceAccessDenied):
        authority.issue(context, purpose="paper_evidence", kinds=[SourceKind.PARSED_TEXT])  # type: ignore[arg-type]
    with pytest.raises(SourceAccessDenied):
        authority.issue(context, purpose=ReadPurpose.RETRIEVAL, kinds=["pdf_bytes"])  # type: ignore[list-item]
    with pytest.raises(SourceAccessDenied):
        authority.issue(context, purpose=ReadPurpose.RETRIEVAL, kinds=[])


def test_source_read_is_not_a_request_capability():
    """It cannot ride in RequestContext.capabilities (rejected by the frozen vocabulary)."""
    assert SOURCE_READ not in CAPABILITIES
    for base in (S.local_context(), S.als_context(admin=True)):
        with pytest.raises(ValidationError):
            RequestContext.model_validate({**base.model_dump(), "capabilities": {SOURCE_READ}})


def test_forged_or_tampered_grants_fail_the_seal():
    authority, context, grant = _authority_and_grant(kinds=[SourceKind.PARSED_TEXT])
    widened = dataclasses.replace(grant, kinds=frozenset(ALL_KINDS))
    handmade = InternalReadGrant(
        grant_id="g", request_id=context.request_id, subject=context.principal.subject,
        purpose=ReadPurpose.RETRIEVAL, kinds=frozenset(ALL_KINDS), corpus_scope=frozenset(),
        work_ids=frozenset(), issued_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1), _seal=b"\x00" * 32)
    other_authority_grant = SourceAccessAuthority().issue(context, purpose=ReadPurpose.RETRIEVAL,
                                                          kinds=ALL_KINDS)
    for forged in (widened, handmade, other_authority_grant):
        with pytest.raises(SourceAccessDenied):
            authority.check(context, forged, kind=SourceKind.PDF_BYTES)


def test_grant_is_bound_to_its_request_and_principal():
    authority, context, grant = _authority_and_grant()
    for other in (S.local_context(request_id="req-other"), S.als_context(admin=True, request_id=context.request_id)):
        with pytest.raises(SourceAccessDenied):
            authority.check(other, grant, kind=SourceKind.PARSED_TEXT)


def test_grant_expiry_and_revocation():
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    authority = SourceAccessAuthority(max_ttl=timedelta(minutes=5), clock=lambda: clock[0])
    context = S.local_context()
    grant = authority.issue(context, purpose=ReadPurpose.RETRIEVAL, kinds=[SourceKind.PARSED_TEXT],
                            ttl=timedelta(hours=10))
    assert grant.expires_at - grant.issued_at == timedelta(minutes=5)  # capped
    authority.check(context, grant, kind=SourceKind.PARSED_TEXT)
    clock[0] += timedelta(minutes=6)
    with pytest.raises(SourceAccessDenied):
        authority.check(context, grant, kind=SourceKind.PARSED_TEXT)
    clock[0] -= timedelta(minutes=6)
    authority.revoke(grant)
    with pytest.raises(SourceAccessDenied):
        authority.check(context, grant, kind=SourceKind.PARSED_TEXT)


def test_live_objects_cannot_be_serialized():
    authority, context, grant = _authority_and_grant()
    for obj in (authority, grant, authority.scope(context, grant)):
        with pytest.raises(TypeError):
            pickle.dumps(obj)


def test_denial_is_a_typed_contract_error_without_internal_reason():
    authority, context, _ = _authority_and_grant()
    with pytest.raises(ContractError) as info:
        authority.check(context, {"agent": True}, kind=SourceKind.PDF_BYTES)
    assert info.value.code is ErrorCode.DENIED
    assert str(info.value) == "source access denied"


# ------------------------------------------------------------------ user surfaces (N12, LOCAL-09, AUTH-10)


@pytest.mark.parametrize("make_ctx", [S.local_context, lambda: S.als_context(admin=True)])
def test_full_access_profiles_hold_no_user_surface_source_access(make_ctx):
    assert user_surface_source_access(make_ctx()) is False
    assert user_surface_source_access(make_ctx(), agent=True, internal=True) is False


def test_verifier_requires_a_scope_and_fails_closed_without_parsed_text():
    with pytest.raises(TypeError):
        QuoteVerifier(lambda v, loc: "text", {"agent": True})  # type: ignore[arg-type]
    authority, context, grant = _authority_and_grant(kinds=[SourceKind.PDF_BYTES])
    verifier = QuoteVerifier(lambda v, loc: "text", authority.scope(context, grant))
    projector = DisclosureProjector(verifier=verifier)
    viewer = Viewer.from_context(context)
    # A projection that cannot verify under source.read raises a typed error; it never returns the raw object.
    with pytest.raises(SourceAccessDenied):
        projector.project_text('As written: "one two three four five six seven" [PDF: x.pdf p.1]', viewer,
                               sources=[S.page_ref(1)])


def test_viewer_comes_only_from_server_context():
    with pytest.raises(TypeError):
        Viewer.from_context({"principal": {"subject": "local-operator"}, "agent": True})  # type: ignore[arg-type]
    ctx = RequestContext.model_validate(S.als_context(admin=False).model_dump())
    assert Viewer.from_context(ctx).actor == "https://tiled.example:alice"


def test_scope_type_is_not_constructible_from_data():
    with pytest.raises(SourceAccessDenied):
        SourceAccessScope({"agent": True}, S.local_context(), None)  # type: ignore[arg-type]
