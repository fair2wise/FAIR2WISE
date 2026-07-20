import asyncio
import json
from pathlib import Path

from app.modules.f2w_agent import coordinator as coord_mod
from app.modules.f2w_agent.coordinator import Coordinator, CoordinatorConfig


class FakeRetrieval:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)

    async def query(self, question):
        verdict = self.verdicts.pop(0)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    async def reload_kg(self, graph_file):
        return {"status": "reloaded", "graph_source_requested": "json", "graph_source_used": "json"}


class FakeDownload:
    def __init__(self, result=None):
        self.result = result or {"count": 1, "candidates": 1, "failed": 0, "semantic_rejected": 0}
        self.called = False

    async def find_and_download(self, *args, **kwargs):
        self.called = True
        return self.result


class FakeExtractor:
    def __init__(self, result):
        self.result = result
        self.pdf_dirs = []

    async def extract(self, *args, **kwargs):
        self.pdf_dirs.append(Path(args[0]))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class CreatingDownload:
    async def find_and_download(self, *args, **kwargs):
        target = Path(kwargs["target_dir"])
        target.mkdir(parents=True, exist_ok=True)
        pdf = target / "new-paper.pdf"
        pdf.write_bytes(b"%PDF-1.4\nbody")
        return {"count": 1, "candidates": 1, "failed": 0, "semantic_rejected": 0, "downloaded": [str(pdf)]}


def _coord(tmp_path):
    return Coordinator(CoordinatorConfig(workdir=Path(tmp_path), max_rounds=1))


def test_configured_graph_syncs_session_kg_without_seed_terms(tmp_path):
    graph_path = tmp_path / "configured.json"
    graph_path.write_text(
        json.dumps(
            {
                "things": [{"id": "matkg:solutionprocessing", "name": "solution processing"}],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )
    workdir = tmp_path / "run"
    workdir.mkdir()
    stale = workdir / "kg.json"
    stale.write_text(json.dumps({"things": [{"id": "old"}], "associations": []}), encoding="utf-8")

    coord = Coordinator(CoordinatorConfig(graph=str(graph_path), workdir=workdir, seed_terms=None))

    assert coord.initial_graph == str(graph_path)
    session = json.loads(stale.read_text())
    assert [node["id"] for node in session["things"]] == ["matkg:solutionprocessing"]


def test_splash_mode_preserves_existing_session_kg_on_restart(tmp_path):
    graph_path = tmp_path / "configured.json"
    graph_path.write_text(
        json.dumps(
            {
                "things": [{"id": "matkg:configured", "name": "configured", "description": "from file"}],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )
    workdir = tmp_path / "run"
    workdir.mkdir()
    session_kg = workdir / "kg.json"
    session_kg.write_text(
        json.dumps(
            {
                "things": [{"id": "matkg:configured", "name": "configured", "description": "edited"}],
                "associations": [],
            }
        ),
        encoding="utf-8",
    )

    Coordinator(
        CoordinatorConfig(
            graph=str(graph_path),
            workdir=workdir,
            seed_terms=None,
            kg_mode="splash",
        )
    )

    session = json.loads(session_kg.read_text(encoding="utf-8"))
    assert session["things"][0]["description"] == "edited"


def test_answer_stops_on_retrieval_error(tmp_path, capsys):
    coord = _coord(tmp_path)
    retrieval = FakeRetrieval([RuntimeError("retrieval boom")])
    download = FakeDownload()

    asyncio.run(coord._answer("question", retrieval, download, FakeExtractor({})))

    out = capsys.readouterr().out
    assert "[stop] retrieval failed: retrieval boom" in out
    assert download.called is False


def test_answer_stops_on_extractor_error_status(tmp_path, capsys):
    coord = _coord(tmp_path)
    retrieval = FakeRetrieval([
        {
            "status": "success",
            "sufficient": False,
            "missing_topics": ["topic"],
            "graph_source_requested": "json",
            "graph_source_used": "json",
        }
    ])

    asyncio.run(
            coord._answer(
                "question",
                retrieval,
                CreatingDownload(),
                FakeExtractor({"status": "error", "message": "extract failed"}),
            )
        )

    out = capsys.readouterr().out
    assert "[stop] extraction failed: extract failed" in out


def test_answer_mocked_loop_extracts_only_unprocessed_pdfs(tmp_path, monkeypatch, capsys):
    coord = Coordinator(CoordinatorConfig(workdir=Path(tmp_path), max_rounds=2))
    retrieval = FakeRetrieval([
        {
            "status": "success",
            "sufficient": False,
            "missing_topics": ["topic"],
            "graph_source_requested": "json",
            "graph_source_used": "json",
        },
        {
            "status": "success",
            "sufficient": True,
            "answer": "grounded",
            "graph_source_requested": "json",
            "graph_source_used": "json",
        },
    ])
    extractor = FakeExtractor({"status": "success", "unique_terms": 3})

    monkeypatch.setattr(
        coord_mod.kg_update,
        "rebuild_kg",
        lambda terms, kg: {"status": "success", "nodes": 2, "edges": 1},
    )

    asyncio.run(coord._answer("question", retrieval, CreatingDownload(), extractor))

    out = capsys.readouterr().out
    assert "[round 1] extracting 1 new PDF(s)" in out
    assert "[Answer]\ngrounded" in out
    assert extractor.pdf_dirs == [tmp_path / "extract_rounds" / "round_1"]
    assert (extractor.pdf_dirs[0] / "new-paper.pdf").exists()
    processed = json.loads((tmp_path / "processed_pdfs.json").read_text())
    assert processed == ["new-paper.pdf"]
