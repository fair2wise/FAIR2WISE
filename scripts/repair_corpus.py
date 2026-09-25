#!/usr/bin/env python3
"""Corpus repair tool for extracted_terms JSON files.

Reads a FAIRtoWISE extracted-terms JSON, applies targeted repairs based on
a QA audit, and writes a repaired copy.  The original file is NEVER modified.

Usage::

    python scripts/repair_corpus.py repair
    python scripts/repair_corpus.py repair --terms storage/terminology/extracted_terms_rsoxs_v1.json
    python scripts/repair_corpus.py repair --output storage/terminology/extracted_terms_rsoxs_v1_repaired.json
    python scripts/repair_corpus.py repair --dry-run

Repairs applied
---------------
1. Hard stubs (no papers, no snippets, no pages) → quarantined.
2. Untyped stubs (category Thing/owl:Thing/Entity) → reclassified.
3. Known-bad relations → predicate corrected.
4. Near-duplicate terms (edit distance ≤ 2, same first 4 chars) → merged.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Optional

import typer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUB_CATEGORIES = {"Thing", "owl:Thing", "Entity", "entity", "NONE"}

# Reclassification keyword sets (checked against lower-cased label)
DETECTOR_KEYWORDS = {"detector", "ccd", "photodiode", "camera"}
INSTRUMENT_KEYWORDS = {
    "synchrotron", "beamline", "endstation", "spectrometer",
    "microscope", "diffractometer",
}
TECHNIQUE_KEYWORDS = {
    "scattering", "spectroscopy", "diffraction", "imaging",
    "microscopy", "tomography",
}
MATERIAL_KEYWORDS = {
    # polymer / formula patterns caught with regex
    "polymer", "thin film", "nanoparticle", "film",
}
MATERIAL_FORMULA_RE = re.compile(
    r"^[A-Z][a-z]?\d*|"          # starts with element symbol
    r"poly\(|poly[- ]|"           # poly(...), poly-, poly
    r"P3HT|PCBM|PC61BM|PC71BM|"  # common polymer abbreviations
    r"P[A-Z]{2,}|"                # polymer abbreviations like PTB7
    r"\bfilm\b|\bnanoparticle\b|\bnano\b",
    re.IGNORECASE,
)


# Known-bad relation patterns: (subject_contains_lower, bad_predicate, object_contains_lower, fixed_predicate)
KNOWN_BAD_FIXES = [
    # P3HT --[processed_by]--> P-RSoXS  (×2 variants)
    ("p3ht", "processed_by", "p-rsoxs", "characterized_by"),
    # CyRSoXS --[measures]--> P-RSoXS  (×3 variants; CyRSoXS is a simulation tool)
    ("cyrso", "measures", "p-rsoxs", "simulates"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise(s: str) -> str:
    """Normalise a string to lowercase ASCII for fuzzy comparisons."""
    s = unicodedata.normalize("NFC", s).lower().strip()
    return re.sub(r"\s+", " ", s)


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
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


def _term_weight(t: dict) -> int:
    """Score for 'how much evidence does this term have'."""
    return (
        len(t.get("source_papers") or []) * 3
        + len(t.get("context_snippets") or []) * 2
        + len(t.get("pages") or [])
    )


def _reclassify(label: str) -> tuple[str, bool]:
    """Return (new_category, needs_review) for a Thing/untyped node.

    Args:
        label: The term label string.

    Returns:
        Tuple of (category_string, needs_review_bool).
    """
    low = label.lower()

    if any(kw in low for kw in DETECTOR_KEYWORDS):
        return "Detector", False

    if any(kw in low for kw in INSTRUMENT_KEYWORDS):
        return "Instrument", False

    # Technique: standalone technique term (not a measurement instance)
    if any(kw in low for kw in TECHNIQUE_KEYWORDS):
        return "ExperimentalTechnique", False

    # Material: polymer names, chemical formulas, film/nanoparticle keywords
    if any(kw in low for kw in MATERIAL_KEYWORDS):
        return "Material", False
    if MATERIAL_FORMULA_RE.search(label):
        return "Material", False

    return "Thing", True  # keep as Thing but flag for review


# ---------------------------------------------------------------------------
# Repair steps
# ---------------------------------------------------------------------------


def step_quarantine_hard_stubs(
    terms: list[dict],
) -> tuple[list[dict], list[dict], int]:
    """Move hard stubs (no papers AND no snippets AND no pages) to quarantine.

    Args:
        terms: Full term list.

    Returns:
        Tuple of (kept_terms, quarantined_stubs, count_quarantined).
    """
    kept: list[dict] = []
    quarantined: list[dict] = []

    for t in terms:
        sp = t.get("source_papers") or []
        cs = t.get("context_snippets") or []
        pg = t.get("pages") or []
        if not sp and not cs and not pg:
            quarantined.append(t)
        else:
            kept.append(t)

    return kept, quarantined, len(quarantined)


def step_reclassify_untyped(terms: list[dict]) -> tuple[list[dict], int]:
    """Reclassify Thing/owl:Thing/Entity nodes based on label heuristics.

    Args:
        terms: Term list (after quarantine step).

    Returns:
        Tuple of (updated_terms, count_reclassified).
    """
    count = 0
    updated: list[dict] = []

    for t in terms:
        cat = t.get("category")
        if cat in STUB_CATEGORIES or cat is None:
            label = t.get("term", "")
            new_cat, needs_review = _reclassify(label)
            t = dict(t)  # shallow copy so we don't mutate original
            t["category"] = new_cat
            if needs_review:
                t["needs_review"] = True
            else:
                t.pop("needs_review", None)
            count += 1
        updated.append(t)

    return updated, count


def step_fix_known_bad_relations(terms: list[dict]) -> tuple[list[dict], int]:
    """Fix known-bad relation predicates.

    Args:
        terms: Term list.

    Returns:
        Tuple of (updated_terms, count_relations_fixed).
    """
    count = 0
    updated: list[dict] = []

    for t in terms:
        term_lower = _normalise(t.get("term", "") or "")
        rels = t.get("relations")
        if not rels:
            updated.append(t)
            continue

        new_rels = []
        changed = False
        for r in rels:
            if not isinstance(r, dict):
                new_rels.append(r)
                continue
            rel = r.get("relation", "")
            obj_lower = _normalise(str(r.get("related_term", "") or ""))
            fixed = False
            for subj_contains, bad_pred, obj_contains, good_pred in KNOWN_BAD_FIXES:
                if (
                    subj_contains in term_lower
                    and rel == bad_pred
                    and obj_contains in obj_lower
                ):
                    r = dict(r)
                    r["relation"] = good_pred
                    r["_repaired_from"] = bad_pred
                    count += 1
                    changed = True
                    fixed = True
                    break
            new_rels.append(r)

        if changed:
            t = dict(t)
            t["relations"] = new_rels
        updated.append(t)

    return updated, count


def step_merge_near_duplicates(
    terms: list[dict],
) -> tuple[list[dict], int, int]:
    """Merge near-duplicate terms (edit distance ≤ 2, same first 4 chars).

    For each mergeable pair:
    - Keep the entry with more papers/snippets (canonical).
    - Merge the other entry's papers/snippets into canonical.
    - Add dropped label to canonical's ``aliases`` list.
    - Remove the dropped entry.

    Pairs where the first 4 chars differ are only flagged, not merged.

    Args:
        terms: Term list.

    Returns:
        Tuple of (updated_terms, aliases_added, near_dupes_merged).
    """
    # Index: norm_label -> list index in `terms`
    norm_to_idx: dict[str, int] = {}
    # Build list of (norm_label, original_idx)
    norm_list: list[tuple[str, int]] = []
    for i, t in enumerate(terms):
        norm = _normalise(t.get("term", ""))
        norm_to_idx[norm] = i
        norm_list.append((norm, i))

    # Sort by norm label for O(n) consecutive comparisons
    norm_sorted = sorted(norm_list, key=lambda x: x[0])

    # Set of indices to drop
    to_drop: set[int] = set()
    # Map: canonical_idx -> list of (alias_label, donor_idx)
    merge_map: dict[int, list[tuple[str, int]]] = defaultdict(list)

    for i in range(len(norm_sorted) - 1):
        a_norm, a_idx = norm_sorted[i]
        b_norm, b_idx = norm_sorted[i + 1]

        if a_norm == b_norm:
            continue  # exact match — same entity already
        if a_idx in to_drop or b_idx in to_drop:
            continue  # already scheduled for removal
        if abs(len(a_norm) - len(b_norm)) > 3:
            continue

        dist = _levenshtein(a_norm[:60], b_norm[:60])
        if dist == 0 or dist > 2:
            continue

        # Same first 4 chars check (avoid false merges like unit variants)
        if len(a_norm) < 4 or len(b_norm) < 4:
            continue
        if a_norm[:4] != b_norm[:4]:
            continue

        # Decide canonical (more evidence wins)
        a_weight = _term_weight(terms[a_idx])
        b_weight = _term_weight(terms[b_idx])
        if a_weight >= b_weight:
            canonical_idx, donor_idx = a_idx, b_idx
            donor_label = terms[b_idx].get("term", b_norm)
        else:
            canonical_idx, donor_idx = b_idx, a_idx
            donor_label = terms[a_idx].get("term", a_norm)

        to_drop.add(donor_idx)
        merge_map[canonical_idx].append((donor_label, donor_idx))

    if not merge_map:
        return terms, 0, 0

    aliases_added = 0
    near_dupes_merged = 0

    # Apply merges
    updated = list(terms)  # shallow copy of list
    for canonical_idx, donor_pairs in merge_map.items():
        canon = dict(updated[canonical_idx])  # copy

        # Merge papers, snippets, pages (union)
        canon_papers = set(canon.get("source_papers") or [])
        canon_snippets = list(canon.get("context_snippets") or [])
        canon_pages = set(canon.get("pages") or [])
        canon_aliases = list(canon.get("aliases") or [])

        for donor_label, donor_idx in donor_pairs:
            donor = updated[donor_idx]
            canon_papers.update(donor.get("source_papers") or [])
            # Append snippets that aren't exact duplicates (snippets may be dicts or strings)
            canon_snippets_json = {json.dumps(s, sort_keys=True) for s in canon_snippets}
            for snip in (donor.get("context_snippets") or []):
                snip_key = json.dumps(snip, sort_keys=True)
                if snip_key not in canon_snippets_json:
                    canon_snippets.append(snip)
                    canon_snippets_json.add(snip_key)
            canon_pages.update(donor.get("pages") or [])

            # Add donor label to aliases if not already present
            if donor_label and donor_label not in canon_aliases:
                canon_aliases.append(donor_label)
                aliases_added += 1

            near_dupes_merged += 1

        canon["source_papers"] = sorted(canon_papers)
        canon["context_snippets"] = canon_snippets
        canon["pages"] = sorted(canon_pages)
        if canon_aliases:
            canon["aliases"] = canon_aliases
        updated[canonical_idx] = canon

    # Filter out dropped entries
    result = [t for i, t in enumerate(updated) if i not in to_drop]
    return result, aliases_added, near_dupes_merged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_main_app = typer.Typer(help=__doc__)
app = typer.Typer(help=__doc__)
_main_app.add_typer(app, name="repair", help="Apply targeted repairs to a corpus.")

DEFAULT_TERMS = Path("storage/terminology/extracted_terms_rsoxs_v1.json")
DEFAULT_OUTPUT = Path("storage/terminology/extracted_terms_rsoxs_v1_repaired.json")


@app.callback(invoke_without_command=True)
def repair(
    terms: Path = typer.Option(
        DEFAULT_TERMS,
        "--terms",
        help="Path to input extracted_terms JSON file.",
        show_default=True,
    ),
    output: Path = typer.Option(
        DEFAULT_OUTPUT,
        "--output",
        help="Path to write repaired JSON file.",
        show_default=True,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print repair stats without writing output file.",
    ),
) -> None:
    """Apply targeted repairs to an extracted_terms JSON file.

    The original file is NEVER modified.
    """
    if not terms.exists():
        typer.echo(f"ERROR: input file not found: {terms}", err=True)
        raise typer.Exit(1)

    if not dry_run and output.resolve() == terms.resolve():
        typer.echo("ERROR: --output must differ from --terms to avoid overwriting.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loading {terms} ({terms.stat().st_size / 1_048_576:.1f} MB) …")
    with terms.open("r", encoding="utf-8") as fh:
        data: dict = json.load(fh)

    original_terms: list[dict] = data.get("terms") or []
    metadata: dict = data.get("metadata") or {}
    typer.echo(f"Loaded {len(original_terms):,} term entries.")

    # ── Step 1: Quarantine hard stubs ────────────────────────────────────────
    typer.echo("\n[1/4] Quarantining hard stubs …")
    terms_list, quarantined, n_quarantined = step_quarantine_hard_stubs(original_terms)
    typer.echo(f"      Hard stubs quarantined : {n_quarantined:,}")
    typer.echo(f"      Remaining entries      : {len(terms_list):,}")

    # ── Step 2: Reclassify untyped stubs ─────────────────────────────────────
    typer.echo("\n[2/4] Reclassifying untyped (Thing/Entity/owl:Thing) nodes …")
    terms_list, n_reclassified = step_reclassify_untyped(terms_list)
    typer.echo(f"      Untyped nodes reclassified: {n_reclassified:,}")

    # ── Step 3: Fix known-bad relations ──────────────────────────────────────
    typer.echo("\n[3/4] Fixing known-bad relation predicates …")
    terms_list, n_relations_fixed = step_fix_known_bad_relations(terms_list)
    typer.echo(f"      Relations fixed            : {n_relations_fixed:,}")

    # ── Step 4: Merge near-duplicates ────────────────────────────────────────
    typer.echo("\n[4/4] Merging near-duplicate terms (edit distance ≤ 2, same first 4 chars) …")
    terms_list, n_aliases_added, n_merged = step_merge_near_duplicates(terms_list)
    typer.echo(f"      Near-dupes merged : {n_merged:,}")
    typer.echo(f"      Aliases added     : {n_aliases_added:,}")
    typer.echo(f"      Final entity count: {len(terms_list):,}")

    # ── Summary ───────────────────────────────────────────────────────────────
    repair_stats = {
        "stubs_quarantined": n_quarantined,
        "untyped_reclassified": n_reclassified,
        "relations_fixed": n_relations_fixed,
        "aliases_added": n_aliases_added,
        "near_dupes_merged": n_merged,
    }
    typer.echo("\n── Repair Stats ──────────────────────────────────────────────")
    for k, v in repair_stats.items():
        typer.echo(f"  {k:<25s}: {v:,}")

    if dry_run:
        typer.echo("\n[dry-run] No file written.")
        return

    # ── Write output ──────────────────────────────────────────────────────────
    output.parent.mkdir(parents=True, exist_ok=True)
    repaired_data = {
        "corpus_revision": "repaired-draft",
        "repaired_at": date.today().isoformat(),
        "repair_stats": repair_stats,
        "metadata": metadata,
        "_quarantined_stubs": quarantined,
        "terms": terms_list,
        # Preserve any other top-level keys from the original
        **{k: v for k, v in data.items() if k not in {
            "corpus_revision", "repaired_at", "repair_stats",
            "metadata", "_quarantined_stubs", "terms",
        }},
    }

    typer.echo(f"\nWriting repaired file to {output} …")
    with output.open("w", encoding="utf-8") as fh:
        json.dump(repaired_data, fh, indent=2, ensure_ascii=False)

    size_mb = output.stat().st_size / 1_048_576
    typer.echo(f"Done. Output: {output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    _main_app()
