"""Local PDF probe: title, page count and first-pages text of an untrusted PDF, with no network (A8, T35).

A user-supplied PDF is parsed by pypdfium2, a native library, so the default probe
(:func:`isolated_probe`) runs in a **child process** with a wall-clock timeout and CPU/memory
limits: a hostile or pathological file can hang or crash only the child. :func:`probe_pdf` is the
in-process function the child runs (and the one tests call directly under a socket block).

The probe uses :func:`app.modules.literature.pdf_reader.open_pdf` (``.metadata``, page text) and nothing
that opens a socket. DOI candidates are derived by the caller with
:func:`app.modules.literature.intake.extract_doi_candidates` / ``filename_doi_candidates``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

#: Leading pages read (a references section would add other papers' DOIs); matches ``intake.DOI_SEARCH_PAGES``.
PROBE_PAGES = 2
#: Wall-clock seconds the child may run.
DEFAULT_TIMEOUT_SECONDS = 20.0
#: Characters of text kept per probe.
MAX_TEXT_CHARS = 200_000
#: Longest title kept.
MAX_TITLE_CHARS = 300

_BAD_TITLE = re.compile(r"^(untitled\b|microsoft word\b|microsoft powerpoint\b|document\d*$|[\d\W_]+$)", re.I)
_BAD_TITLE_SUFFIX = (".pdf", ".doc", ".docx", ".tex", ".dvi", ".ps", ".indd", ".qxd")


@dataclass(frozen=True)
class ProbeResult:
    """What a probe read.

    Attributes:
        status: ``ok``, ``encrypted``, ``unreadable``, ``timeout`` or ``error``.
        title: The PDF Info title cleaned of generator noise, or ``""``.
        pages: Page count (0 when unknown).
        meta_text: The non-empty Info values joined by newlines (DOIs sometimes sit in ``subject``).
        page_text: Text of the first :data:`PROBE_PAGES` pages.
    """

    status: str = "error"
    title: str = ""
    pages: int = 0
    meta_text: str = ""
    page_text: str = ""

    @property
    def ok(self) -> bool:
        """Whether the PDF was read."""
        return self.status == "ok"

    @property
    def text(self) -> str:
        """Metadata plus first-pages text, the shape ``intake.pdf_first_pages_text`` returns."""
        return "\n".join(part for part in (self.meta_text, self.page_text) if part)


#: ``probe(path) -> ProbeResult``.
Probe = Callable[[Path], ProbeResult]


def clean_title(raw: object) -> str:
    """The Info title when it reads like a title; ``""`` for generator noise (``Untitled``, ``Microsoft Word - x``)."""
    title = " ".join(str(raw or "").split())
    if len(title) < 4 or _BAD_TITLE.match(title) or title.lower().endswith(_BAD_TITLE_SUFFIX):
        return ""
    return title[:MAX_TITLE_CHARS]


def probe_pdf(path: str | Path) -> ProbeResult:
    """Read one PDF in this process: Info title, page count, Info values and first-pages text.

    Never raises for a bad file (the status says why). No network is touched.
    """
    from ..literature.pdf_reader import open_pdf

    try:
        with open_pdf(Path(path)) as doc:
            if getattr(doc, "needs_pass", False):
                return ProbeResult(status="encrypted")
            meta = doc.metadata or {}
            meta_text = "\n".join(str(v) for v in meta.values() if v)
            pages = doc.page_count
            chunks = [doc.load_page(i).get_text() for i in range(min(PROBE_PAGES, pages))]
            return ProbeResult(status="ok", title=clean_title(meta.get("title")), pages=pages,
                               meta_text=meta_text[:MAX_TEXT_CHARS],
                               page_text="\n".join(chunks)[:MAX_TEXT_CHARS])
    except Exception:  # noqa: BLE001 - a malformed PDF is a status, not a crash
        return ProbeResult(status="unreadable")


def _child_env() -> dict[str, str]:
    """A minimal environment for the child: no credentials, no proxy settings, no user site."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "HF_HUB_OFFLINE": "1"}


def isolated_probe(path: str | Path, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> ProbeResult:
    """:func:`probe_pdf` in a child interpreter with a wall-clock timeout and CPU/memory limits.

    The child gets no credentials (minimal environment) and is killed at ``timeout``; a crash, a
    timeout or unparsable output is a status (``timeout`` / ``error``), never an exception.
    """
    root = str(Path(__file__).resolve().parents[3])
    cmd = [sys.executable, "-I", str(Path(__file__).resolve()), root, str(path), str(int(timeout))]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout, env=_child_env(), cwd=root,  # noqa: S603
                              stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return ProbeResult(status="timeout")
    except OSError:
        return ProbeResult(status="error")
    if done.returncode != 0:
        return ProbeResult(status="error")
    try:
        data = json.loads(done.stdout.decode("utf-8", "replace"))
        return ProbeResult(status=str(data["status"]), title=str(data.get("title", "")),
                           pages=int(data.get("pages", 0)), meta_text=str(data.get("meta_text", "")),
                           page_text=str(data.get("page_text", "")))
    except (ValueError, KeyError, TypeError):
        return ProbeResult(status="error")


def _limit_resources(timeout: int) -> None:
    """Best effort CPU and address-space caps for the child (not every platform honours them)."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (timeout + 1, timeout + 5))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (4 << 30, 4 << 30))
    except (ImportError, ValueError, OSError):
        logger.debug("resource limits not applied", exc_info=True)


def _child_main(argv: list[str]) -> int:
    """Child entry: ``probe.py <repo-root> <pdf> <timeout>``; prints one JSON object."""
    root, pdf, timeout = argv[1], argv[2], int(argv[3])
    sys.path.insert(0, root)
    _limit_resources(timeout)
    from app.modules.library.probe import probe_pdf as run  # the package import, not this script's ``__main__``

    result = run(pdf)
    sys.stdout.write(json.dumps({"status": result.status, "title": result.title, "pages": result.pages,
                                 "meta_text": result.meta_text, "page_text": result.page_text}))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through isolated_probe
    raise SystemExit(_child_main(sys.argv))


__all__ = ("DEFAULT_TIMEOUT_SECONDS", "PROBE_PAGES", "Probe", "ProbeResult", "clean_title", "isolated_probe",
           "probe_pdf")
