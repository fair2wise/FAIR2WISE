"""Live run feedback over the existing job routes: ``logs?tail=``, ``kept``/``total``, read-time scrub, ``summary``."""
from __future__ import annotations

from _v1_support import ingest_inputs

from app.modules.jobs.store import LOG_ROWS_PER_JOB


def _job(local, **params):
    c = local.client()
    job = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs(**params)}).json()
    local.work()
    return c, job["job_id"]


def _raw_rows(local, job_id, messages):
    """Rows written before a scrub rule existed (straight into the table, bypassing append_log)."""
    store = local.ops.jobs.store
    top = store.log_stats(job_id)["total"]
    with store.db.tx() as con:
        for offset, message in enumerate(messages):
            con.execute("INSERT INTO job_logs VALUES (?, ?, ?, ?, ?, ?)",
                        (job_id, top + offset, 1.0, "info", "parse", message))


def test_logs_are_scrubbed_again_on_read(local):
    c, job_id = _job(local, log=True)
    _raw_rows(local, job_id, ["old Authorization: Bearer leaked-token-1", "OPENALEX_API_KEY=leaked-2",
                              "GET https://x.org/?api_key=leaked-3&p=1", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.leaked_4"])
    body = c.get(f"/v1/jobs/{job_id}/logs").json()
    text = str(body)
    for leaked in ("leaked-token-1", "leaked-2", "leaked-3", "leaked_4"):
        assert leaked not in text
    stream = c.get(f"/v1/jobs/{job_id}/stream").text
    assert "leaked" not in stream and "[redacted]" in stream


def test_logs_report_kept_total_and_tail(local):
    c, job_id = _job(local, log=True)
    _raw_rows(local, job_id, [f"row {i}" for i in range(30)])
    body = c.get(f"/v1/jobs/{job_id}/logs").json()
    assert body["kept"] == body["total"] == len(body["logs"])
    tail = c.get(f"/v1/jobs/{job_id}/logs", params={"tail": "true", "limit": 5, "after": 10_000}).json()
    assert [x["message"] for x in tail["logs"]] == [f"row {i}" for i in range(25, 30)]
    assert tail["total"] == body["total"]
    # the cursor of a reloaded page: last seq of the tail, then nothing newer
    cursor = tail["logs"][-1]["seq"]
    assert c.get(f"/v1/jobs/{job_id}/logs", params={"after": cursor}).json()["logs"] == []
    assert LOG_ROWS_PER_JOB == 2000


def test_progress_carries_the_additive_summary_and_list_detail(local):
    c, job_id = _job(local, log=True)
    progress = c.get(f"/v1/jobs/{job_id}/progress").json()
    assert "phases" in progress and progress["job_id"] == job_id  # unchanged keys
    summary = progress["summary"]
    assert summary["outcome"] == summary["state"] and summary["papers"]["failed"] == 0
    assert set(summary) >= {"outcome", "state", "started_at", "elapsed_seconds", "stage", "current_paper", "papers",
                            "growth", "counters", "params", "error", "logs"}
    assert summary["logs"]["total"] >= 2
    plain = c.get("/v1/jobs").json()["jobs"]
    assert all("summary" not in row for row in plain)
    rows = c.get("/v1/jobs", params={"detail": "summary"}).json()["jobs"]
    assert rows and rows[0]["job_id"] == job_id and rows[0]["summary"]["logs"]["total"] >= 2
    assert "failed_list" not in rows[0]["summary"]["papers"]
    assert c.get("/v1/jobs", params={"detail": "everything"}).status_code == 422
    assert c.get("/v1/jobs/job-missing/progress").status_code == 404
