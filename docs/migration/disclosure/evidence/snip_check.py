"""Check whether KG context_snippets occur verbatim (whitespace-normalized) in local PDFs. Prints counts only."""
import json, glob, re, sys, random, os
import fitz
base = sys.argv[1]
pdfs = {os.path.basename(p): p for p in glob.glob(os.path.join(base, 'papers', '**', '*.pdf'), recursive=True)}
norm = lambda s: re.sub(r'\s+', ' ', s).strip().lower()
cache = {}
def page_text(name, page):
    key = (name, page)
    if key not in cache:
        try:
            with fitz.open(pdfs[name]) as d:
                cache[key] = norm(d[page-1].get_text('text'))
        except Exception:
            cache[key] = None
    return cache[key]
rng = random.Random(0)
samples = []
for f in sorted(glob.glob(os.path.join(base, 'storage/kg/*.json'))):
    try: d = json.load(open(f))
    except Exception: continue
    for t in (d.get('things') or []):
        for s in t.get('context_snippets') or []:
            if isinstance(s, dict) and s.get('text') and os.path.basename(str(s.get('source_paper') or '')) in pdfs and str(s.get('page')).isdigit():
                samples.append((os.path.basename(s['source_paper']), int(s['page']), s['text']))
print('candidates_with_local_pdf', len(samples))
pick = rng.sample(samples, min(300, len(samples)))
exact = prefix = 0; checked = 0; lens = []
for name, page, text in pick:
    pt = page_text(name, page)
    if pt is None: continue
    checked += 1
    n = norm(text); lens.append(len(text))
    if n and n in pt: exact += 1
    elif n[:120] and n[:120] in pt: prefix += 1
print('checked', checked, 'exact_on_cited_page', exact, 'first120_on_cited_page', prefix, 'median_len', sorted(lens)[len(lens)//2] if lens else None)
