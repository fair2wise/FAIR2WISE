# Publication link strategy (planned)

Notes for a later implementation pass. Goal: fast path to the paper, one discovery link when it helps, no CAPTCHA traps, no duplicate links — for a scientific audience at scale.

## Primary rule: direct link when you can

| ID in metadata | Title click |
|----------------|-------------|
| Crossref DOI (`10.xxxx/...`) | `https://doi.org/{doi}` |
| arXiv (`arXiv:2111.08645v1`) | `https://arxiv.org/abs/{id}` |
| Nothing reliable | Semantic Scholar search |

`doi.org` and arXiv are the right defaults. They are stable, expected by researchers, and do not trigger CAPTCHA flows.

No URLs need to be stored in the KG. Links are derived at render time from `doi`, title, authors, and year.

## Secondary link: only when it adds something

| Case | Secondary link |
|------|----------------|
| DOI paper | Semantic Scholar (citations, related work, sometimes open PDF) |
| arXiv paper | **None** — title already goes to arXiv; footer would duplicate it |
| Metadata only | **None** — title already goes to Semantic Scholar search |

For arXiv entries, a second “Semantic Scholar” link is redundant. The footer `arxiv.org/abs/…` would also duplicate the title. Drop both extras; one clean link on the title is enough.

## What not to do

- **Google Scholar deep links** — CAPTCHAs on shared lab/university IPs; not fixable from the app side.
- **Store URLs in the KG** — derive from DOI / arXiv / title at render time.
- **Three links per paper** (title + doi line + Scholar) — clutter for little gain.

## Target UI

### Journal paper with DOI

```
Classification of grazing-incidence…              ↗
Smith · 2020 · Journal of Crystallography
doi.org/10.1107/… · Semantic Scholar
```

### arXiv paper

```
SAXS analysis via deep learning                      ↗
Author · 2021
```

One link on the title. No footer row.

### Metadata only (no DOI / arXiv)

```
Paper title from PDF                                 ↗
Author · 2019
```

Title → Semantic Scholar search. No secondary link.

## Resolver logic (reference)

```
Publication rendered
        │
        ▼
   Normalize `doi` field (strip https://doi.org/ prefix if present)
        │
        ├─ Crossref DOI (10.xxxx/...)     → primary: doi.org
        ├─ arXiv ID (arXiv:2111.08645v1)   → primary: arxiv.org/abs/…
        └─ Else                           → primary: Semantic Scholar search
                                              (title + author + year, or title only)
```

Semantic Scholar search URL:

```
https://www.semanticscholar.org/search?q={encodeURIComponent(query)}
```

Query priority: DOI → `arXiv:{id}` → title + first author + year → title only. Skip `.pdf` filenames as queries.

## Implementation touchpoints

- `ui/src/app/components/publicationLinks.ts` — resolver helpers
- `ui/src/app/components/PublicationList.tsx` — shared by chat answers and node popups

### Planned diff from current behavior

1. Keep doi.org and arXiv as primaries (already implemented).
2. Show Semantic Scholar secondary **only** when `primaryKind === 'doi'`.
3. Hide footer row entirely when `primaryKind === 'arxiv'` (no duplicate arxiv.org line).
4. When `primaryKind === 'search'`, no secondary link (title is already the search).

## Optional polish (later)

- **Copy DOI** icon when a DOI exists.
- **OpenAlex** as an extra secondary for DOI papers (`https://openalex.org/works/https://doi.org/{doi}`) if an open catalog view is useful.

## Summary

1. Keep doi.org and arXiv as primaries.
2. Use Semantic Scholar only as secondary for DOI papers, and as fallback when there is no ID.
3. Strip redundant links for arXiv papers.
4. Do not use Google Scholar as a hotlink.
