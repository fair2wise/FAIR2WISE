"""T08A: Tiled read client - target validation, runtime credentials, error mapping (no server)."""
import socket

import pytest
import requests
from conftest import make_ctx

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.deployment.tiled_config import ALS_PROFILE_URLS, ExternalTiledConfig
from app.modules.graph.backends.tiled import TiledGraphBackend
from app.modules.graph.backends.tiled_client import TiledGraphClient


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_als_hosts_are_never_rewritten_to_localhost():
    for profile in ("local", "test"):
        with pytest.raises(ContractError) as err:
            TiledGraphClient("https://tiled.als.lbl.gov", profile=profile)
        assert err.value.code is ErrorCode.INVALID_INPUT
    with pytest.raises(ContractError):
        TiledGraphClient("http://127.0.0.1:8765", profile="als-prod")
    with pytest.raises(ContractError):
        TiledGraphClient("http://example.org", profile="local")
    client = TiledGraphClient(ALS_PROFILE_URLS["als-dev"] + "/api/graphql", profile="als-dev")
    assert client.endpoint == ALS_PROFILE_URLS["als-dev"] + "/api/graphql"


def test_credentials_come_from_runtime_config_not_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TILED_API_KEY", "envkey")
    monkeypatch.setenv("TILED_URI", "https://tiled.als.lbl.gov")
    secret = tmp_path / "writer.key"
    secret.write_text("filekey123\n", encoding="utf-8")
    cfg = ExternalTiledConfig(profile="test", url="http://127.0.0.1:9", writer_secret_file=secret)
    session = FakeSession(FakeResp(200, {"data": {"entity": None}}))
    client = TiledGraphClient.from_external(cfg, session=session)
    client.query("query { entity(id: \"x\") { id } }")
    url, kw = session.calls[0]
    assert url == "http://127.0.0.1:9/api/graphql" and kw["headers"] == {"Authorization": "Apikey filekey123"}
    assert "filekey123" not in repr(client)
    secret.unlink()
    with pytest.raises(ContractError) as err:
        client.query("query { entity(id: \"x\") { id } }")
    assert err.value.code is ErrorCode.UNAVAILABLE
    anon = TiledGraphClient("http://127.0.0.1:9", profile="test", session=FakeSession(FakeResp(200, {"data": {}})))
    anon.query("{ __typename }")
    assert anon._session.calls[0][1]["headers"] == {}


@pytest.mark.parametrize("response,code", [
    (requests.ConnectionError("x"), ErrorCode.UNAVAILABLE),
    (requests.Timeout("x"), ErrorCode.UNAVAILABLE),
    (FakeResp(503, {}), ErrorCode.UNAVAILABLE),
    (FakeResp(404, {}), ErrorCode.UNAVAILABLE),
    (FakeResp(401, {"detail": "Invalid API key"}), ErrorCode.DENIED),
    (FakeResp(200, ValueError("nope")), ErrorCode.UNAVAILABLE),
    (FakeResp(200, {"errors": [{"message": "Not permitted"}]}), ErrorCode.DENIED),
    (FakeResp(200, {"errors": [{"message": "'anonymous' exceeds maximum operation depth of 10"}]}),
     ErrorCode.INVALID_INPUT),
    (FakeResp(200, {"errors": [{"message": "boom"}]}), ErrorCode.INTERNAL),
    (FakeResp(200, {"data": None}), ErrorCode.UNAVAILABLE),
])
def test_error_mapping_never_returns_empty(response, code):
    client = TiledGraphClient("http://127.0.0.1:9", profile="test", session=FakeSession(response))
    with pytest.raises(ContractError) as err:
        client.query("{ entities { id } }")
    assert err.value.code is code


def test_mutations_are_refused_before_sending():
    session = FakeSession()
    client = TiledGraphClient("http://127.0.0.1:9", profile="test", session=session)
    for doc in ("mutation { deleteEntity(id: \"x\") }", "  # c\n MUTATION m { x }"):
        with pytest.raises(ContractError):
            client.query(doc)
    assert session.calls == []


def test_closed_port_is_unavailable_not_empty():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    backend = TiledGraphBackend(TiledGraphClient(f"http://127.0.0.1:{port}", profile="test", timeout=2))
    with pytest.raises(ContractError) as err:
        backend.find_entities(make_ctx(), "rsoxs_v4", "x", limit=5)
    assert err.value.code is ErrorCode.UNAVAILABLE and err.value.retryable
