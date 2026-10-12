"""Approximate per-paper character coverage of the union of distinct KG context_snippets. Prints numbers only."""
import json, glob, re, sys, os
import fitz
base = sys.argv[1]
pdfs = {os.path.basename(p): p for p in glob.glob(os.path.join(base, 'papers', '**', '*.pdf'), recursive=True)}
norm = lambda s: re.sub(r'\s+', ' ', s).strip().lower()
by_paper = {}
for f in sorted(glob.glob(os.path.join(base, 'storage/kg/*.json'))):
    try: d = json.load(open(f))
    except Exception: continue
    for t in (d.get('things') or []):
        for s in t.get('context_snippets') or []:
            if isinstance(s, dict) and s.get('text'):
                n = os.path.basename(str(s.get('source_paper') or ''))
                if n in pdfs and str(s.get('page')).isdigit():
                    by_paper.setdefault(n, {}).setdefault(int(s['page']), set()).add(norm(s['text']))
rows = []
for name, pages in by_paper.items():
    with fitz.open(pdfs[name]) as d:
        total = covered = 0
        for i in range(len(d)):
            pt = norm(d[i].get_text('text')); total += len(pt)
            mask = bytearray(len(pt))
            for snip in pages.get(i+1, ()):
                start = pt.find(snip)
                while start != -1:
                    mask[start:start+len(snip)] = b'\x01'*len(snip)
                    start = pt.find(snip, start+1)
            covered += sum(mask)
    rows.append(covered/total if total else 0)
rows.sort()
print('papers', len(rows), 'median_coverage', round(rows[len(rows)//2],3), 'max', round(rows[-1],3), 'n_over_50pct', sum(r>0.5 for r in rows), 'n_over_25pct', sum(r>0.25 for r in rows))
