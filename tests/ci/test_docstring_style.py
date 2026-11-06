"""scripts/ci/docstring_style.py (T27, F2.4): one docstring style per module.

Google sections for new code, Sphinx fields for code that already used them, never both in one
module. The classifier is unit-tested on synthetic sources, and the repository gate below fails
when any module under ``app/`` (``legacy/`` excluded) mixes the two styles.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "docstring_style.py"


def _load():
    spec = importlib.util.spec_from_file_location("docstring_style", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault("docstring_style", module)
    spec.loader.exec_module(module)
    return module


ds = _load()

GOOGLE = '''"""Module.

Args:
    x: unused.
"""


def f(a, b):
    """Add.

    Args:
        a: first.
        b: second.

    Returns:
        The sum.
    """
    return a + b
'''

SPHINX = '''"""Module."""


class C:
    """Thing.

    :ivar name: the name.
    """

    def f(self, a):
        """Add.

        :param a: first.
        :returns: the sum.
        :raises ValueError: never.
        """
        return a
'''

MIXED = '''"""Module."""


def f(a):
    """Add.

    Args:
        a: first.
    """
    return a


def g(a):
    """Sub.

    :param a: first.
    """
    return a
'''

NONE = '''"""Just prose. Mentions Args: inline and :param: inline, which are not markers."""


def f(a):
    """Return a. See :class:`C` for the role, which is not a style marker."""
    return a
'''


@pytest.mark.parametrize(
    ("source", "style", "google", "sphinx"),
    [(GOOGLE, "google", 3, 0), (SPHINX, "sphinx", 0, 4), (MIXED, "mixed", 1, 1), (NONE, "none", 0, 0)],
)
def test_classify_source(source, style, google, sphinx):
    result = ds.classify_source(source, "m.py")
    assert result.style == style
    assert (result.google, result.sphinx) == (google, sphinx)


def test_markers_carry_owner_and_line():
    result = ds.classify_source(MIXED, "m.py")
    owners = {(m.owner, m.style) for m in result.markers}
    assert owners == {("f", "google"), ("g", "sphinx")}
    lines = sorted(m.line for m in result.markers)
    assert lines == [7, 16]


def test_iter_modules_skips_legacy_and_caches(tmp_path):
    (tmp_path / "a.py").write_text(GOOGLE, encoding="utf-8")
    (tmp_path / "legacy").mkdir()
    (tmp_path / "legacy" / "b.py").write_text(MIXED, encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "c.py").write_text(MIXED, encoding="utf-8")
    results = ds.classify_tree([tmp_path], root=tmp_path)
    assert [r.path for r in results] == ["a.py"]


def test_check_exit_status(tmp_path, capsys):
    (tmp_path / "ok.py").write_text(GOOGLE, encoding="utf-8")
    assert ds.main([str(tmp_path), "--check"]) == 0
    (tmp_path / "bad.py").write_text(MIXED, encoding="utf-8")
    assert ds.main([str(tmp_path), "--check"]) == 1
    out = capsys.readouterr().out
    assert "1 module(s) mix" in out
    assert "bad.py" in out and "[sphinx] g: :param a:" in out


def test_app_has_no_mixed_module():
    """The repository gate: ``app/`` (legacy excluded) has no module mixing Google and Sphinx."""
    results = ds.classify_tree([REPO / "app"], root=REPO)
    mixed = [r.path for r in results if r.style == "mixed"]
    assert mixed == [], f"modules mixing docstring styles: {mixed}"
    # The scan must have seen the tree (guards against an exclusion rule swallowing everything).
    assert len(results) > 200
