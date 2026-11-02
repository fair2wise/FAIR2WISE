"""Characterization tests: pathlib forms used in extract_terms match the os.path forms they replaced."""

# ruff: noqa: PTH109, PTH110, PTH112, PTH119, PTH120, PTH123, PTH201, PTH208  (the old os.path/os/open forms are the reference side of these comparisons)

import os
from pathlib import Path
import pickle

import pytest


@pytest.mark.parametrize(
    "pdf_path",
    ["paper.pdf", "dir/paper.pdf", "/abs/dir/paper.pdf", "./dir/a b.pdf", "dir/sub/x.y.pdf"],
)
def test_basename_matches_path_name(pdf_path: str) -> None:
    assert Path(pdf_path).name == os.path.basename(pdf_path)


def test_listdir_pdf_filter_matches_iterdir(tmp_path: Path) -> None:
    for name in ("a.pdf", "B.PDF", "c.txt", "d.Pdf"):
        (tmp_path / name).write_text("x")
    (tmp_path / "sub.pdf").mkdir()  # directory named like a pdf: both forms list it
    old = sorted(f for f in os.listdir(tmp_path) if f.lower().endswith(".pdf"))
    new = sorted(f.name for f in Path(tmp_path).iterdir() if f.name.lower().endswith(".pdf"))
    assert old == new
    assert all(isinstance(n, str) for n in new)


def test_isdir_matches_is_dir(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    for p in (tmp_path, tmp_path / "f", tmp_path / "missing"):
        assert Path(str(p)).is_dir() == os.path.isdir(str(p))


def test_mkdir_parent_matches_makedirs(tmp_path: Path) -> None:
    out = str(tmp_path / "a" / "b" / "terms.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).parent.mkdir(parents=True, exist_ok=True)  # exist_ok: second call is a no-op
    assert os.path.isdir(os.path.dirname(out))
    assert Path(out).exists() == os.path.exists(out) is False


def test_open_modes_match_builtin_open(tmp_path: Path) -> None:
    target = str(tmp_path / "out.json")
    with Path(target).open("w") as fh:
        fh.write('{"terms": []}')
    with open(target, "r") as fh:
        assert fh.read() == '{"terms": []}'
    with Path(target).open("r") as fh:
        assert fh.read() == '{"terms": []}'


def test_pickle_handle_roundtrip_with_path_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with Path("user_agent_handle.pkl").open("wb") as handle:
        pickle.dump({"uid": 1}, handle)
    with open("user_agent_handle.pkl", "rb") as handle:
        assert pickle.load(handle) == {"uid": 1}
    with pytest.raises(FileNotFoundError):
        Path("missing.pkl").open("rb")


def test_cwd_and_current_dir_listing_render_identically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "d1").mkdir()
    (tmp_path / "f1").write_text("x")
    monkeypatch.chdir(tmp_path)
    assert f"{Path.cwd()}" == f"{os.getcwd()}"
    assert sorted(d.name for d in Path().iterdir() if d.is_dir()) == sorted(
        d for d in os.listdir(".") if os.path.isdir(d)
    )
    assert Path(".") == Path()
