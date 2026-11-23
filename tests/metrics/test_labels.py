"""Label safety: nothing a person or a source typed can become a label value."""

from __future__ import annotations

import pytest

from app.modules.metrics import labels

HOSTILE = [
    "10.1000/xyz123", "doi:10.1038/s41586-020-2649-2", "https://example.org/a?b=c", "/v1/papers/10.1000/xyz?q=secret",
    "123e4567-e89b-12d3-a456-426614174000", "report final (v2).pdf", "node:abc123", "node-id-42",
    "A Study of Everything: Results from the Advanced Light Source", "alice@example.org", "", " GET ",
]


@pytest.mark.parametrize("value", HOSTILE)
def test_enum_maps_anything_outside_the_vocabulary_to_other(value: str) -> None:
    assert labels.enum(value, ("cborg", "ollama")) == "other"
    assert labels.http_route(value, frozenset({"/v1/jobs/{job_id}"})) == "unmatched"
    assert labels.http_method(value) == "other"


def test_enum_accepts_members_only_exactly() -> None:
    assert labels.enum("cborg", ("cborg", "ollama")) == "cborg"
    assert labels.enum("CBORG", ("cborg", "ollama")) == "other"
    for bad in (None, 3, b"cborg", ["cborg"]):
        assert labels.enum(bad, ("cborg",)) == "other"


def test_route_is_a_declared_template_or_unmatched() -> None:
    known = frozenset({"/v1/jobs/{job_id}"})
    assert labels.http_route("/v1/jobs/{job_id}", known) == "/v1/jobs/{job_id}"
    assert labels.http_route("/v1/jobs/123e4567-e89b-12d3-a456-426614174000", known) == "unmatched"
    assert labels.http_route(None, known) == "unmatched"
    assert labels.http_route("/v1/jobs/{job_id}", None) == "unmatched"


def test_method_and_status_class() -> None:
    assert labels.http_method("get") == "GET" and labels.http_method("PROPFIND") == "other"
    assert [labels.status_class(s) for s in (200, 301, 404, 503)] == ["2xx", "3xx", "4xx", "5xx"]
    assert labels.status_class(True) == "other" and labels.status_class(999) == "other"
    assert labels.status_class("200") == "other"
