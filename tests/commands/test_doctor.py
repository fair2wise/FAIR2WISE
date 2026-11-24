"""``f2w-doctor``: every state, the exit codes, the flags and the scrub (no network: the HTTP layer is injected)."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest

from app.modules.commands import doctor
from app.modules.commands.doctor import Context
from app.modules.literature.safe_fetch import NetworkPolicy

FAKE_KEY = "sk-FAKEKEY0123456789abcdef"
FAKE_HOST = "internal-gateway.example.invalid"
FAKE_ADDR = "203.0.113.77"
Handler = Callable[[httpx.Request], httpx.Response]


def _cborg_factory(handler: Handler, seen: list[httpx.Request] | None = None) -> doctor.CborgClientFactory:
    def build(key: str, base_url: str) -> Any:
        def wrapped(request: httpx.Request) -> httpx.Response:
            if seen is not None:
                seen.append(request)
            return handler(request)

        return openai.OpenAI(api_key=key, base_url=base_url, max_retries=0,
                             http_client=httpx.Client(transport=httpx.MockTransport(wrapped)))

    return build


def _ctx(handler: Handler | None = None, *, env: dict[str, str] | None = None, web: Handler | None = None,
         seen: list[httpx.Request] | None = None, **extra: Any) -> Context:
    ctx = Context(environ=dict(env or {}), policy=NetworkPolicy(resolver=lambda host: ["93.184.216.34"], trust_proxy=False),
                  **extra)
    if handler is not None:
        ctx.cborg_client = _cborg_factory(handler, seen)
    if web is not None:
        ctx.http_client = httpx.Client(transport=httpx.MockTransport(web))
    return ctx


def _models(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"object": "list", "data": [{"id": "lbl/cborg-chat", "object": "model"}]})


def _raiser(exc: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


KEYED = {"CBORG_API_KEY": FAKE_KEY}


# ------------------------------------------------------------------------------------------------ cborg

def test_cborg_ok_makes_one_keyed_models_call() -> None:
    seen: list[httpx.Request] = []
    state = doctor.check_cborg(_ctx(_models, env={**KEYED, "CBORG_BASE_URL": f"https://{FAKE_HOST}/v1"}, seen=seen))
    assert state == "ok"
    assert len(seen) == 1
    assert seen[0].url.path.endswith("/models") and seen[0].method == "GET"
    assert seen[0].headers["authorization"] == f"Bearer {FAKE_KEY}"


def test_cborg_without_a_key_is_no_key_and_asks_nothing() -> None:
    seen: list[httpx.Request] = []
    assert doctor.check_cborg(_ctx(_models, env={}, seen=seen)) == "no_key"
    assert doctor.check_cborg(_ctx(_models, env={"CBORG_API_KEY": "  "}, seen=seen)) == "no_key"
    assert seen == []


def test_cborg_ip_not_authorized() -> None:
    body = {"error": {"type": "ip_not_authorized", "message": f"IP address {FAKE_ADDR} is not authorized"}}
    assert doctor.check_cborg(_ctx(lambda r: httpx.Response(403, json=body), env=KEYED)) == "ip_not_authorized"


@pytest.mark.parametrize("status", [401, 403])
def test_cborg_auth_failed(status: int) -> None:
    body = {"error": {"type": "auth_error", "message": "bad key"}}
    assert doctor.check_cborg(_ctx(lambda r: httpx.Response(status, json=body), env=KEYED)) == "auth_failed"


def test_cborg_unreachable_timeout_and_error() -> None:
    assert doctor.check_cborg(_ctx(_raiser(httpx.ConnectError("refused")), env=KEYED)) == "unreachable"
    assert doctor.check_cborg(_ctx(_raiser(httpx.ProxyError("403 Forbidden")), env=KEYED)) == "unreachable"
    assert doctor.check_cborg(_ctx(_raiser(httpx.ReadTimeout("slow")), env=KEYED)) == "timeout"
    assert doctor.check_cborg(_ctx(lambda r: httpx.Response(500, json={"error": "boom"}), env=KEYED)) == "error"


# -------------------------------------------------------------------------------------- openalex, arxiv

def test_openalex_ok_sends_the_key_in_the_header_only() -> None:
    seen: list[httpx.Request] = []

    def web(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": []})

    assert doctor.check_openalex(_ctx(env={"OPENALEX_API_KEY": FAKE_KEY}, web=web)) == "ok"
    assert seen[0].headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in str(seen[0].url)
    assert seen[0].url.host == "api.openalex.org"


@pytest.mark.parametrize(("env", "status", "expected"), [
    ({}, 403, "no_key"), ({}, 401, "no_key"), ({"OPENALEX_API_KEY": FAKE_KEY}, 401, "rejected"),
    ({"OPENALEX_API_KEY": FAKE_KEY}, 429, "rate_limited"), ({}, 407, "blocked_by_proxy"), ({}, 500, "error"),
])
def test_openalex_status_states(env: dict[str, str], status: int, expected: str) -> None:
    assert doctor.check_openalex(_ctx(env=env, web=lambda r: httpx.Response(status))) == expected


@pytest.mark.parametrize(("exc", "expected"), [
    (httpx.ConnectTimeout("t"), "timeout"), (httpx.ReadTimeout("t"), "timeout"),
    (httpx.ProxyError("403 Forbidden"), "blocked_by_proxy"), (httpx.ConnectError("refused"), "unreachable"),
])
def test_openalex_and_arxiv_transport_states(exc: Exception, expected: str) -> None:
    assert doctor.check_openalex(_ctx(web=_raiser(exc))) == expected
    assert doctor.check_arxiv(_ctx(web=_raiser(exc))) == expected


def test_arxiv_ok_error_and_policy_block() -> None:
    seen: list[httpx.Request] = []

    def web(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="<feed/>")

    assert doctor.check_arxiv(_ctx(web=web)) == "ok"
    assert seen[0].url.host == "export.arxiv.org" and "authorization" not in seen[0].headers
    assert doctor.check_arxiv(_ctx(web=lambda r: httpx.Response(403))) == "error"
    private = _ctx(web=web)
    private.policy = NetworkPolicy(resolver=lambda host: ["10.0.0.5"], trust_proxy=False)
    assert doctor.check_arxiv(private) == "blocked_by_policy"


# ----------------------------------------------------------------------------------------------- ollama

def _fetch(status: int, body: bytes) -> Callable[[str, float, int], tuple[int, bytes]]:
    return lambda url, timeout, max_bytes: (status, body)


def _fetch_raising(exc: Exception) -> Callable[[str, float, int], tuple[int, bytes]]:
    def fetch(url: str, timeout: float, max_bytes: int) -> tuple[int, bytes]:
        raise exc

    return fetch


def test_ollama_states() -> None:
    env = {"OLLAMA_URL": f"http://{FAKE_HOST}:11434"}
    tags = json.dumps({"models": [{"name": "llama3:8b"}]}).encode()
    assert doctor.check_ollama(_ctx(env={})) == "skipped"
    assert doctor.check_ollama(_ctx(env=env, ollama_fetch=_fetch(200, tags))) == "ok"
    assert doctor.check_ollama(_ctx(env=env, ollama_fetch=_fetch(200, b'{"models": []}'))) == "ok"
    assert doctor.check_ollama(_ctx(env=env, ollama_fetch=_fetch_raising(httpx.ConnectError("no")))) == "unreachable"
    assert doctor.check_ollama(_ctx(env=env, ollama_fetch=_fetch_raising(httpx.ReadTimeout("slow")))) == "timeout"
    assert doctor.check_ollama(_ctx(env=env, ollama_fetch=_fetch(500, b""))) == "error"
    assert doctor.check_ollama(_ctx(env=env, ollama_fetch=_fetch(200, b"not json"))) == "error"


# ------------------------------------------------------------------------------------------ directories

@pytest.mark.parametrize(("check", "var"), [(doctor.check_library, "F2W_LIBRARY_DIR"), (doctor.check_state, "F2W_WORKDIR")])
def test_directory_states(check: Callable[[Context], str], var: str, tmp_path: Path) -> None:
    assert check(_ctx(env={var: str(tmp_path / "nope")})) == "missing"
    assert check(_ctx(env={var: str(tmp_path)})) == "ok"
    assert list(tmp_path.iterdir()) == []  # the probe file is gone
    if os.geteuid() != 0:
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            assert check(_ctx(env={var: str(locked)})) == "not_writable"
        finally:
            locked.chmod(0o700)


def _profile(tmp_path: Path, **blocks: dict[str, str]) -> Path:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(blocks), encoding="utf-8")
    return path


def test_only_the_profile_file_present_library_and_state_come_from_it(tmp_path: Path,
                                                                      capsys: pytest.CaptureFixture[str]) -> None:
    lib, state = tmp_path / "lib", tmp_path / "state"
    lib.mkdir()
    state.mkdir()
    profile = _profile(tmp_path, env={"F2W_LIBRARY_DIR": str(lib), "F2W_WORKDIR": str(state)})
    ctx = _ctx(env={"F2W_PROFILE_FILE": str(profile)})
    ctx.environ = doctor.profile_environ(ctx.environ)
    assert doctor.check_library(ctx) == "ok" and doctor.check_state(ctx) == "ok"
    # through main(): environ holds only the profile pointer, no F2W_LIBRARY_DIR / F2W_WORKDIR
    assert doctor.main(["--only", "library,state"], environ={"F2W_PROFILE_FILE": str(profile)}) == 0
    assert capsys.readouterr().out.splitlines() == ["library: ok", "state: ok"]


def test_profile_locked_env_counts_and_a_set_variable_wins(tmp_path: Path) -> None:
    profile_dir, own = tmp_path / "from-profile", tmp_path / "own"
    profile_dir.mkdir()
    own.mkdir()
    profile = _profile(tmp_path, env={"F2W_LIBRARY_DIR": str(profile_dir)}, locked_env={"F2W_WORKDIR": str(profile_dir)})
    merged = doctor.profile_environ({"F2W_PROFILE_FILE": str(profile), "F2W_LIBRARY_DIR": str(own), "F2W_WORKDIR": "  "})
    assert merged["F2W_LIBRARY_DIR"] == str(own)  # the variable wins
    assert merged["F2W_WORKDIR"] == str(profile_dir)  # blank is unset


def test_a_missing_or_broken_profile_changes_nothing(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    for pointer in (str(tmp_path / "absent.json"), str(bad)):
        assert doctor.profile_environ({"F2W_PROFILE_FILE": pointer}) == {"F2W_PROFILE_FILE": pointer}
    assert doctor.profile_environ({}) == {}  # no default path unless the caller passes one


def test_unset_state_dir_checks_the_apps_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.workdir import DEFAULT_WORKDIR

    monkeypatch.chdir(tmp_path)
    assert doctor.check_state(_ctx(env={})) == "missing"  # neither the variable nor the default exists
    (tmp_path / DEFAULT_WORKDIR).mkdir(parents=True)
    assert doctor.check_state(_ctx(env={})) == "ok"
    assert doctor.check_state(_ctx(env={"F2W_WORKDIR": "  "})) == "ok"  # blank is unset, as in the app
    assert doctor.check_state(_ctx(env={"F2W_WORKDIR": str(tmp_path / "nope")})) == "missing"  # the variable wins


def test_state_default_is_the_agents_cli_default() -> None:
    from app.modules.f2w_agent.cli import build_parser
    from app.modules.workdir import DEFAULT_WORKDIR

    assert build_parser().get_default("workdir") == DEFAULT_WORKDIR


def test_library_has_no_app_default_and_relative_is_rejected(tmp_path: Path) -> None:
    assert doctor.check_library(_ctx(env={})) == "skipped"  # the app mounts nothing without the variable
    assert doctor.check_library(_ctx(env={"F2W_LIBRARY_DIR": "relative/papers"})) == "error"  # startup refuses it


def test_tiled_key_default_location_from_the_apps_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.deployment import tiled_config

    monkeypatch.setattr(tiled_config, "DEFAULT_LOCAL_DATA_DIR", str(tmp_path))
    env = {"F2W_DEPLOYMENT_PROFILE": "local"}  # no data dir: the app's local default applies
    assert doctor.check_tiled_key(_ctx(env=env)) == "missing"
    (tmp_path / "secrets").mkdir()
    key = tmp_path / "secrets" / "writer.key"
    key.write_text("x" * 32)
    key.chmod(0o600)
    assert doctor.check_tiled_key(_ctx(env=env)) == "ok"


def test_tiled_key_states(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    env = {"F2W_TILED_DATA_DIR": str(tmp_path)}
    assert doctor.check_tiled_key(_ctx(env={})) == "skipped"
    assert doctor.check_tiled_key(_ctx(env=env)) == "missing"
    key = secrets / "writer.key"
    key.write_text("x" * 32)
    key.chmod(0o644)
    assert doctor.check_tiled_key(_ctx(env=env)) == "too_open"
    key.chmod(0o600)
    assert doctor.check_tiled_key(_ctx(env=env)) == "ok"
    other = tmp_path / "other.key"
    other.write_text("y")
    other.chmod(0o600)
    assert doctor.check_tiled_key(_ctx(env={"F2W_TILED_WRITER_SECRET_FILE": str(other)})) == "ok"


# ------------------------------------------------------------------------- main: output, flags, exit codes

def _healthy(tmp_path: Path) -> Context:
    def web(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    return _ctx(_models, web=web, env={**KEYED, "OPENALEX_API_KEY": FAKE_KEY, "F2W_LIBRARY_DIR": str(tmp_path),
                                        "F2W_WORKDIR": str(tmp_path)})


def test_all_ok_prints_one_line_per_check_and_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.main([], context=_healthy(tmp_path)) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == ["cborg: ok", "openalex: ok", "arxiv: ok", "ollama: skipped", "library: ok", "state: ok",
                                "tiled_key: skipped"]
    assert err == ""


def test_any_failure_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _healthy(tmp_path)
    ctx.environ = {k: v for k, v in ctx.environ.items() if k != "CBORG_API_KEY"}
    assert doctor.main(["--only", "cborg"], context=ctx) == 1
    assert capsys.readouterr().out == "cborg: no_key\n"


def test_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.main(["--json", "--only", "cborg,library"], context=_healthy(tmp_path)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"checks": [{"name": "cborg", "state": "ok"}, {"name": "library", "state": "ok"}], "ok": True}
    ctx = _healthy(tmp_path)
    ctx.environ = {}
    assert doctor.main(["--json", "--only", "cborg,state"], context=ctx) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["checks"][0] == {"name": "cborg", "state": "no_key"}


def test_only_and_skip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.main(["--only", "state,cborg"], context=_healthy(tmp_path)) == 0
    assert capsys.readouterr().out.splitlines() == ["cborg: ok", "state: ok"]  # canonical order, not argv order
    assert doctor.main(["--skip", "openalex,arxiv,ollama,tiled_key"], context=_healthy(tmp_path)) == 0
    assert capsys.readouterr().out.splitlines() == ["cborg: ok", "library: ok", "state: ok"]
    assert doctor.main(["--only", "cborg,state", "--skip", "cborg"], context=_healthy(tmp_path)) == 0
    assert capsys.readouterr().out.splitlines() == ["state: ok"]


@pytest.mark.parametrize("argv", [["--only", "nope"], ["--skip", "cborg,nope"], ["--only", ""], ["--timeout", "0"],
                                  ["--timeout", "abc"], ["--bogus"]])
def test_usage_errors_exit_two(argv: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.main(argv, context=_healthy(tmp_path)) == 2
    assert capsys.readouterr().out == ""


def test_timeout_flag_reaches_the_checks(tmp_path: Path) -> None:
    ctx = _healthy(tmp_path)
    assert doctor.main(["--timeout", "2.5", "--only", "state"], context=ctx) == 0
    assert ctx.timeout == 2.5
    assert doctor.parse_args([]).timeout == 8.0


def test_a_crashing_check_reports_error_and_the_others_still_run(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(ctx: Context) -> str:
        raise RuntimeError(f"{FAKE_KEY} {FAKE_HOST}")

    monkeypatch.setitem(doctor._RUNNERS, "arxiv", boom)
    assert doctor.main(["--only", "arxiv,state"], context=_healthy(tmp_path)) == 1
    assert capsys.readouterr().out.splitlines() == ["arxiv: error", "state: ok"]


# ----------------------------------------------------------------------------------------------- scrub

def _leaky_everything(tmp_path: Path) -> Context:
    msg = f"connect to {FAKE_HOST} ({FAKE_ADDR}) via proxy http://user:{FAKE_KEY}@{FAKE_HOST}:3129 key {FAKE_KEY}"
    ctx = _ctx(_raiser(httpx.ConnectError(msg)), web=_raiser(httpx.ProxyError(msg)),
               env={**KEYED, "OPENALEX_API_KEY": FAKE_KEY, "CBORG_BASE_URL": f"https://{FAKE_HOST}",
                    "OLLAMA_URL": f"http://{FAKE_HOST}:11434", "F2W_LIBRARY_DIR": str(tmp_path / "secret-library-path"),
                    "F2W_WORKDIR": str(tmp_path / "secret-state-path"), "HTTPS_PROXY": f"http://{FAKE_HOST}:3129"},
               ollama_fetch=_fetch_raising(OSError(msg)))
    return ctx


@pytest.mark.parametrize("flags", [[], ["--json"]])
def test_nothing_sensitive_is_printed(flags: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.main(flags, context=_leaky_everything(tmp_path)) == 1
    out, err = capsys.readouterr()
    shown = out + err
    for secret in (FAKE_KEY, FAKE_HOST, FAKE_ADDR, "secret-library-path", "secret-state-path", str(tmp_path), "3129",
                   "http://", "https://", "Bearer"):
        assert secret not in shown, secret
    assert "cborg: unreachable" in shown or '"cborg"' in shown


def test_real_logging_output_shows_no_url_or_host(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                  caplog: pytest.LogCaptureFixture) -> None:
    import logging

    def chatty_web(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    ctx = _ctx(_models, env={**KEYED, "OLLAMA_URL": f"http://{FAKE_HOST}:11434", "F2W_LIBRARY_DIR": str(tmp_path),
                             "F2W_WORKDIR": str(tmp_path)}, web=chatty_web,
               ollama_fetch=lambda url, timeout, max_bytes: _real_httpx_get(f"http://{FAKE_HOST}:11434/api/tags"))
    logging.getLogger().addHandler(logging.StreamHandler())  # a real stderr handler, as basicConfig would add
    try:
        with caplog.at_level(logging.DEBUG):
            logging.getLogger("httpx").setLevel(logging.INFO)  # httpx logs every request at INFO
            assert doctor.main(["--only", "cborg,openalex,ollama,library,state"], context=ctx) in (0, 1)
    finally:
        for handler in [h for h in logging.getLogger().handlers if type(h) is logging.StreamHandler]:
            logging.getLogger().removeHandler(handler)
        logging.getLogger("httpx").setLevel(logging.NOTSET)
    out, err = capsys.readouterr()
    foreign = [r for r in caplog.records if r.name != "f2w.output"]  # the doctor's own lines are its output logger
    logged = " ".join(record.getMessage() for record in foreign)
    for text in (out, err, logged):
        assert FAKE_HOST not in text and "http://" not in text and "https://" not in text and "HTTP Request" not in text
    assert foreign == []
    assert err == "" and all(line.split(":")[0] in doctor.CHECKS for line in out.splitlines())


def _real_httpx_get(url: str) -> tuple[int, bytes]:
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": []}))) as client:
        response = client.get(url)  # a real httpx call: httpx itself logs "HTTP Request: GET <url>" at INFO
    return response.status_code, response.content


def test_logging_levels_are_restored_after_the_run() -> None:
    import logging

    before = (logging.getLogger().level, logging.getLogger("httpx").level)
    with doctor.quiet_logging():
        assert not logging.getLogger("httpx").isEnabledFor(logging.CRITICAL)
    assert (logging.getLogger().level, logging.getLogger("httpx").level) == before


def test_ip_refusal_body_address_is_not_printed(capsys: pytest.CaptureFixture[str]) -> None:
    body = {"error": {"type": "ip_not_authorized", "message": f"IP address {FAKE_ADDR} is not authorized"}}
    ctx = _ctx(lambda r: httpx.Response(403, json=body), env=KEYED)
    assert doctor.main(["--only", "cborg"], context=ctx) == 1
    out, err = capsys.readouterr()
    assert out == "cborg: ip_not_authorized\n" and err == ""
    assert FAKE_ADDR not in out + err


def test_states_come_from_a_fixed_vocabulary(tmp_path: Path) -> None:
    allowed = {"ok", "skipped", "ip_not_authorized", "auth_failed", "unreachable", "no_key", "timeout", "error",
               "blocked_by_proxy", "blocked_by_policy", "rejected", "rate_limited", "not_writable", "missing", "too_open"}
    for _, state in doctor.run_checks(doctor.CHECKS, _leaky_everything(tmp_path)):
        assert state in allowed
