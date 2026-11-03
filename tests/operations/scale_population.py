"""Synthetic populations far larger than any model context, with independent ground truth (T19D).

Everything here is invented: no paper text, title, DOI or file name is real. Three
populations are built deterministically from a seed:

* :func:`build_graph` - a json2kg-family graph (``things``/``associations``) of 60 000
  entities and about 190 000 edge records, citing 2 400 Works through 4 repeated-reference
  patterns (journal only; journal + arXiv preprint labelled with the journal DOI; path
  copies and a byte-identical renamed copy; arXiv-DOI stem + a later arXiv version);
  with duplicate, incomplete, dangling and self-loop edges, and T09 figure/table evidence
  on part of the entities.
* :func:`build_corpus` - an extracted-terms file of 60 000 term entries over 1 500 Works
  (journal / preprint-published pairs with equal titles and first author / path copies)
  with ``source_metadata`` titles, authors and years, figure- and table-derived entries.
* :func:`build_session_terms` - one session terms file with 120 000 records, 52 000 of
  them for the active paper, on text, figure-caption and table pages.

Ground truth is computed **here, by construction and by plain set arithmetic over the
raw payload**, never by calling ``app.modules.operations``: tests compare the operations
against these numbers. Every payload carries canary strings in the fields that must never
reach a report (descriptions, definitions, context snippets, figure captions, crop paths,
page text, abstracts); :data:`CANARIES` lists them.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.modules.vision.results import evidence as t09_evidence
from app.modules.vision.results import locator as t09_locator

SEED = 20261019
#: Characters of serialised population per token, a generous over-estimate of density.
CHARS_PER_TOKEN = 4
#: A context budget larger than any model F2W configures (1M tokens).
PROMPT_BUDGET_TOKENS = 1_000_000

CANARY_DESCRIPTION = "T19D-CANARY-DESCRIPTION-0aa1"
CANARY_SNIPPET = "T19D-CANARY-SNIPPET-PAGE-TEXT-77b2"
CANARY_CAPTION = "T19D-CANARY-FIGURE-CAPTION-3c94"
CANARY_TABLE = "T19D-CANARY-TABLE-BODY-91de"
CANARY_ABSTRACT = "T19D-CANARY-ABSTRACT-55f0"
CANARY_DEFINITION = "T19D-CANARY-DEFINITION-c6e3"
CANARY_CROP_PATH = "/private/f2w-crops/T19D-CANARY-CROP-0d7e.png"
CANARY_PDF_PATH = "/private/f2w-papers/T19D-CANARY-PDF-1f2a"
CANARY_IMAGE_B64 = "data:image/png;base64,VDE5RC1DQU5BUlktSU1BR0U="
#: Strings that must never appear in any operation output (the definition canary is checked
#: separately: the legacy paper summary prints definitions by design, graph/corpus reports never do).
CANARIES = (CANARY_DESCRIPTION, CANARY_SNIPPET, CANARY_CAPTION, CANARY_TABLE, CANARY_ABSTRACT, CANARY_CROP_PATH,
            CANARY_PDF_PATH, CANARY_IMAGE_B64, "/private/f2w-", "base64,")

FIG_SHA = "ab" * 32


def figure_evidence(work: str, page: int, figure_no: int, panel: Optional[str]) -> Dict[str, Any]:
    """A T09 figure/panel EvidenceRef (``vision.results.evidence``), JSON-dumped as a record would hold it."""
    fid = f"fig-p{page:03d}-{figure_no:02d}"
    loc = t09_locator(page, fid, panel, (10.0, 20.0, 200.0, 180.0))
    return t09_evidence(work, FIG_SHA, loc, "panel" if panel else "figure").model_dump(mode="json")


def table_evidence(work: str, page: int, table_no: int) -> Dict[str, Any]:
    """A T09 table EvidenceRef (``vision.tables``: the table id sits in ``locator.figure``)."""
    tid = f"tab-p{page:03d}-{table_no:02d}"
    loc = t09_locator(page, tid, None, (12.0, 300.0, 580.0, 520.0))
    return t09_evidence(work, FIG_SHA, loc, "table", tid).model_dump(mode="json")


def figure_payload(kind: str, paper: str, page: int, n: int) -> Dict[str, Any]:
    """Fields a figure/table-derived record carries: T09 evidence plus source material that must stay private."""
    ev = figure_evidence(paper, page, n, "b") if kind == "figure" else table_evidence(paper, page, n)
    return {
        "evidence": [ev],
        "evidence_modality": kind,
        "figure_caption": CANARY_CAPTION if kind == "figure" else None,
        "table_body": CANARY_TABLE if kind == "table" else None,
        "figure_crop": CANARY_CROP_PATH,
        "image_b64": CANARY_IMAGE_B64,
    }


# =====================================================================================
# graph
# =====================================================================================

@dataclass
class GraphTruth:
    """Exact figures of :func:`build_graph`, computed without the operations code."""

    entities: int
    edge_records: int
    relations: int
    incomplete_edge_records: int
    duplicate_edge_records: int
    dangling_relations: int
    self_loop_relations: int
    unique_predicates: int
    entities_with_relations: int
    isolated_entities: int
    category_count: int
    entities_by_category: Counter
    relations_by_predicate: Counter
    paper_keys: int
    distinct_works: int
    entities_with_citations: int
    figure_entities: int
    table_entities: int
    work_members: Dict[int, Set[str]] = field(default_factory=dict)  # work -> entity ids citing it
    work_keys: Dict[int, List[str]] = field(default_factory=dict)  # work -> basenames
    triples: Set[Tuple[str, str, str]] = field(default_factory=set)
    entity_ids: Set[str] = field(default_factory=set)

    def subgraph(self, work: int) -> Dict[str, int]:
        """``kg_paper_subgraph`` figures for one Work, by set arithmetic."""
        members = self.work_members[work]
        internal = {t for t in self.triples if t[0] in members and t[2] in members}
        boundary = {t for t in self.triples if (t[0] in members) != (t[2] in members)}
        neighbours = {t[2] if t[0] in members else t[0] for t in boundary} & self.entity_ids
        return {"entities": len(members), "internal_relations": len(internal),
                "boundary_relations": len(boundary), "neighbour_entities": len(neighbours),
                "paper_key_count": len(self.work_keys[work])}


def work_reference_keys(w: int) -> Tuple[List[str], List[Dict[str, str]]]:
    """``(source_papers values, publications)`` that cite Work ``w`` (four repeated-reference patterns).

    0: journal DOI stem only;
    1: journal DOI stem + arXiv preprint file whose publication record carries the journal DOI
       (the preprint/published pair, H3 K1 "equal DOI");
    2: the same file under two directories (path copies) + a byte-identical renamed copy whose
       publication record carries the same DOI;
    3: arXiv DOI stem (``10.48550_arXiv...``) + a later arXiv version file (equal arXiv id).
    """
    pattern = w % 4
    if pattern == 0:
        return [f"10.5555_syn.{w}.pdf"], []
    if pattern == 1:
        pre = f"24{w // 10000:02d}.{w % 10000:05d}v1.pdf"
        return [f"10.5555_syn.{w}.pdf", pre], [{"source_paper": pre, "doi": f"10.5555/syn.{w}"}]
    if pattern == 2:
        copy = f"syn_paper_{w} (1).pdf"
        return ([f"papers/rsoxs/2021/syn_paper_{w}.pdf", f"{CANARY_PDF_PATH}/syn_paper_{w}.pdf", copy],
                [{"source_paper": f"syn_paper_{w}.pdf", "doi": f"10.5555/syn.{w}"},
                 {"source_paper": copy, "doi": f"10.5555/syn.{w}"}])
    arx = f"25{w // 10000:02d}.{w % 10000:05d}"
    return [f"10.48550_arXiv.{arx}.pdf", f"{arx}v2.pdf"], []


def _basename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def build_graph(n_entities: int = 60_000, n_works: int = 2_400, n_edges: int = 180_000,
                seed: int = SEED) -> Tuple[Dict[str, Any], GraphTruth]:
    """The large json2kg-family graph and its exact figures."""
    rng = random.Random(seed)
    factor = n_entities / 60_000  # the defects below scale with the population (1.0 at full size)
    n_dangling, n_loops = round(400 * factor), round(300 * factor)
    n_dups, n_incomplete = round(9_000 * factor), round(1_200 * factor)
    things: List[Dict[str, Any]] = []
    cats: Counter = Counter()
    work_members: Dict[int, Set[str]] = {}
    work_keys: Dict[int, List[str]] = {}
    keys_seen: Set[str] = set()
    works_cited: Set[int] = set()
    citing = fig = tab = 0
    for i in range(n_entities):
        nid = f"syn:E{i:06d}"
        cat = f"Category{min(int(rng.expovariate(0.15)), 39):02d}"  # skewed: 40 categories, long tail
        cats[cat] += 1
        node: Dict[str, Any] = {"id": nid, "name": f"Synthetic entity {i}", "category": cat,
                                "description": CANARY_DESCRIPTION, "confidence": 0.5}
        if i % 50 != 7:  # 2% of entities cite nothing
            works = [i % n_works] + ([(i * 7 + 3) % n_works] if i % 3 == 0 else [])
            sources: List[str] = []
            pubs: List[Dict[str, str]] = []
            for w in works:
                refs, wpubs = work_reference_keys(w)
                chosen = refs if i % 5 == 0 else [refs[(i // n_works) % len(refs)]]
                sources += chosen
                # publication records only for the keys this entity cites
                pubs += [p for p in wpubs if p["source_paper"] in {_basename(s) for s in chosen}]
                work_members.setdefault(w, set()).add(nid)
                work_keys.setdefault(w, [])
                for s in chosen:
                    b = _basename(s)
                    keys_seen.add(b)
                    if b not in work_keys[w]:
                        work_keys[w].append(b)
                works_cited.add(w)
            node["source_papers"] = sources
            if pubs:
                node["publications"] = pubs
            node["context_snippets"] = [{"text": CANARY_SNIPPET, "source_paper": sources[0], "page": 1}]
            citing += 1
            if i % 11 == 0:  # T09 figure-derived evidence for the same Work (counted once)
                node.update(figure_payload("figure", _basename(sources[0]), 3, i % 9 + 1))
                fig += 1
            elif i % 13 == 0:
                node.update(figure_payload("table", _basename(sources[0]), 4, i % 4 + 1))
                tab += 1
        things.append(node)
    ids = {t["id"] for t in things}
    triples: Set[Tuple[str, str, str]] = set()
    assoc: List[Dict[str, Any]] = []
    complete_records = 0
    preds = [f"rel:P{j:02d}" for j in range(30)]
    while len(triples) < n_edges:
        s = f"syn:E{rng.randrange(n_entities):06d}"
        o = f"syn:E{rng.randrange(n_entities):06d}"
        p = preds[min(int(rng.expovariate(0.2)), 29)]
        triples.add((s, p, o))
        assoc.append({"subject": s, "predicate": p, "object": o, "has_evidence": None})
        complete_records += 1
    for k in range(n_dangling):  # dangling: an endpoint with no node
        t = (f"syn:E{rng.randrange(n_entities):06d}", "rel:P00", f"syn:Ghost{k}")
        triples.add(t)
        assoc.append({"subject": t[0], "predicate": t[1], "object": t[2]})
        complete_records += 1
    for k in range(n_loops):  # self loops
        e = f"syn:E{rng.randrange(n_entities):06d}"
        t = (e, "rel:SAME_AS", e)
        triples.add(t)
        assoc.append({"subject": e, "predicate": "rel:SAME_AS", "object": e})
        complete_records += 1
    distinct_list = sorted(triples)
    for k in range(n_dups):  # duplicate records of existing triples
        s, p, o = distinct_list[rng.randrange(len(distinct_list))]
        assoc.append({"subject": s, "predicate": p, "object": o, "has_evidence": f"dup{k}"})
        complete_records += 1
    for k in range(n_incomplete):  # incomplete records
        assoc.append({"subject": f"syn:E{k:06d}", "predicate": "" if k % 2 else None, "object": "syn:E000001"})
    rng.shuffle(assoc)
    in_rel = {s for s, _p, _o in triples} | {o for _s, _p, o in triples}
    truth = GraphTruth(
        entities=len(things), edge_records=len(assoc), relations=len(triples),
        incomplete_edge_records=n_incomplete, duplicate_edge_records=complete_records - len(triples),
        dangling_relations=sum(1 for s, _p, o in triples if s not in ids or o not in ids),
        self_loop_relations=sum(1 for s, _p, o in triples if s == o),
        unique_predicates=len({p for _s, p, _o in triples}),
        entities_with_relations=len(in_rel & ids), isolated_entities=len(ids - in_rel),
        category_count=len(cats), entities_by_category=cats,
        relations_by_predicate=Counter(p for _s, p, _o in triples),
        paper_keys=len(keys_seen), distinct_works=len(works_cited), entities_with_citations=citing,
        figure_entities=fig, table_entities=tab, work_members=work_members, work_keys=work_keys,
        triples=triples, entity_ids=ids,
    )
    return {"things": things, "associations": assoc, "metadata": {"id_prefix": "syn", "note": "T19D synthetic"}}, truth


# =====================================================================================
# corpus (extracted-terms file)
# =====================================================================================

@dataclass
class CorpusTruth:
    """Exact figures of :func:`build_corpus`."""

    term_entries: int
    distinct_works: int
    paper_keys: int
    legacy_total_papers: int
    works_with_multiple_keys: int
    works_per_year: Dict[str, int]
    legacy_year_increments: int
    figure_entries: int
    table_entries: int
    multi_version_titles: int


_WORDS = ("resonant", "soft", "x-ray", "scattering", "polymer", "blend", "thin", "film", "morphology", "domain",
          "orientation", "spectroscopy", "copolymer", "interface", "nanostructure", "photovoltaic", "electrolyte",
          "battery", "cathode", "ptychography", "diffraction", "absorption", "edge", "carbon", "nitrogen",
          "oxygen", "contrast", "anisotropy", "molecular", "packing", "crystalline", "amorphous", "kinetics",
          "annealing", "solvent", "vapor", "lamellar", "cylindrical", "spherical", "hierarchical")


def corpus_work_keys(w: int) -> List[Tuple[str, Dict[str, Any]]]:
    """``(source_papers value, source_metadata)`` per reference of corpus Work ``w``.

    Pattern ``w % 3``: 0 journal only; 1 journal + arXiv preprint with the same title and first
    author (no DOI on the preprint: K1 title rule); 2 the same journal file under two paths.
    Half of the preprint files carry an ``arXiv_`` prefix, which the legacy §1 check classifies
    as arXiv (so its arXiv x journal near-duplicate loop runs at scale); the other half are bare
    arXiv ids, which the legacy check counts as journal keys.
    """
    rng = random.Random(SEED * 31 + w)
    title = " ".join(rng.choice(_WORDS) for _ in range(rng.randrange(7, 13))) + f" study {w}"
    year = 2005 + w % 19
    authors = [f"Synthetic{w % 97}, A.", "Other, B."]
    journal = (f"10.5555_corpus.{w}.pdf", {"paper_title": title, "authors": authors, "publication_year": year,
                                           "doi": f"10.5555/corpus.{w}", "abstract": CANARY_ABSTRACT})
    if w % 3 == 0:
        return [journal]
    if w % 3 == 1:
        pre = f"{'arXiv_' if w % 2 else ''}23{w // 10000:02d}.{w % 10000:05d}v1.pdf"
        return [journal, (pre, {"paper_title": title, "authors": [f"A. Synthetic{w % 97}"],
                                "publication_year": year})]
    return [journal, (f"papers/rsoxs/{year}/{journal[0]}", journal[1])]


def build_corpus(n_terms: int = 60_000, n_works: int = 1_500, seed: int = SEED) -> Tuple[Dict[str, Any], CorpusTruth]:
    """The large extracted-terms corpus and its exact figures."""
    rng = random.Random(seed + 1)
    terms: List[Dict[str, Any]] = []
    keys: Set[str] = set()
    legacy_keys: Set[str] = set()
    legacy_increments = 0
    fig = tab = 0
    multi = 0
    for w in range(n_works):
        if len(corpus_work_keys(w)) > 1:
            multi += 1
    for i in range(n_terms):
        w = i % n_works
        refs = corpus_work_keys(w)
        chosen = refs if i < n_works * 2 else [refs[rng.randrange(len(refs))]]  # first pass cites every ref
        sources = [s for s, _m in chosen]
        meta = {s: m for s, m in chosen}
        rec: Dict[str, Any] = {
            "term": f"Synthetic term {i}", "category": f"Category{i % 25:02d}",
            "definition": f"{CANARY_DEFINITION} {i}", "pages": [1 + i % 12], "source_papers": sources,
            "context_snippets": [{"text": CANARY_SNIPPET, "source_paper": s, "page": 1 + i % 12} for s in sources],
            "source_metadata": meta, "relations": [],
        }
        if i % 17 == 0:
            rec.update(figure_payload("figure", _basename(sources[0]), 3, 1))
            fig += 1
        elif i % 19 == 0:
            rec.update(figure_payload("table", _basename(sources[0]), 5, 1))
            tab += 1
        terms.append(rec)
        for s in sources:
            keys.add(_basename(s))
            legacy_keys.add(s.removesuffix(".pdf"))
        legacy_increments += sum(1 for m in meta.values() if m.get("publication_year"))
    per_year: Counter = Counter(str(2005 + w % 19) for w in range(n_works))
    truth = CorpusTruth(
        term_entries=n_terms, distinct_works=n_works, paper_keys=len(keys), legacy_total_papers=len(legacy_keys),
        works_with_multiple_keys=sum(1 for w in range(n_works) if w % 3 == 1),  # path copies share a basename
        works_per_year=dict(sorted(per_year.items())), legacy_year_increments=legacy_increments,
        figure_entries=fig, table_entries=tab, multi_version_titles=multi,
    )
    return {"metadata": {"extraction": "T19D synthetic", "terms_path": CANARY_PDF_PATH}, "terms": terms}, truth


# =====================================================================================
# session terms (one active paper)
# =====================================================================================

ACTIVE = "synthetic_active_paper.pdf"


@dataclass
class SessionTruth:
    """Exact figures of :func:`build_session_terms` for :data:`ACTIVE`."""

    term_count: int
    figure_records: int
    table_records: int
    eligible_pages: List[int]


def build_session_terms(n_terms: int = 120_000, seed: int = SEED) -> Tuple[Dict[str, Any], Dict[str, Any],
                                                                            SessionTruth]:
    """``(terms file, manifest, truth)``: one paper's records among many other papers' records.

    The active paper has 30 pages; pages 1-24 are eligible (``selected_pages``). Records cite
    pages through ``context_snippets`` (text, figure captions on pages 3/7/11, tables on 5/9)
    or only through ``pages``. Truth follows the eligibility rule of
    ``summarize_extracted_terms`` (snippet pages of this file intersected with eligible pages,
    else ``pages`` intersected with eligible pages), applied here independently.
    """
    rng = random.Random(seed + 2)
    eligible = list(range(1, 25))
    elig = set(eligible)
    terms: List[Dict[str, Any]] = []
    count = figs = tables = 0
    for i in range(n_terms):
        mine = i % 30 < 13  # 13/30 of the records belong to the active paper
        paper = ACTIVE if mine else f"synthetic_other_{i % 211}.pdf"
        page = rng.randrange(1, 31)
        kind = "figure" if page in (3, 7, 11) and i % 2 == 0 else "table" if page in (5, 9) and i % 3 == 0 else "text"
        rec: Dict[str, Any] = {"term": f"Session term {i % 40_000}", "category": f"Kind{i % 9}",
                               "definition": f"{CANARY_DEFINITION} {i}", "pages": [page],
                               "source_papers": [paper]}
        if i % 4:
            snippet_page = page if i % 8 else rng.randrange(1, 31)
            rec["context_snippets"] = [{"text": CANARY_CAPTION if kind == "figure" else CANARY_SNIPPET,
                                        "source_paper": f"{CANARY_PDF_PATH}/{paper}", "page": snippet_page}]
        if kind != "text":
            rec.update(figure_payload(kind, paper, page, 1 + i % 3))
        terms.append(rec)
        if not mine:
            continue
        snippet_pages = {s["page"] for s in rec.get("context_snippets", [])} & elig
        pages = snippet_pages or ({page} & elig)
        if pages:
            count += 1
            figs += kind == "figure"
            tables += kind == "table"
    manifest = {"papers": {ACTIVE: {"extraction_state": "partial", "selected_pages": eligible,
                                    "pdf_path": f"{CANARY_PDF_PATH}/{ACTIVE}"}}}
    return {"terms": terms}, manifest, SessionTruth(term_count=count, figure_records=figs, table_records=tables,
                                                    eligible_pages=eligible)


# =====================================================================================
# helpers
# =====================================================================================

def write_json(path: Path, payload: Any) -> Path:
    """Write ``payload`` compactly (the size is reported against the prompt budget)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def prompt_budget_multiple(path: Path, scale: float = 1.0) -> float:
    """How many contexts the serialised population would fill (at 4 characters per token).

    The context is 1M tokens at ``scale=1.0`` (the full populations); a reduced population
    (:class:`Profile`) is held against a context reduced by the same factor, so it exceeds the
    prompt budget by the same multiple.
    """
    return path.stat().st_size / (CHARS_PER_TOKEN * PROMPT_BUDGET_TOKENS * scale)


@dataclass(frozen=True)
class Profile:
    """Sizes of the three populations. ``scale`` is the fraction of the full size (1.0 = T19D size)."""

    name: str
    scale: float
    graph_entities: int
    graph_works: int
    graph_edges: int
    corpus_terms: int
    corpus_works: int
    session_terms: int

    def n(self, full_value: int) -> int:
        """A full-size figure (record bound, slice, count) reduced to this profile."""
        return max(1, round(full_value * self.scale))


#: One tenth of the full size: the inline 7000-character bound, the scan bounds and the prompt
#: budget are all scaled with it, so every ratio the tests assert is the same (the populations
#: are still > 5 x the scaled context); runs in the default suite.
SMALL = Profile("small", 0.1, 6_000, 240, 18_000, 6_000, 150, 12_000)
#: The T19D populations (60k entities, 190k edge records, 120k session records): ``@pytest.mark.slow``.
FULL = Profile("full", 1.0, 60_000, 2_400, 180_000, 60_000, 1_500, 120_000)
PROFILES = {p.name: p for p in (SMALL, FULL)}


def leaked(text: str) -> List[str]:
    """Canaries present in ``text``."""
    return [c for c in CANARIES if c in text]
