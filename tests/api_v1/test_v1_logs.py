"""T33: ``GET /v1/jobs/{id}/logs``, its policy row and the additive ``job_log`` SSE frames."""
from __future__ import annotations

from _v1_support import ingest_inputs

from app.api.auth import undeclared_routes
from app.api.policies import api_route_policy


def _frames(text):
    out = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        out.append((fields.get("event"), fields.get("id"), json_loads(fields.get("data", "null"))))
    return out


def json_loads(text):
    import json

    return json.loads(text)


def _run(local, **params):
    c = local.client()
    job = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs(**params)}).json()
    local.work()
    return c, job["job_id"]


def test_policy_row_v42_matches_v03(local):
    v42 = api_route_policy("GET", "/v1/jobs/{job_id}/logs")
    v03 = api_route_policy("GET", "/v1/jobs/{job_id}")
    assert v42 is not None and v42.route_id == "V42" and v42.capabilities == v03.capabilities == frozenset()
    assert undeclared_routes(local.app) == []


def test_logs_route_returns_scrubbed_ordered_lines_with_after_and_limit(local):
    c, job_id = _run(local, log=True)
    body = c.get(f"/v1/jobs/{job_id}/logs").json()
    assert body["job_id"] == job_id
    lines = body["logs"]
    assert [x["seq"] for x in lines] == [0, 1]
    assert lines[0]["stage"] == "parse" and lines[0]["level"] == "info" and lines[1]["level"] == "warning"
    assert "sk-secret" not in str(body) and "[redacted]" in lines[0]["message"]
    assert set(lines[0]) == {"seq", "at", "level", "stage", "message"}
    assert [x["seq"] for x in c.get(f"/v1/jobs/{job_id}/logs", params={"after": 0}).json()["logs"]] == [1]
    assert [x["seq"] for x in c.get(f"/v1/jobs/{job_id}/logs", params={"limit": 1}).json()["logs"]] == [0]
    assert c.get(f"/v1/jobs/{job_id}/logs", params={"limit": 0}).status_code == 422
    assert c.get("/v1/jobs/job-missing/logs").status_code == 404
    quiet_c, quiet = _run(local, chunk=11)
    assert quiet_c.get(f"/v1/jobs/{quiet}/logs").json()["logs"] == []


def test_stream_keeps_job_event_framing_and_adds_job_log_frames(local):
    c, job_id = _run(local, log=True)
    frames = _frames(c.get(f"/v1/jobs/{job_id}/stream").text)
    kinds = [f[0] for f in frames]
    assert kinds[-1] == "end" and kinds.count("end") == 1 and set(kinds[:-1]) == {"job_event", "job_log"}
    event_ids = [int(f[1]) for f in frames if f[0] == "job_event"]
    assert event_ids == sorted(event_ids) and event_ids == [f[2]["sequence"] for f in frames if f[0] == "job_event"]
    logs = [f for f in frames if f[0] == "job_log"]
    assert [f[1] for f in logs] == [None, None]  # no id: Last-Event-ID stays about job events
    assert [f[2]["seq"] for f in logs] == [0, 1] and "sk-secret" not in str(logs)
    # reconnecting with Last-Event-ID resumes job events only; logs_after resumes logs
    resumed = _frames(c.get(f"/v1/jobs/{job_id}/stream", headers={"Last-Event-ID": str(event_ids[0])},
                            params={"logs_after": 0}).text)
    assert [int(f[1]) for f in resumed if f[0] == "job_event"] == event_ids[1:]
    assert [f[2]["seq"] for f in resumed if f[0] == "job_log"] == [1]
    # a job without logs streams exactly as before
    plain_c, plain = _run(local, chunk=12)
    assert {f[0] for f in _frames(plain_c.get(f"/v1/jobs/{plain}/stream").text)} == {"job_event", "end"}


def test_other_users_cannot_read_an_admins_job_logs(als):
    admin = als.client("admin")
    job_id = admin.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs(log=True)}).json()["job_id"]
    als.work()
    assert [x["seq"] for x in admin.get(f"/v1/jobs/{job_id}/logs").json()["logs"]] == [0, 1]
    for who in ("user", "user2"):
        assert als.client(who).get(f"/v1/jobs/{job_id}/logs").status_code == 404
    assert als.client("anon").get(f"/v1/jobs/{job_id}/logs").status_code == 401
