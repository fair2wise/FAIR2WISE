#!/usr/bin/env python3
"""Corpus QA audit tool for extracted_terms JSON files.

Reads a FAIRtoWISE extracted-terms JSON and produces a structured quality
report to stdout.  Optionally writes a JSON summary to a file.

Usage::

    python scripts/corpus_qa.py audit
    python scripts/corpus_qa.py audit --terms storage/terminology/extracted_terms_rsoxs_v1.json
    python scripts/corpus_qa.py audit --output /tmp/report.json

Non-destructive: the terms file is never written.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

import typer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RSOXS_TECHNIQUE_KEYWORDS = [
    "rsoxs", "r-soxs", "rsxs", "p-rsoxs", "giwaxs", "gisaxs", "nexafs",
    "saxs", "waxs", "xas", "exafs",
]

MEASUREMENT_TYPES = {
    "RSoXSMeasurement", "MeasurementInstance", "Measurement",
    "ScatteringMeasurement", "DiffractionMeasurement",
}

STUB_CATEGORIES = {"Thing", "owl:Thing", "Entity", "entity", "NONE", None}

# RSoXS photon energy valid range (eV)
ENERGY_MIN_EV = 100.0
ENERGY_MAX_EV = 2000.0

# Relation slots treated as photon-energy values
ENERGY_RELATION_SLOTS = {
    "photon_energy_eV", "energy_eV", "energy", "photon_energy",
    "photon_energy_range",
}

# Known bad relations
KNOWN_BAD_PATTERNS = [
    {
        "description": "P3HT processed_by P-RSoXS (technique, not process)",
        "subject_contains": "p3ht",
        "relation": "processed_by",
        "object_contains": "p-rsoxs",
    },
    {
        "description": "CyRSoXS measures P-RSoXS (backward — tool measures technique?)",
        "subject_contains": "cyrso",
        "relation": "measures",
        "object_contains": "p-rsoxs",
    },
]


def _normalise(s: str) -> str:
    """Normalise a string to lowercase ASCII for fuzzy comparisons."""
    s = unicodedata.normalize("NFC", s).lower().strip()
    return re.sub(r"\s+", " ", s)


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Integer edit distance.
    """
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _title_overlap(a: str, b: str) -> float:
    """Return word-level Jaccard overlap between two titles.

    Args:
        a: First title string.
        b: Second title string.

    Returns:
        Float in [0, 1] — 1.0 = identical word sets.
    """
    wa = set(_normalise(a).split())
    wb = set(_normalise(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _doi_from_filename(filename: str) -> str:
    """Convert a source_paper filename back to a canonical DOI-like key.

    Args:
        filename: Filename string such as ``10.1002_aenm.201903248.pdf``.

    Returns:
        Key string without the ``.pdf`` suffix.
    """
    return filename.removesuffix(".pdf")


def _is_arxiv(doi_key: str) -> bool:
    """Return True if the DOI key represents an arXiv preprint."""
    return "arxiv" in doi_key.lower() or doi_key.startswith("10.48550")


def _try_float(value: Any) -> Optional[float]:
    """Try to parse *value* as float.

    Args:
        value: Arbitrary value from a JSON field.

    Returns:
        Float if parseable, else None.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # strip units: "284.0 eV" -> 284.0
        m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", value)
        if m:
            return float(m.group(1))
    return None


def _print_section(title: str, lines: list[str], *, indent: int = 2) -> None:
    """Print a titled section block to stdout.

    Args:
        title: Section heading string.
        lines: List of content lines.
        indent: Number of spaces to indent content lines.
    """
    pad = " " * indent
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print(f"{'=' * 72}")
    for line in lines:
        print(f"{pad}{line}")


# ---------------------------------------------------------------------------
# Section auditors
# ---------------------------------------------------------------------------


def audit_duplicate_works(
    terms: list[dict],
) -> tuple[list[str], dict]:
    """Audit §1 — Duplicate Works (papers).

    Detects:
    - DOI (source_paper filename) appearing in multiple distinct term entries.
    - arXiv/journal pairs with near-identical titles.

    Args:
        terms: List of term dicts from the JSON.

    Returns:
        Tuple of (output lines, summary dict).
    """
    lines: list[str] = []

    # Build: doi_key -> list of term names that cite it
    doi_to_term_names: dict[str, list[str]] = defaultdict(list)
    # Build: doi_key -> title (from source_metadata)
    doi_to_title: dict[str, str] = {}
    # Detect internal dups: same doi_key appearing twice in one term's source_papers
    internal_dup_terms: list[tuple[str, str]] = []

    for t in terms:
        sp = t.get("source_papers") or []
        term_name = t.get("term", "")
        seen_in_term: set[str] = set()
        for fn in sp:
            doi = _doi_from_filename(str(fn))
            doi_to_term_names[doi].append(term_name)
            if doi in seen_in_term:
                internal_dup_terms.append((term_name, doi))
            seen_in_term.add(doi)
        # Gather titles
        sm = t.get("source_metadata") or {}
        for fn, meta in sm.items():
            doi = _doi_from_filename(str(fn))
            if isinstance(meta, dict) and doi not in doi_to_title:
                doi_to_title[doi] = meta.get("paper_title") or ""

    unique_dois = len(doi_to_term_names)
    # A DOI is "duplicated" when the same paper key appears in >threshold terms.
    # More usefully: find DOIs cited by the most terms.
    doi_citation_counts = {d: len(ts) for d, ts in doi_to_term_names.items()}
    top10 = sorted(doi_citation_counts.items(), key=lambda x: -x[1])[:10]

    lines.append(f"Unique DOI/paper keys in corpus: {unique_dois}")
    lines.append(
        f"Internal duplicate source_papers (same doi listed twice in one term): {len(internal_dup_terms)}"
    )
    if internal_dup_terms:
        for tn, doi in internal_dup_terms[:10]:
            lines.append(f"  dup  term={tn!r}  doi={doi}")

    lines.append("\nTop-10 most-cited DOI keys (appears in most term entries):")
    for doi, cnt in top10:
        title = doi_to_title.get(doi, "")[:60]
        lines.append(f"  {cnt:4d}  {doi}  «{title}»")

    # arXiv vs journal near-duplicate detection
    arxiv_papers = {
        doi: title for doi, title in doi_to_title.items() if _is_arxiv(doi)
    }
    journal_papers = {
        doi: title for doi, title in doi_to_title.items() if not _is_arxiv(doi)
    }

    near_dupes: list[tuple[str, str, float]] = []
    for adoi, atitle in arxiv_papers.items():
        if not atitle:
            continue
        for jdoi, jtitle in journal_papers.items():
            if not jtitle:
                continue
            overlap = _title_overlap(atitle, jtitle)
            if overlap >= 0.80:
                near_dupes.append((adoi, jdoi, overlap))
            elif len(atitle) > 30 and len(jtitle) > 30:
                lev = _levenshtein(atitle[:100], jtitle[:100])
                if lev < 10:
                    near_dupes.append((adoi, jdoi, 1.0 - lev / 100))

    lines.append(
        f"\narXiv preprints: {len(arxiv_papers)}  "
        f"Journal papers: {len(journal_papers)}"
    )
    lines.append(
        f"arXiv/journal near-duplicate title pairs found: {len(near_dupes)}"
    )
    for adoi, jdoi, score in sorted(near_dupes, key=lambda x: -x[2])[:10]:
        atitle = arxiv_papers[adoi][:55]
        jtitle = journal_papers[jdoi][:55]
        lines.append(f"  overlap={score:.2f}  arXiv={adoi}")
        lines.append(f"             journal={jdoi}")
        lines.append(f"             arXiv title : {atitle!r}")
        lines.append(f"             journal title: {jtitle!r}")

    summary = {
        "unique_doi_keys": unique_dois,
        "internal_dup_count": len(internal_dup_terms),
        "arxiv_papers": len(arxiv_papers),
        "journal_papers": len(journal_papers),
        "arxiv_journal_near_dupes": len(near_dupes),
    }
    return lines, summary


def audit_stubs(terms: list[dict]) -> tuple[list[str], dict]:
    """Audit §2 — Stub / Thing nodes.

    A stub is a term entry that has no associated paper, no snippet, no page
    references, or whose category indicates it is effectively untyped.

    Args:
        terms: List of term dicts from the JSON.

    Returns:
        Tuple of (output lines, summary dict).
    """
    lines: list[str] = []
    stubs: list[tuple[str, list[str]]] = []

    for t in terms:
        reasons: list[str] = []
        cat = t.get("category")

        sp = t.get("source_papers") or []
        cs = t.get("context_snippets") or []
        pg = t.get("pages") or []

        if not sp:
            reasons.append("no_source_papers")
        if not cs:
            reasons.append("no_snippets")
        if not pg:
            reasons.append("no_pages")
        if cat in STUB_CATEGORIES:
            reasons.append(f"untyped_category={cat!r}")

        if reasons:
            stubs.append((t.get("term", ""), reasons))

    # Classify into "hard stubs" (all 4 conditions) vs "partial"
    hard_stubs = [
        (name, rs)
        for name, rs in stubs
        if "no_source_papers" in rs and "no_snippets" in rs and "no_pages" in rs
    ]
    thing_stubs = [
        (name, rs)
        for name, rs in stubs
        if any("untyped_category" in r for r in rs)
    ]

    lines.append(f"Total terms with ≥1 stub signal: {len(stubs)}")
    lines.append(f"Hard stubs (no papers AND no snippets AND no pages): {len(hard_stubs)}")
    lines.append(f"Untyped-category stubs (Thing/Entity/owl:Thing): {len(thing_stubs)}")
    lines.append("\nFirst 20 hard stubs:")
    for name, rs in hard_stubs[:20]:
        lines.append(f"  {name!r}  reasons={rs}")
    lines.append("\nFirst 20 untyped-category stubs:")
    for name, rs in thing_stubs[:20]:
        lines.append(f"  {name!r}  reasons={rs}")

    summary = {
        "stub_signals_total": len(stubs),
        "hard_stubs": len(hard_stubs),
        "untyped_stubs": len(thing_stubs),
    }
    return lines, summary


def audit_technique_measurement(terms: list[dict]) -> tuple[list[str], dict]:
    """Audit §3 — Technique-as-measurement confusion.

    Looks for entities whose label contains technique keywords and checks
    whether they are classified as ExperimentalTechnique but carry per-paper
    measurement evidence (source_papers or ``measures`` relations).

    Args:
        terms: List of term dicts from the JSON.

    Returns:
        Tuple of (output lines, summary dict).
    """
    lines: list[str] = []
    candidates: list[dict] = []
    correct_measurement: list[str] = []

    for t in terms:
        term_lower = _normalise(t.get("term", ""))
        cat = t.get("category", "")
        has_technique_kw = any(kw in term_lower for kw in RSOXS_TECHNIQUE_KEYWORDS)
        if not has_technique_kw:
            continue

        has_measurement_evidence = bool(t.get("source_papers"))
        has_measures_relation = any(
            isinstance(r, dict) and r.get("relation") == "measures"
            for r in (t.get("relations") or [])
        )

        if cat in MEASUREMENT_TYPES:
            correct_measurement.append(t.get("term", ""))
        elif cat == "ExperimentalTechnique" and (
            has_measurement_evidence or has_measures_relation
        ):
            candidates.append(
                {
                    "term": t.get("term"),
                    "category": cat,
                    "source_papers_count": len(t.get("source_papers") or []),
                    "has_measures_rel": has_measures_relation,
                }
            )

    lines.append(
        f"Technique-keyword terms correctly typed as measurement: {len(correct_measurement)}"
    )
    lines.append(
        f"ExperimentalTechnique terms with measurement evidence (candidates for reclassification): "
        f"{len(candidates)}"
    )
    lines.append(
        "\nFirst 20 reclassification candidates:"
    )
    for c in candidates[:20]:
        lines.append(
            f"  term={c['term']!r}  cat={c['category']}  "
            f"papers={c['source_papers_count']}  has_measures_rel={c['has_measures_rel']}"
        )

    summary = {
        "correct_measurement_typed": len(correct_measurement),
        "technique_reclassification_candidates": len(candidates),
    }
    return lines, summary


def audit_numeric_values(terms: list[dict]) -> tuple[list[str], dict]:
    """Audit §4 — Impossible/suspicious numeric values.

    Scans ``photon_energy_eV`` and related relation slots for out-of-range
    values.  Also scans ``absorption_edge`` relations for known-bad element/
    energy combinations.

    Args:
        terms: List of term dicts from the JSON.

    Returns:
        Tuple of (output lines, summary dict).
    """
    lines: list[str] = []
    suspicious: list[dict] = []
    string_instead_of_num: list[dict] = []

    for t in terms:
        term_name = t.get("term", "")
        for r in t.get("relations") or []:
            if not isinstance(r, dict):
                continue
            rel = r.get("relation", "")
            if rel not in ENERGY_RELATION_SLOTS:
                continue
            raw = r.get("related_term")
            fval = _try_float(raw)
            if fval is None:
                if isinstance(raw, str) and raw.strip():
                    # It's a non-empty string we couldn't parse
                    string_instead_of_num.append(
                        {"term": term_name, "slot": rel, "raw_value": raw}
                    )
            else:
                if fval < ENERGY_MIN_EV or fval > ENERGY_MAX_EV:
                    suspicious.append(
                        {
                            "term": term_name,
                            "slot": rel,
                            "value_eV": fval,
                            "raw": raw,
                        }
                    )

    # Absorption-edge element/formula mismatch heuristic
    edge_mismatches: list[dict] = []
    # Carbon K-edge ~ 284 eV; expect the host material to contain C
    CARBON_EDGE_PATTERN = re.compile(r"carbon\s*k|c\s*k[-\s]?edge", re.IGNORECASE)
    CARBON_FORMULA_RE = re.compile(r"[Cc](?:[^a-z]|$)")

    for t in terms:
        term_name = t.get("term", "")
        formula = t.get("formula") or ""
        for r in t.get("relations") or []:
            if not isinstance(r, dict) or r.get("relation") != "absorption_edge":
                continue
            edge_val = str(r.get("related_term", ""))
            if CARBON_EDGE_PATTERN.search(edge_val):
                if formula and not CARBON_FORMULA_RE.search(formula):
                    edge_mismatches.append(
                        {
                            "term": term_name,
                            "edge": edge_val,
                            "formula": formula,
                            "reason": "Carbon K-edge but no C in formula",
                        }
                    )

    lines.append(
        f"Photon-energy relations with out-of-range values "
        f"(<{ENERGY_MIN_EV} or >{ENERGY_MAX_EV} eV): {len(suspicious)}"
    )
    lines.append(
        f"Photon-energy slots with non-numeric string values: {len(string_instead_of_num)}"
    )
    lines.append(f"Absorption-edge element/formula mismatches: {len(edge_mismatches)}")

    lines.append("\nFirst 10 out-of-range energy values:")
    for s in suspicious[:10]:
        lines.append(
            f"  term={s['term']!r}  slot={s['slot']}  "
            f"value={s['value_eV']} eV  raw={s['raw']!r}"
        )

    lines.append("\nFirst 10 non-numeric energy strings:")
    for s in string_instead_of_num[:10]:
        lines.append(
            f"  term={s['term']!r}  slot={s['slot']}  raw={s['raw_value']!r}"
        )

    lines.append("\nFirst 10 edge/formula mismatches:")
    for m in edge_mismatches[:10]:
        lines.append(
            f"  term={m['term']!r}  edge={m['edge']!r}  "
            f"formula={m['formula']!r}  reason={m['reason']}"
        )

    summary = {
        "out_of_range_energy_values": len(suspicious),
        "non_numeric_energy_strings": len(string_instead_of_num),
        "edge_formula_mismatches": len(edge_mismatches),
    }
    return lines, summary


def audit_zero_measurement_papers(terms: list[dict]) -> tuple[list[str], dict]:
    """Audit §5 — Zero-measurement publications.

    For each unique source paper in the corpus, checks whether at least one
    term citing it is classified as a measurement type.

    Args:
        terms: List of term dicts from the JSON.

    Returns:
        Tuple of (output lines, summary dict).
    """
    lines: list[str] = []

    # doi -> set of categories seen
    doi_categories: dict[str, set[str]] = defaultdict(set)

    for t in terms:
        cat = t.get("category", "") or ""
        for fn in (t.get("source_papers") or []):
            doi = _doi_from_filename(str(fn))
            doi_categories[doi].add(cat)

    zero_measurement_papers = [
        doi
        for doi, cats in doi_categories.items()
        if not (cats & MEASUREMENT_TYPES)
    ]
    with_measurement = len(doi_categories) - len(zero_measurement_papers)

    lines.append(f"Total unique papers in corpus: {len(doi_categories)}")
    lines.append(
        f"Papers with ≥1 measurement-typed entity: {with_measurement}"
    )
    lines.append(
        f"Papers with ZERO measurement-typed entities: {len(zero_measurement_papers)}"
    )
    lines.append("\nFirst 20 zero-measurement papers:")
    for doi in sorted(zero_measurement_papers)[:20]:
        cats = sorted(doi_categories[doi])[:5]
        lines.append(f"  {doi}  entity_cats={cats}")

    summary = {
        "total_papers": len(doi_categories),
        "papers_with_measurement": with_measurement,
        "zero_measurement_papers": len(zero_measurement_papers),
    }
    return lines, summary


def audit_aliases(terms: list[dict]) -> tuple[list[str], dict]:
    """Audit §6 — Alias counts and over/under-merging.

    Counts terms that have ``aliases`` or ``alternate_labels`` fields,
    flags entities with >10 aliases (possible over-merging), and identifies
    pairs with near-identical labels that lack aliases (possible under-merging).

    Args:
        terms: List of term dicts from the JSON.

    Returns:
        Tuple of (output lines, summary dict).
    """
    lines: list[str] = []

    alias_counts: list[tuple[str, int]] = []
    total_aliases = 0

    for t in terms:
        aliases = t.get("aliases") or t.get("alternate_labels") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        n = len(aliases)
        total_aliases += n
        if n > 0:
            alias_counts.append((t.get("term", ""), n))

    over_merged = [(name, cnt) for name, cnt in alias_counts if cnt > 10]
    top10 = sorted(alias_counts, key=lambda x: -x[1])[:10]

    lines.append(f"Total aliases/alternate_labels across all terms: {total_aliases}")
    lines.append(f"Terms with any alias: {len(alias_counts)}")
    lines.append(f"Terms with >10 aliases (possible over-merging): {len(over_merged)}")

    lines.append("\nTop-10 by alias count:")
    for name, cnt in top10:
        lines.append(f"  {cnt:4d} aliases  {name!r}")

    # Under-merging: find term pairs with near-identical labels but no shared aliases
    # Only look at normalised names within short edit distance to keep it O(n log n)
    lines.append("\nPossible under-merging (near-identical labels, distinct entries):")
    # Build sorted list of normalised term names
    norm_terms = [(t.get("term", ""), _normalise(t.get("term", ""))) for t in terms]
    near_pairs: list[tuple[str, str]] = []
    # Only compare consecutive after sort (catches typos / slight variations)
    norm_sorted = sorted(norm_terms, key=lambda x: x[1])
    for i in range(len(norm_sorted) - 1):
        a_raw, a_norm = norm_sorted[i]
        b_raw, b_norm = norm_sorted[i + 1]
        if a_norm == b_norm:
            continue  # exact match — already known
        if len(a_norm) > 5 and abs(len(a_norm) - len(b_norm)) <= 3:
            dist = _levenshtein(a_norm[:60], b_norm[:60])
            if 0 < dist <= 2:
                near_pairs.append((a_raw, b_raw))

    lines.append(f"  Pairs with edit distance ≤2 in normalised label: {len(near_pairs)}")
    for a, b in near_pairs[:20]:
        lines.append(f"  {a!r}  ↔  {b!r}")

    summary = {
        "total_aliases": total_aliases,
        "terms_with_aliases": len(alias_counts),
        "over_merged_candidates": len(over_merged),
        "under_merged_candidates": len(near_pairs),
    }
    return lines, summary


def audit_relations(terms: list[dict]) -> tuple[list[str], dict]:
    """Audit §7 — Relation validation.

    Checks for:
    - Known bad relation patterns.
    - Self-referential edges (subject == object).
    - Overall edge count.

    Args:
        terms: List of term dicts from the JSON.

    Returns:
        Tuple of (output lines, summary dict).
    """
    lines: list[str] = []
    total_edges = 0
    self_refs: list[tuple[str, str]] = []
    known_bad_hits: list[dict] = []

    for t in terms:
        term_name = t.get("term", "") or ""
        term_lower = _normalise(term_name)
        rels = t.get("relations") or []
        for r in rels:
            if not isinstance(r, dict):
                continue
            total_edges += 1
            rel = r.get("relation", "")
            obj = str(r.get("related_term", "") or "")
            obj_lower = _normalise(obj)

            # Self-reference
            if term_lower and obj_lower and term_lower == obj_lower:
                self_refs.append((term_name, rel))

            # Known bad patterns
            for pat in KNOWN_BAD_PATTERNS:
                subj_match = pat["subject_contains"] in term_lower
                rel_match = rel == pat["relation"]
                obj_match = pat["object_contains"] in obj_lower
                if subj_match and rel_match and obj_match:
                    known_bad_hits.append(
                        {
                            "pattern": pat["description"],
                            "subject": term_name,
                            "relation": rel,
                            "object": obj,
                        }
                    )

    lines.append(f"Total relation edges: {total_edges}")
    lines.append(f"Self-referential edges (subject == object): {len(self_refs)}")
    lines.append(
        f"Known-bad pattern matches: {len(known_bad_hits)}"
    )

    lines.append("\nKnown-bad pattern hits:")
    for hit in known_bad_hits[:20]:
        lines.append(f"  [{hit['pattern']}]")
        lines.append(
            f"    {hit['subject']!r}  --[{hit['relation']}]-->  {hit['object']!r}"
        )

    lines.append("\nFirst 20 self-referential edges:")
    for subj, rel in self_refs[:20]:
        lines.append(f"  {subj!r}  --[{rel}]-->  (self)")

    # Top-5 most suspicious by rough heuristic: technique used_in technique
    tech_used_in_tech: list[tuple[str, str, str]] = []
    for t in terms:
        cat = t.get("category", "")
        if cat != "ExperimentalTechnique":
            continue
        for r in t.get("relations") or []:
            if not isinstance(r, dict):
                continue
            if r.get("relation") in ("used_in", "measures", "processed_by"):
                obj = str(r.get("related_term", "") or "")
                obj_lower = _normalise(obj)
                if any(kw in obj_lower for kw in RSOXS_TECHNIQUE_KEYWORDS):
                    tech_used_in_tech.append(
                        (t.get("term", ""), r["relation"], obj)
                    )

    lines.append(
        f"\nSuspicious 'ExperimentalTechnique → technique' cross-edges: "
        f"{len(tech_used_in_tech)}"
    )
    lines.append("Top-5 examples:")
    for subj, rel, obj in tech_used_in_tech[:5]:
        lines.append(f"  {subj!r} --[{rel}]--> {obj!r}")

    summary = {
        "total_edges": total_edges,
        "self_referential_edges": len(self_refs),
        "known_bad_hits": len(known_bad_hits),
        "tech_cross_edges": len(tech_used_in_tech),
    }
    return lines, summary


def audit_summary_stats(terms: list[dict], metadata: dict) -> tuple[list[str], dict]:
    """Audit §8 — Summary statistics.

    Reports totals, entity-type distribution, and papers-per-year histogram.

    Args:
        terms: List of term dicts from the JSON.
        metadata: Top-level metadata dict from the JSON.

    Returns:
        Tuple of (output lines, summary dict).
    """
    lines: list[str] = []

    total_entities = len(terms)
    cat_counter: Counter = Counter()
    all_dois: set[str] = set()
    total_edges = 0
    year_counter: Counter = Counter()

    for t in terms:
        cat = t.get("category") or "UNKNOWN"
        cat_counter[cat] += 1
        for fn in (t.get("source_papers") or []):
            all_dois.add(_doi_from_filename(str(fn)))
        total_edges += len([
            r for r in (t.get("relations") or []) if isinstance(r, dict)
        ])
        sm = t.get("source_metadata") or {}
        for doi_fn, meta in sm.items():
            if isinstance(meta, dict):
                yr = meta.get("publication_year")
                if yr:
                    year_counter[int(yr)] += 1

    top15_cats = cat_counter.most_common(15)

    lines.append(f"Total entities (terms): {total_entities}")
    lines.append(f"Total unique source papers: {len(all_dois)}")
    lines.append(f"Total relation edges: {total_edges}")
    lines.append(
        f"Corpus extraction metadata: {json.dumps(metadata)}"
    )

    lines.append("\nEntity type distribution (top-15):")
    for cat, cnt in top15_cats:
        bar = "█" * min(cnt // 50, 40)
        lines.append(f"  {cnt:5d}  {cat:<35s}  {bar}")

    lines.append("\nPapers per year (unique DOI/year combos in source_metadata):")
    for yr in sorted(year_counter.keys()):
        bar = "█" * min(year_counter[yr] // 3, 50)
        lines.append(f"  {yr}  {year_counter[yr]:3d}  {bar}")

    summary = {
        "total_entities": total_entities,
        "total_papers": len(all_dois),
        "total_edges": total_edges,
        "top_categories": dict(top15_cats),
        "papers_per_year": dict(sorted(year_counter.items())),
    }
    return lines, summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_main_app = typer.Typer(help=__doc__)
app = typer.Typer(help=__doc__)
_main_app.add_typer(app, name="audit", help="Run the full QA audit.")

DEFAULT_TERMS = Path("storage/terminology/extracted_terms_rsoxs_v1.json")


@app.callback(invoke_without_command=True)
def audit(
    terms: Path = typer.Option(
        DEFAULT_TERMS,
        "--terms",
        help="Path to extracted_terms JSON file.",
        show_default=True,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Optional path to write a JSON summary report.",
    ),
) -> None:
    """Run the full corpus QA audit and print a structured report to stdout.

    Non-destructive: the terms file is never modified.
    """
    if not terms.exists():
        typer.echo(f"ERROR: terms file not found: {terms}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loading {terms} ({terms.stat().st_size / 1_048_576:.1f} MB) …")
    with terms.open("r", encoding="utf-8") as fh:
        data: dict = json.load(fh)

    term_list: list[dict] = data.get("terms") or []
    metadata: dict = data.get("metadata") or {}

    typer.echo(f"Loaded {len(term_list):,} term entries.")

    report: dict[str, Any] = {}

    # §1
    sec1_lines, sec1_sum = audit_duplicate_works(term_list)
    _print_section("§1  DUPLICATE WORKS", sec1_lines)
    report["duplicate_works"] = sec1_sum

    # §2
    sec2_lines, sec2_sum = audit_stubs(term_list)
    _print_section("§2  STUB / THING NODES", sec2_lines)
    report["stubs"] = sec2_sum

    # §3
    sec3_lines, sec3_sum = audit_technique_measurement(term_list)
    _print_section("§3  TECHNIQUE-AS-MEASUREMENT CONFUSION", sec3_lines)
    report["technique_measurement"] = sec3_sum

    # §4
    sec4_lines, sec4_sum = audit_numeric_values(term_list)
    _print_section("§4  IMPOSSIBLE / SUSPICIOUS NUMERIC VALUES", sec4_lines)
    report["numeric_values"] = sec4_sum

    # §5
    sec5_lines, sec5_sum = audit_zero_measurement_papers(term_list)
    _print_section("§5  ZERO-MEASUREMENT PUBLICATIONS", sec5_lines)
    report["zero_measurement"] = sec5_sum

    # §6
    sec6_lines, sec6_sum = audit_aliases(term_list)
    _print_section("§6  ALIAS COUNTS", sec6_lines)
    report["aliases"] = sec6_sum

    # §7
    sec7_lines, sec7_sum = audit_relations(term_list)
    _print_section("§7  RELATION VALIDATION", sec7_lines)
    report["relations"] = sec7_sum

    # §8
    sec8_lines, sec8_sum = audit_summary_stats(term_list, metadata)
    _print_section("§8  SUMMARY STATS", sec8_lines)
    report["summary"] = sec8_sum

    # Key numbers recap
    _print_section(
        "KEY NUMBERS RECAP",
        [
            f"Total entities          : {sec8_sum['total_entities']:,}",
            f"Total papers            : {sec8_sum['total_papers']:,}",
            f"Total edges             : {sec8_sum['total_edges']:,}",
            f"Hard stubs              : {sec2_sum['hard_stubs']:,}",
            f"Untyped stubs           : {sec2_sum['untyped_stubs']:,}",
            f"Duplicate DOIs (intrnl) : {sec1_sum['internal_dup_count']:,}",
            f"arXiv/journal near-dupes: {sec1_sum['arxiv_journal_near_dupes']:,}",
            f"Out-of-range energy vals: {sec4_sum['out_of_range_energy_values']:,}",
            f"Non-numeric energy strs : {sec4_sum['non_numeric_energy_strings']:,}",
            f"Zero-measurement papers : {sec5_sum['zero_measurement_papers']:,}",
            f"Reclassification cands  : {sec3_sum['technique_reclassification_candidates']:,}",
            f"Self-referential edges  : {sec7_sum['self_referential_edges']:,}",
            f"Known-bad pattern hits  : {sec7_sum['known_bad_hits']:,}",
            f"Under-merge candidates  : {sec6_sum['under_merged_candidates']:,}",
        ],
    )

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        typer.echo(f"\nJSON summary written to {output}")


if __name__ == "__main__":
    _main_app()
