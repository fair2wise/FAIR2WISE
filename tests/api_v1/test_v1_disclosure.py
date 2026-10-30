"""AUD-06: every ``/v1`` body and ``job_event`` SSE payload passes the D19 projector.

A canary snippet, an absolute path, a ``file:`` URI and a long text are planted in each
store the ``/v1`` routes read (curation cases and their attempts, job stage results and
artifact refs, publication plans); none may reach any body. The 600-character evidence
excerpt (B10) stays the bound for reviewer text, and the data the API legitimately
carries (loopback Tiled targets, ``sha256:`` artifact refs, every event, the
``corpus-audit`` section names) survives the projection.
"""
from __future__ import annotations

import json

import pytest

from _v1_support import CORPUS, build_local, ingest_inputs

from app.modules.contracts.jobs import JobState
from app.modules.curation.models import CaseState, ResolutionAttempt
from app.modules.jobs.pipeline import Pipeline, StageResult, StageSpec
from app.modules.security.disclosure import OMISSION, DisclosureProjector, PathRoots
from tests.curation.cur_fakes import case as make_case

SNIPPET = "SYNTHETIC-SNIPPET-CANARY-7731"
LONG_TEXT = "SYNTHETIC-LONG-TEXT-CANARY-4410 " * 40  # well over the 600-character bound
PRIVATE_DIR = "/abs/private/dir"
PRIVATE_PATH = PRIVATE_DIR + "/paper_a.pdf"
PRIVATE_FILE_URI = "file://" + PRIVATE_DIR + "/report.json"
SHA_REF = "sha256:" + "a" * 64


def _sse(text):
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((fields.get("event"), fields.get("id"), json.loads(fields.get("data", "null"))))
    return events


def _clean(text: str) -> None:
    assert SNIPPET not in text and PRIVATE_DIR not in text and "file:" not in text, text


@pytest.fixture
def local(monkeypatch, tmp_path):
    return build_local(monkeypatch, tmp_path)


# ------------------------------------------------------------------ projector rules


def test_project_api_response_rules():
    projector = DisclosureProjector(roots=PathRoots.of(private=PRIVATE_DIR))
    body = {
        "target": "http://127.0.0.1:59871/api#f2w",
        "snippets": [SNIPPET], "context_snippets": [{"text": SNIPPET}], "prompt": SNIPPET,
        "parameters": {"sections": ["summary", "stubs"]},
        "report": {"sections": [SNIPPET + " is a sentence of section text"]},
        "refs": [SHA_REF, PRIVATE_FILE_URI, PRIVATE_PATH, "/static/crops/1.png"],
        "events": list(range(500)),
        "abstract": LONG_TEXT,
        "note": f"see {PRIVATE_PATH} with api_key=sk-abcdefghijklmnop",
        "n": 3, "flag": True, "none": None,
    }
    out = projector.project_api_response(body)
    assert out["target"] == body["target"]  # loopback Tiled target is data, not an artifact
    assert "snippets" not in out and "context_snippets" not in out and "prompt" not in out
    assert out["parameters"]["sections"] == ["summary", "stubs"]  # section names stay
    assert "sections" not in out["report"]  # section text does not
    assert out["refs"] == [SHA_REF, "f2w://private/paper_a.pdf"]
    assert out["events"] == list(range(500))  # no 200-item cut on API lists
    assert len(out["abstract"]) < len(LONG_TEXT) and out["abstract"].endswith(OMISSION)
    assert out["note"] == "see f2w://private/paper_a.pdf with [redacted]"
    assert out["n"] == 3 and out["flag"] is True and out["none"] is None
    assert projector.project_api_response([body["note"], 1]) == [out["note"], 1]
    assert projector.project_api_response("plain") == "plain"


# ------------------------------------------------------------------ curation


def _plant_case(local):
    store = local.ops.curation
    evidence = ("word " * 100) + PRIVATE_PATH + " " + ("word " * 200) + SNIPPET
    case_id, _ = store.enqueue(make_case(
        evidence=evidence,
        payload={"surface": "GIWAXS", "category": "technique", "snippet": SNIPPET,
                 "context_snippets": [{"text": SNIPPET}], "source": PRIVATE_PATH}))
    (leased,) = store.claim("triage", [CORPUS])
    store.record_attempt(
        ResolutionAttempt(case_id=case_id, provider="fake", requested_model="m", executed_model="m",
                          request_fingerprint="f" * 16, outcome="proposed",
                          response={"messages": [{"role": "user", "content": SNIPPET}], "prompt": SNIPPET,
                                    "proposal": {"term": "GIWAXS", "note": f"from {PRIVATE_PATH}"}},
                          validation={"accepted": False, "reason": f"see {PRIVATE_FILE_URI}"}),
        leased["lease_token"], next_state=CaseState.NEEDS_REVIEW, route="human")
    return case_id


def test_curation_bodies_carry_no_planted_text(local):
    case_id = _plant_case(local)
    c = local.client()
    for path in ("/v1/curation/summary", "/v1/curation/cases", f"/v1/curation/cases/{case_id}"):
        resp = c.get(path)
        assert resp.status_code == 200, resp.text
        _clean(resp.text)
    detail = c.get(f"/v1/curation/cases/{case_id}").json()
    case = detail["case"]
    assert len(case["evidence"]) <= 600 + len(" " + OMISSION) and case["evidence_truncated"] is True
    assert "f2w://" in case["evidence"] and "snippet" not in case["payload"]
    assert case["payload"]["surface"] == "GIWAXS" and case["payload"]["source"].startswith("f2w://")
    (attempt,) = detail["attempts"]
    assert "messages" not in attempt["response"] and "prompt" not in attempt["response"]
    assert attempt["response"]["proposal"]["term"] == "GIWAXS"
    assert attempt["validation"]["accepted"] is False
    done = c.post(f"/v1/curation/cases/{case_id}/review",
                  json={"bound_revision": case["revision"], "accept": False, "reason": "not a term"})
    assert done.status_code == 200, done.text
    _clean(done.text)


# ------------------------------------------------------------------ jobs


def _canary_pipeline() -> Pipeline:
    def scan(sc):
        sc.progress(records=1)
        return StageResult(data={"snippet": SNIPPET, "abstract": LONG_TEXT, "path": PRIVATE_PATH},
                           artifact_refs=(PRIVATE_PATH, PRIVATE_FILE_URI, SHA_REF),
                           progress={"records": 1})

    return Pipeline([StageSpec("scan", scan, phase="parsing")])


def test_job_bodies_and_stream_carry_no_planted_text(local):
    local.ops.jobs.register("canary", _canary_pipeline(), capability="ingest.acquire")
    c = local.client()
    job = c.post("/v1/jobs", json={"kind": "canary", "inputs": ingest_inputs()}).json()
    job_id = job["job_id"]
    assert local.work() == (job_id, JobState.SUCCEEDED)
    bodies = {path: c.get(path) for path in (
        "/v1/jobs", f"/v1/jobs/{job_id}", f"/v1/jobs/{job_id}/events", f"/v1/jobs/{job_id}/progress",
        f"/v1/jobs/{job_id}/view", f"/v1/jobs/{job_id}/stream")}
    for path, resp in bodies.items():
        assert resp.status_code == 200, (path, resp.text)
        _clean(resp.text)
    events = bodies[f"/v1/jobs/{job_id}/events"].json()["events"]
    refs = [ref for e in events for ref in e["artifact_refs"]]
    assert SHA_REF in refs and all(ref == SHA_REF or ref.startswith("f2w://") for ref in refs)
    streamed = [data for event, _id, data in _sse(bodies[f"/v1/jobs/{job_id}/stream"].text) if event == "job_event"]
    assert streamed == events  # the stream projects each event exactly as the polling route does


# ------------------------------------------------------------------ publications


def test_publication_plan_keeps_the_loopback_target(local):
    c = local.client()
    resp = c.post("/v1/publications", json={"snapshot_ref": "rsoxs/v1", "target": local.target})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert local.work() == (job_id, JobState.AWAITING_REVIEW)
    plan = c.get(f"/v1/publications/{job_id}/plan")
    assert plan.status_code == 200, plan.text
    assert plan.json()["target"] == local.target and local.target.startswith("http://127.0.0.1")
    view = c.get(f"/v1/jobs/{job_id}/view").json()
    assert view["publication"]["target"] == local.target and view["publication"]["plan"]["target"] == local.target
