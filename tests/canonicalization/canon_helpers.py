"""Shared fakes for the T07 campaign tests: rows, vocabulary, scripted judge, target and approvals."""

import json
import re
from datetime import datetime, timedelta, timezone

from app.modules.canonicalization import gates
from app.modules.canonicalization.campaign.evidence import GraphRows
from app.modules.canonicalization.vocabulary import VocabEntry, Vocabulary, VocabularyApproval
from app.modules.contracts.changes import ApprovalRecord
from app.modules.contracts.context import LOCAL_OPERATOR, AuthorizationMode, DeploymentProfile

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
TARGET = "http://localhost:8000#canonicalization"


def vocab(status="approved"):
    cand = Vocabulary(
        1,
        [
            VocabEntry(
                "giwaxs", "grazing incidence wide angle x-ray scattering", "technique", aliases=("GIWAXS",)
            ),
            VocabEntry("tem", "transmission electron microscopy", "technique", aliases=("TEM",)),
            VocabEntry("mote2", "MoTe2", "material"),
        ],
        source="test",
    )
    if status == "candidate":
        return cand
    return cand.approve(VocabularyApproval("va-1", cand.revision, "owner", NOW))


def node(node_id, surface, pub, category="technique", text=None):
    return {
        "node_id": node_id,
        "surface": surface,
        "category": category,
        "publication_id": pub,
        "section_id": "s1",
        "text": text or f"We measured the film by {surface} at the beamline.",
    }


def rows():
    return GraphRows(
        [
            node("n1", "GI-WAXS", "p1"),
            node("n2", "GI-WAXS", "p2"),
            node("n3", "GI-WAXS", "p3"),
            node("n4", "WTe2", "p1", "material"),
            node("n5", "WTe2", "p2", "material"),
            node("n6", "ImageJ", "p1"),
            node("n7", "spin coating step", "p2"),
            node("n8", "atomic force microscopy", "p3"),
            {"node_id": "bad", "surface": 12345, "category": "technique"},
        ],
        {"n1": 2, "n2": 1},
    )


class ScriptedJudge:
    """Maps every term to ``slug`` (even wrongly) with a real quote; counts calls."""

    def __init__(self, slug_by_term=None, default="giwaxs"):
        self.slug_by_term = slug_by_term or {}
        self.default = default
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        term = re.search(r'(?:Term: "|Evidence for ")(.*?)"', prompt).group(1)
        quote = re.search(r'^- "(.*?)"$', prompt, re.M)
        slug = self.slug_by_term.get(term, self.default)
        if slug is None:
            return json.dumps({"verdict": "abstain", "confidence": 0.1})
        return json.dumps(
            {
                "verdict": "map_to",
                "target_slug": slug,
                "confidence": 0.95,
                "reason": "scripted",
                "evidence_quote": quote.group(1)[:30] if quote else "",
            }
        )


class FakeTarget:
    def __init__(self, revision):
        self.revision = revision
        self.merged = []

    def source_revision(self):
        return self.revision

    def merge(self, op):
        self.merged.append(op.term_key)


def local_approval(plan, *, approval_id="ap-1", expires=None, scope=None):
    cp = plan.to_change_plan()
    return ApprovalRecord(
        approval_id=approval_id,
        profile=DeploymentProfile.LOCAL,
        mode=AuthorizationMode.LOCAL_TRUSTED,
        plan_id=cp.plan_id,
        plan_sha256=cp.plan_sha256,
        target=cp.target,
        scope=tuple(scope or cp.corpus_scope),
        requested_by=LOCAL_OPERATOR,
        approved_by=LOCAL_OPERATOR,
        executed_as="writer-svc",
        expires_at=expires or NOW + timedelta(days=1),
        approved_at=NOW,
    )


def open_gates(monkeypatch):
    """Test-only: simulate the owner opening every gate at H3."""
    monkeypatch.setattr(gates, "_GATES", {k: True for k in gates._GATES})
    monkeypatch.setattr(gates, "CANONICALIZATION_GATES", gates._GATES)
    monkeypatch.setattr(gates, "_EXTRACTION_GATES", {"canonical_merge": True})
