"""Characterization of the user-facing text of the CLI/agent entry points (task T27, ruff T201).

Each test pins the exact bytes a command writes to stdout and to stderr. They were written against the
``print`` implementation and must pass unchanged once those prints go through ``app.modules.cli_output``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
import uuid

import networkx as nx
import pytest

REPO = Path(__file__).resolve().parents[1]


def _drain(capsys) -> tuple[str, str]:
    captured = capsys.readouterr()
    return captured.out, captured.err


def _env(home: Path, **extra: str) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "HOME": str(home), "F2W_DOTENV": "off", **extra}


# ── agents/chebi.py ──────────────────────────────────────────────────────────


def _chebi_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    graph.add_node(
        "CHEBI:1",
        name="water",
        synonym=['"H2O" EXACT []', '"oxidane" EXACT []'],
        **{"def": 'A "solvent" thing.'},
        is_a=["CHEBI:2"],
        xref=["CAS:7732-18-5", "KEGG:C00001", "plain"],
        property_value=['http://purl.obolibrary.org/obo/chebi/formula "H2O" xsd:string'],
        empty_list=[],
    )
    graph.add_node("CHEBI:2", name="solvent")
    graph.add_edge("CHEBI:1", "CHEBI:3", key="has_role")
    graph.add_edge("CHEBI:1", "CHEBI:4", key="is_conjugate_acid_of")
    return graph


CHEBI_TRANSCRIPT = (
    "Enter a chemical name (exact label or synonym) to look it up in ChEBI (Ctrl+C to exit):\n"
    "No match found for 'nothing'.\n\n"
    "\n✅ Match for 'WATER':\n"
    "  chebi_id: CHEBI:1\n"
    "  name: water\n"
    "  definition: solvent\n"
    "  synonyms: H2O, oxidane\n"
    "  formula: H2O\n"
    "  mass: None\n"
    "  charge: None\n"
    "  inchi: None\n"
    "  inchikey: None\n"
    "  smiles: None\n"
    "  roles: CHEBI:3\n"
    "  parents: CHEBI:2\n"
    "  relationships:\n"
    "    - is_conjugate_acid_of: CHEBI:4\n"
    "  xrefs:\n"
    "    - CAS: 7732-18-5\n"
    "    - KEGG: C00001\n"
    "  all_attributes:\n"
    "    - name: water\n"
    '    - synonym: ["H2O" EXACT [], "oxidane" EXACT []]\n'
    '    - def: A "solvent" thing.\n'
    "    - is_a: [CHEBI:2]\n"
    "    - xref: [CAS:7732-18-5, KEGG:C00001, plain]\n"
    '    - property_value: [http://purl.obolibrary.org/obo/chebi/formula "H2O" xsd:string]\n'
    "    - empty_list: []\n"
    "\n"
    "\nExiting.\n"
)


def test_chebi_interactive_loop_output(monkeypatch, capsys):
    from app.modules.agents import chebi

    monkeypatch.setattr(chebi, "load_chebi_obo", lambda path: _chebi_graph())
    monkeypatch.setattr(sys, "argv", ["chebi.py", "x.obo"])
    answers = iter(["", "  nothing  ", "WATER"])

    def fake_input(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise KeyboardInterrupt from None

    monkeypatch.setattr("builtins.input", fake_input)
    chebi.main()
    assert _drain(capsys) == (CHEBI_TRANSCRIPT, "")


def test_chebi_usage_and_load_failure_go_to_stderr(monkeypatch, capsys):
    from app.modules.agents import chebi

    monkeypatch.setattr(sys, "argv", ["chebi.py"])
    with pytest.raises(SystemExit) as excinfo:
        chebi.main()
    assert excinfo.value.code == 1
    assert _drain(capsys) == ("", "Usage: python chebi.py <path/to/chebi.obo>\n")

    def boom(path):
        raise OSError("nope")

    monkeypatch.setattr(chebi.obonet, "read_obo", boom)
    with pytest.raises(FileNotFoundError):
        chebi.load_chebi_obo("missing.obo")
    assert _drain(capsys) == ("", "Failed to load OBO file 'missing.obo': nope\n")


# ── bl1101_ingest.py ─────────────────────────────────────────────────────────


def test_bl1101_main_summaries(monkeypatch, capsys, tmp_path):
    from app.modules import bl1101_ingest as ing

    base = {"version": 5, "nodes": 10, "edges": 20, "kg_path": "kg/x.json"}
    monkeypatch.setattr(
        ing, "run_motor_link_promote", lambda **kw: {**base, "stats": {"linked": 3, "aliased": 4}}
    )
    assert ing.main(["--from-graph", str(tmp_path / "g.json")]) == 0
    assert _drain(capsys) == (
        "bl1101 v5: 10 nodes, 20 edges → kg/x.json\nmotor links: +3 part_of, +4 alias related_to\n",
        "",
    )

    monkeypatch.setattr(
        ing, "run_ingest", lambda **kw: {**base, "sources_landed": [{"name": "a"}, {}], "sources_skipped": []}
    )
    assert ing.main([]) == 0
    assert _drain(capsys) == ("bl1101 v5: 10 nodes, 20 edges → kg/x.json\nlanded: a, ?\n", "")

    monkeypatch.setattr(
        ing,
        "run_ingest",
        lambda **kw: {
            **base,
            "sources_landed": [],
            "sources_skipped": [{"name": "s1", "reason": "offline"}, {"name": "s2", "status": "gone"}],
        },
    )
    assert ing.main([]) == 0
    assert _drain(capsys) == (
        "bl1101 v5: 10 nodes, 20 edges → kg/x.json\nlanded: (none)\nskipped: s1 (offline), s2 (gone)\n",
        "",
    )


# ── canonicalization/measurement.py ──────────────────────────────────────────


def test_measurement_main_prints_report_paths(tmp_path):
    out_dir = tmp_path / "rep"
    proc = subprocess.run(
        [sys.executable, "-m", "app.modules.canonicalization.measurement", str(out_dir)],
        cwd=REPO,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == f"{out_dir / 'measurement_report.json'}\n{out_dir / 'measurement_report.md'}\n"
    assert (out_dir / "measurement_report.json").is_file()


# ── extract_terms.py ─────────────────────────────────────────────────────────


def test_extract_terms_script_summary_line(tmp_path):
    """The ``✗ Error`` branch is unreachable (``run_extraction`` raises instead); the success line is not."""
    (tmp_path / "empty").mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "app/modules/legacy/extract_terms_cli.py"),
            "--pdf-dir", "empty",
            "--output", "res/out.json",
            "--backend", "ollama",
            "--schema-path", str(REPO / "storage/schema/matkg_schema.yaml"),
        ],
        cwd=tmp_path,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        check=True,
    )
    # the extractor's own logger writes to stdout too; the summary is the last line, byte for byte
    assert proc.stdout.endswith(
        "✓ Processed 0 files, 0 total pages, 0 pages with terms, 0 unique terms → res/out.json\n"
    )
    assert "✗ Error" not in proc.stdout


# ── f2w_agent/cli.py ─────────────────────────────────────────────────────────


def test_f2w_cli_status_chat_and_interrupt(monkeypatch, capsys, tmp_path):
    from app.modules.f2w_agent import cli

    assert cli.main(["--workdir", str(tmp_path), "status"]) == 0
    out, err = _drain(capsys)
    assert err == ""
    data = json.loads(out)
    assert out == json.dumps(data, indent=2) + "\n"
    assert list(data)[:3] == ["backend", "model", "graph"]
    assert data["workdir"] == str(tmp_path)

    asked = []

    class FakeCoordinator:
        def __init__(self, cfg):
            pass

        async def run(self, questions):
            asked.extend(questions)
            if questions == ["stop"]:
                raise KeyboardInterrupt

    monkeypatch.setattr(cli, "Coordinator", FakeCoordinator)
    answers = iter(["hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.main(["--workdir", str(tmp_path), "chat"]) == 0
    assert _drain(capsys) == ("FAIR2WISE orchestrated KG-RAG chat. Type 'exit' to quit.\n", "")
    assert asked == ["hello"]

    assert cli.main(["--workdir", str(tmp_path), "ask", "stop"]) == 130
    assert _drain(capsys) == ("\nInterrupted.\n", "")


# ── api/cli.py ───────────────────────────────────────────────────────────────


def test_api_cli_prints_json_and_refusal(tmp_path, capsys):
    from app.api import cli

    root = str(tmp_path / "ops")
    assert cli.main(["--root", root, "jobs", "list"], environ={}) == 0
    assert _drain(capsys) == ("[]\n", "")
    assert cli.main(["--root", root, "jobs", "get", "job-missing"], environ={}) == 2
    out, err = _drain(capsys)
    assert out == ""
    payload = json.loads(err)
    assert err == json.dumps(payload) + "\n"
    assert payload["code"] == "not_found"
    assert cli.main(["--root", root, "jobs", "list"], environ={"F2W_DEPLOYMENT_PROFILE": "als-dev"}) == 2
    out, err = _drain(capsys)
    assert out == "" and json.loads(err)["code"] == "denied"


# ── run_pipeline_cborg.py ────────────────────────────────────────────────────


def test_pipeline_dry_run_and_banner_script(tmp_path):
    for folder in ("paper_25", "paper_50", "paper_75", "paper_100"):
        (tmp_path / "pdfs" / folder).mkdir(parents=True)
    (tmp_path / "pdfs" / "paper_25" / "one.pdf").write_bytes(b"%PDF-1.4\n")
    proc = subprocess.run(
        [sys.executable, str(REPO / "app/run_pipeline_cborg.py"),
         "--pdf-root", str(tmp_path / "pdfs"), "--models", "vendor/m:t", "--dry-run"],
        cwd=tmp_path,
        env=_env(tmp_path, CBORG_API_KEY="k", CBORG_BASE_URL="https://example.invalid"),
        capture_output=True,
        text=True,
        check=True,
    )
    norm = re.sub(r"\d{8}_\d{6}", "TS", proc.stdout)
    expected = ["Using CBORG_BASE_URL = https://example.invalid"]
    for cp, folder, n in ((25, "paper_25", 1), (50, "paper_50", 0), (75, "paper_75", 0), (100, "paper_100", 0)):
        expected += [
            f"[DRY-RUN] Checkpoint={cp}, model=vendor/m:t",
            f"  → New folders: ['{folder}']",
            f"  → New papers: {n}",
            "  → Prev terms: None",
            f"  → Terms file: storage/terminology/extracted_terms_vendor_m_t_{cp}_TS.json",
            f"  → Graph file: storage/kg/matkg_vendor_m_t_{cp}_TS.json",
        ]
    assert norm == "\n".join(expected) + "\n"
    # logging.basicConfig (stderr, with its own prefix) only carries the diagnostics, never the plan
    assert re.sub(r"^\S+ \S+ ", "", proc.stderr, flags=re.M) == (
        "INFO: " + "=" * 80 + "\nINFO: Starting incremental pipeline for model=vendor/m:t\n"
    )


# ── tiled_sim.py ─────────────────────────────────────────────────────────────


def test_tiled_sim_reporting(monkeypatch, capsys, tmp_path):
    from app.modules import tiled_sim

    fixture = {"seed": 7, "stats": {"esafs": 1, "proposals": 2, "scans": 3, "samples": 4}}
    monkeypatch.setattr(tiled_sim, "load_seed", lambda path=None: {"seed": 7})
    monkeypatch.setattr(tiled_sim, "generate_fixture", lambda seed: fixture)
    monkeypatch.setattr(tiled_sim, "write_fixture", lambda fx, path=None: path)
    monkeypatch.setattr(tiled_sim, "_load_repo_env", lambda: None)
    monkeypatch.setattr(
        tiled_sim,
        "run_tiled_sim_promote",
        lambda **kw: {"version": 9, "nodes": 5, "edges": 6, "kg_path": "kg/y.json"},
    )
    monkeypatch.setattr(
        tiled_sim,
        "push_fixture_to_tiled",
        lambda fx, uri=None: {
            "uri": "http://t", "created": 1, "skipped": 2, "links_created": 3, "links_skipped": 4,
            "existing_after": 5,
        },
    )
    fx_path = tmp_path / "fx.json"
    assert tiled_sim.main(["--fixture", str(fx_path), "--generate-only"]) == 0
    assert _drain(capsys) == (f"fixture seed=7: 1 ESAFs, 2 proposals, 3 scans, 4 samples → {fx_path}\n", "")
    assert tiled_sim.main(["--fixture", str(fx_path)]) == 0
    assert _drain(capsys) == (
        f"fixture seed=7: 1 ESAFs, 2 proposals, 3 scans, 4 samples → {fx_path}\n"
        "bl1101 v9: 5 nodes, 6 edges → kg/y.json\n",
        "",
    )
    # --to-tiled with a missing fixture file: generate, write, push
    assert tiled_sim.main(["--fixture", str(fx_path), "--to-tiled"]) == 0
    assert _drain(capsys) == (
        "pushing fixture seed=7: 1 ESAFs, 2 proposals, 3 scans, 4 samples → Tiled\n"
        "tiled http://t: created 1 skipped 2 links +3 skipped 4 after 5\n",
        "",
    )


# ── Academy agents ───────────────────────────────────────────────────────────


def test_monitored_agent_prints(capsys):
    import logging
    from unittest.mock import AsyncMock, MagicMock

    from app.modules.term_extractor import monitored_agent as ma
    from app.modules.term_extractor.message import Log

    agent = object.__new__(ma.MonitoredAgent)
    agent.agent_name = "A"
    agent._agent_uid_str = "uid-1"
    agent.user_agent = SimpleNamespace(message=AsyncMock())
    asyncio.run(agent._send_message(Log(agent_id="uid-1", agent_name="A", message="m", level="INFO")))
    assert _drain(capsys) == (
        "Sending message to UserAgent Log(agent_id='uid-1', agent_name='A', message='m', level='INFO')\n",
        "",
    )

    agent._send_message = AsyncMock()
    asyncio.run(agent.log("hello", level="WARN"))
    assert _drain(capsys) == ("Monitored Agent : message=hello level=WARN\nAgent id : uid-1\n", "")

    agent._drain_task = MagicMock()
    agent._stats_task = MagicMock()
    agent._log_handler = logging.NullHandler()
    asyncio.run(agent.agent_on_shutdown())
    assert _drain(capsys) == ("Agent on shutdown... closing log handler hooks\n", "")


def test_term_extractor_user_agent_prints(monkeypatch, capsys):
    from unittest.mock import MagicMock

    from app.modules.term_extractor import user_agent as ua

    monkeypatch.setattr(ua, "Dashboard", lambda host, port: MagicMock())
    agent = ua.UserAgent(port=1234)
    assert _drain(capsys) == ("Starting user agent on Port: 1234\n", "")
    agent._dashboard = MagicMock()
    asyncio.run(agent.agent_on_startup())
    assert _drain(capsys) == ("Starting dashboard\n", "")


class _Ctx:
    """Async context manager standing in for an Academy manager."""

    def __init__(self, handle):
        self.handle = handle

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def launch(self, *a, **kw):
        return self.handle

    async def wait(self, *a, **kw):
        return None

    async def shutdown(self, *a, **kw):
        return None


def test_launcher_user_agent_prints_handle(monkeypatch, capsys, tmp_path):
    from app.modules.launchers import user_agent as lu

    uid = "12345678-1234-5678-1234-567812345678"
    handle = SimpleNamespace(agent_id=SimpleNamespace(uid=uuid.UUID(uid)))

    async def from_factory(**kw):
        return _Ctx(handle)

    monkeypatch.setattr(lu, "init_logging", lambda: None)
    monkeypatch.setattr(lu, "get_cached_auth_headers", lambda db: {})
    monkeypatch.setattr(lu, "HttpExchangeFactory", lambda **kw: None)
    monkeypatch.setattr(lu.Manager, "from_exchange_factory", staticmethod(from_factory))
    monkeypatch.setattr(lu, "pickle", SimpleNamespace(dump=lambda obj, fh: None))
    monkeypatch.chdir(tmp_path)
    asyncio.run(lu.launch(port=1))
    assert _drain(capsys) == (f"User Agent Handle >>>> {uid}\n", "")


def test_academy_extractor_prints_result(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "globus_compute_sdk", SimpleNamespace(Executor=object))
    from app.modules.launchers import academy_extractor as ae

    class Handle:
        async def process_directory(self, data_dir):
            return {"n": 1}

        async def get_term_count(self):
            return 3

    async def from_factory(**kw):
        return _Ctx(Handle())

    monkeypatch.setenv("GLOBUS_COMPUTE_ENDPOINT_ID", "ep")
    monkeypatch.setattr(ae, "_load_user_agent_handle", lambda: object())
    monkeypatch.setattr(ae, "GlobusComputeExecutor", lambda ep: object())
    monkeypatch.setattr(ae, "HttpExchangeFactory", lambda *a, **kw: None)
    monkeypatch.setattr(ae, "TermExtractorAgent", lambda **kw: object())
    monkeypatch.setattr(ae.Manager, "from_exchange_factory", staticmethod(from_factory))
    asyncio.run(ae.run("d", "o.json", "m", "s.yaml", "cborg", 1, None))
    assert _drain(capsys) == ("\nResult: {'n': 1}\n", "")
