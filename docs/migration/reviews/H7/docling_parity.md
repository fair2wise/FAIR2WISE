# Parser parity: Docling versus the removed PyMuPDF parser (H7 review packet note)

The migration plan (T09/E2) asks for a parity report before PyMuPDF is dropped. PyMuPDF is now removed
from the repository, the lock and every image (see `pixi_migration.md` for the dependency side). This
note records how the two parsers compared on a sample of the local paper corpus, so the owner can judge
the change in extraction input. It is a spot check, not an evaluation: no extraction quality or
binary-eval metric was run with the new parser.

## Method

- 8 papers drawn at random (seed 21) from the local, private `papers/` corpus (names withheld here: the
  corpus is not for the repository). Both parsers ran on the same files, one after the other.
- Old: `PyMuPDFParser` (font-size headings, embedded raster images as figures, ruled-table finder),
  recovered from commit `dfce595`. New: `DoclingParser` (layout and table-structure models, OCR off),
  pinned weights from `scripts/fetch_docling_models.py`, CPU.
- Counts come from the resulting `ParsedDocument`; `words` is the markdown word count.

## Results (old / new)

| Paper | Pages | Sections | Figures | Tables | Docling table cells | Words |
|---|---|---|---|---|---|---|
| 1 | 8 | 6 / 4 | 1 / 4 | 1 / 1 | 48 | 6125 / 5694 |
| 2 | 9 | 14 / 14 | 14 / 6 | 2 / 2 | 56 | 7134 / 6554 |
| 3 | 2 | 10 / 8 | 0 / 2 | 0 / 0 | 0 | 1085 / 1047 |
| 4 | 13 | 37 / 21 | 10 / 13 | 1 / 1 | 96 | 10926 / 10299 |
| 5 | 21 | 2 / 9 | 25 / 5 | 0 / 0 | 0 | 7029 / 6526 |
| 6 | 28 | 28 / 19 | 37 / 6 | 1 / 1 | 33 | 7203 / 6945 |
| 7 | 9 | 10 / 6 | 5 / 5 | 2 / 2 | 81 | 6287 / 6176 |
| 8 | 40 | 19 / 11 | 13 / 19 | 0 / 0 | 0 | 13238 / 12458 |
| **Total** | 130 | 126 / 92 | 105 / 60 | 7 / 7 | 314 | 59027 / 55699 |

Wall time for the 8 papers: 23 s (PyMuPDF) and 29 s (Docling) on CPU while other jobs ran;
a single 31-page paper took about 13 s with Docling when run alone.

## What differs, and why it matters

- **Tables: same count (7 / 7), now with cells.** Docling returns row/column positions, spans, text and a
  box per cell (314 cells here), so `vision/tables.py` no longer re-detects ruled tables on the PDF.
- **Sections: fewer, structurally better.** The old font-size heuristic produced 126 sections (and, on some
  papers, 2 for a whole article). Docling's layout model produced 92, with levels; it also treats the
  title and front matter differently (the title often appears as a level-1 section). Anything keyed on
  section names or counts will see different values.
- **Figures: different unit.** PyMuPDF reported every embedded raster image (105; sub-panels and
  small inline images included, filtered only by a 120 px floor). Docling reports figure-level pictures
  (60) and also finds vector figures. Figure ids (`fig-pNNN-II`) therefore do not correspond one-to-one
  between the two parsers, and figure/panel counts change per paper (for example 25 raster images became
  5 pictures on one 21-page paper, while another went from 1 to 4).
- **Text: about 94% of the old word count.** Docling drops page headers and footers and some boilerplate;
  page text used for quote checks and extraction comes from the PDF reader (pypdfium2), not from the
  parser, so quote matching is unaffected by this difference.
- **Page text engine changed too.** pypdfium2 agrees with PyMuPDF on 99 to 100% of characters after
  whitespace removal, but not byte for byte: reading order and ligature handling differ on some pages
  (about 3 of 10 pages match exactly after whitespace normalisation). Two engine quirks were normalised in
  `pdf_reader.py`: pdfium's U+FFFE soft-hyphen marks are removed, and page text again ends with a newline.
- **Rendering for the vision kernels** now comes from pdfium; edge anti-aliasing differs from MuPDF. One
  scale-bar test changed which reason it reports for a withheld candidate (both withhold); real scale bars
  on the synthetic fixtures are still measured. No tuned threshold was re-validated against the real
  figure corpus.

## Not done / owner decisions

1. Run the binary-eval figure and extraction cases (H3) with the new parser before relying on counts or ids
   from earlier KG releases; previously recorded figure ids will not match.
2. Decide whether the section-name changes need a migration for stored `ParsedDocument` records: documents
   parsed earlier keep loading (the new `cells` field is optional), but re-parsing yields different
   sections and figures.
3. The plan says the parity report covers the pilot papers; this one used a random local sample.
