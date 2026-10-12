"""Record the T18A legacy UI baseline fixtures from the in-process apps.

Run from a clean export of the frozen baseline (never from a checkout that
has a .env), for example:

    env -i PATH=<venv>/bin:/usr/bin:/bin HOME=<tmp> TMPDIR=<tmp> \
        <venv>/bin/python tests/contracts/legacy_ui/record_fixtures.py

Writes:
    tests/contracts/legacy_ui/fixtures/openapi_ui.json
    tests/contracts/legacy_ui/fixtures/openapi_model_api.json
    tests/contracts/legacy_ui/fixtures/routes.json
    tests/contracts/legacy_ui/fixtures/http/<id>.json      (request/response pairs)
    tests/contracts/legacy_ui/fixtures/sse/<id>.sse        (raw sanitized SSE bytes)
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402  (MonkeyPatch only)

import _legacy_ui_harness as H  # noqa: E402


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")


def _routes(app):
    out = []
    for route in app.routes:
        methods = sorted(getattr(route, "methods", None) or [])
        out.append({"path": route.path, "methods": methods, "name": getattr(route, "name", None)})
    return out


def main() -> int:
    from fastapi.testclient import TestClient

    fixtures = H.FIXTURE_DIR
    for sub in ("http", "sse"):
        shutil.rmtree(fixtures / sub, ignore_errors=True)
        (fixtures / sub).mkdir(parents=True, exist_ok=True)

    work_root = Path(tempfile.mkdtemp(prefix="t18a-record-"))
    index = []
    with pytest.MonkeyPatch.context() as mp:
        ctx = H.install(mp, work_root / "ws", REPO_ROOT)

        # ---------------- UI app: OpenAPI + routes + flow fixtures
        for flow_name, flow in H.flows().items():
            app = H.make_ui_app(ctx, kg_mode=flow["kg_mode"])
            _dump(fixtures / "openapi_ui.json", app.openapi())
            ui_routes = _routes(app)
            client = H.client_for(app)
            for fid, method, path, kwargs, notes in flow["steps"]:
                resp = H.perform(client, ctx, method, path, kwargs)
                record = {
                    "id": fid,
                    "flow": flow_name,
                    "kg_mode": flow["kg_mode"],
                    "notes": notes,
                    "request": {"method": method, "path": path, **H.sanitize_obj(kwargs, ctx)},
                    "response": H.response_record(resp, ctx),
                }
                if resp.headers.get("content-type", "").startswith("text/event-stream"):
                    raw = H.sanitize_text(resp.content.decode("utf-8"), ctx).encode("utf-8")
                    sse_path = fixtures / "sse" / f"{fid}.sse"
                    sse_path.write_bytes(raw)
                    record["response"]["sse_file"] = f"sse/{fid}.sse"
                    record["response"]["sse_sha256"] = hashlib.sha256(raw).hexdigest()
                    record["response"]["sse_bytes"] = len(raw)
                _dump(fixtures / "http" / f"{fid}.json", record)
                index.append({"id": fid, "route": f"{method} {path}", "status": resp.status_code,
                              "terminal_status": _terminal_status(record)})
                print(f"{resp.status_code} {method} {path} -> {fid} {_terminal_status(record) or ''}")

        # ---------------- model-compatible app
        model_app = H.make_model_app(mp, ctx)
        _dump(fixtures / "openapi_model_api.json", model_app.openapi())
        model_routes = _routes(model_app)
        model_client = H.client_for(model_app)
        for fid, method, path, kwargs, notes in H.MODEL_STEPS:
            resp = H.perform(model_client, ctx, method, path, kwargs)
            record = {
                "id": fid,
                "flow": "model_api",
                "notes": notes,
                "request": {"method": method, "path": path, **H.sanitize_obj(kwargs, ctx)},
                "response": H.response_record(resp, ctx),
            }
            _dump(fixtures / "http" / f"{fid}.json", record)
            index.append({"id": fid, "route": f"{method} {path}", "status": resp.status_code,
                          "terminal_status": None})
            print(f"{resp.status_code} {method} {path} -> {fid}")

        _dump(fixtures / "routes.json", {"ui_app": ui_routes, "model_app": model_routes})
        _dump(fixtures / "index.json", {"cases": index, "fake_call_counts": dict(sorted(ctx.calls.items()))})
    shutil.rmtree(work_root, ignore_errors=True)
    return 0


def _terminal_status(record):
    resp = record["response"]
    events = resp.get("sse_events")
    if events:
        return f"{events[-1]['event']}:{events[-1]['data'].get('status')}"
    body = resp.get("json")
    if isinstance(body, dict):
        return body.get("status")
    return None


if __name__ == "__main__":
    raise SystemExit(main())
