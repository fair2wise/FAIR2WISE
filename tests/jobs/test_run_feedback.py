"""Run-feedback building blocks: scrub rules, store counters/status/log file, LLM failure wording, path redaction."""

from __future__ import annotations

import json
import logging

import pytest

from app.modules.cborg_errors import IP_NOT_AUTHORIZED_LOG_TEXT, CborgIpNotAuthorized
from app.modules.contracts.jobs import JobState
from app.modules.jobs import JobStore
from app.modules.jobs.log_capture import redact_paths
from app.modules.jobs.process_support import scrub
from app.modules.jobs.store import LOG_FILE_BYTES, LOG_ROWS_PER_JOB
from app.modules.llm_failures import plain_call_failure, provider_label
from app.modules.term_extractor.models import RelationRecord, TermRecord
from app.modules.term_extractor.store import TermStore


@pytest.mark.parametrize("line, secret", [
    ("Authorization: Bearer abc.def-ghi123", "abc.def-ghi123"),
    ("sent Authorization: Basic dXNlcjpwYXNz ok", "dXNlcjpwYXNz"),
    ('headers={"Authorization": "tok_live_999"}', "tok_live_999"),
    ("got Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sigsig_1", "sigsig_1"),
    ("CBORG_API_KEY=cb-secret-123 loaded", "cb-secret-123"),
    ("OPENALEX_API_KEY: oa-secret-456", "oa-secret-456"),
    ("env MY_SERVICE_KEY=svc-789", "svc-789"),
    ("env GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("GET https://api.example.org/v1/x?api_key=urlsecret1&page=2", "urlsecret1"),
    ("GET https://api.example.org/v1/x?token=urlsecret2", "urlsecret2"),
    ("GET https://maps.example.org/x?key=AIzaSy123456&q=1", "AIzaSy123456"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVP_mB92K27uhbUJU1p1r", "dBjftJeZ4CVP_mB92K27uhbUJU1p1r"),
    ("key sk-proj-abcdefghijklmnop1234", "abcdefghijklmnop1234"),
    ("key sk-abcdefghijklmnop1234", "abcdefghijklmnop1234"),
    ('{"api_key": "json secret value", "n": 1}', "json secret value"),
    ("password=hunter2hunter2", "hunter2hunter2"),
    ("fetch https://user:pw-9876@host.example/p", "pw-9876"),
])
def test_scrub_removes_the_secret(line, secret):
    out = scrub(line)
    assert secret not in out and "[redacted]" in out
    assert scrub(out) == out  # idempotent


@pytest.mark.parametrize("line", [
    "paper 3 of 12: extracting rsoxs-p3ht.pdf", "max_tokens=1000 tokens: 52 used", "primary key: id",
    "Finished 'a.pdf': 4/9 pages yielded terms", "https://arxiv.org/abs/2401.00001 page 2/3",
])
def test_scrub_leaves_ordinary_lines_alone(line):
    assert scrub(line) == line


def test_store_scrubs_on_write_and_the_full_file_never_holds_a_secret(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    lease, job_id = _running(store)
    store.append_log(lease, job_id, "run", "call with Authorization: Bearer topsecrettoken123")
    store.append_log(lease, job_id, "run", "line two\nsecond\tline")
    path = store.log_file(job_id)
    assert path is not None and path.parent == tmp_path / "job-logs" and oct(path.stat().st_mode & 0o777) == "0o600"
    text = path.read_text()
    assert "topsecrettoken123" not in text and text.count("\n") == 2  # one record per line, tab separated
    assert store.logs(job_id)[0].message == "call with [redacted]"


def test_log_file_beyond_the_row_cap_and_its_own_size_cap(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    lease, job_id = _running(store)
    for i in range(LOG_ROWS_PER_JOB + 25):
        store.append_log(lease, job_id, "run", f"line {i}")
    stats = store.log_stats(job_id)
    assert stats == {"kept": LOG_ROWS_PER_JOB, "total": LOG_ROWS_PER_JOB + 25, "cap": LOG_ROWS_PER_JOB}
    assert len(store.log_file(job_id).read_text().splitlines()) == LOG_ROWS_PER_JOB + 25  # the file keeps all
    tail = store.log_tail(job_id, 3)
    assert [row.message for row in tail] == [f"line {LOG_ROWS_PER_JOB + i}" for i in (22, 23, 24)]
    # size cap: a long run stops writing and says so once
    path = store.log_file(job_id)
    with path.open("ab") as handle:
        handle.write(b"x" * (LOG_FILE_BYTES - path.stat().st_size - 10))
    store.append_log(lease, job_id, "run", "y" * 300)
    store.append_log(lease, job_id, "run", "z")
    size = path.stat().st_size
    assert size <= LOG_FILE_BYTES + 200 and "log file full" in path.read_text().splitlines()[-1]
    store.append_log(lease, job_id, "run", "after full")
    assert path.stat().st_size == size


def test_memory_store_keeps_no_file_and_bad_ids_are_refused(tmp_path):
    store = JobStore()
    lease, job_id = _running(store)
    store.append_log(lease, job_id, "run", "x")
    assert store.log_dir is None and store.log_file(job_id) is None
    assert JobStore(tmp_path / "j.sqlite3").log_file("../etc/passwd") is None


def test_status_and_unit_rows_roundtrip(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    lease, job_id = _running(store)
    assert store.status(job_id) is None
    store.set_status(lease, job_id, "run", "extracting", "paper.pdf Authorization: Bearer abc12345")
    store.set_status(lease, job_id, "run", "merging into the KG", "x" * 500)
    status = store.status(job_id)
    assert status["phase"] == "merging into the KG" and len(status["item"]) == 200
    store.save_unit(lease, job_id, "run", "u1", "k", {"relpath": "a/b.pdf"})
    store.save_unit(lease, job_id, "run", "u2", "k", None, "c.pdf: boom")
    rows = store.unit_rows(job_id, "run")
    assert [(r["unit_id"], r["status"]) for r in rows] == [("u1", "done"), ("u2", "failed")]


def test_old_database_gains_the_status_table_without_a_migration(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    JobStore(path).close()
    con = sqlite3.connect(path)
    con.execute("DROP TABLE job_status")
    con.commit()
    con.close()
    store = JobStore(path)  # opening applies the additive schema
    assert store.status("none") is None


def _running(store: JobStore):
    from app.modules.jobs.keys import OperationInputs

    job = store.submit("k", OperationInputs(corpus_scope=("c",), params={}), created_by="u")
    lease = store.acquire_lease(store.job_resource(job.job_id), "w")
    store.set_state(lease, job.job_id, JobState.RUNNING, reason="t")
    store.begin_stage(lease, job.job_id, "run", 0, "op")
    return lease, job.job_id


def test_term_store_growth_counts_new_merged_entities_and_relations(tmp_path):
    store = TermStore(str(tmp_path / "terms.json"))
    rel = RelationRecord(relation="part_of", related_term="film")
    store.upsert(TermRecord(term="P3HT", definition="d", category="Polymer", pages=[1], source_papers=["a"],
                            relations=[rel]))
    store.upsert(TermRecord(term="p3ht", definition="d", category="Polymer", pages=[2], source_papers=["b"],
                            relations=[rel, RelationRecord(relation="has", related_term="chain")]))
    store.upsert(TermRecord(term="PCBM", definition="d", category="Molecule", pages=[1], source_papers=["b"]))
    assert store.growth() == {"entities_new": 2, "entities_merged": 1, "relations_new": 2, "relations_merged": 1,
                              "entities_total": 2, "relations_total": 2}


def test_growth_counts_terms_loaded_from_an_earlier_bundle_as_merged(tmp_path):
    first = TermStore(str(tmp_path / "terms.json"))
    first.upsert(TermRecord(term="P3HT", definition="d", category="Polymer", pages=[1], source_papers=["a"]))
    first.save()
    again = TermStore(str(tmp_path / "terms.json"))
    again.upsert(TermRecord(term="P3HT", definition="d", category="Polymer", pages=[1], source_papers=["z"]))
    assert again.growth()["entities_merged"] == 1 and again.growth()["entities_new"] == 0


class _Status(Exception):
    def __init__(self, status: int) -> None:
        super().__init__("provider said: secret-prompt-text 203.0.113.9")
        self.status_code = status


class RateLimitError(Exception):
    pass


@pytest.mark.parametrize("exc, expected", [
    (TimeoutError("LLM call exceeded its 300s deadline"), "model call timed out after 300 s (cborg, model m-1)"),
    (_Status(429), "cborg rate limit reached (HTTP 429); too many requests (cborg, model m-1)"),
    (RateLimitError("x"), "cborg rate limit reached (HTTP 429); too many requests (cborg, model m-1)"),
    (_Status(401), "cborg rejected the credentials (authentication failed); check the configured key (cborg, model m-1)"),
    (_Status(503), "cborg reported a server error (HTTP 503) (cborg, model m-1)"),
    (ConnectionError("203.0.113.9 refused"), "could not reach cborg (cborg, model m-1)"),
    (ValueError("x"), "model call failed (ValueError) (cborg, model m-1)"),
])
def test_llm_failures_are_plain_words_without_provider_text(exc, expected):
    text = plain_call_failure(exc, provider="cborg", model="m-1")
    assert text == expected and "203.0.113.9" not in text and "secret-prompt-text" not in text


def test_ip_not_authorized_is_the_fixed_sentence_with_no_address():
    class Refused(Exception):
        status_code = 403
        body = {"error": {"type": "ip_not_authorized", "message": "address 203.0.113.9 not allowed"}}

    for exc in (Refused(), CborgIpNotAuthorized()):
        text = plain_call_failure(exc, provider="cborg", model="m-1")
        assert text == IP_NOT_AUTHORIZED_LOG_TEXT == "CBORG refused this machine's address; check network egress"
    # not CBORG: a 403 from Ollama is an authentication-style failure, not the CBORG sentence
    assert "CBORG" not in plain_call_failure(_Status(403), provider="ollama", model="llama")


def test_timeout_uses_the_given_limit_and_provider_labels():
    assert plain_call_failure(TimeoutError(), provider="ollama", model="q", timeout_s=45) == \
        "model call timed out after 45 s (ollama, model q)"
    assert provider_label("cborg-openai") == "cborg" and provider_label("ollama") == "ollama"


def test_redact_paths_keeps_urls_and_names():
    assert redact_paths("Loaded 3 from /srv/f2w/kgs/run1/terms.json now") == "Loaded 3 from terms.json now"
    assert redact_paths("see https://arxiv.org/abs/2401.00001 and 3/4 pages") == "see https://arxiv.org/abs/2401.00001 and 3/4 pages"


def test_extractor_logs_provider_model_and_plain_failures(tmp_path, caplog):
    from app.modules.term_extractor.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)  # no network, no client: only the logging of process_page
    orch.backend, orch.model, orch.refusal, orch.events = "cborg", "m-1", None, None
    orch.cancel_check = None

    class Boom:
        def invoke(self, *_a, **_k):
            raise TimeoutError("hung")

    orch.graph = Boom()
    orch.schema_helper = type("S", (), {"get_schema_context_for_llm": staticmethod(lambda: "ctx")})()
    orch._tool_state = None
    orch.store = TermStore(str(tmp_path / "t.json"))
    orch._uses_cborg_admission = lambda: False
    orch._fallback_json_extract = lambda *a, **k: False
    with caplog.at_level(logging.INFO, logger="app.modules.term_extractor.orchestrator"):
        orch.process_page("word " * 40, "paper.pdf", 0)
    lines = [r.getMessage() for r in caplog.records]
    assert "paper.pdf page 1: asking cborg model m-1" in lines
    assert "paper.pdf page 1: model call timed out after 300 s (cborg, model m-1)" in lines
    assert json.dumps(lines).count("hung") == 0
