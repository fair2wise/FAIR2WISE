import os
import re
import fitz  # pip install pymupdf
from collections import Counter
from datetime import datetime

CURRENT_YEAR = datetime.now().year

# ---------- helpers ----------
def _yy_to_year(yy: int) -> int:
    """Map 2-digit year to 19xx or 20xx with a simple pivot at current year."""
    pivot = CURRENT_YEAR % 100 + 1  # e.g., 26 if 2025
    return (2000 + yy) if yy <= pivot else (1900 + yy)

def year_from_arxiv_filename(fname: str):
    """Handle arXiv new scheme YYYYMM.nnnnn and old scheme category_YYMMNNN."""
    base = os.path.basename(fname)

    # New scheme: e.g., 2306.07295v1.pdf -> '23' '06' => 2023
    m = re.search(r'(?<!\d)(\d{2})(\d{2})\.\d{4,5}(?:v\d+)?\.pdf$', base)
    if m:
        yy = int(m.group(1))
        return _yy_to_year(yy)

    # Old scheme: e.g., cond-mat_0510522v1.pdf or cond-mat/0510522v1.pdf
    m = re.search(r'[_/](\d{2})(\d{2})\d{3,5}(?:v\d+)?\.pdf$', base)
    if m:
        yy = int(m.group(1))
        return _yy_to_year(yy)

    return None

def year_from_metadata(pdf_path: str):
    try:
        doc = fitz.open(pdf_path)
        meta = doc.metadata or {}
        doc.close()
        # fitz uses keys like 'creationDate'/'modDate' in Adobe format D:YYYYMMDD...
        for key in ('creationDate', 'modDate'):
            v = meta.get(key)
            if not v:
                continue
            # Accept formats like D:20230322121500Z or just 2020
            m = re.search(r'(\d{4})', v)
            if m:
                y = int(m.group(1))
                if 1900 <= y <= CURRENT_YEAR:
                    return y
    except Exception:
        pass
    return None

def year_from_text(pdf_path: str, max_pages_front=2):
    years = []
    try:
        doc = fitz.open(pdf_path)
        pages_to_scan = list(range(min(max_pages_front, len(doc))))
        if len(doc) > 0:
            last_idx = len(doc) - 1
            if last_idx not in pages_to_scan:
                pages_to_scan.append(last_idx)

        text = []
        for i in pages_to_scan:
            try:
                text.append(doc[i].get_text())
            except Exception:
                pass
        doc.close()
        text = "\n".join(text)
        # FULL 4-digit year (non-capturing group), bounded by word boundaries
        candidates = re.findall(r'\b(?:19|20)\d{2}\b', text)
        years = [int(y) for y in candidates if 1900 <= int(y) <= CURRENT_YEAR]
        if not years:
            return None

        # Heuristic: prefer years appearing near 'Published', '©', 'Accepted', etc.
        weighted = Counter()
        for y in years:
            weighted[y] += 1

        # Keyword boost
        for kw in (r'Published', r'©|Copyright', r'Accepted', r'Received',
                   r'Version of Record', r'Issue', r'Volume', r'Proceedings'):
            for m in re.finditer(rf'(.{{0,40}})(\b(?:19|20)\d{{2}}\b)(.{{0,40}})', text, flags=re.IGNORECASE):
                y_str = m.group(2)
                if not re.search(kw, m.group(0), flags=re.IGNORECASE):
                    continue
                y = int(y_str)
                if 1900 <= y <= CURRENT_YEAR:
                    weighted[y] += 2  # boost

        # Pick highest weight, break ties by latest year
        best_year, best_score = None, -1
        for y, score in weighted.items():
            if score > best_score or (score == best_score and y > (best_year or 0)):
                best_year, best_score = y, score
        return best_year
    except Exception:
        return None

def guess_year(pdf_path: str):
    # Priority: arXiv from filename -> metadata -> text
    y = year_from_arxiv_filename(pdf_path)
    if y:
        return y
    y = year_from_metadata(pdf_path)
    if y:
        return y
    y = year_from_text(pdf_path)
    if y:
        return y
    return None

# ---------- main ----------
def scan_folder(folder: str, output_csv: str = "pdf_years.csv"):
    rows = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".pdf"):
            continue
        path = os.path.join(folder, name)
        y = guess_year(path)
        rows.append((name, y if y is not None else ""))  # empty if not found
        print(f"{name} -> {y}")

    with open(output_csv, "w", encoding="utf-8") as f:
        f.write("filename,year\n")
        for name, y in rows:
            f.write(f"{name},{y}\n")
    print(f"\nSaved: {output_csv}")

if __name__ == "__main__":
    scan_folder("papers")
