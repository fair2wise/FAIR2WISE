"""scripts/demo/*.sh: syntax, --help, and behaviour against stub ``docker``/``curl``/``pmset``/``sw_vers`` commands.

No real Docker, network or Mac is needed: every external command is a stub in a temporary directory that is the
only entry of PATH besides the system tools. The stubs log each call (one JSON line) so the tests can assert that
``check_machine.sh`` is read-only, that ``seed.sh`` never calls the approval route without ``--i-approve``, that
``smoke.sh`` only sends GET requests and that ``reset.sh`` removes nothing before a successful backup and a typed
confirmation.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "scripts" / "demo"
SCRIPTS = ["check_machine", "install", "seed", "reset", "backup", "restore", "smoke", "keepawake"]
ALL_SH = [f"{n}.sh" for n in SCRIPTS] + ["_common.sh"]
COMMIT = "a" * 40

STUBS_PY = r'''
import json, os, subprocess, sys, time
from email.utils import formatdate

name = os.path.basename(sys.argv[1]); args = sys.argv[2:]
log = os.environ["STUB_LOG"]
with open(log, "a") as fh:
    fh.write(json.dumps([name] + args) + "\n")
state_path = os.environ.get("STUB_STATE", "")

def load():
    if state_path and os.path.exists(state_path):
        return json.load(open(state_path))
    return {"files": [], "jobs": {}, "live": {}}

def save(st):
    if state_path:
        json.dump(st, open(state_path, "w"))

def out(text="", rc=0):
    sys.stdout.write(text if text.endswith("\n") or not text else text + "\n"); sys.exit(rc)

def plan_hash(job):
    import hashlib
    return hashlib.sha256(job.encode()).hexdigest()

if name == "sw_vers":
    out("26.1" if "-productVersion" in args else "25B78")
if name == "pmset":
    if args == ["-g"]:
        out("System-wide power settings:\nCurrently in use:\n standby 1\n sleep 0 (sleep prevented by coreaudiod)\n displaysleep 10\n disksleep 10")
    if args == ["-g", "batt"]:
        out("Now drawing from 'AC Power'")
    out("unexpected pmset call", 9)
if name == "dscacheutil":
    out("name: example\nip_address: 203.0.113.9")
if name == "sleep":
    sys.exit(0)
if name == "uname":
    out({"-s": "Darwin", "-m": os.environ.get("STUB_ARCH", "arm64")}.get(args[0] if args else "-s", "Darwin"))
if name == "sysctl":
    out({"machdep.cpu.brand_string": "Apple Stub", "hw.ncpu": "10", "hw.memsize": "68719476736",
         "sysctl.proc_translated": "0"}.get(args[-1], ""))
if name == "df":
    out("Filesystem 1024-blocks Used Available Capacity iused ifree iusedpct Mounted on\n"
        "/dev/disk3s5 971350180 1 " + os.environ.get("STUB_DF_KB", "104857600") + " 5pct 1 1 1pct /System/Volumes/Data")
if name == "lsof":
    sys.exit(1)   # nothing listens
if name == "pgrep":
    sys.exit(1)
if name == "colima":
    out("colima is running" if args[:1] == ["status"] else "PROFILE STATUS ARCH CPUS MEMORY DISK\ndefault Running aarch64 4 8GiB 100GiB")
if name == "caffeinate":
    sys.exit(0)

if name == "docker":
    a = args
    if a[:1] == ["info"]:
        if "--format" in a:
            out(os.environ.get("STUB_DOCKER_INFO", "4 8589934592 aarch64 linux"))
        out("Server: stub")
    if a[:1] == ["version"]:
        out("27.0.0")
    if a[:1] == ["context"]:
        out("colima")
    if a[:2] == ["system", "df"]:
        out("TYPE TOTAL ACTIVE SIZE RECLAIMABLE\nImages 1 1 1GB 0B")
    if a[:1] == ["ps"]:
        out("")
    if a[:2] in (["image", "ls"], ["volume", "ls"]):
        out("")
    if a[:2] == ["image", "inspect"]:
        sys.exit(0 if os.environ.get("STUB_IMAGE_PRESENT", "1") == "1" else 1)
    if a[:2] == ["volume", "inspect"]:
        sys.exit(0 if os.environ.get("STUB_VOLUMES_EXIST", "0") == "1" else 1)
    if a[:2] in (["volume", "create"], ["volume", "rm"]):
        out(a[-1])
    if a[:1] == ["load"]:
        out("Loaded image: f2w:local")
    if a[:1] == ["inspect"]:
        fmt = a[a.index("-f") + 1] if "-f" in a else ""
        if "Config.Labels" in fmt:
            out(os.environ.get("STUB_LABEL", ""))
        if "Config.Image" in fmt:
            out("f2w:local")
        if "State.Running" in fmt and "Health" not in fmt:
            out(os.environ.get("STUB_RUNNING", "true"))
        if "State.Status" in fmt:
            out("running / health healthy")
        if "Architecture" in fmt:
            out("arm64")
        sys.exit(0)
    if a[:1] in (["stop"], ["start"], ["rm"], ["cp"]):
        sys.exit(0)
    if a[:1] == ["run"]:
        for i, tok in enumerate(a):
            if tok == "-v" and a[i + 1].endswith(":/b"):
                d = a[i + 1][:-3]
                if any(x.startswith("/b/") and x.endswith(".tar") for x in a) and "-cpf" in a:
                    open(os.path.join(d, os.path.basename([x for x in a if x.startswith("/b/")][0])), "w").write("tar")
        if "ls" in a:
            out(os.environ.get("STUB_VOLUME_LISTING", ""))
        sys.exit(int(os.environ.get("STUB_RUN_RC", "0")))
    if a[:1] == ["exec"]:
        rest = a[1:]
        if rest and rest[0] == "-i":
            rest = rest[1:]
        rest = rest[1:]  # container name
        if rest[:1] == ["python"]:
            sys.exit(subprocess.call([sys.executable, "-I"] + rest[2:], stdin=sys.stdin))
        st = load()
        if rest[:1] == ["test"]:
            path = rest[-1]
            if rest[1] == "-x" or path in st["files"] or path.startswith("/app/storage/kg/"):
                sys.exit(0)
            sys.exit(1)
        if rest[:1] == ["mkdir"]:
            sys.exit(0)
        if rest[:1] == ["cp"]:
            st["files"].append(rest[2]); save(st); sys.exit(0)
        if rest[:1] == ["f2w-kg-adopt"] and "adopt" in rest:
            f = rest[rest.index("--only") + 1]
            if "--dry-run" in rest:
                out(json.dumps({"would_adopt": [f]}))
            stem = f[len("matkg_"):-len(".json")]
            snap = {"e2e_demo": "e2e_demo/v1", "qwen3_235b_580papers": "qwen3_235b_580papers/v1"}.get(stem) or stem.replace("_v", "/v")
            st["files"].append("/var/lib/fair2wise/state/session/storage_kg/%s/graph.json" % snap); save(st)
            out(json.dumps({"adopted": [f]}))
        sys.exit(0)
    out("unexpected docker call", 9)

if name == "curl":
    a = args
    method = "GET"; body = ""; url = ""
    for i, tok in enumerate(a):
        if tok == "-X": method = a[i + 1]
        if tok == "-d": body = a[i + 1]
        if tok.startswith("http"): url = tok
    with open(log, "a") as fh:
        fh.write(json.dumps(["HTTP", method, url, body]) + "\n")
    w = a[a.index("-w") + 1] if "-w" in a else ""
    def reply(code, text):
        sys.stdout.write(text + "\n" + str(code))
        sys.exit(0)
    if "time_total" in w:   # check_machine probe
        date = formatdate(time.time(), usegmt=True)
        if "cborg" in url:
            sys.stdout.write('{"error":"IP address 198.51.100.77 is not recognized"}\n403 0.050\n' + date)
        else:
            sys.stdout.write('ok\n200 0.123\n' + date)
        sys.exit(0)
    st = load()
    base = url.split("/fair2wise/api", 1)[-1] if "/fair2wise/api" in url else url.split("/fair2wise", 1)[-1]
    if base == "/healthz": reply(200, "ok")
    if base == "/health": reply(200, "{}")
    if base == "/v1/capabilities":
        reply(200, json.dumps({"profile": "local", "can_approve": True, "can_save": bool(st["live"]), "can_edit": True}))
    if base == "/v1/graph/sources":
        tiled = os.environ.get("STUB_TILED_STATUS", "ok")
        reply(200, json.dumps({"sources": [{"kind": "json", "id": "json:a"}, {"kind": "tiled", "id": "tiled:b"}], "tiled": {"status": tiled}}))
    if base.startswith("/v1/publications/pointer"):
        reply(200, json.dumps({"pointer": None, "corpora": {k: {"snapshot_ref": v} for k, v in st["live"].items()}}))
    if base == "/v1/approvals/pending":
        items = [{"kind": "publication", "job_id": j, "snapshot_ref": v["snap"], "target": "http://127.0.0.1:8765",
                  "plan": {"plan_sha256": plan_hash(j)}} for j, v in st["jobs"].items() if v["state"] == "staged"]
        reply(200, json.dumps({"items": items, "total": len(items), "counts": {"publication": len(items)}}))
    if base == "/v1/library/models":
        ids = os.environ.get("STUB_MODELS", "lbl/cborg-chat,lbl/gpt-oss-120b").split(",")
        reply(200, json.dumps({"available": True, "models": ids, "model_policy": {"free_models": ["lbl/cborg-chat", "lbl/gpt-oss-120b"]}}))
    if base == "/v1/validation/judge-model":
        reply(200, json.dumps({"available_models": ["lbl/cborg-chat"], "model_policy": {"free_models": ["lbl/cborg-chat"]}}))
    if base == "/v1/publications" and method == "POST":
        snap = json.loads(body)["snapshot_ref"]
        job = "job-" + snap.replace("/", "-")
        st["jobs"][job] = {"snap": snap, "state": "staged"}; save(st)
        reply(200, json.dumps({"job_id": job}))
    if base.startswith("/v1/jobs/") and base.endswith("/view"):
        job = base.split("/")[3]
        reply(200, json.dumps({"ui_state": st["jobs"][job]["state"]}))
    if base.startswith("/v1/publications/") and base.endswith("/plan"):
        job = base.split("/")[3]
        reply(200, json.dumps({"plan_sha256": plan_hash(job), "creates": 3, "links": 2, "supersessions": []}))
    if base.startswith("/v1/publications/") and base.endswith("/approval") and method == "POST":
        job = base.split("/")[3]
        if json.loads(body)["plan_sha256"] != plan_hash(job):
            reply(409, json.dumps({"code": "stale_approval"}))
        st["jobs"][job]["state"] = "published"; st["live"][st["jobs"][job]["snap"].split("/")[0]] = st["jobs"][job]["snap"]; save(st)
        reply(200, json.dumps({"approval": {}, "job": {"state": "queued"}}))
    reply(404, json.dumps({"code": "stub_unknown_route", "path": base}))

out("unexpected stub " + name, 9)
'''


def _sh(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture()
def env(tmp_path):
    """A PATH of stubs (docker, curl, pmset, sw_vers, dscacheutil, sleep, caffeinate) plus the system tools."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "stubs.py").write_text(STUBS_PY)
    for name in ("docker", "curl", "pmset", "sw_vers", "dscacheutil", "sleep", "caffeinate", "uname", "sysctl", "df", "lsof",
                 "pgrep", "colima"):
        _sh(bindir / name, f'#!/bin/sh\nexec "{sys.executable}" "{tmp_path}/stubs.py" "$0" "$@"\n')
    # a caffeinate that stays alive until killed, so keepawake can be started and stopped for real
    _sh(bindir / "caffeinate", "#!/bin/sh\ntrap 'kill $!' TERM\nsleep 3600 &\nwait\n")
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        "HOME": str(home),
        "PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "STUB_LOG": str(tmp_path / "calls.log"),
        "STUB_STATE": str(tmp_path / "state.json"),
        "F2W_DEMO_NO_PATH_EXTEND": "1",
        "F2W_DEMO_STATE_DIR": str(tmp_path / "demo-state"),
        "F2W_DEMO_BACKUP_ROOT": str(tmp_path / "backups"),
    }
    (tmp_path / "calls.log").write_text("")
    return environment


def run(script: str, args=(), env=None, stdin="", timeout=60):
    return subprocess.run(["bash", str(DEMO / script), *args], env=env, input=stdin, capture_output=True, text=True,
                          timeout=timeout)


def calls(env) -> list[list[str]]:
    return [json.loads(line) for line in Path(env["STUB_LOG"]).read_text().splitlines() if line.strip()]


def http(env) -> list[tuple[str, str, str]]:
    return [(c[1], c[2], c[3]) for c in calls(env) if c[0] == "HTTP"]


# ------------------------------------------------------------------------------------------ syntax and help


@pytest.mark.parametrize("script", ALL_SH)
def test_bash_syntax(script):
    r = subprocess.run(["bash", "-n", str(DEMO / script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("script", [f"{n}.sh" for n in SCRIPTS])
def test_help_exits_zero_and_describes_usage(script, env):
    r = run(script, ["--help"], env)
    assert r.returncode == 0, r.stderr
    assert "Usage" in r.stdout or "usage" in r.stdout
    # --help must not touch docker, curl or anything else
    assert calls(env) == []


@pytest.mark.parametrize("script", [f"{n}.sh" for n in SCRIPTS] + ["_common.sh"])
def test_bash_32_compatibility_and_strict_mode(script):
    text = (DEMO / script).read_text()
    if script != "_common.sh":
        assert "set -euo pipefail" in text
    assert not re.search(r"declare\s+-A|\bmapfile\b|\breadarray\b|\$\{[A-Za-z_]+(,,|\^\^)\}", text), "bash 4 feature"
    for m in re.finditer(r"\"\$\{([A-Za-z_]+)\[@\]\}\"", text):  # a bare empty-array expansion fails under set -u on 3.2
        start = text.rfind("${" + m.group(1) + "[@]+", 0, m.start() + 3)
        assert start != -1 or "set -u" not in text, f"{script}: unguarded expansion of {m.group(1)}"
    mode = (DEMO / script).stat().st_mode
    if script != "_common.sh":
        assert mode & stat.S_IXUSR


def test_no_script_reads_a_dotenv_or_prints_environment():
    """Only run_local_container.sh reads the key line; the demo scripts pass an env-file path through, nothing else."""
    for script in ALL_SH:
        code = [ln for ln in (DEMO / script).read_text().splitlines() if not ln.lstrip().startswith("#")]
        text = "\n".join(code)
        assert "CBORG_API_KEY" not in text, script
        assert not re.search(r"^\s*(env|printenv|set|export)\s*$", text, re.M), script
        assert not re.search(r"(\bsource\b|^\s*\.)\s+\S*\.env\b|cat\s+\S*\.env\b|grep[^|\n]*\.env\b", text, re.M), script


# ------------------------------------------------------------------------------------------ check_machine


READONLY_DOCKER = {("info",), ("version",), ("ps",), ("system", "df"), ("image", "ls"), ("volume", "ls"),
                   ("context", "show")}


def test_check_machine_is_read_only_and_reports(env):
    r = run("check_machine.sh", [], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Checklist against the demo requirements" in r.stdout
    assert re.search(r"\[(PASS|WARN)\] arm64\b", r.stdout)  # the stub runs on whatever machine runs the tests
    assert re.search(r"\[PASS\] >=8 GiB RAM for Docker\s+8192 MiB", r.stdout)
    assert re.search(r"\[PASS\] docker running", r.stdout)
    assert re.search(r"\[PASS\] >=40 GiB free disk\s+100 GiB", r.stdout)
    assert "macOS: 26.1" in r.stdout
    # CBORG off-network is explained without printing the address from the body
    assert "OFF-NETWORK" in r.stdout and "198.51.100.77" not in r.stdout
    assert "tiled-staging.als.lbl.gov" in r.stdout and "export.arxiv.org" in r.stdout and "api.openalex.org" in r.stdout
    seen = calls(env)
    for c in seen:
        if c[0] == "docker":
            assert tuple(c[1:3]) in READONLY_DOCKER or tuple(c[1:2]) in READONLY_DOCKER, c
        elif c[0] == "pmset":
            assert c[1:] in (["-g"], ["-g", "batt"]), c
        elif c[0] == "sw_vers":
            assert c[1:] in (["-productVersion"], ["-buildVersion"]), c
        elif c[0] == "curl":
            assert "-X" not in c and "-d" not in c and "--data" not in c and "-T" not in c, c
            assert "--max-time" in c and c[c.index("--max-time") + 1] == "8", c
            assert not any(re.search(r"(?i)authorization|api[-_]?key|bearer", t) for t in c), c
        elif c[0] == "colima":
            assert c[1:] in (["status"], ["list"]), c
        elif c[0] == "lsof":
            assert c[1:2] == ["-nP"] and "-i" not in c[1:2] and not any(t.startswith("-k") for t in c), c
        elif c[0] in ("HTTP", "dscacheutil", "uname", "sysctl", "df", "pgrep"):
            pass
        else:
            pytest.fail(f"unexpected stub call {c}")
    assert all(m == "GET" for m, _u, _b in http(env))
    assert not any(c[0] == "docker" and c[1] in ("run", "pull", "rm", "stop", "start", "exec", "load", "build") for c in seen)
    assert {u.split("/")[2] for _m, u, _b in http(env)} == {"api.cborg.lbl.gov", "export.arxiv.org",
                                                           "api.openalex.org", "tiled-staging.als.lbl.gov"}
    # nothing was written under HOME
    assert list(Path(env["HOME"]).iterdir()) == []


def test_check_machine_fails_the_checklist_when_requirements_are_not_met(env):
    env["STUB_DOCKER_INFO"] = "2 4294967296 aarch64 linux"  # 4 GiB for Docker
    r = run("check_machine.sh", [], env)
    assert r.returncode == 1
    assert re.search(r"\[FAIL\] >=8 GiB RAM for Docker\s+4096 MiB", r.stdout)


def test_check_machine_fails_on_low_disk_and_wrong_arch(env):
    r = run("check_machine.sh", [], dict(env, STUB_DF_KB=str(10 * 1048576), STUB_ARCH="x86_64"))
    assert r.returncode == 1
    assert re.search(r"\[FAIL\] >=40 GiB free disk\s+10 GiB", r.stdout)
    assert re.search(r"\[FAIL\] arm64\s+architecture is x86_64", r.stdout)


def test_check_machine_works_with_everything_absent(tmp_path):
    """No docker, curl, pmset or sw_vers on PATH: a report with FAIL/WARN lines, not a crash."""
    home = tmp_path / "h"; home.mkdir()
    bare = tmp_path / "bare"; bare.mkdir()
    for tool in ("uname", "date", "awk", "sed", "grep", "tr", "wc", "df", "tail", "head", "sort", "cat", "dirname", "basename"):
        for d in ("/usr/bin", "/bin"):
            if (Path(d) / tool).exists():
                (bare / tool).symlink_to(Path(d) / tool); break
    environment = {"HOME": str(home), "PATH": str(bare), "F2W_DEMO_NO_PATH_EXTEND": "1"}
    r = subprocess.run(["/bin/bash", str(DEMO / "check_machine.sh")], env=environment, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "[FAIL] docker installed" in r.stdout
    assert "Result:" in r.stdout


def test_check_machine_prints_the_checksum_of_a_given_tar(env, tmp_path):
    tar = tmp_path / "img.tar"
    tar.write_bytes(b"hello")
    r = run("check_machine.sh", [str(tar)], env)
    assert "sha256: 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824" in r.stdout
    r2 = run("check_machine.sh", [str(tmp_path / "nope.tar")], env)
    assert "[FAIL] image tar checksum" in r2.stdout


# ------------------------------------------------------------------------------------------ install


def test_install_stops_on_checksum_mismatch_before_loading(env, tmp_path):
    tar = tmp_path / "img.tar"; tar.write_bytes(b"x")
    sums = tmp_path / "img.sha256"; sums.write_text("0" * 64 + "  img.tar\n")
    r = run("install.sh", [str(tar), "--checksum", str(sums)], env)
    assert r.returncode != 0 and "MISMATCH" in r.stderr
    assert not [c for c in calls(env) if c[:2] == ["docker", "load"]]


def test_install_refuses_existing_volumes_without_fresh(env, tmp_path):
    tar = tmp_path / "img.tar"; tar.write_bytes(b"x")
    env["STUB_VOLUMES_EXIST"] = "1"
    r = run("install.sh", [str(tar)], env)
    assert r.returncode != 0 and "Refusing to overwrite" in r.stderr
    assert not [c for c in calls(env) if c[:2] in (["docker", "load"], ["docker", "rm"]) or c[:3] == ["docker", "volume", "rm"]]


# ------------------------------------------------------------------------------------------ seed


def _seed(env, *args, label=COMMIT):
    env = dict(env, STUB_LABEL=label)
    return run("seed.sh", list(args), env, timeout=120), env


def _approval_posts(env):
    return [h for h in http(env) if h[0] == "POST" and "/approval" in h[1]]


def test_seed_never_approves_without_the_flag_and_prints_the_commands(env):
    r, env = _seed(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _approval_posts(env) == []
    posts = [h for h in http(env) if h[0] == "POST"]
    assert [json.loads(b)["snapshot_ref"] for _m, u, b in posts if u.endswith("/v1/publications")] == ["rsoxs/v4", "qwen3_235b_580papers/v1"]
    assert all(json.loads(b)["target"] == "http://127.0.0.1:8765" for _m, _u, b in posts)
    assert r.stdout.count("/approval") == 2 and r.stdout.count('"plan_sha256":"') == 2
    assert "Nothing was approved by this script" in r.stdout
    adopts = [c for c in calls(env) if "f2w-kg-adopt" in c and "--dry-run" not in c and "adopt" in c]
    assert len(adopts) == 2
    for c in adopts:  # the documented sequence
        assert c[c.index("--storage-kg") + 1] == "/var/lib/fair2wise/state/session/storage_kg"
        assert c[c.index("--code-commit") + 1] == COMMIT and "--only" in c


def test_seed_is_idempotent(env):
    r, env = _seed(env)
    assert r.returncode == 0
    n_adopt = len([c for c in calls(env) if "f2w-kg-adopt" in c])
    n_post = len([h for h in http(env) if h[0] == "POST"])
    r2, env = _seed(env)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert len([c for c in calls(env) if "f2w-kg-adopt" in c]) == n_adopt  # nothing adopted twice
    assert len([h for h in http(env) if h[0] == "POST"]) == n_post       # the waiting plans are reused
    assert "reusing the plan already waiting" in r2.stdout and _approval_posts(env) == []


def test_seed_adds_the_optional_e2e_corpus_only_when_its_file_is_given(env, tmp_path):
    e2e = tmp_path / "matkg_e2e_demo.json"; e2e.write_text("{}")
    r, env = _seed(env, "--file", str(e2e))
    assert r.returncode == 0, r.stdout + r.stderr
    posts = [h for h in http(env) if h[0] == "POST" and h[1].endswith("/v1/publications")]
    assert [json.loads(b)["snapshot_ref"] for _m, _u, b in posts] == ["rsoxs/v4", "qwen3_235b_580papers/v1", "e2e_demo/v1"]


def test_seed_default_list_is_the_two_big_demo_corpora_and_waits_long_enough_for_them():
    text = (DEMO / "seed.sh").read_text()
    assert 'DEMO_CORPORA="rsoxs:matkg_rsoxs_v4.json:rsoxs/v4 qwen3_235b_580papers:matkg_qwen3_235b_580papers.json:qwen3_235b_580papers/v1"' in text
    # the 117,000-record apply takes tens of minutes: the old 300 s publish wait would abort it
    assert int(re.search(r"DEMO_PUBLISH_WAIT_SECONDS:-(\d+)", text).group(1)) >= 3600
    assert int(re.search(r"DEMO_STAGE_WAIT_SECONDS:-(\d+)", text).group(1)) >= 600


def test_seed_with_i_approve_waits_five_seconds_then_posts_the_approval(env):
    r, env = _seed(env, "--i-approve")
    assert r.returncode == 0, r.stdout + r.stderr
    log = calls(env)
    first = next(i for i, c in enumerate(log) if c[0] == "HTTP" and c[1] == "POST" and "/approval" in c[2])
    sleeps = [c for c in log[:first] if c[0] == "sleep"]
    assert sum(int(c[1]) for c in sleeps) >= 5
    assert len(_approval_posts(env)) == 2
    assert "About to approve rsoxs" in r.stdout and "Approving in 5 seconds" in r.stdout
    assert "live corpora: qwen3_235b_580papers=qwen3_235b_580papers/v1, rsoxs=rsoxs/v4" in r.stdout


@pytest.mark.parametrize("label", ["unknown", ""])
def test_seed_needs_a_commit_when_the_image_has_none(env, label):
    r, env = _seed(env, label=label)
    assert r.returncode != 0 and "--code-commit" in r.stderr
    assert not [c for c in calls(env) if "f2w-kg-adopt" in c]
    r2, env = _seed(env, "--code-commit", COMMIT, label=label)
    assert r2.returncode == 0, r2.stderr
    r3, env = _seed(env, "--code-commit", "abc123", label=label)
    assert r3.returncode != 0 and "40 hex" in r3.stderr


# ------------------------------------------------------------------------------------------ smoke


def test_smoke_is_get_only_and_prints_a_table(env):
    seeded, env = _seed(env, "--i-approve")
    assert seeded.returncode == 0
    Path(env["STUB_LOG"]).write_text("")
    r = run("smoke.sh", [], env)
    assert r.returncode == 0, r.stdout + r.stderr
    for check in ("container", "gateway /healthz", "API /health", "capabilities", "graph sources", "pointer corpora",
                  "pending approvals", "models: extraction", "models: settings", "console scripts"):
        assert re.search(rf"^PASS\s+{re.escape(check)}\b", r.stdout, re.M), (check, r.stdout)
    assert "Tiled status ok" in r.stdout
    assert {m for m, _u, _b in http(env)} == {"GET"}
    assert all(b == "" for _m, _u, b in http(env))


def test_smoke_fails_on_tiled_unavailable_missing_corpora_and_paid_models(env):
    env["STUB_TILED_STATUS"] = "unavailable"
    env["STUB_MODELS"] = "lbl/cborg-chat,anthropic/claude-opus"
    r = run("smoke.sh", [], env)  # nothing seeded: both corpora missing
    assert r.returncode == 1
    assert re.search(r"^FAIL\s+graph sources.*unavailable", r.stdout, re.M)
    assert re.search(r"^FAIL\s+pointer corpora.*MISSING: rsoxs qwen3_235b_580papers", r.stdout, re.M)
    assert re.search(r"^FAIL\s+models: extraction.*claude-opus", r.stdout, re.M)
    r2 = run("smoke.sh", ["--expect", ""], dict(env, STUB_TILED_STATUS="ok", STUB_MODELS="lbl/cborg-chat"))
    assert r2.returncode == 0 and re.search(r"^WARN\s+pointer corpora", r2.stdout, re.M)


# ------------------------------------------------------------------------------------------ reset / backup / restore


def _typed(env, args, text):
    return run("reset.sh", args, env, stdin=text)


def test_reset_removes_nothing_without_the_typed_confirmation(env):
    env["STUB_VOLUMES_EXIST"] = "1"
    r = _typed(env, ["--no-start"], "")
    assert r.returncode != 0 and "confirmation not given" in r.stderr
    r = _typed(env, ["--no-start"], "yes\n")
    assert r.returncode != 0
    log = calls(env)
    assert [c for c in log if c[:2] == ["docker", "run"]], "the backup ran"
    assert not [c for c in log if c[:2] == ["docker", "rm"] or c[:3] == ["docker", "volume", "rm"]]


def test_reset_removes_only_after_backup_and_typed_confirmation(env):
    env["STUB_VOLUMES_EXIST"] = "1"
    r = _typed(env, ["--no-start"], "reset\n")
    assert r.returncode == 0, r.stdout + r.stderr
    log = calls(env)
    run_idx = [i for i, c in enumerate(log) if c[:2] == ["docker", "run"] and "tar" in c]
    rm_idx = [i for i, c in enumerate(log) if c[:3] == ["docker", "volume", "rm"] or c[:2] == ["docker", "rm"]]
    assert run_idx and rm_idx and max(run_idx) < min(rm_idx)
    backups = list(Path(env["F2W_DEMO_BACKUP_ROOT"]).glob("*-before-reset"))
    assert len(backups) == 1 and {p.name for p in backups[0].iterdir()} >= {"state.tar", "tiled.tar", "SHA256SUMS", "meta.txt"}


def test_reset_stops_before_removing_when_the_backup_fails(env):
    env["STUB_VOLUMES_EXIST"] = "1"
    env["STUB_RUN_RC"] = "1"
    r = _typed(env, ["--no-start"], "reset\n")
    assert r.returncode != 0
    assert not [c for c in calls(env) if c[:2] == ["docker", "rm"] or c[:3] == ["docker", "volume", "rm"]]


def test_restore_refuses_a_nonempty_volume_without_replace_and_a_damaged_backup(env, tmp_path):
    bdir = tmp_path / "bk"; bdir.mkdir()
    (bdir / "state.tar").write_text("s"); (bdir / "tiled.tar").write_text("t")
    r = run("restore.sh", [str(bdir)], env)
    assert r.returncode != 0 and "SHA256SUMS" in r.stderr
    import hashlib
    (bdir / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((bdir / n).read_bytes()).hexdigest()}  {n}\n" for n in ("state.tar", "tiled.tar")))
    env2 = dict(env, STUB_VOLUMES_EXIST="1", STUB_VOLUME_LISTING="somefile")
    r = run("restore.sh", [str(bdir)], env2)
    assert r.returncode != 0 and "not empty" in r.stderr
    assert not [c for c in calls(env2) if "-xpf" in c]
    (bdir / "state.tar").write_text("tampered")
    r = run("restore.sh", [str(bdir), "--replace"], env2, stdin="restore\n")
    assert r.returncode != 0 and "checksum mismatch" in r.stderr


# ------------------------------------------------------------------------------------------ keepawake


def test_keepawake_start_status_stop(env):
    (Path(env["PATH"].split(":")[0]) / "sleep").unlink()  # the stub sleep would end the stub caffeinate at once
    r = run("keepawake.sh", ["start", "--hours", "2"], env)
    try:
        assert r.returncode == 0, r.stderr
        pidfile = Path(env["F2W_DEMO_STATE_DIR"]) / "keepawake.pid"
        assert pidfile.exists()
        assert run("keepawake.sh", ["status"], env).stdout.startswith("keep-awake: ON")
    finally:
        s = run("keepawake.sh", ["stop"], env)
    assert s.returncode == 0 and not pidfile.exists()
    assert run("keepawake.sh", ["status"], env).returncode == 1


def test_keepawake_passes_a_timeout_because_caffeinate_u_needs_one():
    text = (DEMO / "keepawake.sh").read_text()
    assert "caffeinate -dimsu -t " in text
