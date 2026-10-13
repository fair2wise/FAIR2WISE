"""T18B: opaque ``f2w://`` path ids go out in responses and come back in requests safely."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _wiring_support import UPLOAD  # noqa: E402

from app.api.legacy_ui import OpaquePaths  # noqa: E402

UPLOAD_ID = "f2w://workdir/uploads/wiring_upload.json"


@pytest.mark.parametrize("deployment,who", [("local", "anon"), ("als", "admin")])
def test_uploaded_graph_round_trip_via_the_opaque_id(request, deployment, who):
    dep = request.getfixturevalue(deployment)
    client = dep.client(who)
    up = client.post("/graph/upload", json=UPLOAD)
    assert up.status_code == 200
    body = up.json()
    assert body["graph_path"] == body["graph"]["source_path"] == UPLOAD_ID
    assert str(dep.ctx.workspace) not in up.text
    node = client.get("/graph/node/matkg:U1", params={"json_graph_path": body["graph_path"]})
    assert node.status_code == 200, node.text
    assert node.json()["id"] == "matkg:U1" and node.json()["label"] == "Uploaded node"


def test_als_user_reads_an_uploaded_graph_by_id(als):
    als.client("admin").post("/graph/upload", json=UPLOAD)
    node = als.client("user").get("/graph/node/matkg:U1", params={"json_graph_path": UPLOAD_ID})
    assert node.status_code == 200 and node.json()["id"] == "matkg:U1"  # O-A5: JSON graph reads are open


def test_absolute_upload_path_is_still_accepted_on_local(local):
    client = local.client("anon")
    client.post("/graph/upload", json=UPLOAD)
    absolute = str(local.workdir / "uploads" / "wiring_upload.json")
    assert client.get("/graph/node/matkg:U1", params={"json_graph_path": absolute}).status_code == 200


@pytest.mark.parametrize("bad", [
    "f2w://workdir/../../../../etc/passwd", "f2w://workdir/uploads/../../storage/kg/x.json",
    "f2w://path/wiring_upload.json", "f2w://home/.ssh/id_rsa", "f2w://", "f2w://workdir\\..\\x.json",
])
def test_opaque_ids_resolve_only_inside_allowed_roots(local, bad):
    local.client("anon").post("/graph/upload", json=UPLOAD)
    resp = local.client("anon").get("/graph/node/matkg:U1", params={"json_graph_path": bad})
    assert resp.status_code == 400, resp.text
    assert "/etc" not in resp.text and str(local.ctx.workspace) not in resp.text


def test_opaque_id_inside_workdir_but_outside_graph_roots_is_refused(local):
    """Decoding only maps the id; the service still requires storage/kg or uploads."""
    client = local.client("anon")
    resp = client.put("/settings", json={"graph_source": "json", "json_graph_path": "f2w://workdir/kg.json"})
    assert resp.status_code == 400 and "storage/kg" in resp.json()["detail"]


def test_settings_and_chat_accept_opaque_graph_ids(local):
    client = local.client("anon")
    client.post("/graph/upload", json=UPLOAD)
    resp = client.put("/settings", json={"graph_source": "json", "json_graph_path": UPLOAD_ID,
                                         "json_graph_paths": [UPLOAD_ID]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["json_graph_path"] == UPLOAD_ID and resp.json()["json_graph_paths"] == [UPLOAD_ID]
    graph = client.get("/graph").json()
    assert graph["source_path"] == UPLOAD_ID and [n["id"] for n in graph["nodes"]] == ["matkg:U1"]
    chat = client.post("/chat", json={"message": "What is Term A?", "session_id": "s-o", "graph_source": "json",
                                      "json_graph_paths": [UPLOAD_ID]})
    assert chat.status_code == 200 and str(local.ctx.workspace) not in chat.text
    assert client.post("/chat", json={"message": "x", "json_graph_path": "f2w://path/a.json"}).status_code == 400


def test_opaque_paths_unit(tmp_path):
    work = tmp_path / "run"
    (work / "uploads").mkdir(parents=True)
    paths = OpaquePaths(workdir=work, workspace=tmp_path)
    assert paths.roots.redact(str(work / "uploads" / "g.json")) == "f2w://workdir/uploads/g.json"
    assert paths.roots.redact(str(tmp_path / "storage" / "kg" / "a.json")) == "f2w://workspace/storage/kg/a.json"
    assert paths.decode("f2w://workdir/uploads/g.json") == str((work / "uploads" / "g.json").resolve())
    assert paths.decode("storage/kg/a.json") == "storage/kg/a.json"
    assert paths.decode(None) is None and paths.decode_all(None) is None
    for bad in ("f2w://nope/x", "f2w://workspace/../x", "f2w://path/x"):
        with pytest.raises(ValueError):
            paths.decode(bad)
