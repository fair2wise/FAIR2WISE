"""Chemical identity helpers (C-03). Similar structure or similar spelling is not identity.

Pure-Python formula handling always works; RDKit is optional. When RDKit is missing,
structure-level identity is *unresolved* (``None``), never a punctuation-stripped pseudo
identity. No PubChem or other network lookup lives here: the donor's rate-limited PubChem
resolution is deferred (it needs an owner-approved, injected authority client).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/chemical_identity.py (BSD-3-Clause).
Ported: canonical_smiles, structure_slug, written-formula parsing. Rewritten: the formula guard
used by :func:`chemical_verdict`. Not ported yet: pubchem_structure, formula_ambiguous,
plain_inorganic_name, structure_identifies (need RDKit; deferred to H3 material cases).
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import re

from .merge_safety import MergeVerdict


@lru_cache(maxsize=4096)
def canonical_smiles(smiles: str) -> str | None:
    """Normalize atom order keeping isotopes, stereochemistry and salts; ``None`` if unresolved.

    Wildcard/polymer structures, extended SMILES and a missing RDKit are all unresolved.
    """
    try:
        from rdkit import Chem, rdBase
    except ImportError:
        return None
    if not isinstance(smiles, str):
        return None
    text = smiles.strip()
    if not text or len(text) > 10000 or any(c.isspace() for c in text) or "|" in text:
        return None
    with rdBase.BlockLogs():
        molecule = Chem.MolFromSmiles(text)
    if molecule is None or any(atom.GetAtomicNum() == 0 for atom in molecule.GetAtoms()):
        return None
    for atom in molecule.GetAtoms():
        atom.SetAtomMapNum(0)
    return Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)


def structure_slug(smiles: str) -> str | None:
    """A molecular-graph key (hash of canonical SMILES), not a name or similarity key."""
    normalized = canonical_smiles(smiles)
    return "molecule_" + hashlib.sha256(normalized.encode()).hexdigest() if normalized else None


_FORMULA_TOKEN = re.compile(r"(?:[A-Z][a-z]?\d*(?:\.\d+)?|\([A-Za-z0-9.]+\)\d*)+")
#: A bracketed crystallographic qualifier: ``[0001]``, ``(110)``-style Miller indices, ``-alpha`` phases.
_QUALIFIER = re.compile(r"\[[0-9\-\s]+\]|\([0-9\-\s]{2,5}\)|\b(?:alpha|beta|gamma|delta)\b[-\s]*", re.IGNORECASE)


def formula_counts(text: str) -> dict | None:
    """Element counts of a written formula (``PbI2``, ``(NH4)2SO4``), or ``None``."""
    text = text.strip()
    if not re.fullmatch(r"[A-Za-z0-9()]+", text) or not re.match(r"[A-Z(]", text):
        return None

    def expand(match: re.Match[str]) -> str:
        return match.group(1) * int(match.group(2) or 1)

    flat = text
    while "(" in flat:
        new = re.sub(r"\(([^()]*)\)(\d*)", expand, flat)
        if new == flat:
            return None
        flat = new
    if not re.fullmatch(r"(?:[A-Z][a-z]?\d*)+", flat):
        return None
    counts: dict = {}
    for symbol, number in re.findall(r"([A-Z][a-z]?)(\d*)", flat):
        counts[symbol] = counts.get(symbol, 0) + int(number or 1)
    return counts


def looks_chemical(text: str) -> bool:
    """Whether a surface is written as a formula, optionally with a crystallographic qualifier."""
    core = _QUALIFIER.sub("", text).strip()
    return bool(core) and formula_counts(core) is not None and bool(re.search(r"[A-Z][a-z]?\d|[A-Z][a-z]", core))


def _split(text: str) -> tuple:
    qualifier = "".join(sorted(m.group(0).lower().strip() for m in _QUALIFIER.finditer(text)))
    core = _QUALIFIER.sub("", text).strip()
    return core, qualifier


def chemical_verdict(canonical: str, alias: str) -> MergeVerdict | None:
    """A verdict when either side is a written formula, else ``None`` (no chemical opinion).

    Two formula-like forms merge only when their element counts and crystallographic
    qualifiers are identical and the written forms differ at most in case/spacing. MoTe2 vs
    WTe2, Al2O3[0001] vs Al2O3 and alpha-/gamma-Fe2O3 therefore never merge. A formula-like
    form against a non-formula name is left to :func:`may_merge` plus the human reviewer;
    similarity never makes two chemicals one.
    """
    left, right = looks_chemical(canonical), looks_chemical(alias)
    if not (left and right):
        return None
    core_l, qual_l = _split(canonical)
    core_r, qual_r = _split(alias)
    if qual_l != qual_r:
        return MergeVerdict(False, "different crystallographic qualifier or phase (surface/polymorph is identity)")
    counts_l, counts_r = formula_counts(core_l), formula_counts(core_r)
    if counts_l != counts_r:
        return MergeVerdict(False, "different chemical composition")
    return MergeVerdict(True, "same composition and qualifier")
