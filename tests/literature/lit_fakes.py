"""Offline fixtures for the T05 literature tests: contexts, synthetic PDFs and fake HTTP.

Nothing here touches the network. Provider responses are synthesized from the
shapes the donor tests and the provider docs describe; PDFs are generated with
reportlab (or as minimal hand-written PDF bytes).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import httpx

from app.modules.contracts.context import (
    AuthorizationMode, DeploymentProfile, Principal, RequestContext,
)

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def local_ctx(**kw: Any) -> RequestContext:
    """Local-operator context with chat and acquisition capabilities."""
    base: Dict[str, Any] = dict(
        profile=DeploymentProfile.LOCAL, mode=AuthorizationMode.LOCAL_TRUSTED,
        principal=Principal(subject="local-operator"), request_id="req-lit",
        capabilities=frozenset({"chat.ask", "ingest.acquire", "ingest.extract"}),
    )
    base.update(kw)
    return RequestContext(**base)


def als_user_ctx(**kw: Any) -> RequestContext:
    """Ordinary signed-in ALS user (chat only, no acquisition)."""
    base: Dict[str, Any] = dict(
        profile=DeploymentProfile.ALS_DEV, mode=AuthorizationMode.TILED_AUTHENTICATED,
        principal=Principal(subject="alice", issuer="https://tiled.example/", is_admin=False, verified_at=NOW),
        request_id="req-alice", capabilities=frozenset({"chat.ask"}),
    )
    base.update(kw)
    return RequestContext(**base)


def minimal_pdf_bytes(text: str = "Hello", *, pad: int = 2000) -> bytes:
    """A small, structurally valid one-page PDF with ``text`` (no PDF library needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    out += b"%" + b"x" * pad + b"\n"  # comment padding so the file clears size floors
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def have_pdf_stack() -> bool:
    """Whether the stacks that read and write the fixture PDFs are importable (pypdfium2, reportlab)."""
    try:
        import pypdfium2  # noqa: F401
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


def make_paper_pdf(path: Path, *, title: str, doi: str = "", sections: Iterable[Tuple[str, str]] = (),
                   figure: bool = False, table: bool = False, pages: int = 1) -> Path:
    """Write a synthetic paper PDF with reportlab (callers skip when the reader stack is unavailable).

    Headings are drawn larger than body text so a font-size heading detector sees
    them; an optional raster figure with a caption and a ruled table are added on page 1.
    Coordinates below are top-left points (as the fixtures were designed) and are
    flipped to reportlab's bottom-left origin when drawn.
    """
    from io import BytesIO

    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    height = 792
    ops: list[list[Callable[[Any], None]]] = []

    def text(page: int, x: float, y: float, value: str, size: float) -> None:
        def draw(c: Any) -> None:
            c.setFont("Helvetica", size)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(x, height - y, value)
        ops[page].append(draw)

    ops.append([])
    cur = 0
    y = 72
    text(cur, 72, y, title, 18)
    y += 28
    if doi:
        text(cur, 72, y, f"https://doi.org/{doi}", 9)
        y += 20
    for heading, body in sections:
        if y > 640:
            ops.append([])
            cur += 1
            y = 72
        text(cur, 72, y, heading, 14)
        y += 20
        for line in body.split("\n"):
            text(cur, 72, y, line, 10)
            y += 14
        y += 10
    if figure:
        img = Image.new("RGB", (200, 150))
        img.putdata([(((x * 7) % 256, (yy * 5) % 256, ((x + yy) * 3) % 256))
                     for yy in range(150) for x in range(200)])
        ops[0].append(lambda c: c.drawImage(ImageReader(img), 330, height - 570, width=200, height=150))
        text(0, 330, 590, "Figure 1. Scattering pattern of the film.", 9)
    if table:
        x0, y0, w, h = 72, 620, 240, 60

        def rules(c: Any) -> None:
            c.setStrokeColorRGB(0, 0, 0)
            c.setLineWidth(1)
            for i in range(4):
                c.line(x0, height - (y0 + i * 20), x0 + w, height - (y0 + i * 20))
            for j in range(4):
                c.line(x0 + j * 80, height - y0, x0 + j * 80, height - (y0 + h))
        ops[0].append(rules)
        cells = [["q", "I", "dI"], ["0.1", "10", "1"], ["0.2", "8", "1"]]
        for i, row in enumerate(cells):
            for j, value in enumerate(row):
                text(0, x0 + j * 80 + 5, y0 + i * 20 + 14, value, 9)
        text(0, 72, 612, "Table 1. Fitted parameters.", 9)
    while len(ops) < pages:
        ops.append([])
        text(len(ops) - 1, 72, 72, "continued", 10)
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, height), invariant=1)
    c.setTitle(title)
    c.setSubject(f"doi:{doi}" if doi else "")
    c.setAuthor("")
    c.setCreator("")
    for page_ops in ops:
        for draw in page_ops:
            draw(c)
        c.showPage()
    c.save()
    path.write_bytes(buf.getvalue())
    return path


class Router:
    """A tiny offline HTTP router for ``httpx.MockTransport``.

    Routes match on ``(method, host, path prefix)``; each records its calls. An
    unmatched request fails the test loudly instead of reaching the network.
    """

    def __init__(self) -> None:
        self.routes: List[Tuple[str, str, str, Callable[[httpx.Request], httpx.Response]]] = []
        self.calls: List[httpx.Request] = []

    def add(self, method: str, host: str, path: str, handler: Any) -> "Router":
        """Register a handler (callable or a static ``httpx.Response``-producing tuple)."""
        if not callable(handler):
            status, body = handler
            handler = _static(status, body)
        self.routes.append((method.upper(), host, path, handler))
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for method, host, path, handler in self.routes:
            if request.method == method and request.url.host == host and request.url.path.startswith(path):
                return handler(request)
        raise AssertionError(f"unexpected offline request {request.method} {request.url}")

    def client(self, **kw: Any) -> httpx.Client:
        """An ``httpx.Client`` bound to this router."""
        return httpx.Client(transport=httpx.MockTransport(self), **kw)

    def count(self, host: str, path: str = "") -> int:
        """How many recorded calls hit ``host`` (and ``path`` prefix)."""
        return sum(1 for r in self.calls if r.url.host == host and r.url.path.startswith(path))


def _static(status: int, body: Any) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(body, (bytes, bytearray)):
            return httpx.Response(status, content=bytes(body))
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)
    return handler


def openalex_work(doi: str, title: str, *, wid: str = "W1", year: int = 2020, pdf_url: str = "",
                  abstract: str = "small angle scattering of films") -> Dict[str, Any]:
    """An OpenAlex work record shaped like the API's JSON."""
    words = abstract.split()
    return {
        "id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}" if doi else None,
        "title": title, "display_name": title, "publication_year": year,
        "authorships": [{"author": {"id": "https://openalex.org/A1", "display_name": "Ada Lovelace"},
                         "institutions": [{"display_name": "LBNL"}]}],
        "primary_location": {"source": {"display_name": "J. Synchrotron"}, "pdf_url": pdf_url or None},
        "best_oa_location": {"pdf_url": pdf_url or None, "is_oa": bool(pdf_url)},
        "open_access": {"is_oa": bool(pdf_url), "oa_url": pdf_url or None},
        "cited_by_count": 3, "concepts": [{"display_name": "Scattering"}],
        "abstract_inverted_index": {w: [i] for i, w in enumerate(words)},
        "referenced_works": ["https://openalex.org/W9"], "ids": {},
    }


def json_dump(obj: Any) -> str:
    """Stable JSON for fixture files."""
    return json.dumps(obj, sort_keys=True)


__all__ = (
    "NOW", "Router", "als_user_ctx", "have_pdf_stack", "json_dump", "local_ctx", "make_paper_pdf",
    "minimal_pdf_bytes", "openalex_work",
)


def first(items: Iterable[Any]) -> Optional[Any]:
    """First item or ``None``."""
    for item in items:
        return item
    return None
