"""O-U2 judge-model picker: ``/v1/validation/judge-model`` on ``:local`` and ALS, live model list.

The model list is a fake lister (no network, no key). The routes are deployment-wide and
need ``settings.global``: every ``:local`` caller, ALS admins only.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from _v1_support import build_als, build_local

from app.modules import model_catalog
from app.modules.model_catalog import CachedModelCatalog, ModelListing, list_cborg_models_live
from app.modules.validation_settings import (
    JudgeModelService,
    JudgeModelStore,
    judge_settings_path,
    read_judge_model,
)

LIVE = ("claude-sonnet", "gpt-big", "lbl/cborg-chat")


class FakeLister:
    def __init__(self, listing):
        self.listing = listing
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.listing


def _service(tmp_path, listing=None):
    lister = FakeLister(listing or ModelListing("cborg", "live", LIVE))
    store = JudgeModelStore(judge_settings_path(tmp_path / "wd"))
    return JudgeModelService(store, CachedModelCatalog(lister)), lister


@pytest.fixture
def local_judge(monkeypatch, tmp_path):
    svc, lister = _service(tmp_path)
    return build_local(monkeypatch, tmp_path, judge_settings=svc), svc, lister


@pytest.fixture
def als_judge(monkeypatch, tmp_path):
    svc, lister = _service(tmp_path)
    return build_als(monkeypatch, tmp_path, judge_settings=svc), svc, lister


def test_local_operator_sees_the_live_list_and_no_judge_by_default(local_judge):
    app, _, lister = local_judge
    body = app.client().get("/v1/validation/judge-model").json()
    assert body["judge_model"] is None and body["scope"] == "deployment"
    assert body["available_models"] == list(LIVE)
    assert body["models_source"] == "live" and lister.calls == 1
    assert app.port.calls == 0  # :local makes no identity call


def test_local_operator_sets_a_listed_judge_and_it_persists_for_the_runner(local_judge, tmp_path):
    app, svc, _ = local_judge
    resp = app.client().put("/v1/validation/judge-model", json={"judge_model": "gpt-big"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["judge_model"] == "gpt-big" and resp.json()["judge_model_available"] is True
    assert read_judge_model(tmp_path / "wd") == "gpt-big"
    stored = json.loads(judge_settings_path(tmp_path / "wd").read_text())
    assert stored["updated_by"] == "local-operator" and stored["scope"] == "deployment"


def test_a_model_not_in_the_current_list_is_refused(local_judge, tmp_path):
    app, _, _ = local_judge
    resp = app.client().put("/v1/validation/judge-model", json={"judge_model": "made-up-model"})
    assert resp.status_code == 422 and resp.json()["code"] == "invalid_input"
    assert read_judge_model(tmp_path / "wd") is None


def test_change_is_refused_when_the_list_is_unavailable(monkeypatch, tmp_path):
    svc, _ = _service(tmp_path, ModelListing("cborg", "unavailable", error="no CBORG API key configured"))
    app = build_local(monkeypatch, tmp_path, judge_settings=svc)
    view = app.client().get("/v1/validation/judge-model").json()
    assert view["models_source"] == "unavailable" and view["available_models"] == []
    resp = app.client().put("/v1/validation/judge-model", json={"judge_model": "gpt-big"})
    assert resp.status_code == 503 and resp.json()["code"] == "unavailable"
    assert read_judge_model(tmp_path / "wd") is None


def test_clearing_needs_no_listing(local_judge, tmp_path):
    app, _, _ = local_judge
    app.client().put("/v1/validation/judge-model", json={"judge_model": "gpt-big"})
    resp = app.client().put("/v1/validation/judge-model", json={"judge_model": None})
    assert resp.status_code == 200 and resp.json()["judge_model"] is None
    assert read_judge_model(tmp_path / "wd") is None


def test_unknown_body_fields_are_refused(local_judge):
    app, _, _ = local_judge
    resp = app.client().put("/v1/validation/judge-model", json={"judge_model": "gpt-big", "scope": "personal"})
    assert resp.status_code == 422


def test_als_admin_only_ordinary_user_and_anonymous_refused(als_judge, tmp_path):
    app, _, _ = als_judge
    assert app.client("anon").get("/v1/validation/judge-model").status_code == 401
    user = app.client("user")
    assert user.get("/v1/validation/judge-model").status_code == 403
    assert user.put("/v1/validation/judge-model", json={"judge_model": "gpt-big"}).status_code == 403
    assert read_judge_model(tmp_path / "wd") is None
    admin = app.client("admin")
    assert admin.get("/v1/validation/judge-model").status_code == 200
    resp = admin.put("/v1/validation/judge-model", json={"judge_model": "gpt-big"})
    assert resp.status_code == 200, resp.text
    # deployment-wide: one value, the same for every admin; not stored per principal
    assert read_judge_model(tmp_path / "wd") == "gpt-big"


def test_settings_routes_are_unchanged_and_carry_no_judge_field(local_judge):
    app, _, _ = local_judge
    body = app.client().get("/settings").json()
    assert "judge_model" not in body


# ------------------------------------------------------------------ live listing (fake client)


class FakeModels:
    def __init__(self, ids=None, exc=None):
        self.ids, self.exc, self.calls = ids or [], exc, []

    def list(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(data=[SimpleNamespace(id=i) for i in self.ids])


def test_live_listing_reads_the_endpoint_under_the_admission_slot():
    held = []

    class Slot:
        def __enter__(self):
            held.append("in")

        def __exit__(self, *a):
            held.append("out")

    models = FakeModels(["b", "a", "a", " "])
    listing = list_cborg_models_live(client=SimpleNamespace(models=models), slot=Slot)
    assert listing.source == "live" and listing.models == ("a", "b")
    assert held == ["in", "out"] and models.calls == [{"timeout": 15.0}]


def test_no_key_is_unavailable_and_never_a_fallback_list(monkeypatch):
    monkeypatch.delenv("CBORG_API_KEY", raising=False)
    listing = list_cborg_models_live()
    assert listing.source == "unavailable" and listing.models == ()
    assert "CBORG_API_KEY" in listing.error


def test_provider_error_is_unavailable_and_the_key_is_scrubbed():
    secret = "sk-abcdefghijklmnop"
    models = FakeModels(exc=RuntimeError(f"401 for key {secret}"))
    listing = list_cborg_models_live(client=SimpleNamespace(models=models), slot=_null_slot)
    assert listing.source == "unavailable" and secret not in (listing.error or "")


def test_cache_reuses_a_live_listing_and_retries_unavailable_sooner():
    now = [0.0]
    lister = FakeLister(ModelListing("cborg", "live", ("a",)))
    cat = CachedModelCatalog(lister, ttl=100, failure_ttl=5, clock=lambda: now[0])
    cat.get(), cat.get()
    assert lister.calls == 1
    now[0] = 101
    cat.get()
    assert lister.calls == 2
    cat.get(refresh=True)
    assert lister.calls == 3


def test_model_catalog_module_has_no_hardcoded_model_ids():
    import inspect

    src = inspect.getsource(model_catalog)
    for name in ("gpt-", "claude", "gemini", "llama", "cborg-chat"):
        assert name not in src, name


class _null_slot:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def test_no_application_path_imports_or_names_the_binary_suite():
    """H3 B: the app stores the judge id only; it never reads or waits on a binary verdict."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = [str(p.relative_to(root)) for base in ("app", "scripts", "docker", ".github")
                 for p in (root / base).rglob("*") if p.is_file() and p.suffix in (".py", ".yml", ".yaml", ".sh")
                 and ("evals.binary" in p.read_text(errors="ignore") or "evals/binary" in p.read_text(errors="ignore"))]
    assert offenders == []
