"""Offline fixtures for the T05 literature tests: contexts, synthetic PDFs and fake HTTP.

Nothing here touches the network. Provider responses are synthesized from the
shapes the donor tests and the provider docs describe; PDFs are generated with
reportlab (or as minimal hand-written PDF bytes).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple

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

    def text(page: int, x: float, y: float, value: str, size: float, bold: bool = False) -> None:
        def draw(c: Any) -> None:
            c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(x, height - y, value)
        ops[page].append(draw)

    ops.append([])
    cur = 0
    y = 72
    text(cur, 72, y, title, 18, bold=True)
    y += 28
    if doi:
        text(cur, 72, y, f"https://doi.org/{doi}", 9)
        y += 20
    for heading, body in sections:
        if y > 640:
            ops.append([])
            cur += 1
            y = 72
        y += 20   # a clear gap above a heading, and bold type: the layout model needs both to call it a heading
        text(cur, 72, y, heading, 14, bold=True)
        y += 22
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


class TypesetPaper(NamedTuple):
    """A typeset fixture PDF and where its figure and table sit (top-left points)."""

    path: Path
    page_size: Tuple[float, float]
    figure_box: Tuple[float, float, float, float]
    table_box: Tuple[float, float, float, float]


_TYPESET_PARAGRAPHS = {
    "intro": (
        "Resonant soft X-ray scattering probes the molecular orientation of polymer blends. The technique "
        "combines chemical contrast with the sensitivity of scattering to length scales. Here we describe the "
        "measurement geometry and the analysis used for the blend films. Body line two.",
        "The films were cast from solution onto silicon nitride windows and measured in transmission at the "
        "carbon absorption edge, where the contrast between the two components is largest. Each exposure was "
        "repeated to confirm that radiation damage did not alter the scattering from the films.",
        "The sample environment was held at a pressure below 1e-7 torr throughout the experiment and the sample "
        "temperature was controlled with a resistive heater mounted behind the window holder.",
    ),
    "results": (
        "We found q = 0.1 per angstrom for the dominant scattering feature in every blend. The peak position was "
        "stable across the annealing series and its width narrowed slowly. These observations are consistent "
        "with gradual coarsening of the domains over time.",
        "The fitted width is reported with its standard uncertainty. The uncertainty is dominated by the "
        "background subtraction and not by counting statistics, so longer exposures would not reduce it.",
        "The same trend was seen in all of the films that we measured, although the absolute width differed "
        "between the two casting solvents by a factor that we did not attempt to explain here.",
    ),
}


def make_typeset_paper_pdf(path: Path, *, doi: str = "", page_size: Tuple[float, float] = (612, 792),
                           ruled_table: bool = True) -> TypesetPaper:
    """Write a two-column journal-style paper PDF (generated here, so no third-party licence) with reportlab.

    Unlike :func:`make_paper_pdf` this is laid out the way a typeset article is: a running header, a centred
    title block and italic abstract over two columns of dense serif body text, a raster plot with a small
    ``Figure 1.`` caption *below* it and a ruled table (``ruled_table=False`` gives booktabs rules, whose column structure the table model finds less
    reliably) with a ``Table 1.`` caption *above* it, both one
    column wide. The Docling layout model reads captions in this setting as captions (the lone 9 pt Helvetica
    lines of ``make_paper_pdf`` come back as plain text, as do captions in non-embedded standard fonts), so this is the fixture for asserting caption linkage
    with real weights. ``page_size`` scales the margins and columns.
    """
    from io import BytesIO

    from PIL import Image, ImageDraw
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    import matplotlib
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Embedded STIX General (a Times-like face shipped with matplotlib, SIL OFL). The standard-14 "Times" names are
    # not embedded, so Docling's rasteriser substitutes a fallback face and the layout model stops seeing captions.
    fonts = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    for name, file in (("STIX-Roman", "STIXGeneral.ttf"), ("STIX-Bold", "STIXGeneralBol.ttf"),
                       ("STIX-Italic", "STIXGeneralItalic.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / file)))
    width, height = page_size
    margin, gutter = width * 0.115, 22.0
    col_w = (width - 2 * margin - gutter) / 2
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=page_size, invariant=1)
    c.setTitle("Resonant soft X-ray scattering of blends")
    c.setSubject(f"doi:{doi}" if doi else "")
    c.setAuthor("")
    c.setCreator("")
    c.setFillColorRGB(0, 0, 0)

    def wrap(value: str, font: str, size: float, limit: float) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in value.split():
            trial = f"{current} {word}".strip()
            if current and stringWidth(trial, font, size) > limit:
                lines.append(current)
                current = word
            else:
                current = trial
        return [*lines, current] if current else lines

    def text(x: float, top: float, value: str, font: str, size: float, *, centre: bool = False) -> None:
        c.setFont(font, size)
        (c.drawCentredString if centre else c.drawString)(x, height - top, value)

    # Running header and page number, as a journal page has.
    text(margin, 44, "Journal of Example Physics", "STIX-Italic", 8.5)
    text(width - margin - stringWidth("Vol. 1, 1-4", "STIX-Roman", 8.5), 44, "Vol. 1, 1-4", "STIX-Roman", 8.5)
    text(width / 2, height - 40, "1", "STIX-Roman", 9, centre=True)

    top = 84.0
    text(width / 2, top, "Resonant soft X-ray scattering of blends", "STIX-Bold", 17, centre=True)
    top += 22
    text(width / 2, top, "A. Author and B. Researcher", "STIX-Roman", 10.5, centre=True)
    top += 12.5
    text(width / 2, top, "Department of Materials Science, Example University", "STIX-Italic", 9, centre=True)
    top += 12.5
    if doi:
        text(width / 2, top, f"https://doi.org/{doi}", "STIX-Roman", 9, centre=True)
        top += 12.5
    top += 14
    abstract = ("We measure the molecular orientation and the domain size of polymer blend films by resonant soft "
                "X-ray scattering and follow the coarsening of the domains through an annealing series.")
    text(margin + 18, top, "Abstract", "STIX-Bold", 9.5)
    top += 12
    for part in wrap(abstract, "STIX-Italic", 9, width - 2 * margin - 36):
        text(margin + 18, top, part, "STIX-Italic", 9)
        top += 11
    top += 14

    cursor = [top, top]   # next free line in each column
    col = 0
    fig_box = tab_box = (0.0, 0.0, 0.0, 0.0)

    def x0() -> float:
        return margin + col * (col_w + gutter)

    def heading(value: str) -> None:
        cursor[col] += 8
        text(x0(), cursor[col] + 10, value, "STIX-Bold", 11)
        cursor[col] += 22

    def para(value: str) -> None:
        for part in wrap(value, "STIX-Roman", 9.5, col_w):
            text(x0(), cursor[col] + 9.5, part, "STIX-Roman", 9.5)
            cursor[col] += 11.6
        cursor[col] += 4

    def caption(label: str, value: str) -> None:
        bold = stringWidth(label + " ", "STIX-Bold", 8)
        for n, part in enumerate(wrap(value, "STIX-Roman", 8, col_w - bold)):
            if n == 0:
                text(x0(), cursor[col] + 8, label, "STIX-Bold", 8)
                text(x0() + bold, cursor[col] + 8, part, "STIX-Roman", 8)
            else:
                text(x0(), cursor[col] + 8, part, "STIX-Roman", 8)
            cursor[col] += 9.6

    intro, results = _TYPESET_PARAGRAPHS["intro"], _TYPESET_PARAGRAPHS["results"]
    heading("1. Introduction")
    para(intro[0])
    para(intro[1])
    # Figure 1 (left column): a plot-like raster, caption below.
    cursor[col] += 4
    fig_w, fig_h = col_w, col_w * 0.72
    plot = Image.new("RGB", (600, 432), (236, 240, 248))   # tinted, framed edge to edge: the box is unambiguous
    draw = ImageDraw.Draw(plot)
    draw.rectangle((0, 0, 599, 431), outline="black", width=4)
    draw.rectangle((70, 20, 580, 370), fill="white", outline="black", width=2)
    for k in range(1, 6):
        draw.line((70 + k * 85, 370, 70 + k * 85, 355), fill="black", width=2)
        draw.text((62 + k * 85, 380), f"0.{k}", fill="black")
    for k in range(1, 5):
        draw.line((70, 370 - k * 70, 85, 370 - k * 70), fill="black", width=2)
        draw.text((38, 364 - k * 70), f"{k * 2}", fill="black")
    draw.text((300, 405), "q (1/A)", fill="black")
    draw.text((8, 190), "I", fill="black")
    pts = [(70 + i * 8, 350 - int(300 / (1 + ((i - 30) / 6.0) ** 2)) - (i % 5)) for i in range(64)]
    draw.line(pts, fill=(200, 30, 30), width=3)
    draw.line([(px, min(360, py + 30 + (px % 9))) for px, py in pts], fill=(30, 60, 200), width=3)
    fig_top = cursor[col]
    c.drawImage(ImageReader(plot), x0(), height - fig_top - fig_h, width=fig_w, height=fig_h)
    fig_box = (x0(), fig_top, x0() + fig_w, fig_top + fig_h)
    cursor[col] += fig_h + 5
    caption("Figure 1.", "Scattering pattern of the film, integrated over the azimuth and plotted against the "
                         "scattering vector q for two annealing times.")
    cursor[col] += 8
    para(intro[2])
    # Second column: the results, with Table 1 captioned above.
    col = 1
    heading("2. Results")
    para(results[0])
    cursor[col] += 4
    caption("Table 1.", "Fitted parameters of the peak.")
    cursor[col] += 3
    row_h = 14.0
    tab_top = cursor[col]
    c.setStrokeColorRGB(0, 0, 0)
    for i in range(4):
        if ruled_table or i in (0, 1, 3):
            c.setLineWidth(0.9 if i in (0, 3) else 0.5)
            c.line(x0(), height - (tab_top + i * row_h), x0() + col_w, height - (tab_top + i * row_h))
    if ruled_table:
        c.setLineWidth(0.5)
        for j in range(4):
            c.line(x0() + j * col_w / 3, height - tab_top, x0() + j * col_w / 3, height - (tab_top + 3 * row_h))
    for i, row in enumerate([["q", "I", "dI"], ["0.1", "10", "1"], ["0.2", "8", "1"]]):
        for j, value in enumerate(row):
            text(x0() + (j + 0.5) * col_w / 3, tab_top + i * row_h + 10, value,
                 "STIX-Bold" if i == 0 else "STIX-Roman", 9, centre=True)
    tab_box = (x0(), tab_top, x0() + col_w, tab_top + 3 * row_h)
    cursor[col] += 3 * row_h + 12
    para(results[1])
    para(results[2])
    c.showPage()
    c.save()
    path.write_bytes(buf.getvalue())
    return TypesetPaper(path, page_size, fig_box, tab_box)


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
    "minimal_pdf_bytes", "openalex_work", "TypesetPaper", "make_typeset_paper_pdf",
)


def first(items: Iterable[Any]) -> Optional[Any]:
    """First item or ``None``."""
    for item in items:
        return item
    return None
