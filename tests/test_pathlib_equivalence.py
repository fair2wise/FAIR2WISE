"""Characterization of the os.path -> pathlib replacements (T27, ruff PTH).

Each case asserts the pathlib spelling used in the code equals the old os.path
spelling for the inputs the call sites receive (file paths, never ``dir/``).
"""
# ruff: noqa: PTH112, PTH118, PTH119, PTH120, PTH208

import os
from pathlib import Path

import pytest

from app.modules.term_extractor import dashboard


@pytest.mark.parametrize("pdf_path", ["a.pdf", "dir/a.pdf", "/abs/dir/a b.pdf", "./x/y.PDF", "../up/a.pdf", "a.tar.pdf"])
def test_pdf_filename_matches_basename(pdf_path: str) -> None:
    assert Path(pdf_path).name == os.path.basename(pdf_path)


def test_assets_dir_matches_join_dirname() -> None:
    assert dashboard._ASSETS_DIR == os.path.join(os.path.dirname(dashboard.__file__), "assets")
    assert isinstance(dashboard._ASSETS_DIR, str)


def test_asset_listing_sorted_names_match_listdir(tmp_path: Path) -> None:
    for name in ["b.png", "a.svg", "Z.png", ".hidden", "logo-x.png"]:
        (tmp_path / name).write_bytes(b"")
    assert sorted(e.name for e in tmp_path.iterdir()) == sorted(os.listdir(tmp_path))


def test_empty_data_dir_still_rejected() -> None:
    # os.path.isdir("") is False but Path("").is_dir() is True: the call sites guard on emptiness.
    assert not os.path.isdir("")
    assert Path("").is_dir()
    for data_dir in ("", "/nonexistent-dir-xyz"):
        assert (not data_dir or not Path(data_dir).is_dir()) == (not os.path.isdir(data_dir))
